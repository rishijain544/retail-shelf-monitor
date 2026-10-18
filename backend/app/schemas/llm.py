from typing import Literal
from pydantic import BaseModel

class ShelfReportOutput(BaseModel):
    """Structured output returned by the LLM service (or rule-based fallback)."""
    summary: str
    # Always one of the ordinary levels: a zero-detection capture is NOT given an
    # "unknown" urgency here — its row is flagged low_confidence by the pipeline, which
    # is what tells clients the fill/urgency pair is not a confident measurement.
    urgency: Literal["low", "medium", "high"]
    recommended_action: str
