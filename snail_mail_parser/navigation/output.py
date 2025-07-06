from ruamel.yaml import YAML
from snail_mail_parser.ai.llm import LetterLLMResponse, DocumentType
from enum import Enum
import re
import shutil
from pathlib import Path
from typing import List, Union, Optional # Add Union, Optional
from datetime import datetime # For facsimile date/time
from jinja2 import Environment, BaseLoader # For Jinja2 templating
import numpy as np # For handling preprocessed image arrays
# cv2 might be needed if we decide to use its functions for color conversion,
# but PIL.Image.fromarray and .convert should suffice.
# import cv2 

# Attempt to import Pillow and PyMuPDF, handle if not available for preview generation
try:
    from PIL import Image
except ImportError:
    Image = None # Placeholder if Pillow is not installed
    # Ensure Image.fromarray is available if Image is not None
    if Image:
        try:
            _ = Image.fromarray # Test availability
        except AttributeError:
            print("Warning: PIL.Image.fromarray not available. Preprocessed images might not be saved correctly.")


try:
    import fitz # PyMuPDF
except ImportError:
    fitz = None # Placeholder if PyMuPDF is not installed


def _sanitize_foldername(name: str) -> str:
    """Sanitizes a string to be a valid folder name."""
    if not name or name.strip() == "":
        name = "UnknownSender"
    # Remove characters not allowed in folder names on common OS
    # Keep alphanumeric, spaces, hyphens, underscores. Replace others.
    name = re.sub(r'[^\w\s-]', '', name).strip()
    # Replace spaces with underscores or remove them
    name = re.sub(r'\s+', '_', name)
    # Limit length if necessary (e.g., 50 chars)
    return name[:50] if len(name) > 50 else name


