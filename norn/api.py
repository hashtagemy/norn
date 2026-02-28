#!/usr/bin/env python3
"""
Norn REST API
Serves monitoring data to the React dashboard
"""

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, File, UploadFile, Form, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pathlib import Path
import json
import logging
from typing import List, Dict, Any, Optional, Set
from datetime import datetime
import os
import subprocess
import sys
import tempfile
import shutil
import asyncio
import uuid
import zipfile
import threading

logger = logging.getLogger("norn.api")


_chdir_lock = threading.Lock()


_registry_lock = threading.Lock()


def _read_registry() -> list:
    """Thread-safe registry read."""
    with _registry_lock:
        if not REGISTRY_FILE.exists():
            return []
        try:
            with open(REGISTRY_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []


def _atomic_write_json(path: Path, data: Any) -> None:
    """Write JSON to a file atomically via a temp file + rename.
    Prevents 0-byte files if the process is killed mid-write."""
    tmp_fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _safe_extract(zip_ref: zipfile.ZipFile, extract_path: Path) -> None:
    """Extract ZIP safely — prevents path traversal attacks (../../etc/passwd style).
    BUG-005 fix: validates every member path stays within extract_path."""
    resolved_base = extract_path.resolve()
    for member in zip_ref.namelist():
        member_path = (extract_path / member).resolve()
        if not str(member_path).startswith(str(resolved_base) + os.sep) and member_path != resolved_base:
            raise ValueError(f"ZIP path traversal attempt detected: {member}")
    zip_ref.extractall(extract_path)

app = FastAPI(title="Norn API", version="1.0.0")

# WebSocket connection manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)

    async def broadcast(self, message: dict):
        """Broadcast message to all connected clients"""
        disconnected = set()
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                disconnected.add(connection)
        
        # Clean up disconnected clients
        self.active_connections -= disconnected

manager = ConnectionManager()

# CORS - configurable via env
CORS_ORIGINS = os.environ.get("NORN_CORS_ORIGINS", "http://localhost:5173,http://localhost:3000,http://localhost:3001").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in CORS_ORIGINS],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── API Key Authentication ──────────────────────────────
# Set NORN_API_KEY env var to enable auth. If empty/unset, auth is disabled (dev mode).
API_KEY = os.environ.get("NORN_API_KEY", "")


async def verify_api_key(request: Request):
    """Verify API key if configured. Skip auth in dev mode (no key set)."""
    if not API_KEY:
        return  # No API key configured — dev mode, skip auth
    key = request.headers.get("X-API-Key") or request.query_params.get("api_key")
    if key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# ── Global Error Handler ────────────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch unhandled exceptions and return a clean 500 response."""
    logger.error(f"Unhandled error on {request.method} {request.url}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "error_type": type(exc).__name__}
    )


# All paths derived from one env-configurable root
LOGS_DIR = Path(os.environ.get("NORN_LOG_DIR", "norn_logs"))
SESSIONS_DIR = LOGS_DIR / "sessions"
REGISTRY_FILE = LOGS_DIR / "agents_registry.json"
CONFIG_FILE = LOGS_DIR / "config.json"

DEFAULT_CONFIG = {
    "guard_mode": "monitor",
    "max_steps": 50,
    "enable_ai_eval": True,
    "enable_shadow_browser": False,
    "loop_window": 5,
    "loop_threshold": 3,
    "max_same_tool": 10,
    "security_score_threshold": 70,
    "relevance_score_threshold": 30,
    "auto_intervene_on_loop": False,
    "log_retention_days": 30,
}


def _load_config() -> Dict[str, Any]:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                saved = json.load(f)
            merged = {**DEFAULT_CONFIG, **saved}
            return merged
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def _save_config(config: Dict[str, Any]) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(CONFIG_FILE, config)


@app.get("/")
def root():
    """Health check"""
    return {"status": "online", "service": "Norn API"}


@app.get("/api/sessions", dependencies=[Depends(verify_api_key)])
def get_sessions(limit: int = 50) -> List[Dict[str, Any]]:
    """Get all monitoring sessions"""
    if not SESSIONS_DIR.exists():
        return []
    
    sessions = []
    session_files = sorted(
        SESSIONS_DIR.glob("*.json"),
        key=lambda f: f.stat().st_mtime,
        reverse=True
    )
    
    for file in session_files[:limit]:
        try:
            with open(file) as f:
                session = json.load(f)
                
                # Normalize session data for frontend
                normalized = normalize_session(session)
                sessions.append(normalized)
        except Exception as e:
            logger.warning(f"Error loading session {file}: {e}")
    
    return sessions


