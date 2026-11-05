"""Tests for core functionality."""

import unittest
from queue import Queue

from snail_mail_parser.core.settings import Settings
from snail_mail_parser.core.processor import Processor


class TestSettings(unittest.TestCase):
    """Test settings module."""

    def test_settings_initialization(self):
        """Test that settings can be initialized."""
        settings = Settings()
        self.assertIsInstance(settings, Settings)


class TestProcessor(unittest.TestCase):
    """Test processor module."""

    def test_processor_initialization(self):
        """Test that processor can be initialized."""
        settings = Settings()
        queue = Queue()
        processor = Processor(settings, queue)
        self.assertIsInstance(processor, Processor)


if __name__ == '__main__':
    unittest.main()