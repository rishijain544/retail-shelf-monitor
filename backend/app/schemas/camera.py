from pydantic import BaseModel, ConfigDict

class CameraBase(BaseModel):
    store_id: int
    aisle: str
    name: str

class CameraCreate(CameraBase):
    # store_id is optional here because it's derived from the authenticated
    # user, not accepted from the client. Kept in the schema for backwards
    # compatibility with any external callers.
    store_id: int | None = None

class CameraResponse(CameraBase):
    id: int

    model_config = ConfigDict(from_attributes=True)
