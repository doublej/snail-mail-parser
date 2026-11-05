.PHONY: init test lint clean build run api help

# Default target
help:
	@echo "Available commands:"
	@echo "  init     - Install dependencies"
	@echo "  test     - Run tests"
	@echo "  lint     - Run linting"
	@echo "  clean    - Clean build artifacts"
	@echo "  build    - Build the package"
	@echo "  run      - Run the main application"
	@echo "  api      - Run the web API server"
	@echo "  help     - Show this help message"

init:
	uv sync

test:
	python -m pytest tests/ -v

lint:
	python -m ruff check snail_mail_parser/ scripts/ tests/
	python -m mypy snail_mail_parser/ scripts/

clean:
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

build:
	uv build

run:
	python scripts/main.py

api:
	uvicorn snail_mail_parser.web.web_api:app --host 0.0.0.0 --port 8000 --reload