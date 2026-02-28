# norn/core/interceptor.py
"""
NornHook — Quality monitoring for Strands agents.
Tracks task execution, detects loops, evaluates efficiency.

Usage:
    from norn import NornHook

    guard = NornHook(task="Search for Python tutorials and save to file")
    agent = Agent(tools=[...], hooks=[guard])
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import threading
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from strands.hooks import HookProvider, HookRegistry
from strands.hooks.events import (
    AfterInvocationEvent,
    AfterToolCallEvent,
    BeforeInvocationEvent,
    BeforeToolCallEvent,
    MessageAddedEvent,
)

from norn.core.audit_logger import AuditLogger
from norn.core.step_analyzer import StepAnalyzer
from norn.models.schemas import (
    GuardMode,
    IssueType,
    QualityIssue,
    SessionQuality,
    SessionReport,
    StepRecord,
    StepStatus,
    TaskDefinition,
)

logger = logging.getLogger("norn.interceptor")

# Sensitive field names — values are redacted before writing to logs/dashboard
_SENSITIVE_KEYS: frozenset[str] = frozenset({
    "password", "passwd", "secret", "api_key", "apikey",
    "token", "access_token", "refresh_token", "id_token",
    "private_key", "access_key", "auth_key", "authorization",
    "credential", "credentials", "client_secret",
})


def _mask_sensitive(data: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy of *data* with sensitive values replaced by '***REDACTED***'.

    Only top-level keys are checked (nested dicts inside values are not
    recursed into — tool inputs are typically flat).
    """
    if not data:
        return data
    masked: dict[str, Any] = {}
    for k, v in data.items():
        if any(s in k.lower() for s in _SENSITIVE_KEYS):
            masked[k] = "***REDACTED***"
        else:
            masked[k] = v
    return masked


