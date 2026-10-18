import cv2
import numpy as np
from pathlib import Path
from typing import Tuple, List

class ShelfPreprocessor:
    """
    OpenCV helper class for preprocessing retail shelf camera images.
    Features: grayscale conversion, noise reduction, cropping, and perspective correction.
    """
    
    @staticmethod
    def load_image(image_path: str) -> np.ndarray:
        """Loads an image from the filesystem."""
        img = cv2.imread(image_path)
        if img is None:
            raise FileNotFoundError(f"Could not load image at {image_path}")
        return img

    @staticmethod
    def to_grayscale(image: np.ndarray) -> np.ndarray:
        """Converts BGR image to grayscale."""
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    @staticmethod
    def apply_blur(image: np.ndarray, kernel_size: Tuple[int, int] = (5, 5)) -> np.ndarray:
        """Applies Gaussian Blur to reduce high-frequency noise."""
        return cv2.GaussianBlur(image, kernel_size, 0)

    @staticmethod
    def crop_roi(image: np.ndarray, x: int, y: int, w: int, h: int) -> np.ndarray:
        """Crops a region of interest (ROI) representing a specific shelf aisle or level."""
        return image[y:y+h, x:x+w]

    @staticmethod
    def warp_perspective(image: np.ndarray, pts_src: List[List[float]], output_size: Tuple[int, int]) -> np.ndarray:
        """
        Corrects perspective distortion from a tilted shelf camera.
        
        Args:
            image: Input image.
            pts_src: List of 4 points defining the shelf region in the source image (top-left, top-right, bottom-right, bottom-left).
            output_size: Tuple (width, height) of the output rectified image.
        """
        pts_src = np.float32(pts_src)
        
        # Define destination points for a flat front-facing rectangle
        w, h = output_size
        pts_dst = np.float32([
            [0, 0],
            [w - 1, 0],
            [w - 1, h - 1],
            [0, h - 1]
        ])
        
        # Calculate transformation matrix and warp
        matrix = cv2.getPerspectiveTransform(pts_src, pts_dst)
        return cv2.warpPerspective(image, matrix, output_size)

# Demonstration of execution
if __name__ == "__main__":
    print("ShelfPreprocessor library initialized.")
    # Create an artificial test image if run directly
    dummy_img = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.putText(dummy_img, 'Shelf Test', (200, 240), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
    
    # Run test preprocessing pipeline
    processed = ShelfPreprocessor.to_grayscale(dummy_img)
    processed = ShelfPreprocessor.apply_blur(processed)
    
    print("Preprocessing demo finished. Target resolution:", processed.shape)
