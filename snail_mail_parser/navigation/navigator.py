from pathlib import Path
from typing import List, Optional, Dict, Any
from ruamel.yaml import YAML
import json # For potentially loading LLM interaction logs if needed in future

# Assumed output structure:
# output_dir /
#   sender_name_1 /
#     doc_id_1_1 /
#       original_scans /
#         scan1.jpg
#       previews /
#         preview_scan1_0.jpg
#       llm_interaction_logs /
#         llm_interaction_timestamp.json
#       doc_id_1_1.yaml
#       doc_id_1_1.md
#     doc_id_1_2 /
#       ...
#   sender_name_2 /
#     ...


def get_all_senders(output_dir: Path) -> List[str]:
    """Lists all sender folders in the output directory."""
    if not output_dir.is_dir():
        return []
    return sorted([p.name for p in output_dir.iterdir() if p.is_dir()])


def get_letters_by_sender(output_dir: Path, sender_name: str) -> List[str]:
    """Lists all document IDs (subfolders) for a given sender."""
    sender_dir = output_dir / sender_name
    if not sender_dir.is_dir():
        return []
    return sorted([p.name for p in sender_dir.iterdir() if p.is_dir()])


def _get_doc_path(output_dir: Path, sender_name: str, doc_id: str) -> Optional[Path]:
    """Helper to get the path to a specific document's folder."""
    doc_path = output_dir / sender_name / doc_id
    return doc_path if doc_path.is_dir() else None


def get_letter_details_yaml(output_dir: Path, sender_name: str, doc_id: str) -> Optional[Dict[str, Any]]:
    """Loads and returns the YAML data for a specific document."""
    doc_path = _get_doc_path(output_dir, sender_name, doc_id)
    if not doc_path:
        return None
    
    yaml_file = doc_path / f"{doc_id}.yaml"
    if yaml_file.is_file():
        yaml = YAML()
        try:
            with open(yaml_file, 'r', encoding='utf-8') as yf:
                return yaml.load(yf)
        except Exception as e:
            print(f"Error loading YAML file {yaml_file}: {e}")
            return None
    return None


def get_letter_markdown_content(output_dir: Path, sender_name: str, doc_id: str) -> Optional[str]:
    """Loads and returns the Markdown content for a specific document."""
    doc_path = _get_doc_path(output_dir, sender_name, doc_id)
    if not doc_path:
        return None
        
    md_file = doc_path / f"{doc_id}.md"
    if md_file.is_file():
        try:
            with open(md_file, 'r', encoding='utf-8') as mf:
                return mf.read()
        except Exception as e:
            print(f"Error reading Markdown file {md_file}: {e}")
            return None
    return None


def get_letter_file_list(output_dir: Path, sender_name: str, doc_id: str, subfolder_name: str) -> List[Path]:
    """Helper to list files in a specific subfolder of a document's directory."""
    doc_path = _get_doc_path(output_dir, sender_name, doc_id)
    if not doc_path:
        return []
    
    target_dir = doc_path / subfolder_name
    if not target_dir.is_dir():
        return []
    
    return sorted([f for f in target_dir.iterdir() if f.is_file()])


def get_letter_original_scans(output_dir: Path, sender_name: str, doc_id: str) -> List[Path]:
    """Lists paths to original scans for a document."""
    return get_letter_file_list(output_dir, sender_name, doc_id, "original_scans")


def get_letter_previews(output_dir: Path, sender_name: str, doc_id: str) -> List[Path]:
    """Lists paths to previews for a document."""
    return get_letter_file_list(output_dir, sender_name, doc_id, "previews")


def get_letter_llm_interactions(output_dir: Path, sender_name: str, doc_id: str) -> List[Path]:
    """Lists paths to LLM interaction logs for a document."""
    return get_letter_file_list(output_dir, sender_name, doc_id, "llm_interaction_logs")


def get_letter_facsimile_path(output_dir: Path, sender_name: str, doc_id: str) -> Optional[Path]:
    """Gets the path to the facsimile.txt file for a specific document."""
    doc_path = _get_doc_path(output_dir, sender_name, doc_id)
    if not doc_path:
        return None
    
    facsimile_file = doc_path / f"{doc_id}_facsimile.txt"
    return facsimile_file if facsimile_file.is_file() else None

# Example usage (can be removed or placed under if __name__ == "__main__":)
# if __name__ == "__main__":
#     from settings import Settings # Assuming settings.py is in the same root
#     settings_instance = Settings()
#     current_output_dir = Path(settings_instance.output_dir)
# 
#     print("All Senders:")
#     senders = get_all_senders(current_output_dir)
#     print(senders)
# 
#     if senders:
#         example_sender = senders[0]
#         print(f"\nLetters for sender '{example_sender}':")
#         letters = get_letters_by_sender(current_output_dir, example_sender)
#         print(letters)
# 
#         if letters:
#             example_doc_id = letters[0]
#             print(f"\nDetails for letter '{example_doc_id}' from sender '{example_sender}':")
#             
#             details = get_letter_details_yaml(current_output_dir, example_sender, example_doc_id)
#             if details:
#                 print("YAML Details:", json.dumps(details, indent=2)) # Print dict as json for readability
# 
#             markdown_content = get_letter_markdown_content(current_output_dir, example_sender, example_doc_id)
#             if markdown_content:
#                 print("\nMarkdown Content (first 200 chars):", markdown_content[:200] + "...")
# 
#             originals = get_letter_original_scans(current_output_dir, example_sender, example_doc_id)
#             print("\nOriginal Scans:", [str(p) for p in originals])
# 
#             previews = get_letter_previews(current_output_dir, example_sender, example_doc_id)
#             print("\nPreviews:", [str(p) for p in previews])
# 
#             llm_logs = get_letter_llm_interactions(current_output_dir, example_sender, example_doc_id)
#             print("\nLLM Logs:", [str(p) for p in llm_logs])
# 
#     else:
#         print("No senders found in output directory.")
