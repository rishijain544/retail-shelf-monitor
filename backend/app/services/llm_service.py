"""
backend/app/services/llm_service.py

Generates AI-powered shelf restocking reports by calling the Anthropic Claude API
with *tool use* (structured output) so the JSON shape is always guaranteed.

Falls back to a deterministic rule-based report when the API is unavailable.
"""

from __future__ import annotations

import logging
import time

import anthropic

from app.core.config import settings
from app.schemas.llm import ShelfReportOutput

logger = logging.getLogger(__name__)

# ─── Anthropic tool definition ────────────────────────────────────────────────
# The model is forced to call this tool, which means its reply is always
# structured JSON matching our output schema.
_REPORT_TOOL: anthropic.types.ToolParam = {
    "name": "generate_report",
    "description": (
        "Generate a shelf restocking report based on the supplied detection data. "
        "Always call this tool with all three fields populated."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": (
                    "A concise, human-readable summary of the current shelf condition "
                    "including trend context (e.g. 'Stock declining rapidly — down 18 pp "
                    "since last capture')."
                ),
            },
            "urgency": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": (
                    "Restocking urgency level: "
                    "'low' (fill ≥ 60 %), 'medium' (30–60 %), 'high' (< 30 %)."
                ),
            },
            "recommended_action": {
                "type": "string",
                "description": (
                    "A concrete action for store staff, e.g. "
                    "'Dispatch one pallet of SKU-001 to Aisle 3 immediately.'"
                ),
            },
        },
        "required": ["summary", "urgency", "recommended_action"],
    },
}

_MODEL = "claude-3-5-haiku-20241022"   # fast + cheap; swap for sonnet/opus as needed
_MAX_TOKENS = 512


def _build_user_prompt(detection_data: dict, previous_fill: float | None) -> str:
    """Builds the user-facing prompt from structured detection data."""
    fill_pct = detection_data.get("fill_percentage", 0.0)
    aisle = detection_data.get("aisle_name", "Unknown Aisle")
    empty_count = detection_data.get("empty_region_count", 0)

    # Trend context
    if previous_fill is None:
        trend_text = "No previous capture available for trend comparison."
    else:
        delta = fill_pct - previous_fill
        direction = "increased" if delta >= 0 else "decreased"
        trend_text = (
            f"Since the last capture the fill level has {direction} by "
            f"{abs(delta) * 100:.1f} percentage points "
            f"(from {previous_fill * 100:.1f}% to {fill_pct * 100:.1f}%)."
        )

    return (
        f"You are a retail operations assistant. Analyse the following shelf data and "
        f"call the generate_report tool with an accurate assessment.\n\n"
        f"Aisle: {aisle}\n"
        f"Current fill level: {fill_pct * 100:.1f}%\n"
        f"Number of detected empty regions: {empty_count}\n"
        f"Trend: {trend_text}\n\n"
        f"Provide a clear, actionable report for store staff."
    )


def _call_anthropic(prompt: str) -> ShelfReportOutput:
    """Makes a single Anthropic API call and parses the tool-use response."""
    client = anthropic.Anthropic(
        api_key=settings.ANTHROPIC_API_KEY,
        default_headers=(
            {"anthropic-workspace-id": settings.ANTHROPIC_WORKSPACE_ID}
            if settings.ANTHROPIC_WORKSPACE_ID
            else None
        ),
    )

    response = client.messages.create(
        model=_MODEL,
        max_tokens=_MAX_TOKENS,
        tools=[_REPORT_TOOL],
        # Force the model to call our tool (not emit free text)
        tool_choice={"type": "tool", "name": "generate_report"},
        messages=[{"role": "user", "content": prompt}],
    )

    # Extract the first tool_use block
    tool_block = next(
        (block for block in response.content if block.type == "tool_use"),
        None,
    )
    if tool_block is None:
        raise ValueError("Anthropic response contained no tool_use block.")

    tool_input: dict = tool_block.input  # type: ignore[attr-defined]
    return ShelfReportOutput(**tool_input)


