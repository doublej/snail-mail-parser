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

from snail_mail_parser.document.ocr import ocr_image  # ocr_image now returns text, confidence, and preprocessed_image
from snail_mail_parser.document.qr import scan_qr
from snail_mail_parser.ai.llm import classify_document, LetterLLMResponse, DocumentType, _sanitize_foldername_llm # Added _sanitize_foldername_llm
from snail_mail_parser.navigation.output import save_output, _sanitize_foldername # Added _sanitize_foldername
from snail_mail_parser.navigation import navigator # Added for merge operation
from enum import Enum # Added for Enum check

class Processor:
    def __init__(self, settings, queue: Queue):
        self.settings = settings
        self.queue = queue
        self.doc_sequences: Dict[str, int] = {} # Stores date_str (YYYYMMDD) -> sequence_num
        self.llm_request_seq: int = 0 # Counter for unique LLM request IDs
        # Stores doc_id -> {'letter_data': LetterLLMResponse, 
        #                   'items_to_save': List[Union[Path, Image.Image]], 
        #                   'preprocessed_ocr_images': List[np.ndarray], 
        #                   'raw_ocr_text_content': str,
        #                   'merged_page_constituent_infos': List[Dict[str,str]]}
        self.open_documents = {}  
        # self.document_timeout_s removed

    def _get_llm_request_id(self) -> str:
        """Generates a unique ID for an LLM processing request."""
        self.llm_request_seq += 1
        return f"req_{datetime.now().strftime('%Y%m%d%H%M%S')}_{self.llm_request_seq:03d}"

    def get_new_doc_id(self, document_date_str: Optional[str] = None) -> str:
        """
        Generate a new document ID: YYYYMMDD-XXXX sequence.
        The YYYYMMDD part is based on the document_date_str if provided and valid,
        otherwise defaults to the current system date.
        """
        doc_date = None
        if document_date_str and document_date_str != "Unknown":
            try:
                # LLM is prompted for "YYYY-MM-DD"
                doc_date = datetime.strptime(document_date_str, '%Y-%m-%d').date()
            except ValueError:
                print(f"Warning: Could not parse document date string '{document_date_str}'. Defaulting to current date for ID.")
                doc_date = datetime.now().date()
        else:
            doc_date = datetime.now().date()
        
        date_prefix_for_id = doc_date.strftime('%Y%m%d')
        
        current_seq_for_date = self.doc_sequences.get(date_prefix_for_id, 0)
        current_seq_for_date += 1
        self.doc_sequences[date_prefix_for_id] = current_seq_for_date
        
        return f"{date_prefix_for_id}-{current_seq_for_date:04d}"

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
            print(f"Processor: No text or QR codes found for {current_page_path.name}. Skipping.")
            return

        # Generate a temporary ID for this specific LLM request.
        llm_request_id = self._get_llm_request_id()

        open_docs_summary = self._prepare_open_documents_summary()

        print(f"Processor: Classifying content from {current_page_path.name} (LLM Request ID: {llm_request_id}). Open docs summary: {len(open_docs_summary)}")
        llm_response = classify_document(
            text_all,
            qr_payloads,
            llm_request_id, # Pass the temporary request ID to LLM for logging and initial response.id
            self.settings,
            open_docs_summary
        )

        # llm_response.id is currently llm_request_id.
        # Now, generate the actual, potentially date-aware document ID.
        actual_doc_id = self.get_new_doc_id(document_date_str=llm_response.date_sent)
        
        # Store the original ID used for the LLM request (for log finding)
        # and then update llm_response.id to the actual_doc_id.
        original_llm_request_id = llm_response.id # This is llm_request_id
        llm_response.id = actual_doc_id # Update to the final document ID

        if llm_response.belongs_to_open_doc_id and llm_response.belongs_to_open_doc_id in self.open_documents:
            existing_doc_id = llm_response.belongs_to_open_doc_id # This ID is already a final, date-aware ID
            print(f"Processor: Content from LLM Req ID {original_llm_request_id} (new final doc ID if it were new: {actual_doc_id}) belongs to open document {existing_doc_id}.")

            open_doc_entry = self.open_documents[existing_doc_id]
            existing_letter_data = open_doc_entry['letter_data']

            # Append current page's items (PIL images for PDF, or Path for image)
            open_doc_entry.setdefault('items_to_save', [])
            open_doc_entry.setdefault('preprocessed_ocr_images', []) # Ensure list exists
            open_doc_entry.setdefault('raw_ocr_text_content', '') 
            open_doc_entry.setdefault('merged_page_constituent_infos', [])

            items_from_current_page: List[Union[Path, Image.Image]]
            if current_page_path.suffix.lower() == '.pdf':
                items_from_current_page = images_to_process_pil
            else:
                items_from_current_page = [current_page_path]

            open_doc_entry['items_to_save'].extend(items_from_current_page)
            open_doc_entry['preprocessed_ocr_images'].extend(preprocessed_ocr_images_for_saving)
            # Use original_llm_request_id for page break markers, as it's the ID of this specific processed unit
            open_doc_entry['raw_ocr_text_content'] += f"\n\n--- Page Break (Original Page ID: {original_llm_request_id}) ---\n\n{text_all}"

            # Append content (ensure newline separation)
            existing_letter_data.content += f"\n\n--- Page Break (Original Page ID: {original_llm_request_id}) ---\n\n{llm_response.content}"
            
            # Store info for LLM log consolidation
            open_doc_entry['merged_page_constituent_infos'].append({
                'request_id': original_llm_request_id, # ID used for the LLM call and initial log
                'sender_folder_llm': _sanitize_foldername_llm(llm_response.sender if llm_response.sender else "UnknownSender_LLMLog")
            })

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
                # Call the refactored save and close method
                self._save_and_close_open_document(existing_doc_id, set_complete_true=False)
                print(f"Processor: Successfully processed and closed multi-page document: {existing_doc_id}")
            else:
                print(f"Processor: Document {existing_doc_id} updated, still awaiting more pages.")

        elif not llm_response.is_information_complete or llm_response.is_multipage_explicit:
            # This is a new multi-page document or the first page of one.
            # llm_response.id is already the final actual_doc_id.
            print(f"Processor: Document {llm_response.id} (from LLM Req ID {original_llm_request_id}) is part of a new multi-page document. Keeping it open.")
            self.open_documents[llm_response.id] = {
                'letter_data': llm_response, # llm_response.id is the actual_doc_id
                'items_to_save': images_to_process_pil if current_page_path.suffix.lower() == '.pdf' else [current_page_path],
                'preprocessed_ocr_images': preprocessed_ocr_images_for_saving,
                'raw_ocr_text_content': text_all, # Raw OCR for this first page/file
                'merged_page_constituent_infos': [{ # Info for this first page's LLM log
                    'request_id': original_llm_request_id,
                    'sender_folder_llm': _sanitize_foldername_llm(llm_response.sender if llm_response.sender else "UnknownSender_LLMLog")
                }]
            }
        else:
            # This is a single, complete document.
            # llm_response.id is already the final actual_doc_id.
            print(f"Processor: Document {llm_response.id} (from LLM Req ID {original_llm_request_id}) is a complete single-page document. Saving.")

            items_for_saving_originals: List[Union[Path, Image.Image]]
            if current_page_path.suffix.lower() == '.pdf':
                # For PDFs, images_to_process_pil contains the PIL images of its pages.
                items_for_saving_originals = images_to_process_pil
            else:
                # For original image files, pass the original file path.
                items_for_saving_originals = [current_page_path]

            save_output(
                letter=llm_response, # llm_response.id is the actual_doc_id
                original_items=items_for_saving_originals, 
                settings=self.settings,
                preprocessed_ocr_images=preprocessed_ocr_images_for_saving,
                raw_ocr_text=text_all # Pass raw OCR for this single document/file
            )
            # Note: For single complete documents, LLM logs are under output_dir/sender_llm/llm_request_id/
            # They are not moved/consolidated by _save_and_close_open_document as it's not an "open document".
            # This is acceptable; the logs are still correctly associated with the initial processing.
            print(f"Processor: Successfully processed single-page document: {llm_response.id}")


    # def check_open_document_timeouts(self): # Method entirely removed
    
    def _save_and_close_open_document(self, doc_id: str, set_complete_true: bool = False):
        """
        Core logic to save an open document's data and remove it from open_documents.
        Optionally marks the document as 'is_information_complete'.
        Moves LLM logs from constituent pages and cleans up their original folders.
        """
        open_doc_entry = self.open_documents[doc_id]
        data_to_save = open_doc_entry['letter_data'] # This is the final LetterLLMResponse with final doc_id
        items_to_save_list = open_doc_entry.get('items_to_save', [])
        preprocessed_images_list = open_doc_entry.get('preprocessed_ocr_images', [])
        raw_ocr_text_to_save = open_doc_entry.get('raw_ocr_text_content', '')

        if set_complete_true:
            data_to_save.is_information_complete = True
        
        # This call creates the final document folder (using data_to_save.id) and its primary artifacts
        save_output(
            letter=data_to_save, 
            original_items=items_to_save_list, 
            settings=self.settings,
            preprocessed_ocr_images=preprocessed_images_list,
            raw_ocr_text=raw_ocr_text_to_save
        )

        # Determine the final path structure used by save_output for the consolidated document
        final_doc_main_sender_folder_save = _sanitize_foldername(data_to_save.sender if data_to_save.sender else "UnknownSender")
        final_doc_root_dir_save = Path(self.settings.output_dir) / final_doc_main_sender_folder_save / data_to_save.id # data_to_save.id is the final doc_id
        
        final_target_llm_log_dir = final_doc_root_dir_save / "llm_interaction_logs" # Changed from "llm_interactions" to "llm_interaction_logs" to match llm.py
        final_target_llm_log_dir.mkdir(parents=True, exist_ok=True)

        # Consolidate LLM logs from all constituent pages
        for constituent_page_info in open_doc_entry.get('merged_page_constituent_infos', []):
            constituent_llm_request_id = constituent_page_info['request_id'] # This was the ID used for the LLM call
            constituent_page_sender_folder_llm = constituent_page_info['sender_folder_llm']

            # Path to the original folder where this constituent page's LLM logs were stored
            source_page_root_folder_llm = Path(self.settings.output_dir) / constituent_page_sender_folder_llm / constituent_llm_request_id
            source_llm_log_folder = source_page_root_folder_llm / "llm_interaction_logs" # Matches llm.py

            if source_llm_log_folder.exists() and source_llm_log_folder.is_dir():
                for log_file in source_llm_log_folder.iterdir():
                    if log_file.is_file():
                        try:
                            shutil.move(str(log_file), str(final_target_llm_log_dir / log_file.name))
                        except Exception as e:
                            print(f"Error moving LLM log {log_file.name} from {source_llm_log_folder} to {final_target_llm_log_dir}: {e}")
            
            if source_page_root_folder_llm.resolve() != final_doc_root_dir_save.resolve():
                if source_page_root_folder_llm.exists() and source_page_root_folder_llm.is_dir():
                    try:
                        shutil.rmtree(source_page_root_folder_llm)
                    except Exception as e:
                        print(f"Error deleting original folder for constituent page {source_page_root_folder_llm}: {e}")
        
        del self.open_documents[doc_id]

    def flush_open_documents(self):
        """Saves all currently open documents."""
        print(f"Processor: Flushing all {len(self.open_documents)} open documents...")
        if not self.open_documents:
            print("Processor: No open documents to flush.")
            return
            
        for doc_id in list(self.open_documents.keys()): 
            print(f"Processor: Flushing document {doc_id}.")
            self._save_and_close_open_document(doc_id, set_complete_true=True) # Use helper
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
            self._save_and_close_open_document(doc_id, set_complete_true=True) # Use helper
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
                from snail_mail_parser.ai.llm import Payment # Local import for pydantic model if not globally available
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
