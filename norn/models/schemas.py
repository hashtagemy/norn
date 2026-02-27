# norn/models/schemas.py
"""
Pydantic data models for Norn Quality Monitoring.
Schemas for tracking agent task execution quality, efficiency, and correctness.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

import uuid
from pydantic import BaseModel, Field


# -- Enums --

class StepStatus(str, Enum):
    """Status of a single tool call step."""
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    IRRELEVANT = "IRRELEVANT"  # Tool call not related to task
    REDUNDANT = "REDUNDANT"    # Duplicate/unnecessary call
    BLOCKED = "BLOCKED"        # Blocked due to security concern


class SessionQuality(str, Enum):
    """Overall quality rating for a session."""
    EXCELLENT = "EXCELLENT"    # Task completed efficiently
    GOOD = "GOOD"              # Task completed with minor issues
    POOR = "POOR"              # Task completed inefficiently
    FAILED = "FAILED"          # Task not completed
    STUCK = "STUCK"            # Agent in infinite loop
    PENDING = "PENDING"        # Not yet evaluated


class IssueType(str, Enum):
    """Types of quality and security issues detected."""
    # Quality Issues
    INFINITE_LOOP = "INFINITE_LOOP"           # Same tool called repeatedly
    TASK_DRIFT = "TASK_DRIFT"                 # Agent doing unrelated work
    INEFFICIENCY = "INEFFICIENCY"             # Too many steps for simple task
    INCOMPLETE = "INCOMPLETE"                 # Task not finished
    TOOL_MISUSE = "TOOL_MISUSE"              # Wrong tool for the job
    ERROR_HANDLING = "ERROR_HANDLING"         # Poor error recovery
    
    # Security Issues
    DATA_EXFILTRATION = "DATA_EXFILTRATION"   # Sending sensitive data externally
    PROMPT_INJECTION = "PROMPT_INJECTION"     # Malicious instructions detected
    UNAUTHORIZED_ACCESS = "UNAUTHORIZED_ACCESS" # Accessing forbidden resources
    SUSPICIOUS_BEHAVIOR = "SUSPICIOUS_BEHAVIOR" # Unusual action patterns
    CREDENTIAL_LEAK = "CREDENTIAL_LEAK"       # API keys, passwords in output
    SECURITY_BYPASS = "SECURITY_BYPASS"       # SSL/TLS verification disabled
    MISSING_CONFIG  = "MISSING_CONFIG"        # Required env var / config missing


class GuardMode(str, Enum):
    """Operating mode for Norn."""
    MONITOR = "monitor"      # Passive observation only
    INTERVENE = "intervene"  # Can cancel tool calls if stuck/unsafe
    ENFORCE = "enforce"      # Strict security enforcement


# -- Core Schemas --

class TaskDefinition(BaseModel):
    """Definition of the task the agent should accomplish."""
    
    task_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    description: str  # Natural language task description
    expected_tools: list[str] = Field(default_factory=list)  # Tools agent should use
    max_steps: int = 20  # Maximum reasonable steps
    success_criteria: str = ""  # How to know task is done
    metadata: dict[str, Any] = Field(default_factory=dict)


class StepRecord(BaseModel):
    """Single tool call step in agent execution."""
    
    step_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    step_number: int  # Sequential step counter
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    tool_name: str
    tool_input: dict[str, Any] = Field(default_factory=dict)
    tool_result: str = ""  # Truncated result
    status: StepStatus = StepStatus.SUCCESS
    relevance_score: Optional[int] = Field(ge=0, le=100, default=None)  # None = not yet evaluated
    security_score: Optional[int] = Field(ge=0, le=100, default=None)  # None = not yet evaluated
    reasoning: str = ""  # AI evaluation reasoning
    execution_time_ms: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class QualityIssue(BaseModel):
    """A quality problem detected during execution."""
    
    issue_id: str = Field(default_factory=lambda: f"QI-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{str(uuid.uuid4())[:4]}")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    issue_type: IssueType
    severity: int = Field(ge=1, le=10, default=5)  # 1=minor, 10=critical
    description: str
    affected_steps: list[str] = Field(default_factory=list)  # step_ids
    recommendation: str = ""
    auto_resolved: bool = False


class SessionReport(BaseModel):
    """Complete quality and security report for an agent session."""
    
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])
    agent_name: str = "unknown"
    model: Optional[str] = None
    task: Optional[TaskDefinition] = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    ended_at: Optional[datetime] = None
    
    # Execution metrics
    total_steps: int = 0
    successful_steps: int = 0
    failed_steps: int = 0
    irrelevant_steps: int = 0
    redundant_steps: int = 0
    blocked_steps: int = 0
    
    # Quality metrics
    overall_quality: SessionQuality = SessionQuality.PENDING
    efficiency_score: Optional[int] = Field(ge=0, le=100, default=None)  # None = not yet evaluated
    task_completion: Optional[bool] = None  # None = not yet determined
    completion_confidence: Optional[int] = Field(ge=0, le=100, default=None)

    # Security metrics
    security_score: Optional[int] = Field(ge=0, le=100, default=None)  # None = not yet evaluated
    security_threats_detected: int = 0
    data_exfiltration_attempts: int = 0
    injection_attempts: int = 0
    
    # Issues
    issues: list[QualityIssue] = Field(default_factory=list)
    loop_detected: bool = False
    drift_detected: bool = False
    security_breach_detected: bool = False
    
    # Steps
    steps: list[StepRecord] = Field(default_factory=list)
    
    # Summary
    total_execution_time_ms: float = 0.0
    ai_evaluation: str = ""  # Nova Lite's overall assessment
    tool_analysis: list[dict] = Field(default_factory=list)  # Per-tool usage analysis
    decision_observations: list[str] = Field(default_factory=list)  # Agent decision-making patterns
    efficiency_explanation: str = ""  # Why the efficiency score is what it is
    recommendations: list[str] = Field(default_factory=list)

    # Multi-agent swarm tracking
    swarm_id: Optional[str] = None        # Shared ID across agents in the same swarm
    swarm_order: Optional[int] = None     # Position in the swarm pipeline (1 = first)
    handoff_input: Optional[str] = None  # Data received from the previous agent in the swarm


class TestCase(BaseModel):
    """Automated test case for agent quality."""
    
    test_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str
    description: str
    task: TaskDefinition
    expected_outcome: str
    max_steps: int = 20
    timeout_seconds: int = 60
    tags: list[str] = Field(default_factory=list)
    enabled: bool = True


class TestResult(BaseModel):
    """Result of running a test case."""
    
    result_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    test_case: TestCase
    session_report: SessionReport
    passed: bool = False
    failure_reason: str = ""
    execution_time_seconds: float = 0.0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# -- Legacy compatibility (for gradual migration) --

class ActionRecord(BaseModel):
    """Legacy action record - maps to StepRecord."""
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    agent_name: str = "unknown"
    tool_name: str = "unknown"
    tool_input: dict[str, Any] = Field(default_factory=dict)
    tool_result_snippet: str = ""
    layer: int = 1
    decision: str = "ALLOW"
    policy_id: str = "none"
    anomaly_score: int = 0
    threats: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
