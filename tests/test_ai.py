"""Tests for AI functionality."""

import unittest

from snail_mail_parser.ai.llm import DocumentType, LetterLLMResponse


class TestLLM(unittest.TestCase):
    """Test LLM functionality."""

    def test_document_type_enum_exists(self):
        """Test that DocumentType enum exists."""
        self.assertTrue(hasattr(DocumentType, 'LETTER'))

    def test_letter_llm_response_exists(self):
        """Test that LetterLLMResponse class exists."""
        self.assertTrue(hasattr(LetterLLMResponse, '__init__'))


if __name__ == '__main__':
    unittest.main()