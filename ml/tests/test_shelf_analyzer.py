import os
import tempfile
from pathlib import Path

import pytest
import numpy as np
import cv2

from ml import shelf_analyzer
from ml.shelf_analyzer import ShelfAnalyzer

FIXTURE_DIR = Path(__file__).parent / "fixtures"
# A real shelf photo the fine-tuned model genuinely detects (its 'empty' scores land at
# 0.39-0.88). This is the fixture that makes the tests below meaningful: the previous suite
# only asserted the result dict's shape and 0 <= fill <= 1, which passes just as happily on
# zero detections and therefore let a totally broken detector ship.
KNOWN_DETECTION_IMAGE = FIXTURE_DIR / "empty_shelf.jpg"


@pytest.fixture(scope="module")
def analyzer():
    """One loaded model for the whole module - constructing it takes seconds."""
    return ShelfAnalyzer()


@pytest.fixture
def temp_sample_image():
    """Generates a temporary mock image for testing (no shelf structure -> no detections)."""
    # Create a 200x200 black image with a gray square in the center
    img = np.zeros((200, 200, 3), dtype=np.uint8)
    cv2.rectangle(img, (50, 50), (150, 150), (128, 128, 128), -1)

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        temp_path = tmp.name

    cv2.imwrite(temp_path, img)
    yield temp_path

    # Cleanup
    if os.path.exists(temp_path):
        os.remove(temp_path)


def test_calculate_iou():
    """Tests the IoU calculation method."""
    box1 = [0.0, 0.0, 10.0, 10.0]

    # 1. Full overlap
    assert ShelfAnalyzer.calculate_iou(box1, box1) == pytest.approx(1.0)

    # 2. No overlap
    box2 = [20.0, 20.0, 30.0, 30.0]
    assert ShelfAnalyzer.calculate_iou(box1, box2) == 0.0

    # 3. Partial overlap (half width overlap)
    box3 = [5.0, 0.0, 15.0, 10.0]
    # Intersection: [5, 0, 10, 10] -> Area = 5 * 10 = 50
    # Union: Area1 (100) + Area2 (100) - Intersection (50) = 150
    # IoU: 50 / 150 = 0.3333
    assert ShelfAnalyzer.calculate_iou(box1, box3) == pytest.approx(1.0 / 3.0)


def test_custom_nms():
    """Tests that NMS suppresses overlaps by IoU only - it must NOT filter on confidence."""
    detections = [
        {"bbox": [10.0, 10.0, 50.0, 50.0], "confidence": 0.9, "class_name": "empty"},
        {"bbox": [12.0, 12.0, 52.0, 52.0], "confidence": 0.8, "class_name": "empty"},  # Overlaps significantly with first box
        {"bbox": [80.0, 80.0, 120.0, 120.0], "confidence": 0.85, "class_name": "empty"},  # Separate box
        {"bbox": [100.0, 100.0, 110.0, 110.0], "confidence": 0.3, "class_name": "empty"},  # Low confidence box
    ]

    filtered = ShelfAnalyzer.custom_nms(detections, iou_threshold=0.5)

    # The low-confidence box is still returned: confidence filtering belongs to the single
    # model call in analyze_shelf(), not to a second stacked filter here.
    assert len(filtered) == 3
    confidences = sorted(d["confidence"] for d in filtered)
    assert confidences == pytest.approx([0.3, 0.85, 0.9])


def test_custom_nms_handles_empty_input():
    """An empty detection list must pass through instead of raising."""
    assert ShelfAnalyzer.custom_nms([]) == []


def test_union_area_does_not_double_count_overlap():
    """
    Overlapping boxes must contribute their union, not the sum of their areas.

    The detector legitimately emits several overlapping full-width shelf-row strips, and
    summing their areas drove the empty area past the image area, clamping fill to 0% on an
    almost entirely empty shelf.
    """
    overlapping = [[0.0, 0.0, 100.0, 50.0], [0.0, 25.0, 100.0, 75.0]]
    assert ShelfAnalyzer.union_area(overlapping) == pytest.approx(100.0 * 75.0)

    disjoint = [[0.0, 0.0, 10.0, 10.0], [10.0, 0.0, 20.0, 10.0]]
    assert ShelfAnalyzer.union_area(disjoint) == pytest.approx(200.0)

    assert ShelfAnalyzer.union_area([]) == 0.0
    # Degenerate boxes contribute nothing.
    assert ShelfAnalyzer.union_area([[5.0, 5.0, 5.0, 5.0]]) == 0.0


