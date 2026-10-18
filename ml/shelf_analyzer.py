import os
import sys
import argparse
import json
from pathlib import Path
from typing import Dict, List, Tuple, Any

import cv2
import numpy as np
from ultralytics import YOLO

# The class the shipped weights are trained on. A model whose names do not contain
# this is the wrong checkpoint and must never be used (see __init__).
EMPTY_CLASS_NAME = "empty"


class ImageQualityError(ValueError):
    """Raised when an uploaded image is too blurry, too dark, too small or otherwise
    not a usable shelf photo. The message is USER-FACING: it travels through the capture
    pipeline into capture.error_message and is shown in the upload UI, so it must tell
    the user what was wrong and ask them to re-upload."""

# Single, explicit confidence threshold for the whole pipeline.
#
# Calibrated from measured score distributions rather than inherited convention. Feeding the
# raw (non-enhanced) image to the model at its training resolution of 640:
#   * Genuine 'empty' detections sit at 0.39-0.88 (sample_shelf_test: 11 boxes >= 0.30,
#     lowest genuine box 0.3896).
#   * Every image where the model has no real signal peaks at 0.035 (vase 0.018, grocery
#     0.035, sample_capture_* <= 0.005), and on the signal-bearing image its 12th-ranked box
#     drops to 0.0771.
# So the void between noise (<= 0.035) and real detections (>= 0.39) spans 0.035-0.39; 0.25
# sits in the middle of it with ~0.14 of margin below the lowest genuine detection.
DEFAULT_CONF_THRESHOLD = 0.25
DEFAULT_IOU_THRESHOLD = 0.5

# --- Image-quality gate (runs before any detection) ---
# Measured on the real upload corpus: sharp photos (even tiny thumbnails) have a
# Laplacian variance >= ~85 at canonical scale; the black-frame upload measures 41 and
# heavy motion blur lands far below 60. 60 keeps the synthetic YOLO fixture (84.8) and
# every sharp real photo comfortably above the cut.
MIN_DIMENSION = 150          # below this a photo cannot show shelf rows reliably
BLUR_LAPLACIAN_VARIANCE = 60.0
CANONICAL_MAX_DIM = 640      # gate metrics are computed at this scale for consistency
# A photo that is mostly uniform white with almost no colour is a wall/sign/document,
# not a stocked shelf (the white-wall-plus-red-fixture upload: white 0.41, colour 0.09).
RELEVANCE_MAX_COLORFUL = 0.10
RELEVANCE_MIN_WHITE = 0.30
RELEVANCE_MAX_DARK = 0.85    # almost the whole frame black -> not a shelf photo

# --- Heuristic fallback detector (runs when YOLO finds nothing) ---
# The fine-tuned weights only generalise to their training distribution; on arbitrary
# user photos their best score is noise (<= 0.035). When the model has no signal on an
# image that passed the quality gate, these OpenCV cues decide what is a product:
#   * saturation > HEURISTIC_SAT_THRESHOLD          -> colourful packaging
#   * |Laplacian| > HEURISTIC_LAP_THRESHOLD         -> printed/edged surfaces (incl. white
#     boxes with subtle print, which saturation alone misses)
# Empty = everything else; thick empty areas (eroded) become the reported regions.
HEURISTIC_SAT_THRESHOLD = 45
HEURISTIC_LAP_THRESHOLD = 15
HEURISTIC_MIN_DIM = 512      # analysed at least at this scale for stable cues
HEURISTIC_ERODE_FRAC = 0.03  # erosion radius as a fraction of the smaller dimension
HEURISTIC_MIN_CORE_AREA = 0.003  # min core area (fraction of image) to report a region
HEURISTIC_MAX_DENSITY = 0.60     # boxes that are mostly product are not empty regions
HEURISTIC_BASE_CONFIDENCE = 0.55

