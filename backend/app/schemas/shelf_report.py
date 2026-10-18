from pydantic import BaseModel, ConfigDict
from typing import Optional, List, Any

from app.schemas.common import UTCDateTime

class ShelfReportBase(BaseModel):
    capture_id: int
    fill_percentage: float
    # True when the analysis produced zero detections: the numeric fill is the analyzer's
    # vacuous 1.0, not a confident measurement. Clients must render such reports as
    # "unable to assess" rather than as a genuine 100% full shelf.
    low_confidence: bool = False
    empty_regions_json: Optional[List[Any]] = None
    urgency: str
    llm_summary: Optional[str] = None
    recommended_action: Optional[str] = None

class ShelfReportCreate(ShelfReportBase):
    pass

class ShelfReportResponse(ShelfReportBase):
    id: int
    # Always rendered as explicit UTC so clients can localize it (see schemas/common.py).
    created_at: UTCDateTime

    # Capture context, resolved through capture -> camera -> store. The report row
    # itself only carries capture_id, but a dashboard listing reports needs to say
    # which store and aisle each one came from. Every hop is nullable so a report
    # whose capture, camera or store row has been deleted still serializes.
    camera_id: Optional[int] = None
    camera_name: Optional[str] = None
    aisle: Optional[str] = None
    store_id: Optional[int] = None
    store_name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)