def test_deskew_image():
    """Tests that deskewing functions execute without errors."""
    dummy_img = np.zeros((100, 100, 3), dtype=np.uint8)
    cv2.rectangle(dummy_img, (20, 20), (80, 80), (255, 255, 255), -1)

    rotated, angle = ShelfAnalyzer.deskew_image(dummy_img)
    assert rotated.shape == dummy_img.shape
    assert isinstance(angle, float)


def test_enhance_image():
    """Tests that image enhancement executes and maintains dimensions."""
    dummy_img = np.zeros((100, 100, 3), dtype=np.uint8)
    enhanced = ShelfAnalyzer.enhance_image(dummy_img)
    assert enhanced.shape == dummy_img.shape


def test_real_model_detects_empty_space_in_known_fixture(analyzer):
    """
    End-to-end regression test against the ACTUAL model and weights.

    A shelf image with known empty space must produce real detections and a fill percentage
    that is meaningfully different from 1.0. This is the assertion the old
    'assert 0 <= fill <= 1' test lacked, which is why a detector returning nothing at all
    shipped without anyone noticing.
    """
    result = analyzer.analyze_shelf(str(KNOWN_DETECTION_IMAGE))

    assert result["detection_count"] > 0
    assert len(result["empty_regions"]) > 0
    assert result["fill_percentage"] < 0.5, (
        f"expected a clearly non-full shelf, got fill={result['fill_percentage']}"
    )

    for det in result["detections"]:
        assert det["class_name"] == shelf_analyzer.EMPTY_CLASS_NAME
        assert det["confidence"] >= shelf_analyzer.DEFAULT_CONF_THRESHOLD


def test_preprocessing_does_not_run_before_inference(analyzer):
    """
    Locks in the root-cause fix for the '100% fill on every image' bug.

    CLAHE + bilateral 'enhancement' used to run on the inference path and collapsed the
    model's confidence on this very fixture (max 0.883 -> 0.282, every box >= 0.30 gone), so
    the pipeline reported 100% fill. If enhancement is ever put back in front of the model,
    this test fails.
    """
    image = cv2.imread(str(KNOWN_DETECTION_IMAGE))
    assert image is not None

    def scores(frame):
        boxes = analyzer.model(frame, verbose=False, conf=0.001, iou=0.7)[0].boxes
        confs = [float(b.conf[0].item()) for b in boxes]
        return (max(confs) if confs else 0.0), sum(1 for c in confs if c >= 0.30)

    deskewed, _ = analyzer.deskew_image(image)
    enhanced = analyzer.enhance_image(deskewed)

    plain_max, plain_strong = scores(deskewed)
    enhanced_max, enhanced_strong = scores(enhanced)

    # Measured: 0.883 with 11 boxes >= 0.30 plainly, versus 0.282 with 0 strong boxes once
    # enhanced. The collapse is the reason every image came back "100% full".
    assert plain_max > 0.8
    assert plain_max > 2.5 * enhanced_max
    assert plain_strong >= 8
    assert enhanced_strong == 0

    # And the shipped pipeline must use the confident (un-enhanced) path.
    assert analyzer.analyze_shelf(str(KNOWN_DETECTION_IMAGE))["detection_count"] > 0


def test_flat_synthetic_image_gets_a_heuristic_assessment(analyzer, temp_sample_image):
    """
    A flat black-and-gray synthetic image passes the quality gate (it is sharp, bright
    enough and has edges), so it must now get an honest heuristic assessment — thick flat
    areas become empty regions — rather than the old vacuous 0-detections/100%-full result.
    """
    result = analyzer.analyze_shelf(temp_sample_image)

    assert result["assessed"] is True
    assert result["detector"] == "heuristic"
    assert result["detection_count"] > 0
    assert result["fill_percentage"] < 0.5  # most of that image is flat, empty space


def test_real_photo_of_empty_endcap_reports_low_fill(analyzer):
    """
    Real-world regression: a photo of a completely EMPTY blue-steel endcap used to come
    back as 93% full, because dark blue-grey shelf boards are 'saturated' in HSV and the
    warm wooden floor counted as product. With the fixture-hue and floor exclusions the
    same photo must read mostly empty and report thick empty regions.
    """
    result = analyzer.analyze_shelf(str(FIXTURE_DIR / "empty_endcap.png"))

    assert result["assessed"] is True
    assert result["detector"] == "heuristic"
    assert result["fill_percentage"] < 0.40  # the shelf is nearly bare
    assert result["detection_count"] > 0      # and the empty space is localized


