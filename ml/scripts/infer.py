import argparse
import sys
from pathlib import Path
import cv2
from ultralytics import YOLO

def main():
    parser = argparse.ArgumentParser(description="Inference Script for Retail Empty Space Detection")
    parser.add_argument(
        "--image", 
        type=str, 
        required=True, 
        help="Path to input image for inference"
    )
    parser.add_argument(
        "--model", 
        type=str, 
        default=None, 
        help="Path to trained model weights (.pt file)"
    )
    parser.add_argument(
        "--output", 
        type=str, 
        default="ml/output_detected.jpg", 
        help="Path to save the annotated output image"
    )
    parser.add_argument(
        "--conf", 
        type=float, 
        default=0.25, 
        help="Confidence threshold for detections"
    )
    args = parser.parse_args()

    # Resolve paths
    script_dir = Path(__file__).resolve().parent
    ml_dir = script_dir.parent
    
    # Set default model path if not specified
    if args.model is None:
        model_path = ml_dir / "weights" / "empty_space_best.pt"
        # Fallback to yolov8n.pt if training has not been executed yet
        if not model_path.exists():
            print(f"Warning: Custom weights not found at {model_path}. Falling back to default yolov8n.pt")
            model_path = Path("yolov8n.pt")
    else:
        model_path = Path(args.model).resolve()

    image_path = Path(args.image).resolve()
    output_path = Path(args.output).resolve()

    # Verify input image exists
    if not image_path.exists():
        print(f"Error: Input image does not exist at: {image_path}")
        sys.exit(1)

    # 1. Load the model
    print(f"Loading YOLOv8 model from {model_path}...")
    model = YOLO(str(model_path))

    # 2. Run model inference
    print(f"Running inference on {image_path}...")
    results = model(str(image_path), conf=args.conf)
    result = results[0]  # Get results for the single image

    # 3. Read image with OpenCV for custom drawing
    img = cv2.imread(str(image_path))
    if img is None:
        print(f"Error: OpenCV could not read image at {image_path}")
        sys.exit(1)

    # 4. Extract detections and draw bounding boxes using OpenCV
    print(f"Drawing bounding boxes (confidence >= {args.conf})...")
    detection_count = 0
    
    for box in result.boxes:
        detection_count += 1
        # Bounding box coordinates
        xyxy = box.xyxy[0].tolist()
        xmin, ymin, xmax, ymax = map(int, xyxy)
        
        # Confidence score
        conf = float(box.conf[0].item())
        
        # Class index and name
        class_id = int(box.cls[0].item())
        class_name = model.names[class_id]

        # Define color and label (use bright green for empty or generic boxes)
        color = (0, 255, 0)  # BGR green
        label = f"{class_name} {conf:.2f}"

        # Draw the rectangle
        cv2.rectangle(img, (xmin, ymin), (xmax, ymax), color, 2)
        
        # Calculate label size for background box
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.5
        thickness = 1
        (label_w, label_h), baseline = cv2.getTextSize(label, font, font_scale, thickness)
        
        # Draw background label box
        cv2.rectangle(
            img, 
            (xmin, ymin - label_h - 4), 
            (xmin + label_w, ymin), 
            color, 
            -1 # filled
        )
        
        # Write text label (black text on green background)
        cv2.putText(
            img, 
            label, 
            (xmin, ymin - 4), 
            font, 
            font_scale, 
            (0, 0, 0), # BGR Black
            thickness, 
            cv2.LINE_AA
        )

    # 5. Save the annotated image
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), img)
    print(f"Detected {detection_count} items.")
    print(f"Saved annotated output image to: {output_path}")

if __name__ == "__main__":
    main()
