from pydantic import BaseModel, ConfigDict
from typing import Optional

class StoreBase(BaseModel):
    name: str
    location: Optional[str] = None

class StoreCreate(StoreBase):
    pass

class StoreResponse(StoreBase):
    id: int

    model_config = ConfigDict(from_attributes=True)