# ─── Rule-based fallback ──────────────────────────────────────────────────────

def _rule_based_report(detection_data: dict, previous_fill: float | None) -> ShelfReportOutput:
    """
    Deterministic fallback used when the Anthropic API is unavailable.
    Mirrors the urgency thresholds described in the tool schema.
    """
    # An unassessed capture has no fill level to threshold on. Handle it here too, so this
    # function is safe to call directly (it used to compare None against floats and crash).
    # Zero detections only mean "unassessed" when the analyzer did not actually assess the
    # image: an assessed zero-region result is a real "no distinct empty gaps" measurement.
    if (
        detection_data.get("detection_count") == 0 and not detection_data.get("assessed")
    ) or detection_data.get("fill_percentage") is None:
        return _unassessed_report(detection_data)

    fill_pct = detection_data.get("fill_percentage", 0.0)
    aisle = detection_data.get("aisle_name", "Unknown Aisle")
    empty_count = detection_data.get("empty_region_count", 0)

    # Trend calculation
    if previous_fill is not None:
        delta = fill_pct - previous_fill
        trend_note = (
            f"Fill has {'risen' if delta >= 0 else 'fallen'} by "
            f"{abs(delta) * 100:.1f} pp since the last capture."
        )
    else:
        trend_note = "No prior capture for trend comparison."

    fill_label = f"{fill_pct * 100:.1f}%"

    if fill_pct < 0.30:
        urgency = "high"
        summary = (
            f"{aisle} is critically low ({fill_label}, {empty_count} empty region(s)). "
            f"{trend_note}"
        )
        action = f"Immediately restock {aisle} — at least {empty_count} gap(s) require attention."
    elif fill_pct < 0.60:
        urgency = "medium"
        summary = (
            f"{aisle} fill level is moderate ({fill_label}, {empty_count} empty region(s)). "
            f"{trend_note}"
        )
        action = f"Schedule restocking for {aisle} within the next hour."
    else:
        urgency = "low"
        summary = (
            f"{aisle} is well-stocked ({fill_label}, {empty_count} empty region(s)). "
            f"{trend_note}"
        )
        action = f"No immediate action required for {aisle}. Monitor at next scheduled interval."

    return ShelfReportOutput(summary=summary, urgency=urgency, recommended_action=action)


# ─── Public API ───────────────────────────────────────────────────────────────

def _urgency_for_fill(fill_pct: float) -> str:
    """Documented urgency thresholds, shared by the rule-based and unassessed paths."""
    if fill_pct >= 0.60:
        return "low"
    if fill_pct >= 0.30:
        return "medium"
    return "high"


def _unassessed_report(detection_data: dict) -> ShelfReportOutput:
    """
    Report for a capture the detector could not assess at all (zero detections).

    Deliberately does not go through the LLM: there is no measurement to narrate, and a
    language model handed "0 detections" tends to describe a clean bill of health, which is
    exactly the false negative this path exists to prevent.

    The urgency still uses the ordinary thresholds, applied to the analyzer's vacuous
    1.0 fill ("no empty space found"), so the report stays well-formed — the honest
    "could not be assessed" signal lives in the row's low_confidence flag, which the
    pipeline sets, not in a special urgency value.
    """
    aisle = detection_data.get("aisle_name", "Unknown Aisle")
    fill_pct = detection_data.get("fill_percentage")
    # Vacuous 1.0 ("nothing found") is the normal zero-detection case; a genuinely
    # missing fill (direct/legacy callers) is treated the same way — nothing was
    # measured, so the least alarming ordinary level applies. (Flagged rows are
    # excluded from Slack alerting regardless of this value.)
    urgency = _urgency_for_fill(fill_pct if fill_pct is not None else 1.0)
    return ShelfReportOutput(
        summary=(
            f"No empty-space detections were found in this capture of {aisle}, so its fill "
            "level could not be assessed. This is not a confirmation that the shelf is "
            "full — it means the detector found nothing it could measure."
        ),
        urgency=urgency,
        recommended_action=(
            f"Re-capture {aisle}. If this repeats, check the camera position and image "
            "quality (the detector needs a reasonably sized, clearly lit shelf image) "
            "before trusting any fill level for this aisle."
        ),
    )


