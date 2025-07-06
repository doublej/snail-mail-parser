#!/usr/bin/env bash

# Update package lists and install system dependencies
sudo apt-get update
sudo apt-get install -y poppler-utils

# Create a Python virtual environment and activate it
source .venv/bin/activate

# Upgrade pip and install Python dependencies
uv pip install --upgrade pip
uv pip install -r requirements.txt

# Create a template .env file
cat > .env.template << 'EOF'
# Directory to watch for incoming files
SCAN_DIR=/path/to/watch
# Directory to write output YAML and Markdown
OUTPUT_DIR=/path/to/output
# Session timeout (seconds)
SESSION_TIMEOUT_S=15
# Maximum concurrent processing jobs
MAX_INFLIGHT=1
# OpenAI/OpenRouter API settings
LLM_API_KEY=your_openrouter_api_key
LLM_MODEL=openai/gpt-4o
LLM_BASE_URL=https://openrouter.ai/api/v1
EOF

cp .env.template .env
echo "Setup complete. Please edit .env to configure paths and API credentials."