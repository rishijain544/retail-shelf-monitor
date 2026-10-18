import os
import sys
import argparse
import shutil
from pathlib import Path
from ultralytics import YOLO

def main():
    parser = argparse.ArgumentParser(description="Fine-tune YOLOv8n for Empty Space Detection")
    parser.add_argument(
        "--epochs", 
        type=int, 
        default=50, 
        help="Number of training epochs (default: 50)"
    )
    parser.add_argument(
        "--imgsz", 
        type=int, 
        default=640, 
        help="Input image size (default: 640)"
    )
    parser.add_argument(
        "--data",
        type=str,
        default=None,
        help="Path to your dataset's data.yaml (required; single class 0: empty)"
    )
    args = parser.parse_args()

    # Resolve absolute paths to ensure robustness across directories
    script_dir = Path(__file__).resolve().parent
    ml_dir = script_dir.parent
    
    # The dummy local dataset was removed; the real dataset (single class 0: empty)
    # is exported from Colab (Out-Of-Stock-detection), so --data must be supplied.
    if args.data is None:
        print(
            "Error: --data is required. Provide the path to your dataset's data.yaml "
            "(single class 0: empty), e.g. the exported Out-Of-Stock-detection dataset."
        )
        sys.exit(1)
    data_path = Path(args.data).resolve()

    if not data_path.exists():
        print(f"Error: Dataset configuration file not found at: {data_path}")
        sys.exit(1)

    print(f"Using dataset config: {data_path}")
    print(f"Training parameters: epochs={args.epochs}, imgsz={args.imgsz}")

    # 1. Load the pre-trained YOLOv8n model
    # This will automatically download yolov8n.pt if not present in the current working directory.
    print("Loading pre-trained YOLOv8n model...")
    model = YOLO("yolov8n.pt")

    # 2. Fine-tune the model
    # Note: YOLOv8 saves training outputs under runs/detect/trainX/
    print("Starting training pipeline...")
    results_train = model.train(
        data=str(data_path),
        epochs=args.epochs,
        imgsz=args.imgsz,
        plots=False,  # Turn off plots generation to speed up and reduce size
        device="cpu", # Force CPU training by default (change to 'cuda' or 0 for GPU)
    )

    # 3. Save the best weights to ml/weights/empty_space_best.pt
    # The best weights are located at model.trainer.best
    best_weights_src = Path(model.trainer.best)
    weights_dst_dir = ml_dir / "weights"
    weights_dst_dir.mkdir(parents=True, exist_ok=True)
    best_weights_dst = weights_dst_dir / "empty_space_best.pt"

    if best_weights_src.exists():
        shutil.copy(best_weights_src, best_weights_dst)
        print(f"Saved the best weights to: {best_weights_dst}")
    else:
        print(f"Warning: Could not locate best weights at {best_weights_src}")

    # 4. Print validation metrics
    print("\nEvaluating model on validation dataset...")
    # Load the best model weights for validation
    best_model = YOLO(str(best_weights_dst))
    metrics = best_model.val(data=str(data_path), device="cpu")

    # Print summary metrics
    print("\n" + "=" * 40)
    print("        VALIDATION METRICS SUMMARY")
    print("=" * 40)
    print(f"Precision (P):    {metrics.box.mp:.4f}")
    print(f"Recall (R):       {metrics.box.mr:.4f}")
    print(f"mAP50:            {metrics.box.map50:.4f}")
    print(f"mAP50-95:         {metrics.box.map:.4f}")
    print("=" * 40 + "\n")

if __name__ == "__main__":
    main()