def generate_shelf_report(
    detection_data: dict,
    previous_fill: float | None = None,
) -> ShelfReportOutput:
    """
    Generate a structured shelf restocking report using the Anthropic Claude API.

    Parameters
    ----------
    detection_data : dict
        Must contain:
        - ``fill_percentage``   : float (0.0 – 1.0; the analyzer's vacuous 1.0 when it
          found nothing, in which case the pipeline flags the row low_confidence)
        - ``aisle_name``        : str
        - ``empty_region_count``: int
        - ``detection_count``   : int  (0 means the detector found nothing at all)
        - ``assessed``          : bool (True when the analyzer produced a real
          measurement — an assessed zero-region result means a genuinely full shelf,
          not an unassessable one; defaults to False for legacy callers)
    previous_fill : float | None
        Fill percentage from the camera's most recent *prior* capture (0.0 – 1.0),
        used to provide trend context ("declining rapidly", etc.).

    Returns
    -------
    ShelfReportOutput
        Validated Pydantic model with ``summary``, ``urgency``, and
        ``recommended_action`` fields. A zero-detection capture still gets an
        ordinary urgency (computed from the vacuous fill); its untrustworthiness is
        carried by the row's ``low_confidence`` flag instead.
    """
    detection_count = detection_data.get("detection_count")
    # Zero detections mean "unable to assess" only when the analyzer did not assess the
    # image at all (legacy/canned results and paths that gave up — the pipeline flags
    # those rows low_confidence). An assessed zero-region result is a genuine measurement
    # of a fully-stocked shelf and must flow through the normal report path.
    if (
        detection_count == 0 and not detection_data.get("assessed")
    ) or detection_data.get("fill_percentage") is None:
        logger.warning(
            "LLM path: SKIPPED — capture %s produced no usable detections (not assessed), "
            "reporting it as unassessed rather than as a full shelf.",
            detection_data.get("capture_id", "unknown"),
        )
        return _unassessed_report(detection_data)

    prompt = _build_user_prompt(detection_data, previous_fill)

    if not settings.ANTHROPIC_API_KEY:
        logger.info(
            "LLM path: SKIPPED (no ANTHROPIC_API_KEY) — using rule-based fallback for report %s.",
            detection_data.get("capture_id", "unknown"),
        )
        return _rule_based_report(detection_data, previous_fill)

    # Attempt 1
    try:
        logger.info(
            "LLM path: calling Anthropic API (attempt 1) for report %s…",
            detection_data.get("capture_id", "unknown"),
        )
        return _call_anthropic(prompt)
    except Exception as exc:
        logger.warning(
            "LLM path: Anthropic API call failed (attempt 1) for report %s: %s",
            detection_data.get("capture_id", "unknown"),
            exc,
        )

    # Retry once after a short back-off
    time.sleep(1.5)
    try:
        logger.info(
            "LLM path: calling Anthropic API (attempt 2 — retry) for report %s…",
            detection_data.get("capture_id", "unknown"),
        )
        return _call_anthropic(prompt)
    except Exception as exc:
        logger.warning(
            "LLM path: Anthropic API call failed on retry (report %s) — falling back to rule-based fallback: %s",
            detection_data.get("capture_id", "unknown"),
            exc,
        )

    logger.info(
        "LLM path: fell back to rule-based report for report %s (fill=%.1f%%, aisle=%s, empty=%d).",
        detection_data.get("capture_id", "unknown"),
        detection_data.get("fill_percentage", 0.0) * 100,
        detection_data.get("aisle_name", "unknown"),
        detection_data.get("empty_region_count", 0),
    )
    return _rule_based_report(detection_data, previous_fill)