class NornHook(HookProvider):
    """
    Quality monitoring hook for Strands agents.
    
    Tracks every tool call, detects loops and inefficiencies,
    evaluates task completion with AI.
    
    Args:
        task: Task description (string or TaskDefinition)
        mode: "monitor" (observe only) or "intervene" (can cancel stuck loops)
        max_steps: Maximum steps before intervention
        enable_ai_eval: Whether to use Nova Lite for quality evaluation
        on_issue: Optional callback for quality issues
    """
    
    def __init__(
        self,
        task: str | TaskDefinition | None = None,
        mode: str = "monitor",
        max_steps: int = 50,
        enable_ai_eval: bool = True,
        enable_shadow_browser: bool = False,
        on_issue: Optional[Callable] = None,
        session_id: Optional[str] = None,
        audit_logger: Optional[Any] = None,
        norn_url: Optional[str] = None,    # Dashboard API URL
        agent_name: Optional[str] = None,  # Human-readable agent name
        swarm_id: Optional[str] = None,       # Multi-agent swarm group ID
        swarm_order: Optional[int] = None,    # Position in swarm pipeline (1-based)
        handoff_input: Optional[str] = None,  # Data received from the previous agent
    ):
        # Task definition
        if isinstance(task, str):
            self.task = TaskDefinition(description=task, max_steps=max_steps)
        elif isinstance(task, TaskDefinition):
            self.task = task
        else:
            self.task = None

        self.mode = GuardMode(mode)
        self.max_steps = max_steps
        self.enable_ai_eval = enable_ai_eval
        self.enable_shadow_browser = enable_shadow_browser
        self.on_issue = on_issue
        self.session_id = session_id

        # Swarm tracking
        self.swarm_id = swarm_id
        self.swarm_order = swarm_order
        self.handoff_input = handoff_input

        # Components
        self.step_analyzer = StepAnalyzer()
        self.audit = audit_logger if audit_logger else AuditLogger()

        # Dashboard integration
        self._norn_url: Optional[str] = norn_url.rstrip("/") if norn_url else None
        self._external_agent_name: Optional[str] = agent_name
        self._registered_agent_id: Optional[str] = None
        self._existing_step_count: int = 0  # Steps from previous runs in the same session

        # Runtime state
        self._agent_name: str = "unknown"
        self._session_start: float = 0.0
        self._step_counter: int = 0
        self._steps: list[StepRecord] = []
        self._issues: list[QualityIssue] = []
        self._session_report: Optional[SessionReport] = None
        self._loop_detected: bool = False
        self._pending_tasks: list[asyncio.Task] = []  # Kept for compatibility, no longer used
        self._steps_to_evaluate: list[tuple] = []  # (StepRecord, full_result) — evaluated in bg loop

        # Lazy-loaded components
        self._evaluator = None
        self._shadow_browser = None
    
    # ── Hook Registration ──────────────────────────────────
    
    def register_hooks(self, registry: HookRegistry, **kwargs) -> None:
        """Register callbacks with Strands hook system."""
        registry.add_callback(BeforeInvocationEvent, self._on_session_start)
        registry.add_callback(MessageAddedEvent, self._on_message_added)
        registry.add_callback(BeforeToolCallEvent, self._on_before_tool)
        registry.add_callback(AfterToolCallEvent, self._on_after_tool)
        registry.add_callback(AfterInvocationEvent, self._on_session_end)
        logger.info("Norn quality monitoring active (mode=%s)", self.mode.value)

    # ── Session Lifecycle ──────────────────────────────────

    def _on_session_start(self, event: BeforeInvocationEvent) -> None:
        """Initialize session tracking."""
        if self._external_agent_name:
            self._agent_name = self._external_agent_name
        else:
            self._agent_name = getattr(event.agent, "name", None) or "agent"
        self._session_start = time.time()
        self._step_counter = 0
        self._existing_step_count = 0
        self._steps = []
        self._issues = []
        self._loop_detected = False
        self._pending_tasks = []
        self._steps_to_evaluate = []
        self.step_analyzer.reset()

        # Try to extract model name from the agent
        model_name = None
        agent = event.agent
        if hasattr(agent, "model_id"):
            model_name = agent.model_id
        elif hasattr(agent, "model"):
            model_name = str(agent.model) if agent.model else None

        # Fixed session ID — unique per swarm run, persistent for solo hook agents
        if self._norn_url and self._external_agent_name:
            slug = "".join(
                c if c.isalnum() or c == "_" else "_"
                for c in self._external_agent_name.lower().replace(" ", "_")
            )
            if self.swarm_id:
                # Swarm agent: unique session per swarm run (swarm_id already has timestamp)
                swarm_slug = "".join(
                    c if c.isalnum() or c == "_" else "_"
                    for c in self.swarm_id
                )
                fixed_session_id = f"swarm-{swarm_slug}-{slug}"
            else:
                # Solo hook agent: fixed ID (accumulates across runs)
                fixed_session_id = f"hook-{slug}"
        else:
            fixed_session_id = None

        self._session_report = SessionReport(
            agent_name=self._agent_name,
            model=model_name,
            task=self.task,
            swarm_id=self.swarm_id,
            swarm_order=self.swarm_order,
            handoff_input=self.handoff_input,
        )

        # Apply session ID: fixed slug takes priority, then caller-provided ID
        if fixed_session_id:
            self._session_report.session_id = fixed_session_id
        elif self.session_id:
            # Use the git-* session_id provided by _execute_agent_background()
            # so the hook resumes the file already created by the run endpoint
            self._session_report.session_id = self.session_id

        logger.info(f"Session started: {self._agent_name}")

        # Dashboard entegrasyonu
        if self._norn_url:
            self._dashboard_on_session_start()

    def _on_message_added(self, event: MessageAddedEvent) -> None:
        """Auto-set task from the first user message.
        Also captures assistant reasoning as a synthetic step for tool-less agents.
        """
        msg = event.message
        role = msg.get("role", "")
        content_blocks = msg.get("content", [])

        # Auto-set task from first user message
        if self.task is None and role == "user":
            for block in content_blocks:
                if isinstance(block, dict) and "text" in block:
                    user_text = block["text"].strip()
                    if user_text:
                        self.task = TaskDefinition(
                            description=user_text,
                            max_steps=self.max_steps,
                        )
                        if self._session_report and self._session_report.task is None:
                            self._session_report.task = self.task
                        logger.debug("Task auto-set: %s", user_text[:80])
                        break

        # Capture assistant reasoning as synthetic step (for tool-less / pure-reasoning agents)
        # Only when: role=assistant AND no toolUse blocks AND no tool calls made this invocation
        # Strands uses Bedrock format: {"text": "..."} and {"toolUse": {...}} — NOT Anthropic {"type":"text"}
        if role == "assistant" and content_blocks and self._session_report:
            has_tool_use = any(
                isinstance(b, dict) and "toolUse" in b
                for b in content_blocks
            )
            # Guard: skip if any tool calls were already made in this invocation
            invocation_tool_steps = self._step_counter - self._existing_step_count
            if not has_tool_use and invocation_tool_steps == 0:
                text_parts = []
                for block in content_blocks:
                    if isinstance(block, dict) and "text" in block:
                        text_parts.append(block["text"])
                    elif isinstance(block, str):
                        text_parts.append(block)
                reasoning_text = "\n".join(text_parts).strip()

                if reasoning_text:
                    self._step_counter += 1
                    step = StepRecord(
                        step_number=self._step_counter,
                        tool_name="ai_reasoning",
                        tool_input={"task": self.task.description[:200] if self.task else ""},
                        tool_result=reasoning_text[:800] + ("..." if len(reasoning_text) > 800 else ""),
                        status=StepStatus.SUCCESS,
                        # Pre-mark as relevant & secure — no further LLM eval needed
                        relevance_score=100,
                        security_score=100,
                    )
                    self._steps.append(step)

                    # Real-time dashboard stream
                    if self._norn_url and self._registered_agent_id:
                        self._dashboard_send_step(step)

                    logger.debug("Reasoning step #%d captured (%d chars)", self._step_counter, len(reasoning_text))

    def _on_session_end(self, event: AfterInvocationEvent) -> None:
        """Finalize session and generate report."""
        if not self._session_report:
            return

        execution_time = (time.time() - self._session_start) * 1000  # ms

        # Update report
        self._session_report.ended_at = datetime.now(timezone.utc)
        self._session_report.total_steps = len(self._steps)
        self._session_report.successful_steps = sum(
            1 for s in self._steps if s.status == StepStatus.SUCCESS
        )
        self._session_report.failed_steps = sum(
            1 for s in self._steps if s.status == StepStatus.FAILED
        )
        self._session_report.irrelevant_steps = sum(
            1 for s in self._steps if s.status == StepStatus.IRRELEVANT
        )
        self._session_report.redundant_steps = sum(
            1 for s in self._steps if s.status == StepStatus.REDUNDANT
        )
        self._session_report.steps = self._steps
        self._session_report.issues = self._issues
        self._session_report.loop_detected = self._loop_detected
        self._session_report.total_execution_time_ms = execution_time

        # Check efficiency
        if self.task:
            efficiency_issues = self.step_analyzer.check_efficiency(
                len(self._steps),
                self.task.max_steps
            )
            self._issues.extend(efficiency_issues)

        # Calculate efficiency score (heuristic, may be overridden by AI eval)
        if self.task and self.task.max_steps > 0:
            efficiency = max(0, 100 - (len(self._steps) - self.task.max_steps) * 10)
            self._session_report.efficiency_score = min(100, efficiency)

        # Finalize with heuristic scores first (fast)
        self._finalize_report()

        # Run AI evaluation synchronously — blocks until done.
        #
        # Why not a background thread?
        # CPython runs atexit handlers BEFORE joining non-daemon threads.
        # concurrent.futures._python_exit() (registered via atexit) shuts down
        # ALL thread-pool executors before our bg-thread finishes its Bedrock call
        # → "cannot schedule new futures after interpreter shutdown".
        #
        # Solution: run evaluation in a short-lived ThreadPoolExecutor whose
        # lifetime is managed explicitly by the `with` block — shutdown(wait=True)
        # is called synchronously here, before atexit has any chance to run.
        if self.enable_ai_eval and self.task:
            import concurrent.futures as _cf

            def _eval_target():
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                try:
                    new_loop.run_until_complete(self._await_pending_and_evaluate())
                finally:
                    new_loop.close()
                    asyncio.set_event_loop(None)

            try:
                with _cf.ThreadPoolExecutor(max_workers=1, thread_name_prefix="norn-eval") as _exec:
                    _exec.submit(_eval_target).result(timeout=60)
            except Exception as e:
                logger.error("AI evaluation failed: %s", e)

    async def _await_pending_and_evaluate(self) -> None:
        """Run all queued step evaluations, then run session AI eval.

        Step evals are collected as plain tuples (not asyncio.Task) to avoid
        cross-event-loop issues: tasks created in Strands' loop cannot be
        awaited from the background eval thread's new loop.
        """
        if self._steps_to_evaluate:
            logger.info(f"Evaluating {len(self._steps_to_evaluate)} queued steps...")
            for item in self._steps_to_evaluate:
                step, full_result, tool_name, tool_input, mode = item
                try:
                    await self._evaluate_step_relevance(step, full_result)
                except Exception as e:
                    logger.warning(f"Step eval failed for {step.tool_name}: {e}")
                if mode == "shadow" and self.enable_shadow_browser:
                    try:
                        await self._verify_with_shadow_browser(step, tool_name, tool_input, full_result)
                    except Exception as e:
                        logger.warning(f"Shadow browser eval failed: {e}")
            self._steps_to_evaluate.clear()

        # Now run session-level AI evaluation with all step scores available
        if self.enable_ai_eval:
            await self._run_ai_evaluation()
        else:
            self._finalize_report()
    
    def run_session_evaluation(self) -> None:
        """Run session-level AI evaluation synchronously.

        Call this after agent completes. Step-level scores are already
        populated by Strands' event loop during execution, so we only
        need to run the session-level evaluation in a fresh event loop.
        """
        if not self.enable_ai_eval or not self.task or not self._session_report:
            return

        try:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(self._run_ai_evaluation())
            finally:
                loop.close()
        except Exception as e:
            logger.error(f"Session AI evaluation failed: {e}")
            # Keep heuristic scores from _on_session_end

    # ── Tool Call Interception ─────────────────────────────

    def _on_before_tool(self, event: BeforeToolCallEvent) -> None:
        """Check before tool execution - can intervene if stuck."""
        self._step_counter += 1
        tool_name = event.tool_use.get("name", "unknown")
        tool_input = event.tool_use.get("input", {})
        
        # Check for loops and issues
        status, issues = self.step_analyzer.analyze_step(
            tool_name,
            tool_input,
            self._step_counter
        )
        
        # Record issues
        for issue in issues:
            self._issues.append(issue)
            if self.on_issue:
                self.on_issue(issue)
            
            # Check for critical loop
            if issue.issue_type.value == "INFINITE_LOOP" and issue.severity >= 8:
                self._loop_detected = True

            # Mark security breach for any high-severity security issue found deterministically
            if issue.issue_type.value == "SECURITY_BYPASS" and issue.severity >= 7:
                if self._session_report:
                    self._session_report.security_breach_detected = True
                
                if self.mode == GuardMode.INTERVENE:
                    logger.warning(f"INTERVENING: Loop detected at step {self._step_counter}")
                    event.cancel_tool = True
                    event.cancel_reason = f"Loop detected: {issue.description}"
                    return
        
        # Check max steps
        if self._step_counter > self.max_steps:
            logger.warning(f"Max steps ({self.max_steps}) exceeded")
            if self.mode == GuardMode.INTERVENE:
                event.cancel_tool = True
                event.cancel_reason = f"Exceeded maximum steps ({self.max_steps})"
    
    def _on_after_tool(self, event: AfterToolCallEvent) -> None:
        """Record tool execution result."""
        tool_name = event.tool_use.get("name", "unknown")
        tool_input = event.tool_use.get("input", {})
        
        # Get result — keep full version for security analysis, truncated for storage
        tool_result = getattr(event, 'result', None) or getattr(event, 'tool_result', None)
        if tool_result:
            result_str_full = str(tool_result)
            truncation_limit = getattr(self, '_truncation_limit', 500)
            result_str = result_str_full[:truncation_limit] + ("..." if len(result_str_full) > truncation_limit else "")
        else:
            result_str_full = "No result"
            result_str = "No result"
        
        # Determine status
        status = StepStatus.SUCCESS
        if event.exception:
            status = StepStatus.FAILED
        elif any(i.issue_type.value == "INFINITE_LOOP" for i in self._issues if not i.auto_resolved):
            status = StepStatus.REDUNDANT
        
        # Create step record (scores start as None, filled by async AI eval)
        step = StepRecord(
            step_number=self._step_counter,
            tool_name=tool_name,
            tool_input=_mask_sensitive(tool_input),
            tool_result=result_str,
            status=status,
        )
        
        self._steps.append(step)

        # Dashboard streaming — send step in real time
        if self._norn_url and self._registered_agent_id:
            self._dashboard_send_step(step)

        # Queue for evaluation in background thread's event loop (avoids cross-loop task issue)
        if self.enable_ai_eval and self.task:
            self._steps_to_evaluate.append((step, result_str_full, None, None, None))

        if self.enable_shadow_browser:
            self._steps_to_evaluate.append((step, result_str_full, tool_name, tool_input, "shadow"))
    
    # ── AI Evaluation ──────────────────────────────────────
    
    async def _evaluate_step_relevance(self, step: StepRecord, full_result: str = "") -> None:
        """Evaluate step relevance AND security with AI (async)."""
        if not self.task:
            return

        try:
            evaluator = self._get_evaluator()
            # Use full result for security analysis, not the truncated version
            eval_result = full_result or step.tool_result
            relevance, security, reasoning = await evaluator.evaluate_step_relevance(
                self.task.description,
                step.tool_name,
                step.tool_input,
                eval_result,
                self._steps[:-1],  # Previous steps
            )
            
            step.relevance_score = relevance
            step.security_score = security
            step.reasoning = reasoning

            # Mark as irrelevant if score too low
            if relevance is not None and relevance < 30:
                step.status = StepStatus.IRRELEVANT
                self._issues.append(QualityIssue(
                    issue_type="TASK_DRIFT",
                    severity=6,
                    description=f"Step {step.step_number} ({step.tool_name}) not relevant to task",
                    affected_steps=[step.step_id],
                    recommendation="Agent may be drifting from task objective"
                ))
            
            # Check for security issues (threshold <= 50 catches AI scores of exactly 50)
            if security is not None and security <= 50:
                issue_type = "SUSPICIOUS_BEHAVIOR"
                if "exfiltration" in reasoning.lower() or "external" in reasoning.lower():
                    issue_type = "DATA_EXFILTRATION"
                elif "injection" in reasoning.lower():
                    issue_type = "PROMPT_INJECTION"
                elif "credential" in reasoning.lower() or "password" in reasoning.lower():
                    issue_type = "CREDENTIAL_LEAK"
                elif "ssl" in reasoning.lower() or "verify" in reasoning.lower() or "certificate" in reasoning.lower():
                    issue_type = "SECURITY_BYPASS"

                self._issues.append(QualityIssue(
                    issue_type=issue_type,
                    severity=10 if security < 20 else 8,
                    description=f"Security concern in step {step.step_number}: {reasoning}",
                    affected_steps=[step.step_id],
                    recommendation="Review this action for security implications"
                ))

                if self._session_report:
                    self._session_report.security_breach_detected = True

            # Detect missing config / auth errors from tool result text
            _CONFIG_ERROR_PATTERNS = [
                # Knowledge base
                ("no knowledge base id",          "STRANDS_KNOWLEDGE_BASE_ID environment variable is not set"),
                ("no kb id",                       "STRANDS_KNOWLEDGE_BASE_ID environment variable is not set"),
                ("knowledge base id not provided", "STRANDS_KNOWLEDGE_BASE_ID environment variable is not set"),
                # Generic missing config
                ("api key not found",              "API key is missing — check the relevant environment variable"),
                ("credentials not configured",     "AWS/service credentials are not configured"),
                ("missing environment variable",   "A required environment variable is not set"),
                # Exchange / CCXT authentication errors
                ("authenticationerror",            "Exchange API authentication failed — API key may be invalid or expired"),
                ("api key expired",                "API key has expired — renew it in the exchange dashboard"),
                ("retcode: 33004",                 "Bybit API key authorization error (33004) — key is not authorized"),
                ("invalid api-key",                "Invalid API key — verify the key is correct"),
                ("authentication failed",          "Exchange authentication failed — check your API key and secret"),
                ("invalid credentials",            "Invalid exchange credentials"),
            ]
            result_lower = (full_result or "").lower()
            for _pattern, _hint in _CONFIG_ERROR_PATTERNS:
                if _pattern in result_lower:
                    self._issues.append(QualityIssue(
                        issue_type=IssueType.MISSING_CONFIG,
                        severity=7,
                        description=(
                            f"Step {step.step_number} ({step.tool_name}) failed due to missing configuration: {_hint}"
                        ),
                        affected_steps=[step.step_id],
                        recommendation=f"Fix the missing configuration. Matched pattern: '{_pattern}'"
                    ))
                    break
        
        except Exception as e:
            logger.warning(f"Step evaluation failed: {e}")
    
    async def _run_ai_evaluation(self) -> None:
        """Run full AI evaluation of session (async)."""
        if not self.task or not self._session_report:
            self._finalize_report()
            return
        
        try:
            evaluator = self._get_evaluator()
            eval_result = await evaluator.evaluate_session(
                self.task,
                self._steps,
                self._session_report.total_execution_time_ms,
            )
            
            # Update report — AI eval may return None for scores on failure
            self._session_report.task_completion = eval_result.get("task_completed")
            self._session_report.completion_confidence = eval_result.get("completion_confidence")
            # Only override scores if AI eval provided actual values
            if eval_result.get("efficiency_score") is not None:
                self._session_report.efficiency_score = eval_result["efficiency_score"]
            if eval_result.get("security_score") is not None:
                self._session_report.security_score = eval_result["security_score"]
            self._session_report.overall_quality = eval_result.get("overall_quality", SessionQuality.PENDING)
            self._session_report.ai_evaluation = eval_result.get("reasoning", "")
            self._session_report.tool_analysis = eval_result.get("tool_analysis", [])
            self._session_report.decision_observations = eval_result.get("decision_observations", [])
            self._session_report.efficiency_explanation = eval_result.get("efficiency_explanation", "")
            self._session_report.recommendations = eval_result.get("recommendations", [])
            
            # Count security issues
            security_issues = [i for i in self._issues if i.issue_type.value in [
                "DATA_EXFILTRATION", "PROMPT_INJECTION", "UNAUTHORIZED_ACCESS",
                "SUSPICIOUS_BEHAVIOR", "CREDENTIAL_LEAK"
            ]]
            self._session_report.security_threats_detected = len(security_issues)
            
        except Exception as e:
            logger.error(f"AI evaluation failed: {e}")
            self._session_report.overall_quality = SessionQuality.POOR
            self._session_report.ai_evaluation = f"Evaluation error: {str(e)}"
        
        finally:
            self._finalize_report()
    
    def _finalize_report(self) -> None:
        """Save final report to audit log."""
        if not self._session_report:
            return

        # Override AI quality if deterministic checks found critical issues.
        # AI sees only observable behavior and misses implementation-level threats
        # (e.g. shell=True RCE, loop evasion). Deterministic rules are ground truth.
        #
        # Priority (highest severity wins, applied unconditionally):
        #   loop_detected / INFINITE_LOOP >= 8  → STUCK  ("agent in infinite loop")
        #   SECURITY_BYPASS >= 8                → FAILED ("task not completed safely")
        has_security_bypass = any(
            i.issue_type == IssueType.SECURITY_BYPASS and i.severity >= 8
            for i in self._issues
        )
        has_loop_issue = any(
            i.issue_type == IssueType.INFINITE_LOOP and i.severity >= 8
            for i in self._issues
        )

        if self._loop_detected or has_loop_issue:
            self._session_report.overall_quality = SessionQuality.STUCK
        elif has_security_bypass:
            self._session_report.overall_quality = SessionQuality.FAILED

        # Cap security_score at 40 whenever a hard security bypass was detected
        if has_security_bypass:
            if self._session_report.security_score is None or self._session_report.security_score > 40:
                self._session_report.security_score = 40

        # Write to audit log
        self.audit.record_session(self._session_report)

        # Dashboard integration — send final state
        if self._norn_url and self._registered_agent_id:
            self._dashboard_complete_session()

        logger.info(
            "Session complete: %d steps, quality=%s",
            self._session_report.total_steps,
            self._session_report.overall_quality.value,
        )

    # ── Dashboard Integration ───────────────────────────────

    def _post_to_dashboard(self, path: str, payload: dict) -> Optional[dict]:
        """POST payload to Norn dashboard API. Never raises."""
        if not self._norn_url:
            return None
        url = f"{self._norn_url}{path}"
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                return json.loads(resp.read())
        except Exception as exc:
            logger.debug("Dashboard POST %s failed: %s", path, exc)
            return None

    def _infer_source_file(self) -> str:
        """Infer calling script filename."""
        if self._external_agent_name:
            safe = self._external_agent_name.lower().replace(" ", "_")
            safe = "".join(c if c.isalnum() or c == "_" else "_" for c in safe)
            return f"{safe}.py"
        skip = ("norn", "strands", "site-packages", "runpy", "importlib", "_bootstrap")
        for fi in reversed(inspect.stack()):
            fname = fi.filename
            if not fname or fname.startswith("<"):
                continue
            if any(pat in fname for pat in skip):
                continue
            return os.path.basename(fname)
        return "unknown.py"

    def _dashboard_on_session_start(self) -> None:
        """Register agent and create/resume session on dashboard."""
        display_name = self._external_agent_name or self._agent_name

        # Register agent (idempotent)
        if self._registered_agent_id is None:
            resp = self._post_to_dashboard("/api/agents/register", {
                "name": display_name,
                "task_description": self.task.description if self.task else "Live monitoring",
                "source_file": self._infer_source_file(),
            })
            if resp:
                self._registered_agent_id = resp.get("id") or resp.get("agent_id")
            else:
                logger.warning("Dashboard: agent registration failed, streaming disabled")
                return

        # Create or resume session (steps accumulate across runs on the same card)
        if self._session_report:
            resp = self._post_to_dashboard("/api/sessions/ingest", {
                "session_id": self._session_report.session_id,
                "agent_id": self._registered_agent_id,
                "agent_name": display_name,
                "task": self.task.description if self.task else "",
                "started_at": self._session_report.started_at.isoformat(),
                "model": self._session_report.model,
                "swarm_id": self.swarm_id,
                "swarm_order": self.swarm_order,
                "handoff_input": self.handoff_input,
            })
            # Step counter'ı mevcut adım sayısından devam ettir
            if resp:
                existing_steps = resp.get("steps") or []
                self._existing_step_count = len(existing_steps)
                if self._existing_step_count:
                    self._step_counter = self._existing_step_count
                    logger.info("Resuming session with %d existing steps", self._existing_step_count)

    def _dashboard_send_step(self, step: StepRecord) -> None:
        """Send a completed step to the dashboard for real-time streaming."""
        if not self._session_report:
            return
        self._post_to_dashboard(
            f"/api/sessions/{self._session_report.session_id}/step",
            {
                "step_id": step.step_id,
                "step_number": step.step_number,
                "timestamp": step.timestamp.isoformat(),
                "tool_name": step.tool_name,
                "tool_input": str(step.tool_input),
                "tool_result": str(step.tool_result),
                "status": step.status.value,
                "relevance_score": step.relevance_score,
                "security_score": step.security_score,
                "reasoning": step.reasoning or "",
            }
        )

    def _dashboard_complete_session(self) -> None:
        """Send final session state to the dashboard."""
        report = self._session_report
        if not report:
            return
        issues = [
            {
                "issue_type": i.issue_type.value,
                "severity": i.severity,
                "description": i.description,
                "recommendation": i.recommendation,
            }
            for i in report.issues
        ]
        total_steps = self._existing_step_count + (report.total_steps or 0)
        self._post_to_dashboard(
            f"/api/sessions/{report.session_id}/complete",
            {
                "ended_at": report.ended_at.isoformat() if report.ended_at else datetime.now(timezone.utc).isoformat(),
                "status": "completed",
                "total_steps": total_steps,
                "overall_quality": report.overall_quality.value,
                "efficiency_score": report.efficiency_score,
                "security_score": report.security_score,
                "task_completion": report.task_completion,
                "completion_confidence": report.completion_confidence,
                "loop_detected": report.loop_detected,
                "security_breach_detected": report.security_breach_detected,
                "total_execution_time_ms": report.total_execution_time_ms,
                "issues": issues,
                "ai_evaluation": report.ai_evaluation or "",
                "recommendations": report.recommendations or [],
                "tool_analysis": report.tool_analysis or [],
                "decision_observations": report.decision_observations or [],
                "efficiency_explanation": report.efficiency_explanation or "",
                "swarm_id": report.swarm_id,
                "swarm_order": report.swarm_order,
                "handoff_input": report.handoff_input,
            }
        )

    def _get_evaluator(self):
        """Lazy-load AI evaluator."""
        if self._evaluator is None:
            from norn.agents.quality_evaluator import QualityEvaluator
            self._evaluator = QualityEvaluator()
        return self._evaluator
    
    def _get_shadow_browser(self):
        """Lazy-load Shadow Browser."""
        if self._shadow_browser is None:
            from norn.agents.shadow_browser import ShadowBrowser
            self._shadow_browser = ShadowBrowser()
        return self._shadow_browser
    
    async def _verify_with_shadow_browser(
        self,
        step: StepRecord,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_result: str
    ) -> None:
        """Verify browser actions with Shadow Browser (async)."""
        # Detect browser-related tools
        browser_tools = {
            "navigate_to": "navigation",
            "open_url": "navigation",
            "browse": "navigation",
            "scrape_page": "scraping",
            "extract_data": "scraping",
            "get_content": "scraping",
            "fill_form": "form",
            "submit_form": "form",
            "click_button": "interaction",
        }
        
        if tool_name not in browser_tools:
            return  # Not a browser tool
        
        action_type = browser_tools[tool_name]
        
        # Extract URL from tool input
        url = tool_input.get("url") or tool_input.get("page") or tool_input.get("link")
        if not url:
            return  # No URL to verify
        
        try:
            shadow = self._get_shadow_browser()
            
            # Verify based on action type
            if action_type == "navigation":
                result = await shadow.verify_navigation(url, tool_result[:200])
            elif action_type == "scraping":
                result = await shadow.verify_scraping(url, tool_result, "text")
            elif action_type == "form":
                result = await shadow.verify_form_submission(url, tool_input, tool_result)
            else:
                result = await shadow.verify_navigation(url)
            
            # Update step with shadow verification
            step.metadata["shadow_verification"] = result
            shadow_score = result.get("security_score")
            if shadow_score is not None:
                if step.security_score is not None:
                    step.security_score = min(step.security_score, shadow_score)
                else:
                    step.security_score = shadow_score
            
            # Check for discrepancies
            if result.get("discrepancy_detected") or not result.get("verified"):
                self._issues.append(QualityIssue(
                    issue_type="SUSPICIOUS_BEHAVIOR",
                    severity=7,
                    description=f"Shadow Browser detected discrepancy in {tool_name}: {result.get('details', 'Content mismatch')}",
                    affected_steps=[step.step_id],
                    recommendation="Verify agent's browser actions manually"
                ))
            
            # Check for security issues
            if result.get("security_issues"):
                for issue in result["security_issues"]:
                    self._issues.append(QualityIssue(
                        issue_type="UNAUTHORIZED_ACCESS" if "phishing" in issue.lower() else "SUSPICIOUS_BEHAVIOR",
                        severity=9,
                        description=f"Shadow Browser security alert: {issue}",
                        affected_steps=[step.step_id],
                        recommendation="Review page security before proceeding"
                    ))
                    
                if self._session_report:
                    self._session_report.security_breach_detected = True
            
            logger.info(f"Shadow verification complete for {tool_name}: {result.get('verification_result', 'VERIFIED')}")
            
        except Exception as e:
            logger.warning(f"Shadow Browser verification failed: {e}")
    
    # ── Public API ─────────────────────────────────────────
    
    @property
    def session_report(self) -> Optional[SessionReport]:
        """Get current session report."""
        return self._session_report
    
    def get_session_report(self) -> SessionReport:
        """Generate and return session report."""
        if self._session_report:
            return self._session_report
        
        # Calculate metrics
        total_steps = len(self._steps)
        efficiency_score = self._calculate_efficiency_score()
        security_score = self._calculate_security_score()
        
        # Determine overall quality
        quality = self._determine_quality(efficiency_score, security_score)
        
        # Create report
        report = SessionReport(
            session_id=self.session_id or f"session-{int(time.time())}",
            agent_name=self._agent_name,
            task=self.task.description if self.task else "Unknown task",
            total_steps=total_steps,
            overall_quality=quality,
            efficiency_score=efficiency_score,
            security_score=security_score,
            steps=self._steps,
            issues=self._issues,
            task_completion=not self._loop_detected and total_steps > 0 if not self._loop_detected else False,
            completion_confidence=30,  # Low confidence for heuristic-based completion
            loop_detected=self._loop_detected,
            ai_evaluation="",  # Will be filled by async evaluation
            recommendations=[]
        )
        
        self._session_report = report
        return report
    
    def _calculate_efficiency_score(self) -> int:
        """Calculate efficiency score based on steps."""
        if not self._steps:
            return 0
        
        total_steps = len(self._steps)
        failed_steps = sum(1 for s in self._steps if s.status == StepStatus.FAILED)
        irrelevant_steps = sum(1 for s in self._steps if s.status == StepStatus.IRRELEVANT)
        redundant_steps = sum(1 for s in self._steps if s.status == StepStatus.REDUNDANT)
        
        # Penalize failed, irrelevant, and redundant steps
        penalty = (failed_steps * 10) + (irrelevant_steps * 5) + (redundant_steps * 3)
        
        # Base score
        if total_steps <= self.max_steps:
            base_score = 100
        else:
            base_score = max(0, 100 - ((total_steps - self.max_steps) * 2))
        
        return max(0, min(100, base_score - penalty))
    
    def _calculate_security_score(self) -> Optional[int]:
        """Calculate security score based on issues."""
        if not self._steps:
            return None
        
        # Count security-related issues
        security_issues = [i for i in self._issues if i.issue_type in [
            IssueType.DATA_EXFILTRATION,
            IssueType.PROMPT_INJECTION,
            IssueType.CREDENTIAL_LEAK,
            IssueType.SUSPICIOUS_BEHAVIOR
        ]]
        
        if not security_issues:
            return 100
        
        # Penalize based on severity
        penalty = sum(i.severity * 5 for i in security_issues)
        return max(0, 100 - penalty)
    
    def _determine_quality(self, efficiency: Optional[int], security: Optional[int]) -> SessionQuality:
        """Determine overall quality level."""
        if self._loop_detected:
            return SessionQuality.STUCK

        # If either score is unavailable, return PENDING
        if efficiency is None or security is None:
            return SessionQuality.PENDING

        avg_score = (efficiency + security) / 2

        if avg_score >= 90:
            return SessionQuality.EXCELLENT
        elif avg_score >= 70:
            return SessionQuality.GOOD
        elif avg_score >= 40:
            return SessionQuality.POOR
        else:
            return SessionQuality.FAILED
    
    @property
    def steps(self) -> list[StepRecord]:
        """Get all recorded steps."""
        return self._steps
    
    @property
    def issues(self) -> list[QualityIssue]:
        """Get all detected issues."""
        return self._issues


# Legacy compatibility
class ToolBlockedError(Exception):
    """Raised when a tool call is blocked."""
    def __init__(self, reason: str, verdict: Any = None):
        self.reason = reason
        self.verdict = verdict
        super().__init__(reason)