# Cross-check arbitration between the YOLO path and the classical-cue mask. The fine-
# tuned weights were only trained on ~640x480 photos of mostly-stocked shelves; measured
# on unseen real scenes (Wikimedia empty-shelf corpus) they either stay silent or fire a
# few small boxes on the most blatant gaps only. Reporting fill as "1 - box union" then
# reads a HALF-EMPTY aisle as mostly full. Calibration on the 8-image corpus:
#   * disjoint scenes (cues < 0.65 fill): single YOLO box unions covered 3-9% of frame
#   * agreeing scenes (fixture/sample, sparse grocery): box unions covered 35-95%
# When cues say "mostly empty" but the model localises less than 35% of the frame as
# empty, the model evidence is incomplete — the pixel measurement wins.
ARBITRATION_FILL_MAX = 0.75      # cue fill below this = "the scene is mostly empty"
ARBITRATION_COVERAGE_MAX = 0.40  # model boxes covering less of the frame than this are

# Background exclusion cues. Without them two real-world upload classes were miscounted
# as 90+% full shelves:
#   * warm wooden floors in the bottom of a wide store photo — brown is saturated, so the
#     plain saturation cue read floorboards as dense product;
#   * shelf fixtures photographed while empty — a painted steel unit occupies one hue
#     (measured: hue bucket share 0.84 on the empty-endcap upload) and dark blue-grey is
#     still "saturated" in HSV, so every shelf board counted as packaging. When one hue
#     dominates the saturated non-floor pixels that hard, it is the FIXTURE: smooth
#     (locally untextured, so printed product edges never match) and matte-dark.
HEURISTIC_FLOOR_HUE_RANGE = (5, 35)     # warm browns (wood floors, tiles)
HEURISTIC_FLOOR_MIN_AREA_FRAC = 0.01    # min connected-component area to be floor
HEURISTIC_FIXTURE_HUE_SHARE = 0.45      # share of one 10° hue bucket among saturated px
HEURISTIC_FIXTURE_HUE_TOLERANCE = 15    # +/- degrees around the dominant hue
HEURISTIC_FIXTURE_MAX_VALUE = 210       # fixtures are matte-dark; glossy whites excluded

