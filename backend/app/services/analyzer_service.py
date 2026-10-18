"""
backend/app/services/analyzer_service.py

Thin adapter around the ML package's ShelfAnalyzer (ml/shelf_analyzer.py). The
backend calls the already-built CV/YOLO pipeline directly rather than reimplementing
it. Two concerns are isolated here:

1. Cross-package import — ml/ lives at the repo root, outside the backend package,
   so we put the repo root on sys.path. The backend Docker image packages it too:
   the build context is the repo root, backend/Dockerfile does `COPY ml/ /ml/`
   (which includes ml/weights/empty_space_best.pt) and installs ml/requirements.txt
   alongside backend/requirements.txt. /ml is exactly where parents[3] resolves to
   when the app is at /app, so no code change is needed in the container.

2. Model lifecycle — the YOLO weights are loaded once (lazy singleton) and inference
   is serialized with a lock, since a shared ultralytics model is not guaranteed
   thread-safe and background tasks run in a threadpool.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any, Dict

# Repo root: .../backend/app/services/analyzer_service.py -> parents[3]
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_DEFAULT_WEIGHTS = _REPO_ROOT / "ml" / "weights" / "empty_space_best.pt"

# The single confidence threshold for the whole backend pipeline.
#
# Kept in sync with ml.shelf_analyzer.DEFAULT_CONF_THRESHOLD (asserted by
# backend/tests/test_detection_pipeline.py). It is duplicated rather than imported because
# importing ml/ pulls in ultralytics/torch at module import time, which this module defers
# on purpose. Calibration basis: measured 'empty' scores separate cleanly into genuine
# detections (>= 0.39) and noise (<= 0.035), so 0.25 sits in the middle of that void.
DEFAULT_CONF_THRESHOLD = 0.25
DEFAULT_IOU_THRESHOLD = 0.5

_analyzer = None
_init_lock = threading.Lock()
_inference_lock = threading.Lock()


def get_analyzer():
    """Lazily construct and cache the ShelfAnalyzer (loads YOLO weights once)."""
    global _analyzer
    if _analyzer is None:
        with _init_lock:
            if _analyzer is None:
                # Imported lazily so importing this module (and the FastAPI app) does
                # not pull in ultralytics/torch until the first analysis actually runs.
                from ml.shelf_analyzer import ShelfAnalyzer

                _analyzer = ShelfAnalyzer(model_path=str(_DEFAULT_WEIGHTS))
    return _analyzer


def analyze(
    image_path: str,
    conf: float = DEFAULT_CONF_THRESHOLD,
    iou: float = DEFAULT_IOU_THRESHOLD,
) -> Dict[str, Any]:
    """
    Run the full shelf-analysis pipeline on an image path.

    Returns the ShelfAnalyzer dict: {detections, fill_percentage, empty_regions, skew_angle}.
    Serialized with a lock so concurrent background tasks don't share model state.
    """
    analyzer = get_analyzer()
    with _inference_lock:
        return analyzer.analyze_shelf(image_path, conf_threshold=conf, iou_threshold=iou)
