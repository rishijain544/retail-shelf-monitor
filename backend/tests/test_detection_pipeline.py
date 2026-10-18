"""Regression tests for the shelf-detection threshold wiring.

Context: the detection pipeline used to apply the confidence threshold TWICE with two
different values — ultralytics applied its own implicit 0.25 inside `self.model(...)`, and
then `custom_nms` applied an explicit 0.4 on top. Real detections in the 0.25-0.30 band were
therefore discarded, and because zero surviving detections was reported as a 100% fill, two
completely different shelf photos both came back as "perfectly stocked".

These tests pin down the invariants that prevent that class of bug returning: one threshold,
defined in one place, applied at exactly one point.
"""

from __future__ import annotations

import inspect

import pytest

from app.services import analyzer_service


def test_backend_and_ml_thresholds_agree():
    """
    The backend's default and the ML pipeline's default must be the same number.

    They are defined separately on purpose (analyzer_service must not import ml/ at module
    import time, because that pulls in ultralytics/torch), so nothing but this test keeps them
    from drifting apart.
    """
    from ml import shelf_analyzer

    assert analyzer_service.DEFAULT_CONF_THRESHOLD == shelf_analyzer.DEFAULT_CONF_THRESHOLD
    assert analyzer_service.DEFAULT_IOU_THRESHOLD == shelf_analyzer.DEFAULT_IOU_THRESHOLD


def test_calibrated_threshold_is_not_the_old_value():
    """
    The calibrated threshold sits between the measured noise ceiling and the weakest genuine
    detection, so it must not silently regress to the inherited 0.4 that discarded real boxes.
    """
    assert analyzer_service.DEFAULT_CONF_THRESHOLD == 0.25


def test_analyzer_service_default_matches_its_constant():
    """analyze()'s own default must track the constant rather than a hardcoded literal."""
    signature = inspect.signature(analyzer_service.analyze)
    assert signature.parameters["conf"].default == analyzer_service.DEFAULT_CONF_THRESHOLD
    assert signature.parameters["iou"].default == analyzer_service.DEFAULT_IOU_THRESHOLD


def test_capture_pipeline_default_matches_its_constant():
    """The background pipeline must start from the same threshold as the HTTP layer."""
    from app.services.capture_pipeline import process_capture

    signature = inspect.signature(process_capture)
    assert signature.parameters["conf"].default == analyzer_service.DEFAULT_CONF_THRESHOLD
    assert signature.parameters["iou"].default == analyzer_service.DEFAULT_IOU_THRESHOLD


def test_upload_endpoint_form_default_matches_the_constant():
    """The HTTP Form default must come from analyzer_service, not a hardcoded literal."""
    from app.api.v1.endpoints.captures import upload_capture

    signature = inspect.signature(upload_capture)
    # FastAPI keeps the Form(...) parameter object as the function default.
    conf_param = signature.parameters["conf"].default
    iou_param = signature.parameters["iou"].default

    assert conf_param.default == analyzer_service.DEFAULT_CONF_THRESHOLD
    assert iou_param.default == analyzer_service.DEFAULT_IOU_THRESHOLD


def test_nms_does_not_apply_a_second_confidence_cut():
    """
    NMS must deduplicate purely on IoU. A `conf_threshold` argument here is exactly how the
    double-filtering bug came back last time.
    """
    from ml.shelf_analyzer import ShelfAnalyzer

    nms_params = inspect.signature(ShelfAnalyzer.custom_nms).parameters
    assert "conf_threshold" not in nms_params
    assert "iou_threshold" in nms_params

    # And it must keep a low-confidence box rather than dropping it.
    detections = [
        {"bbox": [0.0, 0.0, 10.0, 10.0], "confidence": 0.9, "class_name": "empty"},
        {"bbox": [50.0, 50.0, 60.0, 60.0], "confidence": 0.01, "class_name": "empty"},
    ]
    assert len(ShelfAnalyzer.custom_nms(detections, iou_threshold=0.5)) == 2


@pytest.mark.parametrize("missing_name", ["empty_space_best.pt"])
def test_missing_weights_are_a_hard_failure(tmp_path, missing_name):
    """
    A missing checkpoint must raise at startup. The old silent fallback to yolov8n.pt would
    have reported COCO objects (vases, people, cars) as shelf gaps with no error signal.
    """
    from ml.shelf_analyzer import ShelfAnalyzer

    with pytest.raises(FileNotFoundError):
        ShelfAnalyzer(model_path=str(tmp_path / missing_name))


def test_shelf_report_fill_is_clean_and_flag_is_present():
    """
    fill_percentage stays NOT NULL so every consumer that assumes a clean float (trends
    chart, sorting/filters, CSV export) keeps working; the unassessable case is carried
    by the low_confidence boolean instead — also NOT NULL with a False default.
    """
    from app.models.shelf_report import ShelfReport

    fill_col = ShelfReport.__table__.c.fill_percentage
    flag_col = ShelfReport.__table__.c.low_confidence
    assert fill_col.nullable is False
    assert "Boolean" in type(flag_col.type).__name__
    assert flag_col.nullable is False
    assert flag_col.default is not None  # False at the ORM level


def test_unassessed_report_does_not_call_the_llm(monkeypatch):
    """
    A zero-detection capture must not be narrated. Handing a language model "0 detections"
    reliably produces a clean bill of health, which is the false negative this path exists to
    prevent, so the LLM is skipped entirely. The urgency is still an ordinary level
    (thresholds on the vacuous fill) — the honesty lives in the pipeline's low_confidence
    flag, not a special urgency value.
    """
    from app.services import llm_service

    calls: list[str] = []
    monkeypatch.setattr(llm_service, "_call_anthropic", lambda prompt: calls.append(prompt))
    monkeypatch.setattr(llm_service.settings, "ANTHROPIC_API_KEY", "test-key")

    output = llm_service.generate_shelf_report(
        {
            "fill_percentage": 1.0,  # the analyzer's vacuous "nothing found" value
            "aisle_name": "Aisle 7",
            "empty_region_count": 0,
            "detection_count": 0,
            "capture_id": 1,
        }
    )

    assert calls == []
    assert output.urgency == "low"  # ordinary level from the vacuous fill
    assert "could not be assessed" in output.summary
    assert "well-stocked" not in output.summary


def test_rule_based_report_handles_unassessed_input():
    """
    The rule-based fallback is reachable directly, so it must handle a zero-detection/
    missing-fill payload by routing to the honest unassessed report rather than comparing
    None against the urgency thresholds — and the result is always an ordinary urgency.
    """
    from app.services.llm_service import _rule_based_report

    zero_detections = _rule_based_report(
        {
            "fill_percentage": 1.0,
            "aisle_name": "Aisle 7",
            "empty_region_count": 0,
            "detection_count": 0,
        },
        None,
    )
    assert zero_detections.urgency == "low"
    assert "could not be assessed" in zero_detections.summary

    missing_fill = _rule_based_report(
        {"aisle_name": "Aisle 7", "empty_region_count": 0, "detection_count": 0},
        None,
    )
    assert missing_fill.urgency in ("low", "medium", "high")
    assert "could not be assessed" in missing_fill.summary


def test_analyze_shelf_result_exposes_detection_count():
    """
    Callers need to distinguish "the model found no empty space" from "the model found
    nothing at all" — the result must carry the count explicitly.
    """
    from ml.shelf_analyzer import ShelfAnalyzer

    source = inspect.getsource(ShelfAnalyzer.analyze_shelf)
    assert '"detection_count"' in source
