"""Shared typed contracts used across SchemaShift subsystems."""

from schemashift.domain.events import (
    ActivityEvent,
    ActivityLevel,
    Component,
    ComponentStatus,
    ComponentStatusEvent,
    HumanDecision,
    HumanDecisionChoice,
    HumanReviewRequiredEvent,
    ReviewRisk,
    RuntimeEvent,
)
from schemashift.domain.ids import (
    ConversationId,
    RunContext,
    RunId,
    SessionId,
    TurnId,
    new_uuid,
    parse_uuid,
)
from schemashift.domain.migration import (
    MappingCandidate,
    MigrationAlternative,
    MigrationProposal,
    SourceRole,
)
from schemashift.domain.validation import (
    ExecutionComparison,
    IssueSeverity,
    StructuralChecks,
    ValidationIssue,
    ValidationReport,
    ValidationVerdict,
)

__all__ = [
    "ActivityEvent",
    "ActivityLevel",
    "Component",
    "ComponentStatus",
    "ComponentStatusEvent",
    "ConversationId",
    "ExecutionComparison",
    "HumanDecision",
    "HumanDecisionChoice",
    "HumanReviewRequiredEvent",
    "IssueSeverity",
    "MappingCandidate",
    "MigrationAlternative",
    "MigrationProposal",
    "ReviewRisk",
    "RunContext",
    "RunId",
    "RuntimeEvent",
    "SessionId",
    "SourceRole",
    "StructuralChecks",
    "TurnId",
    "ValidationIssue",
    "ValidationReport",
    "ValidationVerdict",
    "new_uuid",
    "parse_uuid",
]
