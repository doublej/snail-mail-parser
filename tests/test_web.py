"""Tests for web functionality."""

import unittest

from snail_mail_parser.web.web_api import app


class TestWebAPI(unittest.TestCase):
    """Test web API functionality."""

    def test_fastapi_app_exists(self):
        """Test that FastAPI app exists."""
        self.assertIsNotNone(app)


if __name__ == '__main__':
    unittest.main()