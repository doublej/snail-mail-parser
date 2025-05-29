from openai import OpenAI
from typing import List, Optional
from pydantic import BaseModel, ValidationError
import json 
from enum import Enum
import os 
from pathlib import Path 
from datetime import datetime, timezone # Added for logging timestamp
import re # Added for sanitizing folder name for logs

# Define an Enum for document types
class DocumentType(str, Enum):
    INVOICE = "invoice"
    RECEIPT = "receipt"
    STATEMENT = "statement"
    LETTER = "letter"
    TAXES = "taxes"
    FORM = "form"
    REPORT = "report"
    OTHER = "other"
    ERROR = "Error" # For fallback cases

class Payment(BaseModel):
    iban: Optional[str] = None
    amount: Optional[float] = None
    due_date: Optional[str] = None

class LetterLLMResponse(BaseModel):
    id: str
    sender: str
    date_sent: str
    subject: str
    type: DocumentType # Changed to use the Enum
    content: str
    qr_payloads: List[str]
    payment: Optional[Payment] = None # Allow payment to be None if not applicable
    explicit_document_id: Optional[str] = None 
    # New fields for multi-page document handling
    is_multipage_explicit: Optional[bool] = False 
    is_information_complete: Optional[bool] = True 
    belongs_to_open_doc_id: Optional[str] = None 

# Helper function to sanitize folder names, similar to the one in output.py
# but can be kept local to llm.py or moved to a common utils.py
def _sanitize_foldername_llm(name: str) -> str:
    """Sanitizes a string to be a valid folder name for LLM log paths. """
    if not name or name.strip() == "":
        name = "UnknownSender_LLMLog" # Default for logging if sender unknown early
    name = re.sub(r'[^\w\s-]', '', name).strip()
    name = re.sub(r'\s+', '_', name)
    return name[:50] if len(name) > 50 else name

def _log_llm_interaction(
    doc_id: str, 
    settings, 
    request_data: dict, 
    response_data: Optional[dict] = None, 
    error_details: Optional[dict] = None,
    final_sender_name_for_path: str = "UnknownSender_LLMLog" # Pass the determined sender name
):
    """Helper function to log LLM request, response, and errors to a JSON file. """
    try:
        # Use the provided sender name for the path, sanitized
        sane_sender_folder = _sanitize_foldername_llm(final_sender_name_for_path)

        log_parent_dir = Path(settings.output_dir) / sane_sender_folder / doc_id / "llm_interaction_logs"
        log_parent_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        # doc_id is part of the folder structure, so timestamp makes the filename unique
        log_file_name = f"llm_interaction_{timestamp_str}.json"
        log_file_path = log_parent_dir / log_file_name
        
        log_content = {
            "doc_id": doc_id, # Still useful to have in the log content itself
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "request": request_data,
        }
        if response_data:
            log_content["response"] = response_data
        if error_details:
            log_content["error_details"] = error_details
            
        with open(log_file_path, 'w', encoding='utf-8') as f:
            json.dump(log_content, f, indent=2, ensure_ascii=False)
    except Exception as e:
        # Print to console if logging itself fails, but don't crash the main process
        print(f"CRITICAL: Failed to write LLM interaction log for {doc_id}. Error: {e}")

def _anonymize_text(text: str, anonymization_pairs: List[List[str]]) -> str:
    """
    Applies search-and-replace pairs to the text for anonymization.
    Search is case-insensitive.
    """
    if not anonymization_pairs:
        return text
    
    modified_text = text
    print(f"DEBUG _anonymize_text: Initial text snippet: {text[:100]}") 
    # Sort pairs by length of original string in descending order to prevent partial replacements
    # e.g., replace "John Doe" before "John"
    sorted_pairs = sorted(anonymization_pairs, key=lambda x: len(x[0]) if isinstance(x, list) and len(x) > 0 else 0, reverse=True)

    for pair_idx, pair in enumerate(sorted_pairs): 
        print(f"DEBUG _anonymize_text: Processing pair #{pair_idx}: {pair}") 
        if len(pair) == 2:
            search_str, replace_str = pair
            try:
                # Check if search_str (case-insensitive) is in modified_text before attempting sub
                if re.search(re.escape(search_str), modified_text, flags=re.IGNORECASE):
                    print(f"DEBUG _anonymize_text: Found '{search_str}' (case-insensitive) in text. Attempting replacement with '{replace_str}'.")
                
                temp_text_before_sub = modified_text # For comparison
                # Added word boundaries \b to avoid partial replacements within words
                modified_text = re.sub(r'\b' + re.escape(search_str) + r'\b', replace_str, modified_text, flags=re.IGNORECASE) 
                
                if temp_text_before_sub != modified_text:
                    print(f"DEBUG _anonymize_text: Replacement made for '{search_str}'. Snippet after this rule: {modified_text[:100]}")
                else:
                    print(f"DEBUG _anonymize_text: No replacement made for '{search_str}' with this rule (it might not have been found, or search/replace are identical, or word boundaries prevented it).")

            except re.error as e:
                print(f"Warning: Regex error during anonymization for search string '{search_str}': {e}")
        else:
            print(f"Warning: Invalid anonymization pair format: {pair}. Skipping.")
    
    print(f"DEBUG _anonymize_text: Final text snippet after all pairs: {modified_text[:100]}") 
    return modified_text

