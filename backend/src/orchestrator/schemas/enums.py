"""Status and category enums shared across the system.

StrEnum members serialize to their string values in JSON — what both the API
and LLM structured outputs need.
"""

from enum import StrEnum


class TaskStatus(StrEnum):
    PENDING = "pending"
    PLANNING = "planning"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SubTaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    IN_REVIEW = "in_review"
    REVISING = "revising"
    COMPLETED = "completed"
    FAILED = "failed"
    ESCALATED = "escalated"


class SpecialistType(StrEnum):
    RESEARCH = "research"
    WRITER = "writer"
    DATA_ANALYSIS = "data_analysis"
    CODE_EXECUTION = "code_execution"


class AgentRole(StrEnum):
    SUPERVISOR = "supervisor"
    SPECIALIST = "specialist"
    REVIEWER = "reviewer"


class ReviewVerdict(StrEnum):
    ACCEPT = "accept"
    REVISE = "revise"
    ESCALATE = "escalate"


class EscalationReason(StrEnum):
    LOW_CONFIDENCE = "low_confidence"
    REPEATED_FAILURE = "repeated_failure"
    SENSITIVE_TOOL = "sensitive_tool"
    LOW_REVIEW_SCORE = "low_review_score"
    EXPLICIT_REQUEST = "explicit_request"


class ApprovalLevel(StrEnum):
    """How much human involvement an escalation demands."""

    NOTIFY = "notify"  # proceed, but inform the human
    APPROVE_ACTION = "approve_action"  # human confirms the next step
    APPROVE_PLAN = "approve_plan"  # human reviews the full plan before work starts
    TAKE_OVER = "take_over"  # human supplies the output; agents stand down


class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    MODIFIED = "modified"