def normalize_session(session: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize session data for consistent frontend consumption"""
    # Ensure task is a string for taskPreview
    task = session.get('task', '')
    if isinstance(task, dict):
        task_str = task.get('description', '')
    else:
        task_str = str(task)

    # Normalize issues - keep full objects for frontend detail view
    issues = session.get('issues', [])
    normalized_issues = []
    for issue in issues:
        if isinstance(issue, dict):
            normalized_issues.append({
                'issue_id': issue.get('issue_id', ''),
                'issue_type': issue.get('issue_type', 'NONE'),
                'severity': issue.get('severity', 5),
                'description': issue.get('description', ''),
                'recommendation': issue.get('recommendation', ''),
                'affected_steps': issue.get('affected_steps', []),
            })
        else:
            normalized_issues.append({
                'issue_type': str(issue),
                'severity': 5,
                'description': str(issue),
                'recommendation': '',
            })

    # Normalize steps - include full data for timeline rendering
    steps = session.get('steps', [])
    normalized_steps = []
    for step in steps:
        # Format tool input as readable string
        tool_input = step.get('tool_input', {})
        if isinstance(tool_input, dict):
            input_parts = [f'{k}={repr(v)}' for k, v in tool_input.items()]
            input_str = ', '.join(input_parts)
        else:
            input_str = str(tool_input)

        tool_name = step.get('tool_name', '') or step.get('action', '')
        tool_result = step.get('tool_result', '')

        # Truncate long results for display
        if len(str(tool_result)) > 300:
            tool_result = str(tool_result)[:300] + '...'

        normalized_steps.append({
            'step_id': step.get('step_id', ''),
            'step_number': step.get('step_number', 0),
            'timestamp': step.get('timestamp', ''),
            'tool_name': tool_name,
            'tool_input': input_str,
            'tool_result': str(tool_result),
            'status': step.get('status', 'SUCCESS'),
            'relevance_score': step.get('relevance_score'),
            'security_score': step.get('security_score'),
            'reasoning': step.get('reasoning', ''),
        })

    # Derive session status from quality and completion
    overall_quality = session.get('overall_quality', 'GOOD')
    if session.get('loop_detected') or overall_quality == 'STUCK':
        status = 'terminated'
    elif session.get('ended_at') or session.get('end_time'):
        status = 'completed'
    else:
        status = 'active'

    # Override with explicit status if present
    explicit_status = session.get('status')
    if explicit_status and explicit_status in ('active', 'terminated'):
        status = explicit_status

    # Stale session detection: mark active sessions as terminated
    # if they started more than 5 minutes ago with no end time
    if status == 'active':
        started_at = session.get('started_at') or session.get('start_time')
        if started_at:
            try:
                start_dt = datetime.fromisoformat(str(started_at).replace('Z', '+00:00'))
                now = datetime.now(start_dt.tzinfo) if start_dt.tzinfo else datetime.now()
                elapsed_minutes = (now - start_dt).total_seconds() / 60
                if elapsed_minutes > 5 and not session.get('ended_at'):
                    status = 'terminated'
                    overall_quality = 'FAILED'
            except (ValueError, TypeError):
                pass

    return {
        'session_id': session.get('session_id', ''),
        'agent_name': session.get('agent_name', 'Unknown'),
        'model': session.get('model'),
        'task': task_str,
        'start_time': session.get('started_at') or session.get('start_time', ''),
        'end_time': session.get('ended_at') or session.get('end_time'),
        'status': status,
        'total_steps': session.get('total_steps', 0),
        'overall_quality': overall_quality,
        'efficiency_score': session.get('efficiency_score'),
        'security_score': session.get('security_score'),
        'issues': normalized_issues,
        'steps': normalized_steps,
        'ai_evaluation': session.get('ai_evaluation'),
        'tool_analysis': session.get('tool_analysis', []),
        'decision_observations': session.get('decision_observations', []),
        'efficiency_explanation': session.get('efficiency_explanation', ''),
        'recommendations': session.get('recommendations', []),
        'task_completion': session.get('task_completion', False),
        'loop_detected': session.get('loop_detected', False),
        'security_breach_detected': session.get('security_breach_detected', False),
        'total_execution_time_ms': session.get('total_execution_time_ms', 0),
    }


@app.get("/api/sessions/{session_id}", dependencies=[Depends(verify_api_key)])
def get_session(session_id: str) -> Dict[str, Any]:
    """Get specific session details"""
    session_file = SESSIONS_DIR / f"{session_id}.json"
    
    if not session_file.exists():
        raise HTTPException(status_code=404, detail="Session not found")
    
    try:
        with open(session_file) as f:
            session = json.load(f)
            return normalize_session(session)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/agents", dependencies=[Depends(verify_api_key)])
def get_agents() -> List[Dict[str, Any]]:
    """Get all registered agents"""
    return _read_registry()


@app.get("/api/agents/{agent_id}", dependencies=[Depends(verify_api_key)])
def get_agent(agent_id: str) -> Dict[str, Any]:
    """Get specific agent details"""
    agents = _read_registry()
    agent = next((a for a in agents if a["id"] == agent_id), None)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


def _discover_agent(agent_path: Path, main_file: str) -> Dict[str, Any]:
    """Simple agent discovery without external script"""
    try:
        import ast
        
        agent_file = agent_path / main_file
        if not agent_file.exists():
            return {"status": "error", "error": "Main file not found"}
        
        with open(agent_file) as f:
            content = f.read()
        
        tree = ast.parse(content)
        
        # Extract imports
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)
        
        # Detect agent type
        agent_type = "Unknown"
        if "strands" in imports:
            agent_type = "Strands Agent"
        elif "langchain" in imports:
            agent_type = "LangChain Agent"
        elif "crewai" in imports:
            agent_type = "CrewAI Agent"
        
        return {
            "status": "success",
            "tools": [],
            "functions": [],
            "classes": [],
            "imports": list(set(imports)),
            "dependencies": [],
            "potential_issues": [],
            "agent_type": agent_type,
            "entry_points": ["__main__"] if "__main__" in content else []
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _run_discovery_only(agent_path: Path, main_file: str) -> Dict[str, Any]:
    """Run agent_discovery.py on a single file without installing deps."""
    try:
        result = subprocess.run(
            [sys.executable, "norn/utils/agent_discovery.py", str(agent_path), main_file],
            capture_output=True,
            text=True,
            timeout=30
        )

        output = result.stdout
        if "DISCOVERY_JSON_START" not in output:
            return {"status": "ready"}

        json_start = output.index("DISCOVERY_JSON_START") + len("DISCOVERY_JSON_START")
        json_end = output.index("DISCOVERY_JSON_END")
        discovery_result = json.loads(output[json_start:json_end].strip())
        return {"status": "analyzed", "discovery": discovery_result}
    except Exception as e:
        logger.warning(f"Discovery failed for {main_file}: {e}")
        return {"status": "ready"}


def _install_discovered_deps(discovery_result: Dict[str, Any]) -> None:
    """Install missing and local dependencies found by discovery."""
    missing_deps = [d for d in discovery_result.get("dependencies", []) if d["status"] == "missing"]
    local_deps = [d for d in discovery_result.get("dependencies", []) if d["status"] == "local"]

    if missing_deps:
        print(f"📦 Installing {len(missing_deps)} missing dependencies...")
        for dep in missing_deps:
            try:
                package_name = dep["name"].replace("_", "-").split(".")[0]
                print(f"   Installing {package_name}...")
                install_result = subprocess.run(
                    [sys.executable, "-m", "pip", "install", package_name, "-q"],
                    capture_output=True,
                    text=True,
                    timeout=120
                )
                if install_result.returncode == 0:
                    print(f"   ✅ {package_name} installed")
                    for d in discovery_result["dependencies"]:
                        if d["name"] == dep["name"]:
                            d["status"] = "installed"
                else:
                    print(f"   ⚠️  {package_name} installation failed")
            except Exception as e:
                print(f"   ⚠️  Failed to install {dep['name']}: {e}")

    if local_deps:
        print(f"📦 Installing {len(local_deps)} local packages...")
        for dep in local_deps:
            try:
                local_path = dep.get("path")
                if local_path and Path(local_path).exists():
                    print(f"   Installing local package: {dep['name']}...")
                    install_result = subprocess.run(
                        [sys.executable, "-m", "pip", "install", "-e", local_path, "-q"],
                        capture_output=True,
                        text=True,
                        timeout=120
                    )
                    if install_result.returncode == 0:
                        print(f"   ✅ {dep['name']} installed (local)")
                        for d in discovery_result["dependencies"]:
                            if d["name"] == dep["name"]:
                                d["status"] = "installed"
                    else:
                        print(f"   ⚠️  {dep['name']} local installation failed")
                        print(f"   Error: {install_result.stderr}")
            except Exception as e:
                print(f"   ⚠️  Failed to install local {dep['name']}: {e}")

    if missing_deps or local_deps:
        print("🔄 Recalculating issues after dependency installation...")
        remaining_missing = [d for d in discovery_result["dependencies"] if d["status"] == "missing"]

        discovery_result["potential_issues"] = [
            issue for issue in discovery_result["potential_issues"]
            if issue["type"] not in ["MISSING_DEPENDENCIES", "MISSING_TOOL_PACKAGES"]
        ]

        if remaining_missing:
            external_tool_packages = [d for d in remaining_missing if any(
                keyword in d["name"].lower() for keyword in ['tool', 'amadeus', 'langchain', 'crewai']
            )]
            critical_missing = [d for d in remaining_missing if d not in external_tool_packages]

            if critical_missing:
                discovery_result["potential_issues"].append({
                    "type": "MISSING_DEPENDENCIES",
                    "severity": "HIGH",
                    "description": f"Missing dependencies: {', '.join(d['name'] for d in critical_missing)}"
                })

            if external_tool_packages:
                discovery_result["potential_issues"].append({
                    "type": "MISSING_TOOL_PACKAGES",
                    "severity": "LOW",
                    "description": f"External tool packages not installed: {', '.join(d['name'] for d in external_tool_packages)}"
                })


def _discover_and_install_deps(agent_path: Path, main_file: str) -> Dict[str, Any]:
    """Run agent discovery and auto-install missing dependencies (backward-compatible wrapper)."""
    info = _run_discovery_only(agent_path, main_file)
    if "discovery" in info:
        _install_discovered_deps(info["discovery"])
    return info


def _generate_auto_task(agent_name: str, discovery: Dict[str, Any], task_description: str, clone_path: Optional[Path] = None) -> str:
    """Generate a structured, tool-aware test task based on agent's capabilities."""
    try:
        import json as _json
        from strands import Agent as StrandsAgent
        from strands.models import BedrockModel

        tools = discovery.get("tools", [])
        agent_type = discovery.get("agent_type", "unknown")
        system_prompt = discovery.get("system_prompt", "")

        # --- Enrich context from repo files ---
        readme_content = ""
        pyproject_description = ""
        tool_file_summaries = ""

        if clone_path and clone_path.exists():
            # Read README.md for agent purpose
            for readme_name in ("README.md", "readme.md", "README.rst", "README.txt"):
                readme_path = clone_path / readme_name
                if readme_path.exists():
                    try:
                        readme_content = readme_path.read_text(encoding="utf-8", errors="ignore")[:2000]
                    except Exception:
                        pass
                    break

            # Read pyproject.toml description
            pyproject_path = clone_path / "pyproject.toml"
            if pyproject_path.exists():
                try:
                    try:
                        import tomllib as _tomllib
                    except ImportError:
                        import tomli as _tomllib  # type: ignore
                    with open(pyproject_path, "rb") as _f:
                        _pdata = _tomllib.load(_f)
                    pyproject_description = _pdata.get("project", {}).get("description", "")
                except Exception:
                    pass

            # Collect tool docstrings from tool files (tools/ directory)
            tool_summaries = []
            for tools_dir in (clone_path / "tools", clone_path / "src"):
                if tools_dir.exists():
                    for py_file in sorted(tools_dir.rglob("*.py"))[:10]:
                        if py_file.name == "__init__.py":
                            continue
                        try:
                            import ast as _ast
                            content = py_file.read_text(encoding="utf-8", errors="ignore")
                            tree = _ast.parse(content)
                            for node in _ast.walk(tree):
                                if isinstance(node, _ast.FunctionDef):
                                    has_tool = any(
                                        (isinstance(d, _ast.Name) and d.id == "tool") or
                                        (isinstance(d, _ast.Attribute) and d.attr == "tool")
                                        for d in node.decorator_list
                                    )
                                    if has_tool:
                                        docstring = _ast.get_docstring(node) or ""
                                        tool_summaries.append(f"- {node.name}: {docstring[:120]}")
                        except Exception:
                            pass
            if tool_summaries:
                tool_file_summaries = "\n".join(tool_summaries[:20])

        # Deduplicate tools by name (discovery sometimes returns external + local duplicates)
        seen = set()
        unique_tools = []
        for t in tools:
            if t["name"] not in seen:
                seen.add(t["name"])
                unique_tools.append(t)

        tool_names = [t["name"] for t in unique_tools]
        tool_details = "\n".join(
            f"- {t['name']}: {t.get('description', 'no description')}"
            for t in unique_tools
        ) if unique_tools else "No tools detected"

        # Detect capability categories from tool names
        CATEGORY_KEYWORDS = {
            "web": ["http_request", "http_fetch", "fetch_webpage", "fetch", "web_search", "browse", "request"],
            "file": ["file_read", "file_write", "read_file", "write_file", "summarize_file", "write_report"],
            "shell": ["shell", "bash", "execute", "run_command"],
            "search": ["web_search", "search", "ddg_search"],
        }
        categories = []
        for cat, keywords in CATEGORY_KEYWORDS.items():
            if any(kw in tool_names for kw in keywords):
                categories.append(cat)

        # Fallback: infer categories from system prompt if no tools detected
        if not categories and system_prompt:
            sp_lower = system_prompt.lower()
            if any(w in sp_lower for w in ["web", "http", "url", "fetch", "browse"]):
                categories.append("web")
            if any(w in sp_lower for w in ["file", "read", "write", "document"]):
                categories.append("file")
            if any(w in sp_lower for w in ["shell", "command", "terminal", "bash"]):
                categories.append("shell")

        # Pick test strategy based on detected categories
        if "web" in categories and "file" in categories:
            strategy = "Fetch content from https://example.com, then write a short summary to a NEW file (e.g. result.txt). Do not reference files that don't exist yet."
        elif "file" in categories:
            strategy = "First write a new file with some sample text content, then read it back and summarize what was written."
        elif "web" in categories:
            strategy = "Fetch https://example.com and summarize the page content in a short paragraph."
        elif "shell" in categories:
            strategy = "Run a safe read-only shell command (e.g. date, echo, or ls /tmp) and report the output."
        else:
            strategy = "Perform a reasoning or analysis task appropriate to the agent's described purpose."

        # If README is available, it drives the task; otherwise fall back to tool-based strategy
        if readme_content:
            task_guidance = f"""The agent's README describes its purpose and capabilities in detail.
Use the README as your PRIMARY source to understand what this agent is designed to do,
and generate a task that tests its ACTUAL PURPOSE — not just its tools.
Do NOT default to fetching example.com unless it genuinely fits the agent's purpose."""
        else:
            task_guidance = f"Suggested test strategy (based on detected tools): {strategy}"

        prompt = f"""You are a QA engineer creating a meaningful, agent-specific test task.

=== AGENT IDENTITY ===
Name: {agent_name}
Type: {agent_type}
Description: {pyproject_description if pyproject_description else 'N/A'}

=== README (primary source — read carefully) ===
{readme_content[:2000] if readme_content else 'N/A'}

=== SYSTEM PROMPT (if available) ===
{system_prompt[:400] if system_prompt else 'N/A'}

=== AVAILABLE TOOLS ===
From main file:
{tool_details}

From tool files:
{tool_file_summaries if tool_file_summaries else 'N/A'}

=== TASK GUIDANCE ===
{task_guidance}

Generate a test task as a JSON object with exactly these fields:
{{
  "description": "The task text — specific, meaningful, reflects the agent's actual purpose (max 150 words)",
  "expected_tools": ["tool1", "tool2"],
  "max_steps": 15,
  "success_criteria": "One sentence describing what successful completion looks like"
}}

RULES:
1. The task MUST reflect what the agent is actually designed to do (use README for this)
2. Use only tool names from the available tools lists above in expected_tools
3. NEVER reference local files that don't already exist — if using file tools, the task must CREATE the file first
4. Task must be completable autonomously within 2-3 minutes
5. Task must produce observable, verifiable output
6. Be specific — mention real topics, real actions, real expected outcomes

Respond with ONLY valid JSON. No markdown fences, no explanation."""

        model = BedrockModel(
            model_id="us.amazon.nova-2-lite-v1:0",
            temperature=0.2,
        )
        task_agent = StrandsAgent(
            model=model,
            system_prompt="You generate structured test tasks as JSON. Output only valid JSON with no markdown.",
            tools=[],
        )
        result = task_agent(prompt)
        response_text = str(result).strip()

        # Strip markdown code fences if model wrapped the JSON
        if "```" in response_text:
            parts = response_text.split("```")
            for part in parts:
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:].strip()
                if part.startswith("{"):
                    response_text = part
                    break

        task_json = _json.loads(response_text)
        description = task_json.get("description", "").strip()

        logger.info(
            f"Auto-generated task for {agent_name}: {description[:80]}... | "
            f"expected_tools={task_json.get('expected_tools')} | "
            f"max_steps={task_json.get('max_steps')} | "
            f"success_criteria={str(task_json.get('success_criteria', ''))[:60]}"
        )

        if len(description) > 10 and len(description) < 600:
            return description
        return task_description
    except Exception as e:
        logger.warning(f"Auto-task generation failed for {agent_name}: {e}")
        return task_description


