import argparse
import json
from pathlib import Path
from ultralytics import YOLO
import requests

class ShelfDetector:
    """
    YOLOv8 inference detector for recognizing and counting objects on retail shelves.
    """
    
    def __init__(self, model_name: str = "yolov8n.pt"):
        """
        Initializes the detector with a pre-trained YOLOv8 model.
        In a production environment, you would use a custom-trained model (e.g. 'shelf_best.pt').
        """
        print(f"Loading YOLOv8 model: {model_name}...")
        self.model = YOLO(model_name)

    def detect_and_count(self, image_path: str, confidence: float = 0.25) -> dict:
        """
        Runs object detection on the image and counts detected products.
        """
        if not Path(image_path).exists():
            raise FileNotFoundError(f"Image not found at {image_path}")

        # Run inference
        results = self.model(image_path, conf=confidence)
        result = results[0]
        
        detections = []
        counts = {}

        # Parse results
        for box in result.boxes:
            class_id = int(box.cls[0].item())
            class_name = self.model.names[class_id]
            conf = float(box.conf[0].item())
            xyxy = box.xyxy[0].tolist() # [xmin, ymin, xmax, ymax]
            
            detections.append({
                "class_name": class_name,
                "confidence": conf,
                "box": xyxy
            })
            
            counts[class_name] = counts.get(class_name, 0) + 1

        return {
            "image": str(image_path),
            "detections": detections,
            "counts": counts
        }

    def post_to_backend(self, api_url: str, shelf_id: int, counts: dict):
        """
        Posts the detected counts back to the FastAPI backend.
        """
        payload = {
            "shelf_id": shelf_id,
            "counts": counts
        }
        try:
            print(f"Sending counts to backend api ({api_url})...")
            response = requests.post(f"{api_url}/shelves/{shelf_id}/detect", json=payload, timeout=5)
            response.raise_for_status()
            print("Successfully updated database stock levels!")
            return response.json()
        except Exception as e:
            print(f"Failed to post to backend: {e}")
            return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="YOLOv8 Shelf Stock Detector")
    parser.add_argument("--image", type=str, help="Path to shelf image/photo")
    parser.add_argument("--model", type=str, default="yolov8n.pt", help="YOLOv8 model weight filename")
    parser.add_argument("--conf", type=float, default=0.25, help="Confidence threshold")
    parser.add_argument("--api-url", type=str, default="http://localhost:8000/api/v1", help="FastAPI endpoint base URL")
    
    args = parser.parse_args()

    if args.image:
        detector = ShelfDetector(model_name=args.model)
        try:
            results = detector.detect_and_count(args.image, confidence=args.conf)
            print("\n--- Detection Results ---")
            print(json.dumps(results, indent=2))
        except Exception as err:
            print(f"Error: {err}")
    else:
        print("ShelfDetector script initialized. Run with '--image <path>' to execute detection.")
