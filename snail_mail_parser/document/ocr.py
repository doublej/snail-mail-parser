from pathlib import Path
from typing import Tuple, List # Added List
from PIL import Image
import pytesseract

import cv2
import numpy as np
from PIL import Image, ImageFilter, ImageOps
from matplotlib import pyplot as plt


def display_image(title, image):


    # Get image dimensions (height, width)
    if len(image.shape) == 2:  # Grayscale
        img_height, img_width = image.shape
    else:  # Color
        img_height, img_width, _ = image.shape

    # Get DPI for the figure
    dpi = plt.rcParams['figure.dpi']

    # Calculate figure size in inches
    figsize_width = img_width / dpi
    figsize_height = img_height / dpi

    plt.figure(figsize=(figsize_width, figsize_height))
    if len(image.shape) == 2:  # Grayscale
        plt.imshow(image, cmap='gray')
    else:  # Color
        plt.imshow(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
    plt.title(title)
    plt.axis('off')
    plt.show()


def preprocess_image_for_ocr(pil_image):
    """
    Preprocess a PIL image to enhance OCR accuracy.
    Steps:
    1. Convert to grayscale.
    2. Apply median filter to reduce noise.
    3. Normalize the image intensity.
    4. Convert to OpenCV format for further processing.
    5. Apply adaptive thresholding.
    6. Deskew the image to correct any rotation.
    7. Convert back to PIL image.
    """
    # 2. Convert to grayscale
    # pil to cv2

    gray = cv2.cvtColor(np.array(pil_image), cv2.COLOR_BGR2GRAY)

    # 3. Noise removal
    gray = cv2.medianBlur(gray, 3)

    # 4. Binarization
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    return binary


def ocr_image(image: Image.Image) -> Tuple[str, float, np.ndarray]: # Updated return type hint
    """
    Perform OCR on an image file.
    Returns extracted text, mean confidence, and the preprocessed image (numpy array).
    """

    preprocessed_image_cv = preprocess_image_for_ocr(image) # Renamed variable for clarity

    text = pytesseract.image_to_string(preprocessed_image_cv) # Use the preprocessed image for OCR

    # Get OCR data to compute confidence scores
    data = pytesseract.image_to_data(preprocessed_image_cv, output_type=pytesseract.Output.DICT) # Use preprocessed image

    confs = [int(c) for c in data.get('conf', []) if c != '-1']

    mean_conf = sum(confs) / len(confs) if confs else 0.0

    return text, mean_conf, preprocessed_image_cv
