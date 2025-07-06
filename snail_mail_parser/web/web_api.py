import uvicorn
from fastapi import FastAPI, HTTPException, Request # Added Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates # Added for templating
from pathlib import Path
from typing import List, Dict, Any
import os 

from snail_mail_parser.core.settings import Settings
from snail_mail_parser.navigation import navigator
from snail_mail_parser.core.processor import Processor # Added
from queue import Queue # Added
from pydantic import BaseModel as FastAPIBaseModel # Alias to avoid clash with our LetterLLMResponse

# Initialize FastAPI app and settings
app = FastAPI(title="Document Processing API", version="0.1.0")
settings = Settings()
OUTPUT_DIR = Path(settings.output_dir)

# Ensure output directory exists (though navigator functions handle non-existence gracefully)
if not OUTPUT_DIR.exists():
    print(f"Warning: Output directory {OUTPUT_DIR} does not exist. API might return empty results.")

# Create a global processor instance for the API.
# This instance is separate from any processor run by main.py if main.py also creates one.
# For a shared state, a different architecture (e.g., shared service or running FastAPI via main.py) would be needed.
doc_processing_queue_api = Queue() # API's own queue, likely unused if API only manages open docs
processor_instance_api = Processor(settings, doc_processing_queue_api) # API's own processor instance

# Setup Jinja2 templating
templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def root():
    """Simple HTML root page with links to API docs."""
    return """
    <html>
        <head>
            <title>Document API</title>
        </head>
        <body>
            <h1>Document Processing API</h1>
            <p>Welcome to the Document Processing API.</p>
            <p>Swagger UI for API documentation: <a href="/docs">/docs</a></p>
            <p>ReDoc for API documentation: <a href="/redoc">/redoc</a></p>
        </body>
    </html>
    """

@app.get("/senders", response_model=List[str], tags=["Navigation"])
async def list_all_senders():
    """Lists all unique sender names based on folder structure."""
    return navigator.get_all_senders(OUTPUT_DIR)

@app.get("/senders/{sender_name}/letters", response_model=List[str], tags=["Navigation"])
async def list_letters_for_sender(sender_name: str):
    """Lists all document IDs for a given sender."""
    letters = navigator.get_letters_by_sender(OUTPUT_DIR, sender_name)
    if not letters and not (OUTPUT_DIR / sender_name).exists():
        raise HTTPException(status_code=404, detail=f"Sender '{sender_name}' not found.")
    return letters

@app.get("/senders/{sender_name}/letters/{doc_id}/details", response_model=Dict[str, Any], tags=["Document Access"])
async def get_document_details(sender_name: str, doc_id: str):
    """Retrieves the YAML details for a specific document."""
    details = navigator.get_letter_details_yaml(OUTPUT_DIR, sender_name, doc_id)
    if details is None:
        raise HTTPException(status_code=404, detail=f"Document '{doc_id}' from sender '{sender_name}' not found or YAML missing.")
    return details

@app.get("/senders/{sender_name}/letters/{doc_id}/markdown", response_model=str, tags=["Document Access"])
async def get_document_markdown(sender_name: str, doc_id: str):
    """Retrieves the Markdown content for a specific document."""
    content = navigator.get_letter_markdown_content(OUTPUT_DIR, sender_name, doc_id)
    if content is None:
        raise HTTPException(status_code=404, detail=f"Document '{doc_id}' from sender '{sender_name}' not found or Markdown missing.")
    return content

def _get_filenames_from_paths(paths: List[Path]) -> List[str]:
    return [p.name for p in paths]

@app.get("/senders/{sender_name}/letters/{doc_id}/originals", response_model=List[str], tags=["File Listing"])
async def list_original_scans(sender_name: str, doc_id: str):
    """Lists filenames of original scans for a document."""
    doc_path = navigator._get_doc_path(OUTPUT_DIR, sender_name, doc_id)
    if not doc_path: 
        raise HTTPException(status_code=404, detail=f"Document '{doc_id}' from sender '{sender_name}' not found.")
    paths = navigator.get_letter_original_scans(OUTPUT_DIR, sender_name, doc_id)
    return _get_filenames_from_paths(paths)

@app.get("/senders/{sender_name}/letters/{doc_id}/previews", response_model=List[str], tags=["File Listing"])
async def list_previews(sender_name: str, doc_id: str):
    """Lists filenames of previews for a document."""
    doc_path = navigator._get_doc_path(OUTPUT_DIR, sender_name, doc_id)
    if not doc_path:
        raise HTTPException(status_code=404, detail=f"Document '{doc_id}' from sender '{sender_name}' not found.")
    paths = navigator.get_letter_previews(OUTPUT_DIR, sender_name, doc_id)
    return _get_filenames_from_paths(paths)

@app.get("/senders/{sender_name}/letters/{doc_id}/llm_logs", response_model=List[str], tags=["File Listing"])
async def list_llm_interaction_logs(sender_name: str, doc_id: str):
    """Lists filenames of LLM interaction logs for a document."""
    doc_path = navigator._get_doc_path(OUTPUT_DIR, sender_name, doc_id)
    if not doc_path:
        raise HTTPException(status_code=404, detail=f"Document '{doc_id}' from sender '{sender_name}' not found.")
    paths = navigator.get_letter_llm_interactions(OUTPUT_DIR, sender_name, doc_id)
    return _get_filenames_from_paths(paths)

