import io
import traceback
from datetime import datetime
from queue import Queue
# import time # No longer needed for last_seen_timestamp
import shutil # Added for rmtree in merge operation
from pathlib import Path # Added for merge operation
from typing import List, Dict, Any, Tuple, Union, Optional  # For type hints

import fitz
from PIL import Image # Added for handling image files consistently
import pytesseract # Added for direct OCR on PIL Image
import numpy as np # For type hinting preprocessed images

from ocr import ocr_image  # ocr_image now returns text, confidence, and preprocessed_image
from qr import scan_qr
from llm import classify_document, LetterLLMResponse, DocumentType, _sanitize_foldername_llm # Added _sanitize_foldername_llm
from output import save_output, _sanitize_foldername # Added _sanitize_foldername
import navigator # Added for merge operation
from enum import Enum # Added for Enum check

class Processor:
    def __init__(self, settings, queue: Queue):
        self.settings = settings
        self.queue = queue
        self.doc_seq = 0
        # Stores doc_id -> {'letter_data': LetterLLMResponse, 'items_to_save': List[Union[Path, Image.Image]], 'preprocessed_ocr_images': List[np.ndarray], 'raw_ocr_text_content': str}
        self.open_documents = {}  
        # self.document_timeout_s removed

    def get_new_doc_id(self) -> str:
        """Generate a new document ID: YYYYMMDD-XXXX sequence."""
        self.doc_seq += 1
        return f"{datetime.now().strftime('%Y%m%d')}-{self.doc_seq:04d}"

    def _prepare_open_documents_summary(self):
        summary = []
        for doc_id, data in self.open_documents.items():
            letter_data = data['letter_data']
            summary.append({
                'id': doc_id,
                'subject': letter_data.subject,
                'content_snippet': letter_data.content[:200] # First 200 chars as snippet
            })
        return summary

    def process_document_pages(self, pages):
        """
        Processes a list of pages (currently expected to be a single page/file path).
        Manages multi-page document assembly based on LLM feedback.
        """
        if not pages or not isinstance(pages, list) or len(pages) != 1:
            print(f"Processor: Not implemented. Expected a single page path in a list, but got: {pages}. Skipping.")
            return
        
        current_page_path = pages[0]
        print(f"Processor: Starting processing for page: {current_page_path.name}")

        text_all = "" # This will accumulate raw OCR text for the current input file (could be multi-page PDF)
        qr_payloads = []
        # This list will hold PIL.Image.Image objects to be processed by OCR/QR
        images_to_process_pil: List[Image.Image] = []
        # This list will hold the preprocessed images (np.ndarray) from OCR for saving
        preprocessed_ocr_images_for_saving: List[np.ndarray] = []

        if current_page_path.suffix.lower() == '.pdf':
            print(f"Processor: Converting PDF {current_page_path.name} to images...")
            # ocr_pdf now returns a list of PIL.Image objects
            images_from_pdf_doc = convert_pdf_to_pil(current_page_path)
            if images_from_pdf_doc:
                images_to_process_pil.extend(images_from_pdf_doc)
            else:
                print(f"Processor: No images extracted from PDF {current_page_path}. Skipping.")
                return
        else:  # Original file is an image
            print(f"Processor: Loading image file: {current_page_path.name}")
            try:
                # Open the image file and add it to the list for consistent processing
                img = Image.open(current_page_path)
                images_to_process_pil.append(img)
            except Exception as e:
                print(f"Error opening image file {current_page_path}: {e}")
                return  # Skip if image can't be opened

        if not images_to_process_pil:
            print(f"Processor: No images to process for {current_page_path.name}. Skipping.")
            return

        # Process each PIL image (whether from PDF or an original image file)
        for i, pil_image_page in enumerate(images_to_process_pil):
            page_description = f"page {i+1} of {current_page_path.name}" if len(images_to_process_pil) > 1 else current_page_path.name

            # Perform OCR on the PIL image
            print(f"Processor: OCRing {page_description}")
            # ocr_image now returns: text, mean_conf, preprocessed_image_cv
            page_text, mean_conf, preprocessed_image_cv = ocr_image(pil_image_page)
            preprocessed_ocr_images_for_saving.append(preprocessed_image_cv) # Store for saving

            try:
                text_all += page_text + "\n" # Accumulate raw OCR text for the current input file
            except Exception as e:
                print(f"Error during OCR for {page_description}: {e}")

            # Perform QR scan on the PIL image
            print(f"Processor: Scanning QR codes for {page_description}")
            try:
                page_qr_payloads = scan_qr(pil_image_page)  # scan_qr accepts PIL.Image
                if page_qr_payloads:
                    # Ensure QR payloads are unique if that's a requirement, though extend is fine.
                    for payload in page_qr_payloads:
                        if payload not in qr_payloads:
                            qr_payloads.append(payload)
            except Exception as e:
                print(f"Error during QR scan for {page_description}: {e}")

        text_all = text_all.strip()  # Remove leading/trailing whitespace from aggregated text

        if not text_all and not qr_payloads:
            print(f"Processor: No text or QR codes found for page {current_page_path}. Skipping.")
            return

        # Generate a candidate ID for this new page.
        # This ID will become the document's ID if it's a new document,
        # or it might be superseded if this page belongs to an existing open document.
        candidate_page_id = self.get_new_doc_id()

        open_docs_summary = self._prepare_open_documents_summary()

        print(f"Processor: Classifying page {candidate_page_id} with LLM. Open docs summary: {len(open_docs_summary)}")
        llm_response = classify_document(
            text_all,
            qr_payloads,
            candidate_page_id, # Pass the new page's candidate ID
            self.settings,
            open_docs_summary
        )

        # Ensure the ID from LLM response is the one we manage, not one LLM might make up for the current page.
        # The llm_response.id should be the candidate_page_id we sent.
        # If belongs_to_open_doc_id is set, that takes precedence for document identity.

        final_doc_id_to_process = llm_response.id # This should be candidate_page_id

        if llm_response.belongs_to_open_doc_id and llm_response.belongs_to_open_doc_id in self.open_documents:
            existing_doc_id = llm_response.belongs_to_open_doc_id
            print(f"Processor: Page {candidate_page_id} (LLM ID: {llm_response.id}) belongs to open document {existing_doc_id}.")

            open_doc_entry = self.open_documents[existing_doc_id]
            existing_letter_data = open_doc_entry['letter_data']

            # Append current page's items (PIL images for PDF, or Path for image)
            open_doc_entry.setdefault('items_to_save', [])
            open_doc_entry.setdefault('preprocessed_ocr_images', []) # Ensure list exists
            open_doc_entry.setdefault('raw_ocr_text_content', '') # Ensure raw OCR text content exists

            items_from_current_page: List[Union[Path, Image.Image]]
            if current_page_path.suffix.lower() == '.pdf':
                items_from_current_page = images_to_process_pil # This is List[Image.Image]
            else:
                items_from_current_page = [current_page_path] # This is List[Path]

            open_doc_entry['items_to_save'].extend(items_from_current_page)
            open_doc_entry['preprocessed_ocr_images'].extend(preprocessed_ocr_images_for_saving) # Append preprocessed images
            open_doc_entry['raw_ocr_text_content'] += f"\n\n--- Page Break (Original Page ID: {llm_response.id}) ---\n\n{text_all}" # Append raw OCR text

            # Append content (ensure newline separation)
            existing_letter_data.content += f"\n\n--- Page Break (Original Page ID: {llm_response.id}) ---\n\n{llm_response.content}"
            # Merge QR payloads (avoid duplicates)
            for qr in llm_response.qr_payloads:
                if qr not in existing_letter_data.qr_payloads:
                    existing_letter_data.qr_payloads.append(qr)

            # Update completeness based on the new page. If any page says it's not complete, the doc isn't.
            # Or, if the new page says it IS complete, and previous also thought so, it remains complete.
            # A more sophisticated logic might be needed if LLM gives conflicting "is_complete" for same doc.
            # For now, if the new page says it's not complete, the whole document is marked not complete.
            if not llm_response.is_information_complete:
                existing_letter_data.is_information_complete = False
            # If the new page IS complete, but the existing doc was marked incomplete, it remains incomplete
            # unless this page explicitly completes it (e.g. "page 2 of 2").
            # The current Pydantic model doesn't capture "final page" explicitly, relies on is_information_complete.

            # open_doc_entry['last_seen_timestamp'] = time.time() # Removed

            if existing_letter_data.is_information_complete:
                print(f"Processor: Document {existing_doc_id} now considered complete. Saving.")
                items_to_save_list = open_doc_entry.get('items_to_save', [])
                preprocessed_images_list = open_doc_entry.get('preprocessed_ocr_images', [])
                raw_ocr_text_to_save = open_doc_entry.get('raw_ocr_text_content', '')
                save_output(
                    letter=existing_letter_data, 
                    original_items=items_to_save_list, 
                    settings=self.settings,
                    preprocessed_ocr_images=preprocessed_images_list,
                    raw_ocr_text=raw_ocr_text_to_save
                )
                del self.open_documents[existing_doc_id]
                print(f"Successfully processed and closed multi-page document: {existing_doc_id}")
            else:
                print(f"Processor: Document {existing_doc_id} updated, still awaiting more pages.")

        elif not llm_response.is_information_complete or llm_response.is_multipage_explicit:
            # This is a new multi-page document or the first page of one.
            # The llm_response.id (which is candidate_page_id) becomes the ID for this new open document.
            actual_doc_id = llm_response.id
            print(f"Processor: Page {actual_doc_id} is part of a new multi-page document. Keeping it open.")
            self.open_documents[actual_doc_id] = {
                'letter_data': llm_response,
                'items_to_save': images_to_process_pil if current_page_path.suffix.lower() == '.pdf' else [current_page_path],
                'preprocessed_ocr_images': preprocessed_ocr_images_for_saving, # Store preprocessed images
                'raw_ocr_text_content': text_all # Store raw OCR text for this initial page(s)
            }
        else:
            # This is a single, complete document.
            actual_doc_id = llm_response.id
            print(f"Processor: Page {actual_doc_id} is a complete single-page document. Saving.")

            items_for_saving_originals: List[Union[Path, Image.Image]]
            if current_page_path.suffix.lower() == '.pdf':
                # For PDFs, images_to_process_pil contains the PIL images of its pages.
                items_for_saving_originals = images_to_process_pil
            else:
                # For original image files, pass the original file path.
                items_for_saving_originals = [current_page_path]

            save_output(
                letter=llm_response, 
                original_items=items_for_saving_originals, 
                settings=self.settings,
                preprocessed_ocr_images=preprocessed_ocr_images_for_saving, # Pass preprocessed images
                raw_ocr_text=text_all # Pass raw OCR text for this single document
            )
            print(f"Successfully processed single-page document: {actual_doc_id}")


    # def check_open_document_timeouts(self): # Method entirely removed

    def flush_open_documents(self):
        """Saves all currently open documents."""
        print(f"Processor: Flushing all {len(self.open_documents)} open documents...")
        if not self.open_documents:
            print("Processor: No open documents to flush.")
            return
            
        for doc_id in list(self.open_documents.keys()): 
            print(f"Processor: Flushing document {doc_id}.")
            open_doc_entry = self.open_documents[doc_id]
            data_to_save = open_doc_entry['letter_data']
            items_to_save_list = open_doc_entry.get('items_to_save', [])
            preprocessed_images_list = open_doc_entry.get('preprocessed_ocr_images', [])
            raw_ocr_text_to_save = open_doc_entry.get('raw_ocr_text_content', '')

            data_to_save.is_information_complete = True 
            save_output(
                letter=data_to_save, 
                original_items=items_to_save_list, 
                settings=self.settings,
                preprocessed_ocr_images=preprocessed_images_list,
                raw_ocr_text=raw_ocr_text_to_save
            )
            del self.open_documents[doc_id]
            print(f"Successfully flushed document: {doc_id}")
        print("Processor: All open documents flushed.")

    def get_open_documents_summary(self) -> List[Dict[str, Any]]:
        """Returns a summary of currently open multi-page documents."""
        summary_list = []
        for doc_id, data in self.open_documents.items():
            letter_data = data['letter_data']
            summary_list.append({
                "id": doc_id,
                "subject": letter_data.subject,
                "sender": letter_data.sender,
                "type": letter_data.type.value if isinstance(letter_data.type, Enum) else letter_data.type,
                "page_count": len(data.get('items_to_save', [])),
                "is_information_complete_llm": letter_data.is_information_complete, # LLM's original assessment
                "is_multipage_explicit_llm": letter_data.is_multipage_explicit # LLM's original assessment
            })
        return summary_list

    def force_complete_open_document(self, doc_id: str) -> bool:
        """Forces a specific open document to be considered complete, saves, and closes it."""
        if doc_id in self.open_documents:
            print(f"Processor: Force completing document {doc_id}.")
            open_doc_entry = self.open_documents[doc_id]
            data_to_save = open_doc_entry['letter_data']
            items_to_save_list = open_doc_entry.get('items_to_save', [])
            preprocessed_images_list = open_doc_entry.get('preprocessed_ocr_images', [])
            raw_ocr_text_to_save = open_doc_entry.get('raw_ocr_text_content', '')

            data_to_save.is_information_complete = True # Mark as complete by user action
            save_output(
                letter=data_to_save, 
                original_items=items_to_save_list, 
                settings=self.settings,
                preprocessed_ocr_images=preprocessed_images_list,
                raw_ocr_text=raw_ocr_text_to_save
            )
            del self.open_documents[doc_id]
            print(f"Successfully force-completed and closed document: {doc_id}")
            return True
        else:
            print(f"Processor: Cannot force complete. Document ID {doc_id} not found in open documents.")
            return False

    def merge_processed_document_into_open_document(
        self, 
        target_open_doc_id: str, 
        source_sender_name: str, 
        source_doc_id: str
    ) -> bool:
        """
        Merges an already processed and saved standalone document (source) 
        into an existing open multi-page document (target).
        The source document's folder will be deleted after successful merge.
        """
        output_base_dir = Path(self.settings.output_dir) # Use output_dir from settings

        if target_open_doc_id not in self.open_documents:
            print(f"Processor: Target open document {target_open_doc_id} not found.")
            return False

        source_yaml_data = navigator.get_letter_details_yaml(output_base_dir, source_sender_name, source_doc_id)
        if not source_yaml_data:
            print(f"Processor: Source document {source_doc_id} (sender: {source_sender_name}) YAML details not found.")
            return False
        
        # Reconstruct source LetterLLMResponse to easily access fields with correct types
        try:
            # Ensure 'type' from YAML is converted back to DocumentType Enum if needed
            source_type_str = source_yaml_data.get("type")
            if source_type_str:
                 source_yaml_data["type"] = DocumentType(source_type_str)
            # Same for payment if it's a dict
            if isinstance(source_yaml_data.get("payment"), dict):
                from llm import Payment # Local import for pydantic model if not globally available
                source_yaml_data["payment"] = Payment.model_validate(source_yaml_data["payment"])

            source_letter = LetterLLMResponse.model_validate(source_yaml_data)
        except Exception as e:
            print(f"Processor: Error reconstructing source document {source_doc_id} from YAML: {e}")
            return False
        
        source_content = source_letter.content
        source_qr_payloads = source_letter.qr_payloads
        source_original_scans_paths = navigator.get_letter_original_scans(output_base_dir, source_sender_name, source_doc_id)
        
        target_doc_entry = self.open_documents[target_open_doc_id]
        target_letter_data = target_doc_entry['letter_data']

        target_letter_data.content += f"\n\n--- Merged Page (Original Source ID: {source_doc_id}, Sender: {source_letter.sender}) ---\n\n{source_content}"
        
        if source_qr_payloads:
            for qr in source_qr_payloads:
                if qr not in target_letter_data.qr_payloads:
                    target_letter_data.qr_payloads.append(qr)
        
        if source_original_scans_paths: # These are List[Path] from navigator
            target_doc_entry.setdefault('items_to_save', []).extend(source_original_scans_paths) # Ensure items_to_save exists
        
        # Note: We are not merging raw_ocr_text_content from the source document here.
        # The raw_ocr_text_content for the target document should only reflect the OCR
        # performed on its own pages as they were processed. The LLM content from the
        # merged document is appended to the target's LLM content.

        print(f"Processor: Merged source document {source_sender_name}/{source_doc_id} into {target_open_doc_id}.")

        source_doc_folder_path = navigator._get_doc_path(output_base_dir, source_sender_name, source_doc_id)
        if source_doc_folder_path and source_doc_folder_path.exists():
            try:
                shutil.rmtree(source_doc_folder_path)
                print(f"Processor: Deleted original folder for merged document {source_doc_folder_path}.")
            except Exception as e:
                print(f"Processor: Error deleting folder for merged document {source_doc_folder_path}: {e}")
        
        return True

    def process_next_item_from_queue(self):
        """
        Gets one item from the queue (if available without blocking) and processes it.
        Returns True if an item was processed, False otherwise.
        """
        if not self.queue.empty():
            pages_to_process = self.queue.get_nowait() # Get item without blocking
            if pages_to_process: # Check if it's not a None sentinel or empty list by mistake
                self.process_document_pages(pages_to_process)
                self.queue.task_done() # Signal that the item from queue is processed
                return True
        return False

    # The run() method and _worker() method are removed as processing is now driven by main.py


def convert_pdf_to_pil(path: Path) -> List[Image.Image]: # Changed return type hint to List[Image.Image]
    """
    Convert PDF to a list of PIL Image objects, one for each page.
    Does NOT perform OCR or QR scanning itself.
    Returns a list of PIL.Image objects.
    """
    images_from_pdf: List[Image.Image] = []
    try:
        doc = fitz.open(path)
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            pix = page.get_pixmap(
                alpha=False,
                dpi=300
            )  # Render without alpha for consistency

            img_bytes = pix.tobytes("png")  # Convert to PNG bytes

            # It's good practice to ensure the image mode is suitable for pytesseract, e.g. 'L' or 'RGB'
            pil_image = Image.open(io.BytesIO(img_bytes)).convert('RGB')
            images_from_pdf.append(pil_image)
        doc.close()
        # Debug page show and input were here, now removed as per plan.
    except Exception as e:
        print(f"Error converting PDF {path} page to image: {e}")
    return images_from_pdf
