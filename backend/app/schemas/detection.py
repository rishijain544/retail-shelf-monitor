from pydantic import BaseModel, Field, ConfigDict
from typing import List, Any

class DetectionBase(BaseModel):
    capture_id: int
    class_: str = Field(..., alias="class")
    bbox: List[Any]  # bbox details (e.g., coordinates)
    confidence: float

    # populate_by_name allows instantiating the model using the field name (class_) or the alias (class)
    # from_attributes allows reading it from SQLAlchemy attributes
    model_config = ConfigDict(
        populate_by_name=True,
        from_attributes=True
    )

class DetectionCreate(DetectionBase):
    pass

class DetectionResponse(DetectionBase):
    id: int
