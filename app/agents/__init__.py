"""Agent output contracts.

Handoff section 11, phase 0: "JSON Schema for the six text agents and the
marketing agent".  Every agent's output is validated against one of these
models before it is stored, and the same schema is handed to vLLM as a guided
decoding constraint so the model is steered into the shape rather than merely
checked against it afterwards.
"""

from app.agents.contracts import (
    BriefSuggestion,
    Claim,
    DraftOutput,
    GeoOptimizedOutput,
    KeywordCandidate,
    MarketizedOutput,
    OutlineSection,
    QaReport,
    ResearchOutput,
    SeoOptimizedOutput,
    StrategyOutput,
    TopicPlanOutput,
)
from app.agents.registry import (
    AGENT_OUTPUTS,
    AgentOutputError,
    json_schema_for,
    model_for,
    validate_agent_output,
)

__all__ = [
    "AGENT_OUTPUTS",
    "AgentOutputError",
    "BriefSuggestion",
    "Claim",
    "DraftOutput",
    "GeoOptimizedOutput",
    "KeywordCandidate",
    "MarketizedOutput",
    "OutlineSection",
    "QaReport",
    "ResearchOutput",
    "SeoOptimizedOutput",
    "StrategyOutput",
    "TopicPlanOutput",
    "json_schema_for",
    "model_for",
    "validate_agent_output",
]