def _find_main_file_from_pyproject(clone_path: Path) -> Optional[Path]:
    """Parse pyproject.toml to find the main agent file. Returns absolute path or None."""
    pyproject = clone_path / "pyproject.toml"
    if not pyproject.exists():
        return None
    try:
        try:
            import tomllib
        except ImportError:
            try:
                import tomli as tomllib  # type: ignore
            except ImportError:
                return None
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)

        # Priority 1: [project.scripts] → "module.submodule:function"
        scripts = data.get("project", {}).get("scripts", {})
        for _cmd, entry in scripts.items():
            module_part = entry.split(":")[0]  # e.g. "strands_research_agent.agent"
            parts = module_part.split(".")
            # Try src/ layout first, then flat
            for src_prefix in [clone_path / "src", clone_path]:
                candidate = src_prefix.joinpath(*parts).with_suffix(".py")
                if candidate.exists():
                    return candidate

        # Priority 2: [tool.hatch.build.targets.wheel] packages = ["src/pkg"]
        hatch_pkgs = (data.get("tool", {}).get("hatch", {})
                      .get("build", {}).get("targets", {})
                      .get("wheel", {}).get("packages", []))
        for pkg_path in hatch_pkgs:
            pkg_dir = clone_path / pkg_path
            for name in ("agent.py", "main.py", "app.py"):
                candidate = pkg_dir / name
                if candidate.exists():
                    return candidate

        # Priority 3: [tool.setuptools.packages.find] where = ["src"]
        where_list = (data.get("tool", {}).get("setuptools", {})
                      .get("packages", {}).get("find", {}).get("where", []))
        for where in where_list:
            for sub in (clone_path / where).iterdir() if (clone_path / where).exists() else []:
                if sub.is_dir():
                    for name in ("agent.py", "main.py", "app.py"):
                        candidate = sub / name
                        if candidate.exists():
                            return candidate

    except Exception as e:
        logger.warning(f"pyproject.toml parse failed: {e}")
    return None


_SKIP_FILENAMES = frozenset({
    "__init__.py", "setup.py", "conftest.py", "constants.py",
    "config.py", "utils.py", "helpers.py", "test.py", "tests.py",
})
_AGENT_IMPORTS = frozenset({
    "strands", "strands_tools", "langchain", "crewai", "autogpt", "anthropic", "openai",
})


def _is_agent_file(file_path: Path) -> bool:
    """Return True if a Python file looks like an agent (not a utility module)."""
    import ast as _ast

    if file_path.name.lower() in _SKIP_FILENAMES:
        return False

    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        tree = _ast.parse(content)
    except SyntaxError:
        return False

    for node in _ast.walk(tree):
        # Heuristic 1: imports an agent framework
        if isinstance(node, (_ast.Import, _ast.ImportFrom)):
            module = ""
            if isinstance(node, _ast.Import):
                for alias in node.names:
                    module = alias.name
            else:
                module = node.module or ""
            root = module.split(".")[0]
            if root in _AGENT_IMPORTS:
                return True

        # Heuristic 2: @tool decorator
        if isinstance(node, _ast.FunctionDef):
            for d in node.decorator_list:
                if (isinstance(d, _ast.Name) and d.id == "tool") or \
                   (isinstance(d, _ast.Attribute) and d.attr == "tool"):
                    return True

        # Heuristic 3: Agent(...) call or module-level `agent` variable
        if isinstance(node, _ast.Call):
            func = node.func
            if isinstance(func, _ast.Name) and func.id == "Agent":
                return True
        if isinstance(node, _ast.Assign):
            for target in node.targets:
                if isinstance(target, _ast.Name) and target.id == "agent":
                    return True

    # Heuristic 4: if __name__ == "__main__" block
    for node in _ast.walk(tree):
        if isinstance(node, _ast.If):
            test = node.test
            if (isinstance(test, _ast.Compare) and
                    isinstance(test.left, _ast.Name) and
                    test.left.id == "__name__"):
                return True

    return False


def _derive_agent_name(file_path: Path, prefix: str = "") -> str:
    """Derive a human-readable agent name from a Python file."""
    import ast as _ast

    # Priority 1: pyproject.toml [project] name in parent directories
    for parent in [file_path.parent, file_path.parent.parent, file_path.parent.parent.parent]:
        pyproject = parent / "pyproject.toml"
        if pyproject.exists():
            try:
                try:
                    import tomllib as _tl
                except ImportError:
                    import tomli as _tl  # type: ignore
                with open(pyproject, "rb") as _f:
                    _pd = _tl.load(_f)
                name = _pd.get("project", {}).get("name", "")
                if name:
                    friendly = name.replace("-", " ").replace("_", " ").title()
                    return f"{prefix} {friendly}".strip() if prefix else friendly
            except Exception:
                pass

    # Priority 2: module-level docstring
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        tree = _ast.parse(content)
        doc = _ast.get_docstring(tree)
        if doc:
            first_line = doc.split("\n")[0].strip()
            if 0 < len(first_line) <= 80:
                return f"{prefix} {first_line}".strip() if prefix else first_line
    except Exception:
        pass

    # Fallback: filename stem to Title Case
    stem = file_path.stem.replace("_", " ").replace("-", " ").title()
    return f"{prefix} {stem}".strip() if prefix else stem


