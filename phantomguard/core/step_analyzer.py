# phantomguard/core/step_analyzer.py
"""
Deterministic step analysis for loop detection and relevance checking.
Fast, rule-based checks before AI evaluation.
"""

from collections import Counter, deque
from typing import Any
import logging

from phantomguard.models.schemas import StepRecord, StepStatus, IssueType, QualityIssue

logger = logging.getLogger(__name__)


class StepAnalyzer:
    """
    Analyzes tool call patterns for loops, redundancy, and drift.
    Uses deterministic rules for fast detection.
    """
    
    def __init__(
        self,
        loop_window: int = 5,
        loop_threshold: int = 3,
        max_same_tool: int = 10,
    ):
        """
        Args:
            loop_window: Number of recent steps to check for loops
            loop_threshold: How many times same pattern = loop
            max_same_tool: Max times same tool can be called
        """
        self.loop_window = loop_window
        self.loop_threshold = loop_threshold
        self.max_same_tool = max_same_tool
        
        # State tracking
        self._recent_steps: deque[tuple[str, str]] = deque(maxlen=loop_window)
        self._tool_counter: Counter[str] = Counter()
        self._input_hashes: set[str] = set()
    
    def analyze_step(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        step_number: int,
    ) -> tuple[StepStatus, list[QualityIssue]]:
        """
        Analyze a single step for issues.
        
        Returns:
            (status, issues) - status is SUCCESS/REDUNDANT, issues is list of problems
        """
        issues = []
        status = StepStatus.SUCCESS

        # 0. Deterministik güvenlik kontrolleri (AI değerlendirmesinden önce)
        # SSL/TLS bypass tespiti
        _SSL_BYPASS_KEYS = ("verify_ssl", "verify", "ssl_verify", "check_ssl", "ssl_check")
        for _key in _SSL_BYPASS_KEYS:
            if tool_input.get(_key) is False:
                issues.append(QualityIssue(
                    issue_type=IssueType.SECURITY_BYPASS,
                    severity=8,
                    description=(
                        f"'{_key}=False' tespit edildi: SSL sertifika doğrulaması devre dışı bırakıldı. "
                        "Bu bir MITM (ortadaki adam) saldırısına kapı açar."
                    ),
                    affected_steps=[f"step_{step_number}"],
                    recommendation=(
                        f"'{_key}' parametresini kaldırın veya True olarak bırakın. "
                        "SSL hatası yaşıyorsanız sertifikayı düzgün yükleyin (certifi / CA bundle)."
                    )
                ))
                break

        # 1. Check for exact duplicate calls
        input_hash = self._hash_input(tool_name, tool_input)
        if input_hash in self._input_hashes:
            status = StepStatus.REDUNDANT
            issues.append(QualityIssue(
                issue_type=IssueType.INEFFICIENCY,
                severity=3,
                description=f"Duplicate call to {tool_name} with same inputs",
                affected_steps=[],
                recommendation="Avoid calling same tool with same inputs multiple times"
            ))
        else:
            self._input_hashes.add(input_hash)
        
        # 2. Check for same tool called too many times
        self._tool_counter[tool_name] += 1
        if self._tool_counter[tool_name] > self.max_same_tool:
            issues.append(QualityIssue(
                issue_type=IssueType.INFINITE_LOOP,
                severity=8,
                description=f"{tool_name} called {self._tool_counter[tool_name]} times - possible infinite loop",
                affected_steps=[],
                recommendation="Agent may be stuck in a loop, consider intervention"
            ))
        
        # 3. Check for repeating patterns (A→B→A→B→A→B)
        step_signature = (tool_name, str(sorted(tool_input.items())))
        self._recent_steps.append(step_signature)
        
        if len(self._recent_steps) >= self.loop_window:
            pattern_counts = Counter(self._recent_steps)
            most_common = pattern_counts.most_common(1)[0]
            if most_common[1] >= self.loop_threshold:
                issues.append(QualityIssue(
                    issue_type=IssueType.INFINITE_LOOP,
                    severity=9,
                    description=f"Repeating pattern detected: {most_common[0][0]} called {most_common[1]} times in last {self.loop_window} steps",
                    affected_steps=[],
                    recommendation="Agent is stuck in a loop, intervention recommended"
                ))
        
        return status, issues
    
    def check_efficiency(self, total_steps: int, max_expected: int) -> list[QualityIssue]:
        """Check if agent is taking too many steps."""
        issues = []
        
        if total_steps > max_expected * 1.5:
            issues.append(QualityIssue(
                issue_type=IssueType.INEFFICIENCY,
                severity=5,
                description=f"Task took {total_steps} steps (expected ~{max_expected})",
                affected_steps=[],
                recommendation="Agent may be inefficient, review task approach"
            ))
        
        return issues
    
    def reset(self):
        """Reset state for new session."""
        self._recent_steps.clear()
        self._tool_counter.clear()
        self._input_hashes.clear()
    
    @staticmethod
    def _hash_input(tool_name: str, tool_input: dict[str, Any]) -> str:
        """Create hash of tool call for duplicate detection."""
        # Sort keys for consistent hashing
        items = sorted(tool_input.items())
        return f"{tool_name}:{str(items)}"