def test_fully_stocked_synthetic_shelf_reports_high_fill_with_no_regions(analyzer, tmp_path):
    """
    A sharp, colourful, textured image (stand-in for a fully stocked shelf) passes the
    gate and the heuristic finds no thick empty pockets anywhere: the correct answer is a
    confident high fill with zero distinct regions — NOT the legacy 'unable to assess'.
    """
    rng = np.random.default_rng(7)
    noise = rng.integers(0, 255, (300, 400, 3), dtype=np.uint8)
    noise[:, :, 1] = np.clip(noise[:, :, 1].astype(int) + 60, 0, 255).astype(np.uint8)
    image_path = tmp_path / "stocked.jpg"
    cv2.imwrite(str(image_path), noise)

    result = analyzer.analyze_shelf(str(image_path))

    assert result["assessed"] is True
    assert result["detector"] == "heuristic"
    assert result["detection_count"] == 0
    assert result["fill_percentage"] > 0.9


def test_analyze_shelf_uses_the_explicit_threshold(analyzer):
    """
    Raising the confidence threshold must actually change what the MODEL survives: at 0.95
    the YOLO head finds nothing and the pipeline hands over to the heuristic fallback
    (detector == 'heuristic'), while the default threshold keeps the model in charge.
    """
    relaxed = analyzer.analyze_shelf(str(KNOWN_DETECTION_IMAGE), conf_threshold=0.25)
    strict = analyzer.analyze_shelf(str(KNOWN_DETECTION_IMAGE), conf_threshold=0.95)

    assert relaxed["detection_count"] > 0
    assert relaxed["detector"] == "yolo"
    assert strict["detector"] == "heuristic"


def test_too_small_image_is_rejected(analyzer, tmp_path):
    import cv2 as _cv2
    image_path = tmp_path / "tiny.jpg"
    _cv2.imwrite(str(image_path), np.full((91, 195, 3), 200, dtype=np.uint8))

    with pytest.raises(shelf_analyzer.ImageQualityError) as excinfo:
        analyzer.analyze_shelf(str(image_path))
    assert "too small" in str(excinfo.value)


def test_too_dark_image_is_rejected(analyzer, tmp_path):
    image_path = tmp_path / "black.jpg"
    cv2.imwrite(str(image_path), np.zeros((400, 300, 3), dtype=np.uint8))

    with pytest.raises(shelf_analyzer.ImageQualityError) as excinfo:
        analyzer.analyze_shelf(str(image_path))
    assert "dark" in str(excinfo.value)


def test_blurry_image_is_rejected(analyzer, tmp_path):
    rng = np.random.default_rng(11)
    noise = rng.integers(0, 255, (400, 300, 3), dtype=np.uint8)
    image_path = tmp_path / "blurry.jpg"
    cv2.imwrite(str(image_path), cv2.GaussianBlur(noise, (31, 31), 0))

    with pytest.raises(shelf_analyzer.ImageQualityError) as excinfo:
        analyzer.analyze_shelf(str(image_path))
    assert "blurry" in str(excinfo.value)


def test_flat_featureless_surface_is_rejected(analyzer, tmp_path):
    """A sharp but flat mostly-white surface (a wall/sign) is not a shelf with products."""
    rng = np.random.default_rng(13)
    white = np.full((300, 400, 3), 235, dtype=np.uint8)
    cv2.rectangle(white, (30, 130), (90, 190), (40, 40, 220), -1)
    # enough sensor noise to be 'sharp', so the relevance check (not blur) rejects it
    white = np.clip(white.astype(int) + rng.integers(-8, 9, white.shape), 0, 255).astype(np.uint8)
    image_path = tmp_path / "wall.jpg"
    cv2.imwrite(str(image_path), white)

    with pytest.raises(shelf_analyzer.ImageQualityError) as excinfo:
        analyzer.analyze_shelf(str(image_path))
    assert "does not look like a shelf" in str(excinfo.value)


def test_missing_weights_raise_instead_of_falling_back(tmp_path):
    """
    A missing checkpoint is a deployment fault and must raise. It must never quietly
    substitute yolov8n.pt (80 COCO classes), which would report vases and cars as shelf gaps
    with no error signal at all.
    """
    missing = tmp_path / "does_not_exist.pt"

    with pytest.raises(FileNotFoundError) as excinfo:
        ShelfAnalyzer(model_path=str(missing))

    assert "does_not_exist.pt" in str(excinfo.value)


def test_wrong_class_model_is_rejected(tmp_path):
    """A checkpoint lacking the 'empty' class must be refused up front."""
    coco_weights = Path(__file__).resolve().parents[2] / "yolov8n.pt"
    if not coco_weights.is_file():
        pytest.skip("stock yolov8n.pt not present to use as a wrong-model sample")

    with pytest.raises(ValueError) as excinfo:
        ShelfAnalyzer(model_path=str(coco_weights))

    assert shelf_analyzer.EMPTY_CLASS_NAME in str(excinfo.value)
