from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path
from typing import List # ADDED THIS IMPORT

class Settings(BaseSettings):
    scan_dir: Path             # Directory to monitor for new files
    output_dir: Path           # Directory to write YAML/Markdown outputs
    max_inflight: int = 1
    llm_api_key: str
    llm_model: str = "openai/gpt-4o"
    llm_base_url: str = "https://openrouter.ai/api/v1"
    scan_interval_s: int = 1  # Interval in seconds for the main loop's idle time
    # session_timeout_s: int = 15 # Removed, was for old watcher session logic
    # document_timeout_s: int = 300 # Removed, processor auto-timeout logic is gone

    # Facsimile Specific Settings
    facsimile_recipient_name: str = "-"
    facsimile_recipient_address: str = "-"
    facsimile_recipient_city: str = "-"
    
    facsimile_sender_name: str = "-"
    facsimile_sender_title: str = "-"
    facsimile_sender_fax: str = "-"
    facsimile_sender_phone: str = "-"

    facsimile_subject_prefix: str = "-"
    facsimile_closing_line: str = "-"
    facsimile_margot_flair: str = "-"
    facsimile_item_notes_placeholder: str = "-" # Placeholder for item.notes
    facsimile_item_margot_comment_placeholder: str = "-" # Placeholder for item.margot_comment

    # NEW FIELD FOR ANONYMIZATION
    anonymization_pairs: List[List[str]] = [] 
    # Example .env entry:
    # APP_ANONYMIZATION_PAIRS='[["John Doe", "PERSON_A"], ["Acme Corp", "COMPANY_X"]]'

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding='utf-8',
        extra='ignore'
    )