class ShelfAnalyzer:
    """
    OpenCV and YOLOv8 analyzer for shelf empty space detection and fill percentage metrics.
    """

    def __init__(self, model_path: str = "ml/weights/empty_space_best.pt"):
        """
        Initializes the analyzer with the YOLOv8 model.

        Deliberately fails loudly instead of substituting a different checkpoint. An earlier
        version fell back to stock yolov8n.pt (80 COCO classes) when the custom weights were
        missing, which would silently classify vases, people and cars as shelf gaps with no
        error signal at all. A missing/wrong model is a deployment fault, not something to
        paper over at runtime.
        """
        self.model_path = Path(model_path).resolve()
        if not self.model_path.is_file():
            raise FileNotFoundError(
                f"ShelfAnalyzer model weights not found at {self.model_path}. Refusing to start: "
                "the app must load the fine-tuned '{EMPTY_CLASS_NAME}' detector, not fall back to "
                "a generic COCO model. Check that ml/weights/ is present in the image."
            )

        print(f"Loading custom YOLOv8 model: {self.model_path}...", flush=True)
        self.model = YOLO(str(self.model_path))

        # A model that doesn't know the 'empty' class would report COCO objects as shelf gaps,
        # so reject it up front rather than producing confident nonsense downstream.
        class_names = set(self.model.names.values())
        if EMPTY_CLASS_NAME not in class_names:
            raise ValueError(
                f"Model at {self.model_path} does not define the '{EMPTY_CLASS_NAME}' class "
                f"(its classes are {sorted(class_names)}). This looks like a generic COCO "
                "checkpoint rather than the fine-tuned empty-space detector."
            )

        # Cheap one-line startup record of exactly which model is serving traffic.
        try:
            weights_size = self.model_path.stat().st_size
        except OSError:  # pragma: no cover - the file was just opened successfully
            weights_size = -1
        print(
            f"[MODEL-DIAG] resolved={str(self.model_path)!r} size_bytes={weights_size} "
            f"names={self.model.names}",
            flush=True,
        )

    @staticmethod
    def deskew_image(image: np.ndarray) -> Tuple[np.ndarray, float]:
        """
        Detects the skew angle of the shelf text/edges and rotates the image to align rows.
        """
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        
        # Threshold the image to isolate structures
        thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
        
        # Find coordinates of non-zero pixels
        coords = np.column_stack(np.where(thresh > 0))
        
        # Get minimum area bounding box enclosing all coordinates
        # minAreaRect returns (center(x, y), size(w, h), angle)
        rect = cv2.minAreaRect(coords)
        angle = rect[-1]
        
        # Adjust angle to rotate upright
        if angle < -45:
            angle = -(90 + angle)
        else:
            angle = -angle
            
        # Rotate image if angle is significant
        if abs(angle) > 0.5:
            (h, w) = image.shape[:2]
            center = (w // 2, h // 2)
            M = cv2.getRotationMatrix2D(center, angle, 1.0)
            rotated = cv2.warpAffine(
                image, M, (w, h), 
                flags=cv2.INTER_CUBIC, 
                borderMode=cv2.BORDER_REPLICATE
            )
            return rotated, angle
            
        return image, 0.0

    @staticmethod
    def enhance_image(image: np.ndarray) -> np.ndarray:
        """
        Applies adaptive contrast enhancement (CLAHE in LAB color space) and bilateral denoising.
        """
        # 1. Bilateral Denoising (preserves edges, unlike simple Gaussian blur)
        denoised = cv2.bilateralFilter(image, d=9, sigmaColor=75, sigmaSpace=75)
        
        # 2. Adaptive Contrast via CLAHE in LAB color space to preserve hue
        lab = cv2.cvtColor(denoised, cv2.COLOR_BGR2LAB)
        l_channel, a_channel, b_channel = cv2.split(lab)
        
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        cl = clahe.apply(l_channel)
        
        merged_lab = cv2.merge((cl, a_channel, b_channel))
        enhanced_bgr = cv2.cvtColor(merged_lab, cv2.COLOR_LAB2BGR)
        
        return enhanced_bgr

    @staticmethod
    def _canonical_scale(image: np.ndarray, max_dim: int = CANONICAL_MAX_DIM) -> np.ndarray:
        """Resize so the larger dimension is at most max_dim (down only)."""
        h, w = image.shape[:2]
        scale = max_dim / max(h, w)
        if scale < 1.0:
            return cv2.resize(image, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        return image

    @classmethod
    def validate_image_quality(cls, image: np.ndarray) -> None:
        """
        Reject images that cannot produce a meaningful shelf assessment.

        Raises ImageQualityError with a user-facing message (it is surfaced in the upload
        UI verbatim). Ordered cheapest/most-specific first: unreadable frames are already
        handled by the caller; darkness and blur are objective defects, then relevance.
        Thresholds are calibrated on the real upload corpus (see constants above).
        """
        h, w = image.shape[:2]
        if min(h, w) < MIN_DIMENSION:
            raise ImageQualityError(
                f"This image is too small ({w}x{h}) to analyse reliably. Please re-upload a "
                "larger, clearer photo of the shelf."
            )

        canonical = cls._canonical_scale(image)
        gray = cv2.cvtColor(canonical, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(canonical, cv2.COLOR_BGR2HSV)
        laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
        dark_fraction = float((gray < 25).mean())
        colorful_fraction = float((hsv[:, :, 1] > 60).mean())
        white_fraction = float((gray > 210).mean())

        if dark_fraction > RELEVANCE_MAX_DARK:
            raise ImageQualityError(
                "This image is too dark (or mostly black) to be a shelf photo. Please "
                "re-upload a well-lit photo of the shelf."
            )
        # Relevance before blur: a sharp but flat white wall is not "blurry", and a
        # genuinely blurry shelf photo still has colour, so it falls through to the blur
        # check below.
        if colorful_fraction < RELEVANCE_MAX_COLORFUL and white_fraction > RELEVANCE_MIN_WHITE:
            raise ImageQualityError(
                "This image does not look like a shelf with products (it is mostly a flat, "
                "featureless surface). Please re-upload a photo of a retail shelf."
            )
        if laplacian_var < BLUR_LAPLACIAN_VARIANCE:
            raise ImageQualityError(
                "This image appears to be blurry. Please re-upload a sharper photo of "
                "the shelf."
            )

    @classmethod
    def heuristic_empty_detection(cls, image: np.ndarray) -> Tuple[List[Dict[str, Any]], float, float]:
        """
        Classical CV fallback for images the YOLO weights cannot read.

        Products are pixels that are saturated (colourful packaging) or edged (printed
        boxes, incl. white ones); everything else is candidate empty shelf. Returns:
          * detections: bbox in the coordinates of `image` + confidence + class 'empty'
          * empty_fraction: fraction of pixels classified as not-product (the honest
            continuous fill basis — thin slivers between products count toward empty
            area even when too small to be a reportable region)
          * fill_percentage: 1 - empty_fraction, clipped to [0, 1]
        """
        h, w = image.shape[:2]
        scale = max(1.0, HEURISTIC_MIN_DIM / min(h, w))
        work = (
            cv2.resize(image, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)
            if scale > 1.0
            else image
        )
        wh, ww = work.shape[:2]

        hsv = cv2.cvtColor(work, cv2.COLOR_BGR2HSV)
        hue = hsv[:, :, 0].astype(np.int32)
        sat = hsv[:, :, 1].astype(np.int32)
        val = hsv[:, :, 2].astype(np.int32)
        gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (3, 3), 0)
        lap = np.abs(cv2.Laplacian(blurred, cv2.CV_64F)).astype(np.uint8)

        # Background exclusion pass 1 — floor. Warm brown components touching the bottom
        # edge of the frame are the store floor, never shelf product. The warm mask is
        # dilated before component analysis because speckled/terrazzo flooring (measured
        # on a real panic-buying aisle: warm tile split into hundreds of sub-1% fragments
        # by grey speckles) otherwise evades the minimum-area test entirely; the seed
        # mask is then ANDed back so the reported floor keeps its true (holed) shape.
        # Excluded from the product mask AND from the fill denominator (floor area is
        # not shelf area).
        floor_lo, floor_hi = HEURISTIC_FLOOR_HUE_RANGE
        warm = ((hue >= floor_lo) & (hue <= floor_hi) & (sat > 50) & (val > 60)).astype(np.uint8)
        warm_dilated = cv2.dilate(warm, np.ones((9, 9), np.uint8))
        n_warm, warm_labels, warm_stats, _ = cv2.connectedComponentsWithStats(warm_dilated, 8)
        floor = np.zeros((wh, ww), dtype=np.uint8)
        for i in range(1, n_warm):
            x, y, bw_, bh_, area = warm_stats[i]
            if (y + bh_) >= wh - 8 and area > HEURISTIC_FLOOR_MIN_AREA_FRAC * wh * ww:
                floor |= (warm_labels == i).astype(np.uint8)
        floor &= warm

        # Background exclusion pass 2 — fixture colour. When one hue bucket dominates
        # the saturated non-floor pixels, that colour belongs to the shelving unit
        # itself. Its mask is the dominant hue AND locally smooth AND not glossy:
        # printed packaging and white-label products inside the same hue have texture
        # (Laplacian > HEURISTIC_LAP_THRESHOLD) or high brightness, and stay "product".
        fixture = np.zeros((wh, ww), dtype=np.uint8)
        hue_sel = (sat > 60) & (val > 60) & (floor == 0)
        if bool(hue_sel.any()):
            hue_hist = np.bincount(np.clip(hue[hue_sel], 0, 179) // 10, minlength=18)
            if hue_hist.max() / max(1, int(hue_sel.sum())) >= HEURISTIC_FIXTURE_HUE_SHARE:
                fixture_hue = int(np.argmax(hue_hist)) * 10 + 5
                hue_dist = np.abs(hue - fixture_hue)
                hue_dist = np.minimum(hue_dist, 180 - hue_dist)
                fixture = (
                    (hue_dist <= HEURISTIC_FIXTURE_HUE_TOLERANCE)
                    & (sat > 40)
                    & (val < HEURISTIC_FIXTURE_MAX_VALUE)
                    & (lap <= HEURISTIC_LAP_THRESHOLD)
                    & (floor == 0)
                ).astype(np.uint8)

        product = (
            (sat > HEURISTIC_SAT_THRESHOLD) | (lap > HEURISTIC_LAP_THRESHOLD)
        ) & (floor == 0) & (fixture == 0)
        product = product.astype(np.uint8) * 255

        # Fill is measured on a CONTOUR-FILLED product mask — each connected product
        # blob has its interior holes filled, with NO morphological welding between
        # blobs. Measured on the 8-image calibration corpus this beat both the raw cue
        # mask (which under-counts packed shelves: printed white packaging slivers read
        # as gaps) and the old CLOSE(9)+OPEN(5) morphology (which welds shelf-edge
        # strips and tile grout lines into product blobs, measuring a half-stocked
        # aisle at 84% full). Average absolute error at CLOSE kernel sweep and open-
        # empty variants was strictly worse on both axes simultaneously; per-blob hole
        # filling is the only variant that is accurate on stocked AND empty scenes at
        # once.
        valid = (floor == 0)
        contours, _ = cv2.findContours(product, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        product_solid = np.zeros_like(product)
        if contours:
            cv2.drawContours(product_solid, contours, -1, 255, thickness=cv2.FILLED)
            product_solid &= (valid * 255).astype(np.uint8)
        empty_solid = ((product_solid == 0) & (valid == 1))
        empty_fraction = float(empty_solid.sum()) / max(1, float(valid.sum()))
        fill_percentage = max(0.0, min(1.0, 1.0 - empty_fraction))

        product_morph = cv2.morphologyEx(product, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
        product_morph = cv2.morphologyEx(product_morph, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))

        # Region extraction uses the CLEANED mask so thin bridging edges do not
        # fragment the cores. Empty = not product and not floor.
        empty_mask = ((product_morph == 0) & valid).astype(np.uint8)

        # Only THICK empty areas become reported regions: erode away slivers between
        # products, take connected cores, then grow each core's bbox back by the erosion
        # radius. Bounding the raw mask instead swallows the product islands an empty
        # area wraps around. Cores are grown only over the valid area, so floor regions
        # are never reported as empty shelf.
        radius = max(5, int(min(wh, ww) * HEURISTIC_ERODE_FRAC))
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1))
        cores = cv2.erode((empty_mask * (1 - floor)) * 255, kernel)
        n_labels, _, stats, _ = cv2.connectedComponentsWithStats(cores, 8)

        detections: List[Dict[str, Any]] = []
        for i in range(1, n_labels):
            x, y, bw_, bh_, area = stats[i]
            if area < HEURISTIC_MIN_CORE_AREA * wh * ww:
                continue
            x0, y0 = max(0, x - radius // 2), max(0, y - radius // 2)
            x1, y1 = min(ww, x + bw_ + radius // 2), min(wh, y + bh_ + radius // 2)
            density = float(product[y0:y1, x0:x1].mean()) / 255.0
            if density > HEURISTIC_MAX_DENSITY:
                continue  # this box is mostly product — not an empty region
            detections.append({
                "bbox": [x0 / scale, y0 / scale, x1 / scale, y1 / scale],
                "confidence": min(0.8, HEURISTIC_BASE_CONFIDENCE + 0.3 * (1.0 - density)),
                "class_name": EMPTY_CLASS_NAME,
            })

        detections.sort(key=lambda d: (d["bbox"][2] - d["bbox"][0]) * (d["bbox"][3] - d["bbox"][1]), reverse=True)
        return detections[:12], empty_fraction, fill_percentage

    @staticmethod
    def calculate_iou(box1: List[float], box2: List[float]) -> float:
        """Computes the Intersection over Union (IoU) of two bounding boxes."""
        # box: [xmin, ymin, xmax, ymax]
        ixmin = max(box1[0], box2[0])
        iymin = max(box1[1], box2[1])
        ixmax = min(box1[2], box2[2])
        iymax = min(box1[3], box2[3])

        iw = max(0.0, ixmax - ixmin)
        ih = max(0.0, iymax - iymin)
        intersection = iw * ih

        area1 = (box1[2] - box1[0]) * (box1[3] - box1[1])
        area2 = (box2[2] - box2[0]) * (box2[3] - box2[1])
        union = area1 + area2 - intersection

        return intersection / union if union > 0 else 0.0

    @classmethod
    def custom_nms(cls, detections: List[Dict[str, Any]], iou_threshold: float = DEFAULT_IOU_THRESHOLD) -> List[Dict[str, Any]]:
        """
        Deduplicates overlapping empty-space boxes by IoU only.

        Confidence is NOT filtered here: the single confidence cut happens once, inside the
        model call in analyze_shelf(). Stacking a second confidence filter on top of
        ultralytics' own (implicit 0.25 plus an explicit 0.4) silently discarded every
        detection the model produced on real images, which is the bug this replaced.

        detections: List of dicts with 'bbox' [xmin, ymin, xmax, ymax], 'confidence', and 'class_name'
        """
        if not detections:
            return []

        # Sort by confidence descending so the strongest box wins each overlap group.
        filtered = sorted(detections, key=lambda x: x["confidence"], reverse=True)
        keep = []

        while filtered:
            best = filtered.pop(0)
            keep.append(best)
            
            remaining = []
            for item in filtered:
                iou = cls.calculate_iou(best["bbox"], item["bbox"])
                if iou < iou_threshold:
                    remaining.append(item)
            filtered = remaining

        return keep

    @staticmethod
    def union_area(boxes: List[List[float]]) -> float:
        """
        Exact area covered by the union of axis-aligned boxes.

        Fill percentage is a fraction of shelf area, so overlapping boxes must not be counted
        twice. Summing individual box areas instead over-counts (the detector legitimately
        emits several overlapping full-width shelf-row strips), which drove the empty area
        past the image area and clamped fill to 0% on an almost entirely empty shelf.
        """
        clean = [b for b in boxes if b[2] > b[0] and b[3] > b[1]]
        if not clean:
            return 0.0

        xs = sorted({v for b in clean for v in (b[0], b[2])})
        total = 0.0
        for i in range(len(xs) - 1):
            x0, x1 = xs[i], xs[i + 1]
            if x1 <= x0:
                continue
            # Vertical spans of every box covering this x-slab, merged into runs.
            spans = sorted((b[1], b[3]) for b in clean if b[0] <= x0 and b[2] >= x1)
            covered = 0.0
            run = None
            for start, end in spans:
                if run is None:
                    run = [start, end]
                elif start > run[1]:
                    covered += run[1] - run[0]
                    run = [start, end]
                else:
                    run[1] = max(run[1], end)
            if run is not None:
                covered += run[1] - run[0]
            total += (x1 - x0) * covered
        return total

    def analyze_shelf(self, image_path: str, conf_threshold: float = DEFAULT_CONF_THRESHOLD, iou_threshold: float = DEFAULT_IOU_THRESHOLD) -> Dict[str, Any]:
        """
        Runs the complete analysis pipeline on a shelf image:
        1. Loads image.
        2. Deskews (geometric correction only - no photometric preprocessing, see below).
        3. Runs YOLOv8 at its training resolution with ONE explicit confidence threshold.
        4. Deduplicates overlapping empty-space boxes by IoU (no second confidence cut).
        5. Computes fill percentage from the union of the remaining boxes.

        The model is fed the image essentially as captured. It was trained on ordinary shelf
        photos, and the CLAHE + bilateral "enhancement" that used to run on the inference path
        pushed the input out of that distribution: on an image the model scores 0.883 it
        collapsed the best detection to 0.282 and eliminated all 11 boxes >= 0.30, while on
        images where the model has no real signal the marginal gain (0.035 -> 0.107) never
        reached a usable threshold and one image inflated 3 boxes into 138 noise boxes.
        """
        path = Path(image_path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"Image not found at {path}")

        # 1. Load image
        img = cv2.imread(str(path))
        if img is None:
            raise ImageQualityError(
                "This file could not be read as an image. Please re-upload a valid JPG/PNG "
                "photo of the shelf."
            )

        # 2. Quality gate: reject blurry, too-dark, too-small or irrelevant uploads with a
        #    user-facing message instead of analysing them into confident nonsense.
        self.validate_image_quality(img)

        # 3. Deskew only. Measured to be a strict no-op when the detected skew is below the
        #    0.5-degree cutoff (every image in the repo reports 0.00), so it cannot cost
        #    accuracy, and it still corrects genuinely tilted captures.
        analysis_img, skew_angle = self.deskew_image(img)

        # 3b. The model was trained on ~640px photos; thumbnails starve it. Upscale small
        #     images soYOLO sees its training scale, and remember the factor so boxes can
        #     be mapped back to original-image coordinates for storage/overlay.
        h, w = analysis_img.shape[:2]
        yolo_scale = 1.0
        if min(h, w) < 480:
            yolo_scale = 640.0 / min(h, w)
            analysis_img = cv2.resize(
                analysis_img,
                (int(w * yolo_scale), int(h * yolo_scale)),
                interpolation=cv2.INTER_CUBIC,
            )

        # 4. YOLOv8 inference with the confidence threshold passed EXPLICITLY. Supplying `conf`
        #    here overrides ultralytics' implicit internal 0.25, so there is exactly one
        #    filter point instead of two different ones stacked in series.
        result = self.model(
            analysis_img,
            conf=conf_threshold,
            iou=iou_threshold,
            verbose=False,
        )[0]

        raw_detections = []
        for box in result.boxes:
            class_id = int(box.cls[0].item())
            class_name = self.model.names[class_id]
            confidence = float(box.conf[0].item())
            xyxy = box.xyxy[0].tolist()  # [xmin, ymin, xmax, ymax]
            if yolo_scale != 1.0:
                xyxy = [v / yolo_scale for v in xyxy]

            raw_detections.append({
                "bbox": xyxy,
                "confidence": confidence,
                "class_name": class_name
            })

        # 5. IoU-only deduplication.
        final_detections = self.custom_nms(raw_detections, iou_threshold=iou_threshold)
        detector = "yolo"

        # 5b. Heuristic fallback. The fine-tuned weights only generalise to their training
        #     distribution — on arbitrary user photos their best score is noise. If the
        #     model found nothing on an image that PASSED the quality gate, fall back to
        #     classical CV cues instead of reporting a vacuous 100% full shelf.
        if not final_detections:
            # to their training distribution: on unseen real scenes they either say
            # nothing (handled here) or fire on just a few of the most blatant gaps,
            # which would report a half-empty aisle as mostly full.
            heuristic_detections, _empty_fraction, heuristic_fill = (
                self.heuristic_empty_detection(analysis_img)
            )
            detector = "heuristic"
            final_detections = heuristic_detections
        else:
            # INCOMPLETE-COVERAGE ARBITRATION. Always compute the classical-cue
            # measurement and compare: when the cues say the scene is mostly empty but
            # the model's boxes cover only a small part of the frame, the model found
            # exceptions, not the whole picture (it was trained on mostly-stocked
            # ~640x480 photos) — adopt the pixel-level assessment (its region list and
            # its fill) instead of "1 - small box union", which would imply a full shelf.
            heuristic_detections, _empty_fraction, heuristic_fill = (
                self.heuristic_empty_detection(analysis_img)
            )
            model_box_area = self.union_area(
                [det["bbox"] for det in final_detections]
            )
            ih, iw = analysis_img.shape[:2]
            model_coverage = model_box_area / float(ih * iw) if ih * iw > 0 else 0.0
            if (
                heuristic_fill < ARBITRATION_FILL_MAX
                and model_coverage < ARBITRATION_COVERAGE_MAX
            ):
                print(
                    f"[ARBITRATION] adopting heuristic: cue fill {heuristic_fill:.2f} "
                    f"< {ARBITRATION_FILL_MAX} and model coverage {model_coverage:.2f} "
                    f"< {ARBITRATION_COVERAGE_MAX}",
                    flush=True,
                )
                detector = "heuristic"
                final_detections = heuristic_detections

        # 6. Compute fill percentage: 1 - (union area of empty boxes / total shelf area)
        ah, aw = analysis_img.shape[:2]
        total_shelf_area = float(aw * ah)
        empty_regions = [det["bbox"] for det in final_detections]

        if detector == "heuristic":
            # Pixel-level product coverage, not the union of region boxes: on sparse
            # shelves the empty area is fragmented into thin strips between products that
            # no reportable region box captures, so a box-union fill would badly
            # understate the emptiness the user can see.
            fill_percentage = heuristic_fill
        elif total_shelf_area > 0 and empty_regions:
            empty_area = self.union_area(empty_regions)
            fill_percentage = 1.0 - (empty_area / total_shelf_area)
            # Clip between 0.0 and 1.0
            fill_percentage = max(0.0, min(1.0, fill_percentage))
        else:
            fill_percentage = 1.0

        return {
            "detections": final_detections,
            "fill_percentage": float(fill_percentage),
            "empty_regions": empty_regions,
            "skew_angle": float(skew_angle),
            "detector": detector,
            # The image passed the quality gate, so a fill number here is a real
            # measurement (model- or heuristic-derived), not a vacuous default. Callers
            # use `assessed` to distinguish that from a canned/legacy zero-detection
            # result, which must still be treated as low confidence.
            "assessed": True,
            # `detection_count == 0` with `assessed` true means "no distinct empty regions
            # were found" (a genuinely full shelf), NOT missing evidence.
            "detection_count": len(final_detections),
        }

def main():
    parser = argparse.ArgumentParser(description="Shelf Empty Space & Fill Percentage Analyzer")
    parser.add_argument("--image", type=str, required=True, help="Path to shelf image file")
    parser.add_argument("--model", type=str, default="ml/weights/empty_space_best.pt", help="Path to YOLOv8 weights")
    parser.add_argument("--conf", type=float, default=DEFAULT_CONF_THRESHOLD, help="Confidence threshold")
    parser.add_argument("--iou", type=float, default=DEFAULT_IOU_THRESHOLD, help="IoU threshold for NMS")
    parser.add_argument("--output", type=str, default=None, help="Save annotated output image to file")
    args = parser.parse_args()

    try:
        analyzer = ShelfAnalyzer(model_path=args.model)
        results = analyzer.analyze_shelf(
            args.image, 
            conf_threshold=args.conf, 
            iou_threshold=args.iou
        )
        
        # Print JSON summary to stdout
        print("\n" + "=" * 45)
        print("          SHELF ANALYSIS METRICS")
        print("=" * 45)
        print(f"Fill Percentage: {results['fill_percentage'] * 100:.2f}%")
        print(f"Empty Regions Detected: {len(results['empty_regions'])}")
        print(f"Skew Correction Angle: {results['skew_angle']:.2f}°")
        print("=" * 45)
        print("\nJSON Response:")
        print(json.dumps(results, indent=2))

        # Annotate and save output image if path is requested
        if args.output:
            img = cv2.imread(args.image)
            if img is not None:
                # Deskew only, exactly as analyze_shelf() does, so the drawn boxes line up with
                # the canvas the detector actually saw. (This used to re-apply enhance_image(),
                # which would have shifted every box once enhancement left the inference path.)
                img, _ = ShelfAnalyzer.deskew_image(img)

                # Draw boxes
                for bbox in results["empty_regions"]:
                    xmin, ymin, xmax, ymax = map(int, bbox)
                    cv2.rectangle(img, (xmin, ymin), (xmax, ymax), (0, 0, 255), 2)  # Red for empty space
                    cv2.putText(
                        img, "Empty", (xmin, ymin - 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1
                    )

                # Write fill percentage text
                cv2.putText(
                    img, f"Fill: {results['fill_percentage'] * 100:.1f}%",
                    (15, 30), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2
                )

                cv2.imwrite(args.output, img)
                print(f"\nSaved annotated image to: {args.output}")

    except Exception as e:
        print(f"Analysis failed: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