@app.post("/api/agents/import/github", dependencies=[Depends(verify_api_key)])
def import_github_agent(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Import agent(s) from GitHub repository. Returns a list of discovered agents."""
    repo_url = data.get("repo_url")
    branch = data.get("branch", "main")
    branch_from_url = False

    if not repo_url:
        raise HTTPException(status_code=400, detail="repo_url is required")

    try:
        # Smart URL conversion
        subfolder = None
        if "github.com" in repo_url and "/tree/" in repo_url:
            parts = repo_url.split("/tree/")
            base_url = parts[0]
            if len(parts) > 1:
                path_parts = parts[1].split("/", 1)
                branch = path_parts[0]
                branch_from_url = True
                if len(path_parts) > 1:
                    subfolder = path_parts[1]
            repo_url = f"{base_url}.git"
        elif "github.com" in repo_url and not repo_url.endswith(".git"):
            repo_url = f"{repo_url.rstrip('/')}.git"

        # Detect default branch if not explicitly provided
        if not branch_from_url and not data.get("branch"):
            try:
                ls_result = subprocess.run(
                    ["git", "ls-remote", "--symref", repo_url, "HEAD"],
                    capture_output=True, text=True, timeout=15
                )
                if ls_result.returncode == 0 and "refs/heads/" in ls_result.stdout:
                    for line in ls_result.stdout.splitlines():
                        if line.startswith("ref:"):
                            branch = line.split("refs/heads/")[1].split()[0]
                            logger.info(f"Detected default branch: {branch}")
                            break
            except Exception:
                pass

        # Clone repository
        temp_dir = Path(tempfile.mkdtemp())
        clone_path = temp_dir / "agent_repo"
        repo_root = clone_path  # Preserve repo root before subfolder navigation

        result = subprocess.run(
            ["git", "clone", "-b", branch, repo_url, str(clone_path)],
            capture_output=True,
            text=True,
            timeout=60
        )

        # Fallback: if branch not found, clone without -b (uses repo default)
        if result.returncode != 0:
            fallback_result = subprocess.run(
                ["git", "clone", repo_url, str(clone_path)],
                capture_output=True, text=True, timeout=60
            )
            if fallback_result.returncode != 0:
                raise Exception(f"Git clone failed: {result.stderr}")

        # Handle subfolder
        if subfolder:
            clone_path = clone_path / subfolder
            if not clone_path.exists():
                raise Exception(f"Subfolder not found: {subfolder}")

        # --- Multi-agent discovery ---
        explicit_main_file = data.get("main_file")
        if explicit_main_file:
            # User pinned a specific file — single-agent mode
            candidate_files = [clone_path / explicit_main_file]
        else:
            # If clone_path has a pyproject.toml → single package → single card
            pyproject_main = _find_main_file_from_pyproject(clone_path)
            if pyproject_main is not None:
                logger.info(f"pyproject.toml detected single-package agent: {pyproject_main}")
                candidate_files = [pyproject_main]
            else:
                # Always ONE card per repo import.
                # Pick the single best entry point; discovery runs on the whole clone_path dir.
                # Priority: main.py > agent.py > app.py > run.py > __main__.py > first found
                _ROOT_ENTRY_POINTS = ("main.py", "agent.py", "app.py", "run.py", "__main__.py")
                root_entry = next(
                    (clone_path / ep for ep in _ROOT_ENTRY_POINTS
                     if (clone_path / ep).exists()),
                    None,
                )
                if root_entry is None:
                    # Fallback: deepest-last agent file in the repo
                    all_found = sorted([
                        p for p in clone_path.rglob("*.py")
                        if len(p.relative_to(clone_path).parts) <= 4
                        and _is_agent_file(p)
                    ], key=lambda p: (len(p.relative_to(clone_path).parts), p.name))
                    root_entry = all_found[0] if all_found else None

                candidate_files = [root_entry] if root_entry else []

        if not candidate_files:
            available = [f.name for f in clone_path.rglob("*.py") if f.name != "__init__.py"]
            raise Exception(
                f"No agent files detected. "
                f"Available Python files: {', '.join(available[:10])}"
            )

        prefix = data.get("agent_name", "").strip()
        now = datetime.now()
        timestamp_base = now.strftime('%Y%m%d%H%M%S')
        created_agents: List[Dict[str, Any]] = []

        # Load existing registry once (short lock — git clone happens outside)
        REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
        agents_list: List[Dict[str, Any]] = _read_registry()
        existing_ids = {a["id"] for a in agents_list}

        # Install deps once using the first candidate
        first_rel = str(candidate_files[0].relative_to(clone_path))
        first_discovery = _discover_and_install_deps(clone_path, first_rel)

        for i, candidate in enumerate(candidate_files):
            rel_main = str(candidate.relative_to(clone_path))
            safe_stem = candidate.stem.lower().replace("_", "-")[:32]
            agent_id = f"git-{timestamp_base}-{safe_stem}"

            # Ensure ID uniqueness
            while agent_id in existing_ids:
                agent_id = f"{agent_id}-{len(existing_ids)}"
            existing_ids.add(agent_id)

            agent_name = _derive_agent_name(candidate, prefix=prefix)
            task_description = f"Execute {agent_name}"

            # Run discovery (first already done with dep install, rest are discovery-only)
            if i == 0:
                discovery_info = first_discovery
            else:
                discovery_info = _run_discovery_only(clone_path, rel_main)

            agent_info: Dict[str, Any] = {
                "id": agent_id,
                "name": agent_name,
                "source": "git",
                "repo_url": repo_url,
                "branch": branch,
                "main_file": rel_main,
                "task_description": task_description,
                "clone_path": str(clone_path),
                "repo_root": str(repo_root),
                "added_at": now.isoformat(),
                "status": "analyzing",
            }

            agent_info["status"] = discovery_info.get("status", "ready")
            if "discovery" in discovery_info:
                agent_info["discovery"] = discovery_info["discovery"]
                auto_task = _generate_auto_task(agent_name, discovery_info["discovery"], task_description, clone_path)
                agent_info["task_description"] = auto_task

            agents_list.append(agent_info)
            created_agents.append(agent_info)

        # Write registry — re-read under lock to merge with any concurrent imports
        _captured_agents = list(created_agents)
        with _registry_lock:
            current = []
            if REGISTRY_FILE.exists():
                try:
                    with open(REGISTRY_FILE) as f:
                        current = json.load(f)
                except (json.JSONDecodeError, OSError):
                    current = []
            current_ids = {a["id"] for a in current}
            for a in _captured_agents:
                if a["id"] not in current_ids:
                    current.append(a)
            REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_json(REGISTRY_FILE, current)

        return created_agents

    except subprocess.TimeoutExpired:
        # BUG-011: clean up temp_dir on failure so /tmp doesn't fill up
        if 'temp_dir' in locals():
            shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(status_code=408, detail="Clone timeout")
    except Exception as e:
        if 'temp_dir' in locals():
            shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/agents/import/zip", dependencies=[Depends(verify_api_key)])
async def import_zip_agent(file: UploadFile = File(...), agent_name: str = Form(...), main_file: Optional[str] = Form(None)) -> Dict[str, Any]:
    """Import agent from uploaded ZIP file"""
    
    try:
        import io

        # Read uploaded file
        file_content = await file.read()
        
        # Create temp directory for extraction
        temp_dir = Path(tempfile.mkdtemp())
        extract_path = temp_dir / "agent_files"
        extract_path.mkdir(parents=True, exist_ok=True)
        
        # Extract ZIP (BUG-005: safe extract prevents path traversal attacks)
        zip_buffer = io.BytesIO(file_content)
        with zipfile.ZipFile(zip_buffer, 'r') as zip_ref:
            _safe_extract(zip_ref, extract_path)
        
        # Auto-detect main file if not provided
        if not main_file:
            candidates = ["agent.py", "main.py", "app.py", "run.py"]
            for candidate in candidates:
                if (extract_path / candidate).exists():
                    main_file = candidate
                    break
            
            if not main_file:
                for py_file in extract_path.rglob("*.py"):
                    name = py_file.name.lower()
                    if 'agent' in name or 'main' in name:
                        main_file = str(py_file.relative_to(extract_path))
                        break
            
            if not main_file:
                raise Exception("Could not auto-detect main file")
        
        # Auto-detect task description
        agent_file = extract_path / main_file
        task_description = f"Execute {agent_name}"
        try:
            with open(agent_file) as f:
                content = f.read()
                import ast
                tree = ast.parse(content)
                docstring = ast.get_docstring(tree)
                if docstring:
                    task_description = docstring.split('\n')[0].strip()
        except Exception:
            pass

        # Create agent entry
        agent_info = {
            "id": f"zip-{uuid.uuid4().hex[:12]}",
            "name": agent_name,
            "source": "zip",
            "main_file": main_file,
            "task_description": task_description,
            "extract_path": str(extract_path),
            "repo_root": str(extract_path),
            "added_at": datetime.now().isoformat(),
            "status": "analyzing"
        }
        
        # Run discovery and install dependencies
        discovery_info = _discover_and_install_deps(extract_path, main_file)
        agent_info["status"] = discovery_info["status"]
        if "discovery" in discovery_info:
            agent_info["discovery"] = discovery_info["discovery"]
            # Generate auto-task based on discovered capabilities
            auto_task = _generate_auto_task(agent_name, discovery_info["discovery"], task_description, extract_path)
            agent_info["task_description"] = auto_task

        # Save to registry — under lock to prevent concurrent import clobber
        with _registry_lock:
            REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
            agents = []
            if REGISTRY_FILE.exists():
                try:
                    with open(REGISTRY_FILE) as f:
                        agents = json.load(f)
                except (json.JSONDecodeError, OSError):
                    agents = []
            agents.append(agent_info)
            _atomic_write_json(REGISTRY_FILE, agents)

        return agent_info

    except Exception as e:
        # BUG-011: clean up temp_dir on failure
        if 'temp_dir' in locals():
            shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/agents/{agent_id}/run", dependencies=[Depends(verify_api_key)])
def run_agent(agent_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Execute an agent with Norn monitoring"""
    task = data.get("task")
    if not task:
        raise HTTPException(status_code=400, detail="task is required")
    
    agents = _read_registry()
    agent = next((a for a in agents if a["id"] == agent_id), None)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    try:
        # Create session ID
        session_id = f"{agent_id}-{datetime.now().strftime('%Y%m%d%H%M%S')}"

        # Prepare agent execution environment
        agent_path = Path(agent.get("clone_path") or agent.get("extract_path", ""))
        if not agent_path.exists():
            raise HTTPException(status_code=400, detail="Agent files not found")

        main_file = agent_path / agent["main_file"]
        if not main_file.exists():
            raise HTTPException(status_code=400, detail=f"Main file not found: {agent['main_file']}")

        # Execute agent with Norn monitoring
        # For now, we'll create a basic session entry and mark it as running
        # The actual execution will be handled by a background process
        session_data = {
            "session_id": session_id,
            "agent_name": agent["name"],
            "agent_id": agent_id,
            "task": task,
            "started_at": datetime.now().isoformat(),
            "status": "active",
            "total_steps": 0,
            "steps": [],
            "issues": [],
            "overall_quality": "PENDING",
            "efficiency_score": None,
            "security_score": None,
        }

        # Save initial session
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        session_file = SESSIONS_DIR / f"{session_id}.json"
        _atomic_write_json(session_file, session_data)

        # Update agent status — under lock to prevent concurrent run clobber
        _aid = agent_id
        with _registry_lock:
            _agents = []
            if REGISTRY_FILE.exists():
                try:
                    with open(REGISTRY_FILE) as f:
                        _agents = json.load(f)
                except (json.JSONDecodeError, OSError):
                    _agents = []
            for a in _agents:
                if a["id"] == _aid:
                    a["status"] = "running"
                    a["last_run"] = datetime.now().isoformat()
                    break
            _atomic_write_json(REGISTRY_FILE, _agents)

        # Start background execution
        import threading
        thread = threading.Thread(
            target=_execute_agent_background,
            args=(agent_id, session_id, str(agent_path), agent["main_file"], task,
                  agent.get("repo_root", str(agent_path))),
            daemon=True
        )
        thread.start()
        
        return {
            "status": "started",
            "session_id": session_id,
            "agent_id": agent_id,
            "message": "Agent execution started"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _detect_package_info(agent_path: str, main_file: str, repo_root: str = None):
    """Detect if the agent file is inside a Python package.

    Returns (package_root, module_name, is_package) tuple.

    Examples:
      agent_path="/tmp/repo/server", main_file="main.py"
        -> ("/tmp/repo", "server.main", True)   if server/__init__.py exists

      agent_path="/tmp/repo", main_file="server/main.py"
        -> ("/tmp/repo", "server.main", True)   if server/__init__.py exists

      agent_path="/tmp/repo", main_file="agent.py"
        -> ("/tmp/repo", None, False)           no __init__.py
    """
    main_path = Path(agent_path) / main_file
    current = main_path.parent
    package_parts = [main_path.stem]

    # Walk up from the agent file looking for __init__.py
    while True:
        init_file = current / "__init__.py"
        if not init_file.exists():
            break
        package_parts.insert(0, current.name)
        current = current.parent

    if len(package_parts) > 1:
        # Found a package hierarchy
        module_name = ".".join(package_parts)
        return str(current), module_name, True

    # Fallback: check pyproject.toml at repo_root
    if repo_root:
        repo_root_path = Path(repo_root)
        pyproject = repo_root_path / "pyproject.toml"
        if pyproject.exists():
            try:
                import tomllib
            except ImportError:
                try:
                    import tomli as tomllib
                except ImportError:
                    return agent_path, None, False
            try:
                with open(pyproject, "rb") as f:
                    config = tomllib.load(f)
                packages = config.get("tool", {}).get("setuptools", {}).get("packages", [])
                agent_dir_name = Path(agent_path).name
                if agent_dir_name in packages:
                    module_name = f"{agent_dir_name}.{main_path.stem}"
                    return str(repo_root_path), module_name, True
            except Exception:
                pass

    return agent_path, None, False


def _execute_agent_background(agent_id: str, session_id: str, agent_path: str, main_file: str, task: str, repo_root: str = None):
    """Background thread to execute agent with Norn monitoring"""
    import importlib.util
    import time
    from norn.core.interceptor import NornHook
    from norn.core.audit_logger import AuditLogger
    
    session_file = SESSIONS_DIR / f"{session_id}.json"
    start_time = time.time()

    # Per-session workspace: agent file output goes here, not the project root
    workspace_dir = LOGS_DIR / "workspace" / session_id
    workspace_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Load current session
        with open(session_file) as f:
            session = json.load(f)

        # Record workspace path in session for dashboard visibility
        session["workspace"] = str(workspace_dir)
        
        # Load config
        config = {}
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE) as f:
                config = json.load(f)
        
        # Initialize Norn components
        audit_logger = AuditLogger()
        
        # Create Norn hook
        guard = NornHook(
            task=task,
            mode=config.get("guard_mode", "monitor"),
            max_steps=config.get("max_steps", 100),
            enable_ai_eval=config.get("enable_ai_eval", True),
            enable_shadow_browser=config.get("enable_shadow_browser", False),
            session_id=session_id,
            audit_logger=audit_logger
        )
        
        # Auto-install agent dependencies before execution
        agent_path_obj = Path(agent_path)
        repo_root_obj = Path(repo_root) if repo_root else agent_path_obj

        agent_pyproject_file = agent_path_obj / "pyproject.toml"
        pyproject_file = repo_root_obj / "pyproject.toml"
        req_file = repo_root_obj / "requirements.txt"
        agent_req_file = agent_path_obj / "requirements.txt"

        pip_base = [sys.executable, "-m", "pip", "install", "-q", "--user"]

        def _run_pip(args: list, label: str) -> None:
            result = subprocess.run(pip_base + args, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                logger.warning(f"pip install failed ({label}): {result.stderr[:500]}")
            else:
                logger.info(f"pip install succeeded ({label})")

        if agent_pyproject_file.exists():
            logger.info(f"Installing agent package from {agent_path_obj}")
            _run_pip(["-e", str(agent_path_obj)], str(agent_pyproject_file))
        elif pyproject_file.exists():
            logger.info(f"Installing agent package from {repo_root_obj}")
            _run_pip(["-e", str(repo_root_obj)], str(pyproject_file))
        elif req_file.exists():
            logger.info(f"Installing agent requirements from {req_file}")
            _run_pip(["-r", str(req_file)], str(req_file))
        elif agent_req_file.exists() and str(agent_req_file) != str(req_file):
            logger.info(f"Installing agent requirements from {agent_req_file}")
            _run_pip(["-r", str(agent_req_file)], str(agent_req_file))

        # Disable prompt caching to avoid Bedrock ValidationException
        # ("There is nothing available to cache") when system prompt is short
        if "STRANDS_CACHE_PROMPT" not in os.environ:
            os.environ["STRANDS_CACHE_PROMPT"] = ""
        if "STRANDS_CACHE_TOOLS" not in os.environ:
            os.environ["STRANDS_CACHE_TOOLS"] = ""

        # Detect package structure
        package_root, module_name, is_package = _detect_package_info(
            agent_path, main_file, repo_root
        )
        main_file_path = agent_path_obj / main_file

       
        _added_sys_paths: list[str] = []

        def _add_to_sys_path(p: str) -> None:
            if p not in sys.path:
                sys.path.insert(0, p)
                _added_sys_paths.append(p)

        if is_package:
            logger.info(f"Loading as package: {module_name} from {package_root}")
            _add_to_sys_path(package_root)
            # Also add the agent's own directory so local siblings (utils/, tools/, etc.) are importable
            _add_to_sys_path(str(agent_path))
            try:
                agent_module = importlib.import_module(module_name)
            except ImportError as e:
                logger.warning(f"Package import failed ({e}), trying pip install...")
                install_target = agent_pyproject_file if agent_pyproject_file.exists() else (repo_root_obj if pyproject_file.exists() else None)
                if install_target:
                    _run_pip(["-e", str(install_target.parent if install_target == agent_pyproject_file else install_target)], "retry")
                    agent_module = importlib.import_module(module_name)
                else:
                    raise
        else:
            logger.info(f"Loading as single file: {main_file_path}")
            _add_to_sys_path(str(agent_path))

            spec = importlib.util.spec_from_file_location("agent_module", main_file_path)
            if spec is None or spec.loader is None:
                raise Exception(f"Could not load agent from {main_file_path}")

            agent_module = importlib.util.module_from_spec(spec)
            agent_module.__name__ = "agent_module"  # Prevent __main__ block from running
            sys.modules["agent_module"] = agent_module
            spec.loader.exec_module(agent_module)

        # Try to find the agent instance
        from strands import Agent as StrandsAgent
        agent_instance = None

        # 1. Check common variable names
        for attr_name in ('agent', 'code_assistant', 'assistant', 'my_agent'):
            obj = getattr(agent_module, attr_name, None)
            if isinstance(obj, StrandsAgent):
                agent_instance = obj
                break

        # 2. If not found, scan all module attributes for any Agent instance
        if agent_instance is None:
            for attr_name in dir(agent_module):
                if attr_name.startswith('_'):
                    continue
                obj = getattr(agent_module, attr_name, None)
                if isinstance(obj, StrandsAgent):
                    agent_instance = obj
                    logger.info(f"Found agent instance: {attr_name}")
                    break

        # 3. If still not found, look for factory functions (create_*_agent, make_agent, etc.)
        if agent_instance is None:
            factory_patterns = ['create_agent', 'make_agent', 'build_agent', 'get_agent']
            for attr_name in dir(agent_module):
                if attr_name.startswith('_'):
                    continue
                # Match create_*_agent pattern (e.g., create_trading_agent)
                if (attr_name.startswith('create_') and attr_name.endswith('_agent')) or \
                   attr_name in factory_patterns:
                    func = getattr(agent_module, attr_name, None)
                    if callable(func):
                        try:
                            import inspect as _inspect
                            sig = _inspect.signature(func)
                            required = [
                                p for p in sig.parameters.values()
                                if p.default is _inspect.Parameter.empty
                                and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
                            ]
                            if required:
                                logger.info(f"Skipping factory {attr_name}(): requires args {[p.name for p in required]}")
                                continue
                            logger.info(f"Calling factory function: {attr_name}()")
                            result_obj = func()

                            # Handle direct Agent return
                            if isinstance(result_obj, StrandsAgent):
                                agent_instance = result_obj
                                logger.info(f"Got Agent from factory: {attr_name}")
                                break

                            # Handle tuple/list return (e.g. (agent, mcp_client))
                            if isinstance(result_obj, (tuple, list)):
                                for item in result_obj:
                                    if isinstance(item, StrandsAgent):
                                        agent_instance = item
                                        logger.info(f"Got Agent from factory tuple: {attr_name}")
                                        break
                                if agent_instance is not None:
                                    break

                            # Handle dict return (e.g. {"agent": agent, ...})
                            if isinstance(result_obj, dict):
                                for val in result_obj.values():
                                    if isinstance(val, StrandsAgent):
                                        agent_instance = val
                                        logger.info(f"Got Agent from factory dict: {attr_name}")
                                        break
                                if agent_instance is not None:
                                    break

                        except Exception as e:
                            logger.warning(f"Factory {attr_name}() failed: {e}")

        if agent_instance is None:
            # Fallback: execute as subprocess with environment variables
            if is_package:
                cmd = [sys.executable, "-m", module_name]
                cwd = package_root
            else:
                cmd = [sys.executable, str(main_file_path)]
                cwd = agent_path

            result = subprocess.run(
                cmd,
                cwd=str(workspace_dir),
                capture_output=True,
                text=True,
                timeout=300,
                env={
                    **os.environ,
                    "NORN_SESSION_ID": session_id,
                    "NORN_ENABLED": "true",
                    "NORN_MODE": config.get("mode", "monitor"),
                    "NORN_TASK": task,
                    "NORN_WORKSPACE": str(workspace_dir),
                    # Keep agent source importable
                    "PYTHONPATH": cwd + os.pathsep + os.environ.get("PYTHONPATH", ""),
                }
            )
            
            # Update session with subprocess results
            session["ended_at"] = datetime.now().isoformat()
            session["status"] = "completed" if result.returncode == 0 else "terminated"
            session["total_execution_time_ms"] = int((time.time() - start_time) * 1000)
            
            if result.returncode != 0:
                session["issues"].append({
                    "issue_type": "EXECUTION_ERROR",
                    "severity": 9,
                    "description": f"Agent execution failed: {result.stderr[:200]}",
                    "recommendation": "Check agent code and dependencies"
                })
        else:
            # Inject Norn hook into agent via HookRegistry
            if hasattr(agent_instance, 'hooks') and hasattr(agent_instance.hooks, 'add_hook'):
                agent_instance.hooks.add_hook(guard)
            
            # Execute agent with task — run inside workspace so file outputs
            # go to the isolated session directory, not the project root.
            os.environ["NORN_WORKSPACE"] = str(workspace_dir)
            with _chdir_lock:
                _original_cwd = os.getcwd()
                os.chdir(workspace_dir)
            try:
                result = agent_instance(task)

                # Run session-level AI evaluation (step scores already populated by Strands loop)
                try:
                    guard.run_session_evaluation()
                except Exception as e:
                    logger.warning(f"AI evaluation failed, using heuristic scores: {e}")

                # Get session report from guard
                report = guard.get_session_report()
                
                # Update session with Norn data
                session["ended_at"] = datetime.now().isoformat()
                session["status"] = "completed"
                session["total_steps"] = report.total_steps
                session["overall_quality"] = report.overall_quality.value
                session["efficiency_score"] = report.efficiency_score
                session["security_score"] = report.security_score
                session["task_completion"] = report.task_completion
                session["loop_detected"] = report.loop_detected
                session["total_execution_time_ms"] = int((time.time() - start_time) * 1000)
                
                # Add issues
                for issue in report.issues:
                    session["issues"].append({
                        "issue_type": issue.issue_type.value,
                        "severity": issue.severity,
                        "description": issue.description,
                        "recommendation": issue.recommendation
                    })
                
                # Add steps
                for step in report.steps:
                    session["steps"].append({
                        "step_id": step.step_id,
                        "step_number": step.step_number,
                        "timestamp": step.timestamp.isoformat(),
                        "tool_name": step.tool_name,
                        "tool_input": str(step.tool_input),
                        "tool_result": str(step.tool_result),
                        "status": step.status.value,
                        "relevance_score": step.relevance_score,
                        "security_score": step.security_score,
                        "reasoning": step.reasoning or ""
                    })
                
                # Add AI evaluation if available
                if report.ai_evaluation:
                    session["ai_evaluation"] = report.ai_evaluation

                if report.tool_analysis:
                    session["tool_analysis"] = report.tool_analysis

                if report.decision_observations:
                    session["decision_observations"] = report.decision_observations

                if report.efficiency_explanation:
                    session["efficiency_explanation"] = report.efficiency_explanation

                if report.recommendations:
                    session["recommendations"] = report.recommendations
                
            except Exception as e:
                session["ended_at"] = datetime.now().isoformat()
                session["status"] = "terminated"
                session["total_execution_time_ms"] = int((time.time() - start_time) * 1000)
                session["issues"].append({
                    "issue_type": "EXECUTION_ERROR",
                    "severity": 9,
                    "description": f"Agent execution error: {str(e)}",
                    "recommendation": "Check agent code and dependencies"
                })
            finally:
                # Always restore original working directory after in-process execution
                with _chdir_lock:
                    os.chdir(_original_cwd)
                # BUG-003: Clean up sys.path entries added for this agent
                for _p in _added_sys_paths:
                    try:
                        sys.path.remove(_p)
                    except ValueError:
                        pass
        
        # Save session
        _atomic_write_json(session_file, session)

        logger.info(f"Session {session_id} saved successfully")

        # Update agent status back to analyzed so it can be run again
        _reset_agent_status(agent_id)

    except subprocess.TimeoutExpired:
        
        try:
            with open(session_file) as f:
                session = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            session = {"session_id": session_id, "agent_id": agent_id, "status": "active", "steps": [], "issues": []}

        session["ended_at"] = datetime.now().isoformat()
        session["status"] = "terminated"
        session["total_execution_time_ms"] = int((time.time() - start_time) * 1000)
        session["issues"].append({
            "issue_type": "TIMEOUT",
            "severity": 8,
            "description": "Agent execution exceeded 5 minute timeout",
            "recommendation": "Optimize agent or increase timeout"
        })
        _atomic_write_json(session_file, session)

        logger.info(f"Session {session_id} timed out")
        _reset_agent_status(agent_id)

    except Exception as e:
        logger.error(f"Error executing agent: {e}")
        import traceback
        traceback.print_exc()

        
        try:
            try:
                with open(session_file) as f:
                    session = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                session = {"session_id": session_id, "agent_id": agent_id, "status": "active", "steps": [], "issues": []}

            session["ended_at"] = datetime.now().isoformat()
            session["status"] = "terminated"
            session["total_execution_time_ms"] = int((time.time() - start_time) * 1000)
            session["issues"].append({
                "issue_type": "EXECUTION_ERROR",
                "severity": 9,
                "description": f"Fatal error: {str(e)}",
                "recommendation": "Check logs for details"
            })
            _atomic_write_json(session_file, session)
        except Exception as save_err:
            logger.error(f"Failed to save error session: {save_err}")

        _reset_agent_status(agent_id)


def _reset_agent_status(agent_id: str):
    """Reset agent status back to 'analyzed' so it can be run again."""
    try:
        with _registry_lock:
            if REGISTRY_FILE.exists():
                with open(REGISTRY_FILE) as f:
                    agents = json.load(f)
                for agent in agents:
                    if agent["id"] == agent_id:
                        agent["status"] = "analyzed"
                        agent["last_run"] = datetime.now().isoformat()
                        break
                _atomic_write_json(REGISTRY_FILE, agents)
    except Exception:
        pass


@app.delete("/api/agents/{agent_id}", dependencies=[Depends(verify_api_key)])
def delete_agent(agent_id: str) -> Dict[str, str]:
    """Delete an agent"""
    if not REGISTRY_FILE.exists():
        raise HTTPException(status_code=404, detail="No agents registered")
    
    try:
        with _registry_lock:
            with open(REGISTRY_FILE) as f:
                agents = json.load(f)
            
            agent = next((a for a in agents if a["id"] == agent_id), None)
            if not agent:
                raise HTTPException(status_code=404, detail="Agent not found")
            
            # Remove from registry
            agents = [a for a in agents if a["id"] != agent_id]
            _atomic_write_json(REGISTRY_FILE, agents)

        # Clean up temp files — only for git/zip agents, never for hook agents
        # (done outside the lock since it can be slow)
    
        if agent.get("source") == "git":
            path = Path(agent.get("clone_path", ""))
            # clone_path is temp_dir/agent_repo — delete temp_dir (parent)
            cleanup_dir = path.parent if path and path.parent.name else path
            if cleanup_dir and cleanup_dir.exists():
                shutil.rmtree(cleanup_dir, ignore_errors=True)
        elif agent.get("source") == "zip":
            path = Path(agent.get("extract_path", ""))
            # extract_path is temp_dir/agent_files — delete temp_dir (parent)
            cleanup_dir = path.parent if path and path.parent.name else path
            if cleanup_dir and cleanup_dir.exists():
                shutil.rmtree(cleanup_dir, ignore_errors=True)
        
        return {"status": "deleted", "id": agent_id}
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/audit-logs", dependencies=[Depends(verify_api_key)])
def get_audit_logs(limit: int = 200) -> List[Dict[str, Any]]:
    """Get chronological audit log events extracted from all sessions"""
    if not SESSIONS_DIR.exists():
        return []

    events: List[Dict[str, Any]] = []
    session_files = sorted(
        SESSIONS_DIR.glob("*.json"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )

    for file in session_files[:50]:
        try:
            with open(file) as f:
                session = json.load(f)
        except Exception:
            continue

        sid = session.get("session_id", "")
        agent = session.get("agent_name", "Unknown")

        # Session start event
        start_time = session.get("started_at") or session.get("start_time", "")
        if start_time:
            events.append({
                "id": f"{sid}-start",
                "timestamp": start_time,
                "event_type": "session_start",
                "session_id": sid,
                "agent_name": agent,
                "summary": f"Session started – {(session.get('task', {}).get('description', '') if isinstance(session.get('task'), dict) else str(session.get('task', '')))[:80]}",
                "severity": "info",
            })

        # Step-level events
        for step in session.get("steps", []):
            ts = step.get("timestamp", start_time)
            tool = step.get("tool_name", "unknown")
            status = step.get("status", "SUCCESS")
            sec = step.get("security_score", 100)
            rel = step.get("relevance_score", 100)

            severity = "info"
            if sec is not None and sec < 70:
                severity = "critical"
            elif sec is not None and sec < 90:
                severity = "warning"
            elif status in ("IRRELEVANT", "REDUNDANT"):
                severity = "warning"
            elif status in ("FAILED", "BLOCKED"):
                severity = "critical"

            events.append({
                "id": step.get("step_id", ""),
                "timestamp": ts,
                "event_type": "tool_call",
                "session_id": sid,
                "agent_name": agent,
                "summary": f"{tool}() → {status}  |  Security: {sec if sec is not None else 'N/A'}%  Relevance: {rel if rel is not None else 'N/A'}%",
                "severity": severity,
                "detail": step.get("reasoning", ""),
            })

        # Issue events
        for issue in session.get("issues", []):
            if isinstance(issue, dict):
                sev_num = issue.get("severity", 5)
                severity = "critical" if sev_num >= 8 else ("warning" if sev_num >= 5 else "info")
                events.append({
                    "id": issue.get("issue_id", ""),
                    "timestamp": issue.get("timestamp", start_time),
                    "event_type": "issue",
                    "session_id": sid,
                    "agent_name": agent,
                    "summary": f"[{issue.get('issue_type', 'UNKNOWN')}] {issue.get('description', '')}",
                    "severity": severity,
                    "detail": issue.get("recommendation", ""),
                })

        # Session end event
        end_time = session.get("ended_at") or session.get("end_time")
        if end_time:
            quality = session.get("overall_quality", "GOOD")
            severity = "info" if quality in ("EXCELLENT", "GOOD") else ("warning" if quality == "POOR" else "critical")
            events.append({
                "id": f"{sid}-end",
                "timestamp": end_time,
                "event_type": "session_end",
                "session_id": sid,
                "agent_name": agent,
                "summary": f"Session ended – Quality: {quality}, Efficiency: {session.get('efficiency_score', 0)}%, Security: {session.get('security_score', 'N/A')}{'%' if session.get('security_score') is not None else ''}",
                "severity": severity,
            })

    # Sort all events by timestamp descending
    events.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    return events[:limit]


@app.get("/api/stats", dependencies=[Depends(verify_api_key)])
def get_stats() -> Dict[str, Any]:
    """Get dashboard statistics"""
    sessions = get_sessions()
    agents = get_agents()
    
    if not sessions:
        return {
            "total_sessions": 0,
            "active_sessions": 0,
            "critical_threats": 0,
            "avg_efficiency": 0,
            "avg_security": 100,
            "total_agents": len(agents)
        }
    
    quality_map = {"EXCELLENT": 100, "GOOD": 75, "POOR": 50, "FAILED": 25, "STUCK": 0}
    
    return {
        "total_sessions": len(sessions),
        "active_sessions": sum(1 for s in sessions if s.get("status") == "active"),
        "critical_threats": sum(1 for s in sessions if (s.get("security_score") or 100) < 70),
        "avg_efficiency": sum(s.get("efficiency_score") or 0 for s in sessions) / len(sessions),
        "avg_security": sum(s.get("security_score") or 100 for s in sessions) / len(sessions),
        "total_agents": len(agents)
    }


@app.get("/api/config", dependencies=[Depends(verify_api_key)])
def get_config() -> Dict[str, Any]:
    """Get current Norn configuration"""
    config = _load_config()

    # Add runtime info
    sessions_count = len(list(SESSIONS_DIR.glob("*.json"))) if SESSIONS_DIR.exists() else 0
    agents_count = 0
    if REGISTRY_FILE.exists():
        try:
            with open(REGISTRY_FILE) as f:
                agents_count = len(json.load(f))
        except Exception:
            pass

    steps_files = list((LOGS_DIR / "steps").glob("*.jsonl")) if (LOGS_DIR / "steps").exists() else []
    issues_files = list((LOGS_DIR / "issues").glob("*.json")) if (LOGS_DIR / "issues").exists() else []

    config["_runtime"] = {
        "api_version": "1.0.0",
        "log_directory": str(LOGS_DIR.resolve()),
        "sessions_directory": str(SESSIONS_DIR.resolve()),
        "total_session_files": sessions_count,
        "total_agent_files": agents_count,
        "total_step_log_files": len(steps_files),
        "total_issue_files": len(issues_files),
        "config_file": str(CONFIG_FILE.resolve()),
        "config_exists": CONFIG_FILE.exists(),
    }

    return config


@app.put("/api/config", dependencies=[Depends(verify_api_key)])
def update_config(data: Dict[str, Any]) -> Dict[str, Any]:
    """Update Norn configuration"""
    config = _load_config()

    allowed_keys = set(DEFAULT_CONFIG.keys())
    updated = []
    for key, value in data.items():
        if key in allowed_keys:
            config[key] = value
            updated.append(key)

    if not updated:
        raise HTTPException(status_code=400, detail="No valid config keys provided")

    _save_config(config)
    return {"status": "updated", "updated_keys": updated, "config": config}


@app.websocket("/ws/sessions")
async def websocket_sessions(websocket: WebSocket):
    """WebSocket endpoint for real-time session updates"""
    # BUG-012: Auth check — mirrors REST endpoint verify_api_key logic
    if API_KEY:
        api_key = websocket.query_params.get("api_key") or websocket.headers.get("x-api-key")
        if api_key != API_KEY:
            await websocket.close(code=4001, reason="Unauthorized")
            return
    await manager.connect(websocket)

    try:
        # Send initial data
        sessions = get_sessions()
        agents = get_agents()
        await websocket.send_json({
            "type": "initial",
            "sessions": sessions,
            "agents": agents
        })

        # Keep connection alive and send periodic updates
        import time as _time
        last_update = _time.time()

        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=2.0)
                if data == "ping":
                    await websocket.send_json({"type": "pong"})
            except asyncio.TimeoutError:
                pass

            # Send updates every 5 seconds regardless of ping/pong
            now = _time.time()
            if now - last_update >= 5.0:
                last_update = now
                sessions = get_sessions()
                agents = get_agents()
                await websocket.send_json({
                    "type": "update",
                    "sessions": sessions,
                    "agents": agents
                })

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket)


async def notify_session_update(session_id: str):
    """Notify all connected clients about a session update"""
    try:
        session = get_session(session_id)
        await manager.broadcast({
            "type": "session_update",
            "session": session
        })
    except Exception as e:
        logger.warning(f"Failed to notify WebSocket clients for session {session_id}: {e}")


# ── Hook Agent Endpoints ────────────────────────────────

@app.post("/api/agents/register")
def register_hook_agent(data: Dict[str, Any]) -> Dict[str, Any]:
    """Register a hook agent (idempotent — returns existing if name matches)."""
    name = data.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    with _registry_lock:
        REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
        agents: List[Dict[str, Any]] = []
        if REGISTRY_FILE.exists():
            try:
                with open(REGISTRY_FILE) as f:
                    agents = json.load(f)
            except Exception:
                agents = []

        # Return existing hook agent with this name
        for agent in agents:
            if agent.get("name") == name and agent.get("source") == "hook":
                return agent

        # Create new hook agent entry
        agent_id = f"hook-{datetime.now().strftime('%Y%m%d%H%M%S')}-{name.lower().replace(' ', '_')[:20]}"
        agent_info: Dict[str, Any] = {
            "id": agent_id,
            "name": name,
            "source": "hook",
            "source_file": data.get("source_file", "unknown.py"),
            "task_description": data.get("task_description", f"Live monitoring: {name}"),
            "added_at": datetime.now().isoformat(),
            "status": "analyzed",
            "discovery": {
                "agent_type": "Hook Agent",
                "tools": [],
                "functions": [],
                "imports": [],
                "dependencies": [],
                "potential_issues": [],
                "entry_points": [],
            },
        }

        agents.append(agent_info)
        _atomic_write_json(REGISTRY_FILE, agents)

    logger.info("Hook agent registered: %s (%s)", name, agent_id)
    return agent_info


@app.post("/api/sessions/ingest")
def ingest_session(data: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new session or resume an existing one (by session_id)."""
    session_id = data.get("session_id", "")
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")

    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    session_file = SESSIONS_DIR / f"{session_id}.json"

    # Resume existing session
    if session_file.exists():
        try:
            with open(session_file) as f:
                existing = json.load(f)
            existing["status"] = "active"
            existing["ended_at"] = None
            if data.get("task") and not existing.get("task"):
                existing["task"] = data["task"]
            # Backfill swarm fields if not yet set
            if data.get("swarm_id") and not existing.get("swarm_id"):
                existing["swarm_id"] = data["swarm_id"]
            if data.get("swarm_order") is not None and existing.get("swarm_order") is None:
                existing["swarm_order"] = data["swarm_order"]
            _atomic_write_json(session_file, existing)
            logger.info("Session resumed: %s (%d existing steps)", session_id, len(existing.get("steps", [])))
            return existing
        except Exception as e:
            logger.warning("Failed to resume session %s: %s", session_id, e)

    # Create new session
    session_data: Dict[str, Any] = {
        "session_id": session_id,
        "agent_id": data.get("agent_id", ""),
        "agent_name": data.get("agent_name", "Unknown"),
        "model": data.get("model"),
        "task": data.get("task", ""),
        "started_at": data.get("started_at", datetime.now().isoformat()),
        "ended_at": None,
        "status": "active",
        "total_steps": 0,
        "steps": [],
        "issues": [],
        "overall_quality": "PENDING",
        "efficiency_score": None,
        "security_score": None,
        "task_completion": None,
        "completion_confidence": None,
        "loop_detected": False,
        "security_breach_detected": False,
        "total_execution_time_ms": 0,
        "ai_evaluation": "",
        "recommendations": [],
        "tool_analysis": [],
        "decision_observations": [],
        "efficiency_explanation": "",
        # Multi-agent swarm tracking
        "swarm_id": data.get("swarm_id"),
        "swarm_order": data.get("swarm_order"),
        "handoff_input": data.get("handoff_input"),
    }

    _atomic_write_json(session_file, session_data)
    logger.info("Session created: %s", session_id)
    return session_data


@app.post("/api/sessions/{session_id}/step")
async def add_session_step(session_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Add a step to a session in real-time."""
    session_file = SESSIONS_DIR / f"{session_id}.json"
    if not session_file.exists():
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        with open(session_file) as f:
            session = json.load(f)

        # Append new step
        session.setdefault("steps", []).append(data)
        session["total_steps"] = len(session["steps"])
        session["status"] = "active"

        _atomic_write_json(session_file, session)

        # Broadcast to WebSocket clients
        try:
            await manager.broadcast({
                "type": "session_update",
                "session": normalize_session(session),
            })
        except Exception:
            pass

        return {"status": "ok", "total_steps": session["total_steps"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/sessions/{session_id}/complete")
async def complete_session(session_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Mark session as complete and update scores. Preserves existing steps."""
    session_file = SESSIONS_DIR / f"{session_id}.json"
    if not session_file.exists():
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        with open(session_file) as f:
            session = json.load(f)

        # Preserve existing steps
        existing_steps = session.get("steps", [])

        # Update with completion data
        session.update(data)

        # Restore steps if payload didn't include them
        if not data.get("steps"):
            session["steps"] = existing_steps
            session["total_steps"] = len(existing_steps)

        session["status"] = data.get("status", "completed")

        _atomic_write_json(session_file, session)

        # Broadcast to WebSocket clients
        try:
            await manager.broadcast({
                "type": "session_update",
                "session": normalize_session(session),
            })
        except Exception:
            pass

        return {"status": "ok", "session_id": session_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/sessions/{session_id}", dependencies=[Depends(verify_api_key)])
def delete_session(session_id: str) -> Dict[str, str]:
    """Delete a session. Does not affect the agent registry."""
    session_file = SESSIONS_DIR / f"{session_id}.json"
    if not session_file.exists():
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        session_file.unlink()
        return {"status": "deleted", "id": session_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/sessions/{session_id}/steps/{step_id}", dependencies=[Depends(verify_api_key)])
def delete_step(session_id: str, step_id: str) -> Dict:
    """Delete a single step from a session."""
    session_file = SESSIONS_DIR / f"{session_id}.json"
    if not session_file.exists():
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        with open(session_file) as f:
            session = json.load(f)
        steps = session.get("steps", [])
        new_steps = [s for s in steps if s.get("step_id") != step_id]
        if len(new_steps) == len(steps):
            raise HTTPException(status_code=404, detail="Step not found")
        session["steps"] = new_steps
        session["total_steps"] = len(new_steps)
        _atomic_write_json(session_file, session)
        return {"ok": True, "remaining": len(new_steps)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Swarm Endpoints ────────────────────────────────────────────────────────


def _load_all_sessions() -> list[dict]:
    """Load all session JSON files from SESSIONS_DIR."""
    sessions = []
    if not SESSIONS_DIR.exists():
        return sessions
    for f in SESSIONS_DIR.glob("*.json"):
        try:
            with open(f) as fp:
                sessions.append(json.load(fp))
        except Exception:
            pass
    return sessions


def _drift_score(sessions: list[dict]) -> float:
    """
    Calculate collective drift: how much do later agents' tasks diverge
    from the first agent's task in the swarm?

    Uses simple word-overlap (Jaccard similarity) — no embeddings needed.
    Returns a float 0.0–1.0 where 1.0 means perfect alignment (no drift).
    """
    if len(sessions) < 2:
        return 1.0

    def _task_str(s: dict) -> str:
        """Extract task as plain string — handles both str and TaskDefinition dict."""
        t = s.get("task") or ""
        if isinstance(t, dict):
            t = t.get("description") or ""
        return str(t)

    sorted_sessions = sorted(sessions, key=lambda s: s.get("swarm_order") or 0)
    first_task = _task_str(sorted_sessions[0]).lower().split()
    if not first_task:
        return 1.0

    first_set = set(first_task)
    scores = []
    for s in sorted_sessions[1:]:
        other_task = _task_str(s).lower().split()
        if not other_task:
            scores.append(0.0)
            continue
        other_set = set(other_task)
        intersection = first_set & other_set
        union = first_set | other_set
        scores.append(len(intersection) / len(union) if union else 1.0)

    return round(sum(scores) / len(scores), 3) if scores else 1.0


@app.get("/api/swarms")
def list_swarms() -> list[dict]:
    """
    Return all swarm groups: sessions that share a swarm_id,
    summarised into one card per swarm.
    """
    all_sessions = _load_all_sessions()

    # Group by swarm_id (only sessions that have one)
    groups: dict[str, list[dict]] = {}
    for s in all_sessions:
        sid = s.get("swarm_id")
        if sid:
            groups.setdefault(sid, []).append(s)

    swarms = []
    for swarm_id, members in groups.items():
        sorted_members = sorted(members, key=lambda s: s.get("swarm_order") or 0)

        quality_counts: dict[str, int] = {}
        for m in members:
            q = m.get("overall_quality", "PENDING")
            quality_counts[q] = quality_counts.get(q, 0) + 1

        # Overall swarm quality = worst individual quality
        priority = ["FAILED", "STUCK", "POOR", "PENDING", "GOOD", "EXCELLENT"]
        overall = "PENDING"
        for q in priority:
            if quality_counts.get(q):
                overall = q
                break

        swarms.append({
            "swarm_id": swarm_id,
            "agent_count": len(members),
            "overall_quality": overall,
            "drift_score": _drift_score(members),
            "started_at": min((m.get("started_at") or "") for m in members),
            "ended_at": max((m.get("ended_at") or "") for m in members),
            "agents": [
                {
                    "session_id": m.get("session_id"),
                    "agent_name": m.get("agent_name"),
                    "swarm_order": m.get("swarm_order"),
                    "overall_quality": m.get("overall_quality", "PENDING"),
                    "efficiency_score": m.get("efficiency_score"),
                    "security_score": m.get("security_score"),
                    "task": (m.get("task") or {}).get("description", "") if isinstance(m.get("task"), dict) else (m.get("task") or ""),
                    "status": m.get("status"),
                    "total_steps": m.get("total_steps", 0),
                    "handoff_input": m.get("handoff_input"),
                }
                for m in sorted_members
            ],
        })

    # Most recent swarms first
    swarms.sort(key=lambda s: s.get("started_at") or "", reverse=True)
    return swarms


@app.get("/api/swarms/{swarm_id}")
def get_swarm(swarm_id: str) -> dict:
    """Return full detail for a single swarm."""
    all_sessions = _load_all_sessions()
    members = [s for s in all_sessions if s.get("swarm_id") == swarm_id]
    if not members:
        raise HTTPException(status_code=404, detail="Swarm not found")

    sorted_members = sorted(members, key=lambda s: s.get("swarm_order") or 0)
    return {
        "swarm_id": swarm_id,
        "agent_count": len(members),
        "drift_score": _drift_score(members),
        "sessions": sorted_members,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="::", port=8000)
