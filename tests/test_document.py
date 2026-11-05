"""Tests for document processing functionality."""

import unittest

from snail_mail_parser.document.ocr import ocr_image
from snail_mail_parser.document.qr import scan_qr


class TestOCR(unittest.TestCase):
    """Test OCR functionality."""

    def test_ocr_function_exists(self):
        """Test that OCR function exists."""
        self.assertTrue(callable(ocr_image))


class TestQR(unittest.TestCase):
    """Test QR code functionality."""

    def test_qr_function_exists(self):
        """Test that QR scan function exists."""
        self.assertTrue(callable(scan_qr))


if __name__ == '__main__':
    unittest.main()