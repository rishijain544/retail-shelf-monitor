"""
Tests for backend/app/services/llm_service.py

Covers:
- Rule-based fallback thresholds (low / medium / high urgency)
- Trend text generation
- Successful Anthropic API path (mocked)
- Single retry on first failure, then success (mocked)
- Full fallback to rule-based when both API attempts fail
- ShelfReportOutput schema validation
"""

import sys
import types
from unittest.mock import MagicMock, patch, call
import pytest

# ── bootstrap: create a minimal stub for 'anthropic' so tests don't require the
#    real package to be installed in every environment ─────────────────────────
def _make_anthropic_stub():
    """Return a minimal stub module that satisfies the imports in llm_service."""
    stub = types.ModuleType("anthropic")

    class _APIError(Exception): ...
    stub.APIError = _APIError

    class _ToolParam(dict): ...
    stub.types = types.SimpleNamespace(ToolParam=_ToolParam)

    class _Anthropic:
        def __init__(self, api_key=None):
            self.messages = MagicMock()

    stub.Anthropic = _Anthropic
    return stub

if "anthropic" not in sys.modules:
    sys.modules["anthropic"] = _make_anthropic_stub()
# ─────────────────────────────────────────────────────────────────────────────

# Now safe to import the module under test
from app.services.llm_service import (          # noqa: E402
    generate_shelf_report,
    _rule_based_report,
    _build_user_prompt,
    _call_anthropic,
    ShelfReportOutput,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def low_fill_data():
    return {"fill_percentage": 0.18, "aisle_name": "Aisle 3", "empty_region_count": 7}

@pytest.fixture
def medium_fill_data():
    return {"fill_percentage": 0.45, "aisle_name": "Aisle 5", "empty_region_count": 3}

@pytest.fixture
def high_fill_data():
    return {"fill_percentage": 0.82, "aisle_name": "Aisle 1", "empty_region_count": 1}


# ── Rule-based fallback tests ─────────────────────────────────────────────────

class TestRuleBasedReport:
    def test_urgency_high_when_fill_below_30(self, low_fill_data):
        result = _rule_based_report(low_fill_data, previous_fill=None)
        assert result.urgency == "high"
        assert isinstance(result.summary, str) and len(result.summary) > 0
        assert isinstance(result.recommended_action, str) and len(result.recommended_action) > 0

    def test_urgency_medium_when_fill_between_30_and_60(self, medium_fill_data):
        result = _rule_based_report(medium_fill_data, previous_fill=None)
        assert result.urgency == "medium"

    def test_urgency_low_when_fill_above_60(self, high_fill_data):
        result = _rule_based_report(high_fill_data, previous_fill=None)
        assert result.urgency == "low"

    def test_trend_context_included_when_previous_fill_given(self, medium_fill_data):
        result = _rule_based_report(medium_fill_data, previous_fill=0.65)
        # Fill dropped from 65 % to 45 % → delta = -20 pp
        assert "fallen" in result.summary or "20" in result.summary

    def test_no_trend_text_when_no_previous_fill(self, low_fill_data):
        result = _rule_based_report(low_fill_data, previous_fill=None)
        assert "No prior capture" in result.summary

    def test_output_schema_valid(self, low_fill_data):
        result = _rule_based_report(low_fill_data, previous_fill=None)
        assert isinstance(result, ShelfReportOutput)
        assert result.urgency in ("low", "medium", "high")


# ── Prompt builder tests ──────────────────────────────────────────────────────

class TestBuildUserPrompt:
    def test_contains_aisle_name(self, medium_fill_data):
        prompt = _build_user_prompt(medium_fill_data, previous_fill=None)
        assert "Aisle 5" in prompt

    def test_contains_fill_percentage(self, medium_fill_data):
        prompt = _build_user_prompt(medium_fill_data, previous_fill=None)
        assert "45.0%" in prompt

    def test_trend_in_prompt_when_previous_fill_given(self, medium_fill_data):
        prompt = _build_user_prompt(medium_fill_data, previous_fill=0.70)
        # 70 % → 45 %: decrease of 25 pp
        assert "25.0" in prompt

    def test_no_previous_note_when_none(self, low_fill_data):
        prompt = _build_user_prompt(low_fill_data, previous_fill=None)
        assert "No previous capture" in prompt


# ── API path (mocked) tests ───────────────────────────────────────────────────

def _make_mock_response(summary="All good.", urgency="low", action="No action."):
    """Build a mock Anthropic messages.create response."""
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.input = {
        "summary": summary,
        "urgency": urgency,
        "recommended_action": action,
    }
    response = MagicMock()
    response.content = [tool_block]
    return response


class TestGenerateShelfReport:
    def test_returns_rule_based_when_no_api_key(self, low_fill_data):
        with patch("app.services.llm_service.settings") as mock_settings:
            mock_settings.ANTHROPIC_API_KEY = None
            result = generate_shelf_report(low_fill_data, previous_fill=None)
        assert isinstance(result, ShelfReportOutput)
        assert result.urgency == "high"   # rule-based threshold

    def test_successful_api_call_returns_llm_output(self, medium_fill_data):
        mock_response = _make_mock_response(
            summary="Moderate shortage.", urgency="medium", action="Restock soon."
        )
        with patch("app.services.llm_service.settings") as mock_settings, \
             patch("app.services.llm_service.anthropic.Anthropic") as mock_cls:
            mock_settings.ANTHROPIC_API_KEY = "sk-ant-test"
            mock_cls.return_value.messages.create.return_value = mock_response
            result = generate_shelf_report(medium_fill_data, previous_fill=0.60)

        assert result.urgency == "medium"
        assert result.summary == "Moderate shortage."
        assert result.recommended_action == "Restock soon."

    def test_retry_on_first_failure_then_success(self, high_fill_data):
        """First call raises, second succeeds — should return LLM output."""
        mock_response = _make_mock_response(urgency="low", summary="OK", action="Monitor.")

        with patch("app.services.llm_service.settings") as mock_settings, \
             patch("app.services.llm_service.anthropic.Anthropic") as mock_cls, \
             patch("app.services.llm_service.time.sleep"):           # skip real sleep
            mock_settings.ANTHROPIC_API_KEY = "sk-ant-test"
            create_mock = mock_cls.return_value.messages.create
            create_mock.side_effect = [
                Exception("timeout"),   # first attempt fails
                mock_response,          # retry succeeds
            ]
            result = generate_shelf_report(high_fill_data, previous_fill=0.85)

        assert result.urgency == "low"
        assert create_mock.call_count == 2

    def test_falls_back_to_rules_when_both_api_attempts_fail(self, low_fill_data):
        """Both API attempts fail → rule-based fallback."""
        with patch("app.services.llm_service.settings") as mock_settings, \
             patch("app.services.llm_service.anthropic.Anthropic") as mock_cls, \
             patch("app.services.llm_service.time.sleep"):
            mock_settings.ANTHROPIC_API_KEY = "sk-ant-test"
            mock_cls.return_value.messages.create.side_effect = Exception("API down")
            result = generate_shelf_report(low_fill_data, previous_fill=None)

        # Rule-based: fill=0.18 → urgency should be "high"
        assert result.urgency == "high"