def save_output(
    letter: LetterLLMResponse, 
    original_items: List[Union[Path, Image.Image]], 
    settings,
    preprocessed_ocr_images: Optional[List[np.ndarray]] = None, # Existing parameter
    raw_ocr_text: Optional[str] = None # New parameter for raw OCR text
):
    """
    Save the LLM response, original scans, preprocessed OCR images, previews, YAML, and Markdown
    into a document-specific subfolder: output_dir/sender/doc_id/.
    """
    base_output_dir = Path(settings.output_dir)
    
    sender_name_for_folder = _sanitize_foldername(letter.sender if letter.sender else "UnknownSender")
    
    # Create the root directory for this specific document
    doc_root_dir = base_output_dir / sender_name_for_folder / letter.id
    doc_root_dir.mkdir(parents=True, exist_ok=True)

    # 1. Save Original Scans
    originals_dir = doc_root_dir / "original_scans"
    originals_dir.mkdir(exist_ok=True)
    if original_items:
        for idx, item in enumerate(original_items):
            try:
                if isinstance(item, Path):
                    if item.exists():
                        shutil.copy(item, originals_dir / item.name)
                    else:
                        print(f"Warning: Original scan file not found: {item}")
                elif isinstance(item, Image.Image):
                    # Save PIL Image as JPEG. Generate a unique name.
                    # Using original document ID and page index for clarity.
                    filename = f"{letter.id}_original_page_{idx + 1:02d}.jpg" # Changed extension to .jpg
                    
                    # Convert to RGB if image has alpha channel or is palette-based, as JPEG doesn't support these well.
                    image_to_save = item
                    if image_to_save.mode == 'RGBA' or image_to_save.mode == 'P' or image_to_save.mode == 'LA':
                        image_to_save = image_to_save.convert('RGB')
                    
                    image_to_save.save(originals_dir / filename, "JPEG", quality=90) # Changed format to JPEG, added quality
                else:
                    print(f"Warning: Unknown type in original_items at index {idx}: {type(item)}")
            except Exception as e:
                print(f"Error saving original item at index {idx} ({item}): {e}")

    # 2. Generate and Save Previews
    previews_dir = doc_root_dir / "previews"
    previews_dir.mkdir(exist_ok=True)
    preview_max_dim = 1024 # Max width/height for previews

    # Iterate over the files saved in the originals_dir
    saved_original_files = sorted([p for p in originals_dir.iterdir() if p.is_file()])

    if saved_original_files and Image: # Check if Image (Pillow) is available
        for idx, original_file_on_disk_path in enumerate(saved_original_files):
            preview_filename = f"preview_{original_file_on_disk_path.stem}_{idx}.jpg"
            preview_save_path = previews_dir / preview_filename
            try:
                img_to_save = Image.open(original_file_on_disk_path)
                
                img_to_save.thumbnail((preview_max_dim, preview_max_dim))
                if img_to_save.mode == 'RGBA' or img_to_save.mode == 'P' or img_to_save.mode == 'LA': # Convert to RGB for JPEG
                    img_to_save = img_to_save.convert('RGB')
                img_to_save.save(preview_save_path, "JPEG", quality=85)
            except Exception as e:
                print(f"Error generating preview for {original_file_on_disk_path.name}: {e}")
    elif not Image:
        print("Warning: Pillow not installed. Skipping preview generation.")
    elif not saved_original_files:
        print(f"No original files found in {originals_dir} to generate previews for.")

    # 3. Save Preprocessed OCR Images
    preprocessed_ocr_dir = doc_root_dir / "preprocessed_ocr_scans"
    preprocessed_ocr_dir.mkdir(exist_ok=True)

    if preprocessed_ocr_images and Image: # Check if Image (Pillow) and data are available
        for idx, ocr_img_np in enumerate(preprocessed_ocr_images):
            try:
                # The preprocessed image from ocr.py is a NumPy array (likely grayscale)
                # Convert NumPy array to PIL Image
                # preprocess_image_for_ocr returns a binary image (single channel)
                if ocr_img_np.ndim == 2: # Grayscale/Binary
                    pil_ocr_image = Image.fromarray(ocr_img_np, mode='L')
                elif ocr_img_np.ndim == 3 and ocr_img_np.shape[2] == 3: # BGR/RGB
                    # If it were BGR from OpenCV, convert to RGB for PIL
                    # pil_ocr_image = Image.fromarray(cv2.cvtColor(ocr_img_np, cv2.COLOR_BGR2RGB))
                    # However, preprocess_image_for_ocr returns a binary image.
                    # If it somehow returned color, this would be the place to handle it.
                    # For now, assuming it's 'L' mode compatible.
                    pil_ocr_image = Image.fromarray(ocr_img_np.astype(np.uint8)) # Ensure correct dtype
                else:
                    print(f"Warning: Preprocessed OCR image at index {idx} has unexpected shape: {ocr_img_np.shape}. Skipping.")
                    continue
                
                # Save as PNG to preserve quality, especially for binary images
                ocr_img_filename = f"preprocessed_ocr_page_{idx + 1:02d}.png"
                pil_ocr_image.save(preprocessed_ocr_dir / ocr_img_filename, "PNG")
            except Exception as e:
                print(f"Error saving preprocessed OCR image at index {idx}: {e}")
    elif not Image:
        print("Warning: Pillow not installed. Skipping preprocessed OCR image saving.")
    elif not preprocessed_ocr_images:
        print(f"No preprocessed OCR images provided for {letter.id}.")

    # 4. Save Raw OCR Text (New Section)
    raw_ocr_output_path = doc_root_dir / "raw_ocr_output.txt"
    if raw_ocr_text:
        try:
            with open(raw_ocr_output_path, 'w', encoding='utf-8') as f:
                f.write(raw_ocr_text)
        except Exception as e:
            print(f"Error saving raw OCR text for {letter.id}: {e}")
    else:
        print(f"Warning: No raw OCR text provided for {letter.id}. Skipping raw_ocr_output.txt creation.")


    # 5. Prepare data for YAML and Markdown (existing logic)
    payment_data_yaml = {}
    if letter.payment is not None:
        payment_data_yaml = {
            "iban": letter.payment.iban,
            "amount": letter.payment.amount,
            "due_date": letter.payment.due_date
        }
    else:
        payment_data_yaml = {
            "iban": None,
            "amount": None,
            "due_date": None
        }
    
    # Serialize letter.type to its value if it's an Enum instance
    letter_type_value = letter.type.value if isinstance(letter.type, Enum) else letter.type

    # Prepare data dict for YAML
    data = {
        "id": letter.id,
        "sender": letter.sender,
        "date_sent": letter.date_sent,
        "subject": letter.subject,
        "type": letter_type_value, # Use serialized value
        "content": letter.content,
        "qr_payloads": letter.qr_payloads,
        "payment": payment_data_yaml, # Use safely constructed payment_data_yaml
        "is_multipage_explicit": letter.is_multipage_explicit,
        "is_information_complete": letter.is_information_complete,
        "belongs_to_open_doc_id": letter.belongs_to_open_doc_id
    }

    # Write YAML file
    yaml_path = doc_root_dir / f"{letter.id}.yaml" # Save in doc_root_dir
    yaml = YAML()
    with open(yaml_path, 'w', encoding='utf-8') as yf: 
        yaml.dump(data, yf)

    # Prepare Markdown content with metadata header
    md_lines = []
    md_lines.append("---")
    md_lines.append(f"id: {letter.id}")
    md_lines.append(f"sender: {letter.sender}")
    md_lines.append(f"date_sent: {letter.date_sent}")
    md_lines.append(f"subject: {letter.subject}")
    md_lines.append(f"type: {letter_type_value}") # Use serialized value
    md_lines.append("qr_payloads:")
    # Pydantic model should ensure qr_payloads is a list, but check for None if it could be optional
    if letter.qr_payloads is not None:
        for payload in letter.qr_payloads:
            md_lines.append(f"  - {payload}")
    else:
        md_lines.append("  - None") # Or handle as empty list as appropriate
    
    # Handle payment information safely for Markdown
    md_lines.append("payment:")
    if letter.payment is not None:
        md_lines.append(f"  iban: {letter.payment.iban if letter.payment.iban is not None else 'None'}")
        md_lines.append(f"  amount: {letter.payment.amount if letter.payment.amount is not None else 'None'}")
        md_lines.append(f"  due_date: {letter.payment.due_date if letter.payment.due_date is not None else 'None'}")
    else:
        md_lines.append(f"  iban: None")
        md_lines.append(f"  amount: None")
        md_lines.append(f"  due_date: None")

    # Add multi-page fields to Markdown metadata
    md_lines.append(f"is_multipage_explicit: {letter.is_multipage_explicit}")
    md_lines.append(f"is_information_complete: {letter.is_information_complete}")
    md_lines.append(f"belongs_to_open_doc_id: {letter.belongs_to_open_doc_id if letter.belongs_to_open_doc_id is not None else 'None'}")

    md_lines.append("---\n")
    md_lines.append(letter.content if letter.content is not None else "") # Ensure content is not None

    md_text = "\n".join(md_lines)
    
    # The markdown-it rendering was for validation and can be removed as per instructions.

    # Write Markdown file
    md_path = doc_root_dir / f"{letter.id}.md" # Save in doc_root_dir
    with open(md_path, 'w', encoding='utf-8') as mf: 
        mf.write(md_text)

    # 6. Generate and Save Facsimile Output
    # The template string provided by the user
    facsimile_template_str = """===============================================================================
                                FACSIMILE TRANSMISSION
===============================================================================

TO:        {{ recipient.name | upper }}                          DATE:     {{ date }}
           {{ recipient.address }}               TIME:     {{ time }}
           {{ recipient.city }}                                  PAGES:    {{ page_info }}

FROM:      {{ sender.name }}                                 FAX:     {{ sender.fax }}
           {{ sender.title }}            TEL:     {{ sender.phone }}

RE:        {{ subject | upper }}
===============================================================================

{% for item in items %}
{{ loop.index }}. {{ item.sender }} – {{ item.header }}
   DATED: {{ item.date }}
   RE: {{ item.subject }}
   NOTES: {{ item.notes }}
{% endfor %}

===============================================================================
PHYSICAL LETTERS IN YOUR TRAY | RED TAB = REQUIRES ACTION | 
{{ closing_line }}
===============================================================================

{% for item in items %}
--- PAGE {{ loop.index + 1 }} ---

**{{ item.sender }} – FULL TEXT SUMMARY**

{{ item.body | indent(4) }}

**Margot’s Note:**  
{{ item.margot_comment }}

{% endfor %}

===============================================================================
{{ sender.name }}  •  TYPED AT {{ typed_time }}  •  {{ margot_flair }}
===============================================================================
"""
    try:
        now = datetime.now()
        current_date_str = now.strftime("%Y-%m-%d")
        current_time_str = now.strftime("%H:%M:%S")

        # For a single letter, the facsimile has 1 cover page + 1 content page
        num_content_pages = 1 
        total_facsimile_pages = 1 + num_content_pages 
        page_info_str = f"{total_facsimile_pages} (INC. COVER)"

        facsimile_data = {
            "recipient": { # Facsimile recipient details from settings
                "name": settings.facsimile_recipient_name,
                "address": settings.facsimile_recipient_address,
                "city": settings.facsimile_recipient_city,
            },
            "sender": { # Facsimile sender details (MARGOT) from settings
                "name": settings.facsimile_sender_name,
                "title": settings.facsimile_sender_title,
                "fax": settings.facsimile_sender_fax,
                "phone": settings.facsimile_sender_phone,
            },
            "date": current_date_str,
            "time": current_time_str,
            "typed_time": current_time_str, # Using current time for "TYPED AT"
            "page_info": page_info_str,
            "subject": f"{settings.facsimile_subject_prefix}: {letter.subject if letter.subject else 'N/A'}",
            "items": [ # The template loops through 'items'. For a single letter, this is a list with one item.
                {
                    "sender": letter.sender if letter.sender else "Unknown Sender",
                    "header": letter.subject if letter.subject else "N/A", # Using letter's subject as header
                    "date": letter.date_sent if letter.date_sent else "N/A",
                    "subject": letter.subject if letter.subject else "N/A", # Letter's subject again for RE:
                    "notes": settings.facsimile_item_notes_placeholder, # Placeholder from settings
                    "body": letter.content if letter.content else "No content available.",
                    "margot_comment": settings.facsimile_item_margot_comment_placeholder, # Placeholder from settings
                }
            ],
            "closing_line": settings.facsimile_closing_line,
            "margot_flair": settings.facsimile_margot_flair,
        }

        env = Environment(loader=BaseLoader())
        template = env.from_string(facsimile_template_str)
        facsimile_content = template.render(facsimile_data)

        facsimile_path = doc_root_dir / f"{letter.id}_facsimile.txt"
        with open(facsimile_path, 'w', encoding='utf-8') as ff:
            ff.write(facsimile_content)
            
    except Exception as e:
        print(f"Error generating facsimile for {letter.id}: {e}")
