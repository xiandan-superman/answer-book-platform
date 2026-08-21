from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Iterable


class FindingSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class PolicyAction(str, Enum):
    IGNORE = "ignore"
    WARN = "warn"
    BLOCK = "block"


@dataclass(frozen=True)
class QualityFinding:
    """A validator observation; it deliberately does not decide workflow state."""

    code: str
    message: str
    source: str
    severity: FindingSeverity
    confidence: float
    scope: str = "question"
    subject_id: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    suggested_fix: str = ""

    def __post_init__(self) -> None:
        if not self.code.strip() or not self.message.strip() or not self.source.strip():
            raise ValueError("finding code, message and source must not be empty")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("finding confidence must be between 0 and 1")

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "severity": self.severity.value}


@dataclass(frozen=True)
class QualityPolicy:
    """Central policy that maps observations to actions for one execution profile."""

    blocking_codes: frozenset[str] = frozenset()
    warning_codes: frozenset[str] = frozenset()
    minimum_block_confidence: float = 0.98

    def action_for(self, finding: QualityFinding) -> PolicyAction:
        if finding.code in self.blocking_codes and finding.confidence >= self.minimum_block_confidence:
            return PolicyAction.BLOCK
        if finding.code in self.warning_codes or finding.severity in {FindingSeverity.WARNING, FindingSeverity.ERROR}:
            return PolicyAction.WARN
        return PolicyAction.IGNORE

    def evaluate(self, findings: Iterable[QualityFinding]) -> list[dict[str, Any]]:
        return [{**finding.to_dict(), "action": self.action_for(finding).value} for finding in findings]