@app.get("/senders/{sender_name}/letters/{doc_id}/file/{subfolder}/{filename}", tags=["File Access"])
async def get_document_file(sender_name: str, doc_id: str, subfolder: str, filename: str):
    """
    Serves a specific file (original, preview, or log) for a document.
    'subfolder' must be one of 'original_scans', 'previews', 'llm_interaction_logs'.
    """
    allowed_subfolders = ["original_scans", "previews", "llm_interaction_logs"]
    if subfolder not in allowed_subfolders:
        raise HTTPException(status_code=400, detail=f"Invalid subfolder. Must be one of: {allowed_subfolders}")

    doc_path = navigator._get_doc_path(OUTPUT_DIR, sender_name, doc_id)
    if not doc_path:
        raise HTTPException(status_code=404, detail=f"Document '{doc_id}' from sender '{sender_name}' not found.")

    # Construct the full path to the requested file
    file_path = (doc_path / subfolder / filename).resolve()
    
    # Security check: Ensure the resolved path is within the intended subfolder
    # This helps prevent path traversal attacks (e.g., filename containing '..')
    intended_subfolder_path = (doc_path / subfolder).resolve()
    if not file_path.is_file() or not str(file_path).startswith(str(intended_subfolder_path)):
        raise HTTPException(status_code=404, detail=f"File '{filename}' not found or access denied in '{subfolder}' for document '{doc_id}'.")
    
    return FileResponse(file_path)

@app.get("/senders/{sender_name}/letters/{doc_id}/facsimile/view", response_class=HTMLResponse, tags=["Document Views"])
async def view_document_facsimile(request: Request, sender_name: str, doc_id: str):
    """Serves an HTML page displaying the facsimile text in a retro style."""
    facsimile_file_path = navigator.get_letter_facsimile_path(OUTPUT_DIR, sender_name, doc_id)
    
    if not facsimile_file_path:
        raise HTTPException(status_code=404, detail=f"Facsimile file for document '{doc_id}' from sender '{sender_name}' not found.")

    try:
        with open(facsimile_file_path, 'r', encoding='utf-8') as f:
            facsimile_content = f.read()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading facsimile file: {e}")

    return templates.TemplateResponse(
        "facsimile_viewer.html", 
        {"request": request, "doc_id": doc_id, "facsimile_content": facsimile_content}
    )

# --- Endpoints for managing open documents ---

@app.post("/processor/actions/flush_all_open", status_code=200, tags=["Processor Actions"])
async def flush_all_open_documents_api():
    """
    Forces all currently open multi-page documents to be considered complete, saved, and closed.
    """
    try:
        # Use the API's processor instance
        processor_instance_api.flush_open_documents()
        return {"message": "All open documents flushed successfully."}
    except Exception as e:
        print(f"Error during /processor/actions/flush_all_open: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error flushing open documents: {str(e)}")

@app.get("/processor/open_documents", response_model=List[Dict[str, Any]], tags=["Processor Actions"])
async def get_open_documents_api():
    """Lists all currently 'open' multi-page documents awaiting more pages."""
    try:
        summary = processor_instance_api.get_open_documents_summary()
        return summary
    except Exception as e:
        print(f"Error during /processor/open_documents: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error retrieving open documents: {str(e)}")

@app.post("/processor/open_documents/{doc_id}/force_complete", status_code=200, tags=["Processor Actions"])
async def force_complete_document_api(doc_id: str):
    """
    Manually marks a specific open multi-page document as complete.
    The document will be saved and removed from the open documents list.
    """
    success = processor_instance_api.force_complete_open_document(doc_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Open document with ID '{doc_id}' not found.")
    return {"message": f"Document '{doc_id}' marked as complete and processed."}

class MergeDocumentRequest(FastAPIBaseModel): # Use aliased BaseModel
    source_sender_name: str
    source_doc_id: str

@app.post("/processor/open_documents/{target_open_doc_id}/merge", status_code=200, tags=["Processor Actions"])
async def merge_document_api(target_open_doc_id: str, merge_request: MergeDocumentRequest):
    """
    Merges an already processed and saved standalone document (source)
    into an existing open multi-page document (target).
    The source document's folder will be deleted after successful merge.
    """
    success = processor_instance_api.merge_processed_document_into_open_document(
        target_open_doc_id,
        merge_request.source_sender_name,
        merge_request.source_doc_id
    )
    if not success:
        # More specific error messages would come from the processor logs
        raise HTTPException(status_code=400, detail="Failed to merge documents. Check server logs for details (e.g., source or target not found, or target already closed).")
    return {"message": f"Document {merge_request.source_sender_name}/{merge_request.source_doc_id} merged into {target_open_doc_id}."}


if __name__ == "__main__":
    print(f"Starting API server. Output directory configured to: {OUTPUT_DIR}")
    uvicorn.run("web_api:app", host="0.0.0.0", port=8000, reload=True)

# To run this API:
# 1. Ensure FastAPI and Uvicorn are installed: pip install fastapi "uvicorn[standard]"
# 2. Save this file as web_api.py
# 3. Run from your terminal: python web_api.py
# 4. Access the API at http://localhost:8000 and docs at http://localhost:8000/docs
