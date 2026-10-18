from app.core.database import Base
from app.models.product import Product
from app.models.store import Store
from app.models.camera import Camera
from app.models.capture import Capture
from app.models.detection import Detection
from app.models.shelf_report import ShelfReport
from app.models.alert import Alert
from app.models.user import User
from app.models.email_setting import EmailSetting

__all__ = [
    "Base",
    "Product",
    "Store",
    "Camera",
    "Capture",
    "Detection",
    "ShelfReport",
    "Alert",
    "User",
    "EmailSetting",
]
