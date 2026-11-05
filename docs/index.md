# Snail Mail Parser Documentation

Welcome to the Snail Mail Parser documentation. This system processes physical mail documents using OCR, QR code detection, and AI-powered content extraction.

## Features

- **Document Processing**: OCR and QR code detection
- **AI Integration**: LLM-powered content extraction and classification
- **Web Interface**: FastAPI-based web API
- **File Monitoring**: Automatic processing of new documents
- **Modular Design**: Clean separation of concerns

## Package Structure

- `snail_mail_parser.core`: Core functionality and settings
- `snail_mail_parser.document`: OCR and QR code processing
- `snail_mail_parser.ai`: LLM integration
- `snail_mail_parser.navigation`: Output processing and navigation
- `snail_mail_parser.web`: Web API and templates
- `snail_mail_parser.utils`: Utility functions

## Quick Start

```bash
# Install dependencies
make init

# Run tests
make test

# Run the application
make run
```

## API Reference

For detailed API documentation, see the module documentation.