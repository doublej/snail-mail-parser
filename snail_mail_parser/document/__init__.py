"""Document processing modules."""

from .ocr import ocr_image
from .qr import scan_qr

__all__ = ["ocr_image", "scan_qr"]