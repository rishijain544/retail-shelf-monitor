from pydantic import BaseModel, ConfigDict
from typing import List, Optional

from app.schemas.common import OptionalUTCDateTime, UTCDateTime
from app.schemas.detection import DetectionResponse
from app.schemas.llm import ShelfReportOutput

class CaptureBase(BaseModel):
    camera_id: int
    image_url: str

class CaptureCreate(CaptureBase):
    pass

class CaptureResponse(CaptureBase):
    id: int
    # Explicit UTC so the client can render it in the viewer's timezone.
    captured_at: UTCDateTime

    model_config = ConfigDict(from_attributes=True)


class CaptureUploadResponse(BaseModel):
    """202 response for POST /captures/upload — analysis runs in the background."""
    capture_id: int
    status: str  # "processing"
    image_url: str
    captured_at: UTCDateTime
    report_url: str


class CaptureReportResponse(BaseModel):
    """
    GET /captures/{id}/report.

    Status reflects the background pipeline:
      * "processing" — pipeline still running; report fields are null.
      * "completed"  — pipeline succeeded; `report` holds the full LLM output.
      * "failed"     — pipeline errored; `error_message` explains why and the
                       report fields are null.
    """
    capture_id: int
    status: str  # "processing" | "completed" | "failed"
    image_url: Optional[str] = None
    captured_at: OptionalUTCDateTime = None
    fill_percentage: Optional[float] = None
    # True when the analysis produced zero detections: fill_percentage is then the
    # analyzer's vacuous 1.0, not a confident measurement. Clients must render the
    # report as "unable to assess" rather than as a genuine 100% full shelf.
    low_confidence: bool = False
    empty_region_count: Optional[int] = None
    previous_fill: Optional[float] = None
    report: Optional[ShelfReportOutput] = None
    detections: Optional[List[DetectionResponse]] = None
    error_message: Optional[str] = None  # populated only when status == "failed"
