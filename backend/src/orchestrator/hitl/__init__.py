"""Human-in-the-loop: escalation policy + approvals."""

from orchestrator.hitl.middleware import SensitiveToolGate
from orchestrator.hitl.policy import (
    EscalationPolicy,
    evaluate_plan,
    evaluate_result,
    evaluate_tool,
)

__all__ = [
    "EscalationPolicy",
    "SensitiveToolGate",
    "evaluate_plan",
    "evaluate_result",
    "evaluate_tool",
]
