from pydantic import BaseModel, ConfigDict
from typing import Optional

from app.schemas.common import OptionalUTCDateTime

class AlertBase(BaseModel):
    shelf_report_id: int
    sent_to_slack: bool = False
    # The same alert is also emailed to the store's login address(es).
    sent_to_email: bool = False
    # Comma-joined addresses that actually received the email (None when none did).
    email_recipients: Optional[str] = None
    # Timestamps are serialized as explicit UTC (see schemas/common.py).
    sent_at: OptionalUTCDateTime = None

class AlertCreate(AlertBase):
    pass

class AlertResponse(AlertBase):
    id: int

    # Context resolved through shelf_report -> capture -> camera -> store. An
    # alert row only references its shelf report, but an alert feed has to say
    # which aisle fired and why, so the API joins these in rather than making the
    # client fetch a report (and a camera, and a store) per row. All nullable so
    # an alert whose upstream rows were deleted still serializes.
    camera_id: Optional[int] = None
    camera_name: Optional[str] = None
    aisle: Optional[str] = None
    store_id: Optional[int] = None
    store_name: Optional[str] = None

    # Pulled from the shelf report the alert fired for.
    urgency: Optional[str] = None
    llm_summary: Optional[str] = None
    report_created_at: OptionalUTCDateTime = None

    model_config = ConfigDict(from_attributes=True)