def classify_document(text: str, qr_payloads: List[str], doc_id: str, settings, open_docs_summary: Optional[List[dict]] = None) -> LetterLLMResponse:
    """
    Send OCR text and QR payloads to LLM and parse the JSON response into a LetterLLMResponse.
    """
    print(f"CRITICAL_DEBUG: classify_document called for doc_id: {doc_id}. Anonymization pairs from settings: {getattr(settings, 'anonymization_pairs', 'NOT_FOUND')}")
    # Initialize OpenAI client (for v1.0.0+ SDK)
    client = OpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
    )

    # <<< START OF ANONYMIZATION AND DEBUGGING BLOCK >>>
    print(f"DEBUG LLM classify_document: doc_id={doc_id}")
    # Use getattr to safely access anonymization_pairs, defaulting to None or an empty list if not present
    current_anonymization_pairs = getattr(settings, 'anonymization_pairs', [])
    print(f"DEBUG LLM settings.anonymization_pairs: {current_anonymization_pairs}") 
    
    text_lower_for_check = text.lower() # Check original text
    # Example check for "smit", adapt if you have a specific term you expect to be anonymized
    if "smit" in text_lower_for_check: 
        print(f"DEBUG LLM: 'smit' (case-insensitive) FOUND in raw text for doc_id {doc_id}.")
        start_index = text_lower_for_check.find("smit")
        print(f"DEBUG LLM: Context: ...{text[max(0, start_index-30):start_index+30]}...")
    else:
        print(f"DEBUG LLM: 'smit' (case-insensitive) NOT FOUND in raw text for doc_id {doc_id}.")

    original_text = text # Keep original for logging and comparison
    if current_anonymization_pairs: # Check if there are any pairs to process
        print(f"LLM: Applying {len(current_anonymization_pairs)} anonymization rule(s) to text for doc_id {doc_id}.")
        original_text_snippet_for_log = text[:200] 
        text = _anonymize_text(text, current_anonymization_pairs) # text is modified here
        anonymized_text_snippet_for_log = text[:200]
        if original_text_snippet_for_log != anonymized_text_snippet_for_log:
            print(f"LLM: Text anonymized for doc_id {doc_id}. Original snippet: '{original_text_snippet_for_log}...', Anonymized snippet: '{anonymized_text_snippet_for_log}...'")
        else:
            print(f"LLM: Anonymization rules applied, but no changes made to the text snippet for doc_id {doc_id}.")
            # If "smit" was expected to be replaced, and this message appears, let's check the anonymized text directly for "smit"
            if "smit" in original_text_snippet_for_log.lower() and "smit" in anonymized_text_snippet_for_log.lower():
                 print(f"DEBUG LLM: 'smit' was in original snippet and STILL in anonymized snippet for doc_id {doc_id}. Anonymization for 'smit' might have failed within _anonymize_text (e.g. due to word boundaries or rule issues).")
    else:
        print(f"LLM: No anonymization rules found or applied for doc_id {doc_id}.")
    # <<< END OF ANONYMIZATION AND DEBUGGING BLOCK >>>

    response_log_data = {}
    error_log_details = {}
    # This will hold the Pydantic model instance to be returned
    returned_response_object: Optional[LetterLLMResponse] = None
    # This will hold the sender name determined from the response, for logging path

    # List existing sender folders
    existing_sender_folders = []
    output_path = Path(settings.output_dir)
    if output_path.exists() and output_path.is_dir():
        try:
            existing_sender_folders = [d.name for d in output_path.iterdir() if d.is_dir()]
        except Exception as e:
            print(f"Warning: Could not list sender folders in {output_path}: {e}")

    # System prompt defining the AI's role and general instructions
    system_prompt_parts = [
        "You are an AI assistant expert at extracting structured information from documents. "
        "Analyze the provided OCR generated text and QR codes. Populate all fields of the LetterLLMResponse schema accurately. ",
        "For the 'sender' field, provide the sender's name in a simple, concise, and folder-friendly format (e.g., 'Belastingdienst' instead of 'Belasting-dienst Amersfoort'). Avoid special characters that are problematic for directory names. ",
        "The 'type' field must be one of the following: " + ", ".join([t.value for t in DocumentType if t != DocumentType.ERROR]) + ". ",
        "For the 'explicit_document_id' field, extract any specific document identifier like 'Invoice No.', 'Reference #', 'Document ID', 'Betalingskenmerk', 'Factuurnummer' found directly on the letter. This should be an ID for the letter/document itself, not a general customer account number or BSN. If no such specific document ID is found, set this field to null or omit it. ",
        "For the 'payment' field: If payment information (like amount, IBAN, due date) is present, this field MUST be an OBJECT containing sub-fields like 'amount', 'iban', 'due_date'. For example: \"payment\": {\"amount\": 123.45, \"iban\": \"NL00ABCD1234567890\", \"due_date\": \"2024-12-31\"}. If no payment information is found, the 'payment' field MUST be null. DO NOT put a simple string or number (e.g., '€ 4.459') directly into the 'payment' field itself; that value should go into the 'amount' sub-field WITHIN the 'payment' object. ",
        "Pay close attention to the multi-page document instructions if provided. ",
        "'content': Returning content corrected and cleaned of any OCR artifacts or other errors. Format it neatly and spelled validely. ",
        "Return the content as as markdown. Do not label it, do not add \"```\". ",
        "Return any labels, id's, or other scannable or suspected tabular data as neatly formatted tabbed labels and values. These are placed in context where you found them. ",
        "If there is a sequence of characters, a part of the scan that is clearly jibberish from poor OCR recognition, or a part of the scan that is clearly not a letter, you may gather all these at the end of the content "

    ]

    # User prompt parts
    user_prompt_parts = [
        f"Document ID for the current page: {doc_id}",
        f"OCR Text: \"\"\"\n{text}\n\"\"\"",
        f"QR Payloads found on this page: {qr_payloads}",
    ]

    if open_docs_summary:
        system_prompt_parts.extend([
                "\nMulti-page document parameters:",
                "- 'is_multipage_explicit': Set to true if the page explicitly mentions being part of a multi-page document (e.g., 'page 1 of 2', 'continued on next page'). Otherwise, false. ",
                "- 'is_information_complete': Set to false if the text seems obviously cut off or incomplete and it is safe to assume its part of more pages. Otherwise, true. ",
                "- 'belongs_to_open_doc_id': If this page belongs to one of the multipage candidates listed below, set this to the ID of that document. Otherwise, set it to null. Sender and date needs to be the same, this is a sure indicator. If there is a message / correspondence ID this is a sure indicator. ",

                "\nMulti-page document considerations:",
                "If one of the pages says [page number] of [total pages], ensure the matched document mentions the same number of pages. Page 1 of 2 cant match with page 1 of 3 (as that page is already found is taken), it cant match with page 2 of 2 (as that total pages does not match). It can only match with page 2 of 3 and 3 of 3. ",
                "The date of the document should be the same for all pages. If the date is not the same, it is a sure indicator that it does not match. ",
                "",
                "",
            ])

        user_prompt_parts.append("\nCurrent multipage candidates:")

        if not open_docs_summary:
            user_prompt_parts.append("  (None)")
        for open_doc in open_docs_summary:
            user_prompt_parts.append(f"  - ID: {open_doc['id']}, Subject: {open_doc['subject']}, Snippet: {open_doc['content_snippet']}... ")
    else:
        user_prompt_parts.append("\nNo documents are currently open and awaiting more pages. ")

    if existing_sender_folders:
        user_prompt_parts.append("\nConsider these existing sender names/folders when determining the sender (try to be consistent if a similar sender already exists):")
        for folder_name in existing_sender_folders:
            user_prompt_parts.append(f"  - {folder_name}")
    else:
        user_prompt_parts.append("\nNo existing sender folders found to consider for sender name consistency. ")

    # Combine system and user prompts into one string for the LLM
    system_prompt = "\n".join(system_prompt_parts)
    user_prompt = "\n".join(user_prompt_parts)

    # Logging related initializations
    request_log_data = {
        "model": settings.llm_model,
        "system_prompt": system_prompt,
        "user_prompt": user_prompt, # This will now contain the (potentially anonymized) text
        "anonymization_applied": bool(current_anonymization_pairs) 
    }
    if current_anonymization_pairs:
        request_log_data["original_text_length"] = len(original_text)
        request_log_data["anonymized_text_length"] = len(text) # 'text' here is the one sent to LLM

    final_sender_name_for_log_path: str = "UnknownSender_LLMLog"

    raw_content_for_error = "" # Store raw text for error reporting if needed

    try:
        completion = client.beta.chat.completions.parse(
            model=settings.llm_model, # Ensure this model supports structured outputs (e.g., gpt-4o)
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            response_format=LetterLLMResponse # Pass the Pydantic model directly
        )

        if completion.choices[0].message.refusal:
            print(f"LLM refused to process request for doc_id {doc_id}: {completion.choices[0].message.refusal}")
            # Create a fallback error response
            error_subject = f"LLM Refusal: {completion.choices[0].message.refusal}"
            # The raw_content might not be available or relevant in case of refusal before generation
            # We'll use the refusal message as the content.
            raw_content_for_error = f"Refusal: {completion.choices[0].message.refusal}"
            raise ValueError(error_subject) # Trigger the common error handling path

        result = completion.choices[0].message.parsed
        if not result: # Should not happen if no refusal and parse is successful
             raise ValueError("LLM returned no parsed data despite no refusal. ")

        # Ensure our doc_id is used, overriding any ID from the LLM
        result.id = doc_id
        # Ensure qr_payloads are correctly set if LLM missed them or returned non-list
        if not isinstance(result.qr_payloads, list):
            result.qr_payloads = qr_payloads

        response_log_data["parsed_result"] = result.model_dump()
        final_sender_name_for_log_path = result.sender # Get sender for log path
        returned_response_object = result

    except Exception as e:
        print(f"Error during LLM processing or Pydantic parsing for doc_id {doc_id}: {e}")
        error_log_details["exception_type"] = type(e).__name__
        error_log_details["exception_message"] = str(e)
        if raw_content_for_error: # Populated if refusal path was taken before another error
            error_log_details["contextual_raw_content"] = raw_content_for_error
        # so actual LLM output might not be available.
        # If 'e' is a Pydantic ValidationError, it might have more details.
        # If 'e' is an OpenAI API error, it will have its own structure.

        error_subject_detail = str(e)
        if isinstance(e, ValidationError):
            error_subject_detail = f"{e.errors()[0]['msg'] if e.errors() else 'Unknown validation issue'}"

        error_subject = f"LLM/Validation Error: {error_subject_detail}"

        # Try to get raw content if available from the exception (e.g. if it's a custom error that wrapped it)
        # For now, we rely on raw_content_for_error or a generic message.
        # If the error is from client.beta.chat.completions.parse, the raw response might be harder to get
        # than with the older client.chat.completions.create.
        # The 'raw_content' variable from the old code isn't directly available here.
        # We'll use a placeholder if specific raw output isn't captured.

        final_error_content = f"Failed to get valid structured response from LLM. Error: {str(e)}"
        if raw_content_for_error: # This would be set if it was a refusal
            final_error_content = f"LLM Refusal: {raw_content_for_error}"

        print(f"Creating fallback error response for doc_id {doc_id} due to: {error_subject}")
        error_data = {
            "id": doc_id,
            "sender": "Unknown",
            "date_sent": "Unknown",
            "subject": error_subject,
            "type": DocumentType.ERROR, # Use the Enum for error type
            "content": final_error_content,
            "qr_payloads": qr_payloads,
            "payment": None, # Simpler fallback for payment
            "is_multipage_explicit": False,
            "is_information_complete": True,
            "belongs_to_open_doc_id": None
        }
        # Validate the error data itself to ensure it conforms to LetterLLMResponse
        fallback_response = LetterLLMResponse.model_validate(error_data)
        error_log_details["fallback_response_generated"] = fallback_response.model_dump()
        # Use sender from fallback for log path, which might be "Unknown"
        final_sender_name_for_log_path = fallback_response.sender
        returned_response_object = fallback_response
    finally:
        _log_llm_interaction(
            doc_id,
            settings,
            request_log_data,
            response_log_data if response_log_data else None, # Pass None if empty
            error_log_details if error_log_details else None, # Pass None if empty
            final_sender_name_for_log_path
        )

    if returned_response_object is None:
        # This case should ideally not be reached if logic is correct,
        # but as a safeguard, create a very generic error and log it.
        print(f"CRITICAL: returned_response_object is None for {doc_id} after try-except-finally. Defaulting to error. ")
        critical_error_data = {
            "id": doc_id, "sender": "System", "date_sent": "Unknown",
            "subject": "Critical internal error in LLM processing",
            "type": DocumentType.ERROR, "content": "Failed to produce a response object. ",
            "qr_payloads": qr_payloads, "payment": None,
            "is_multipage_explicit": False, "is_information_complete": True,
            "belongs_to_open_doc_id": None
        }
        returned_response_object = LetterLLMResponse.model_validate(critical_error_data)
        # Log this critical failure specifically if the main logging in finally didn't capture it well
        # (though it should have run with some error details)
        _log_llm_interaction(
            doc_id, settings, request_log_data,
            response_data={"critical_error_event": "returned_response_object was None"},
            error_details={"message": "Forced critical error response generation"},
            final_sender_name_for_path="System_CriticalError"
        )

    return returned_response_object
