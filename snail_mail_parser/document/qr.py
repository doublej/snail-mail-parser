from pathlib import Path
from typing import List, Union # Added Union
import cv2
import numpy as np # Added for image conversion
from PIL import Image # Added for type hinting and instance check

# Initialize QRCodeDetector. It's good practice to create it once if used frequently.
# However, for simplicity in this function structure, creating it per call is also acceptable,
# but less performant if scan_qr is called very often.
# For now, let's initialize it globally within the module.
_qr_detector = None

def get_qr_detector():
    global _qr_detector
    if _qr_detector is None:
        _qr_detector = cv2.QRCodeDetector()
    return _qr_detector

def scan_qr(path_or_image: Union[Path, Image.Image]) -> List[str]:
    """
    Decode all QR codes in an image file or PIL Image object using OpenCV's QRCodeDetector.
    Returns a list of decoded strings.
    """
    image_np = None
    if isinstance(path_or_image, Path):
        img_path_str = str(path_or_image)
        image_cv2 = cv2.imread(img_path_str)
        if image_cv2 is None:
            print(f"Warning: Could not read image from path: {path_or_image}")
            return []
        image_np = image_cv2
    elif isinstance(path_or_image, Image.Image):
        pil_image = path_or_image
        # Convert PIL Image to OpenCV format (NumPy array)
        # PIL Images are RGB(A), OpenCV uses BGR by default
        if pil_image.mode == 'RGBA':
            pil_image = pil_image.convert('RGB')  # Convert RGBA to RGB first
        
        image_np = np.array(pil_image)
        # Convert RGB to BGR
        image_np = cv2.cvtColor(image_np, cv2.COLOR_RGB2BGR)
    else:
        print(f"Error: scan_qr received an unsupported type: {type(path_or_image)}")
        return []

    if image_np is None: # Should be caught by earlier checks, but as a safeguard
        return []

    detector = get_qr_detector()
    
    # detectAndDecodeMulti is used for potentially multiple QR codes
    # It returns:
    #   retval: boolean, True if QR codes were found and decoded.
    #   decoded_info: A tuple of decoded strings.
    #   points: A tuple of NumPy arrays, each representing the vertices of a detected QR code.
    #   straight_qrcode: A tuple of rectified QR code images.
    retval, decoded_info, points, straight_qrcode = detector.detectAndDecodeMulti(image_np)

    if retval and decoded_info is not None:
        # decoded_info can be a tuple of strings. Filter out any empty strings.
        return [info for info in decoded_info if info]
    
    return []
