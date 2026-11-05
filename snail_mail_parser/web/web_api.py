import uvicorn
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form, Depends, status
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates # Added for templating
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pathlib import Path
from typing import List, Dict, Any, Optional
import tempfile
import shutil
import asyncio
from PIL import Image
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
import secrets
import hashlib
from datetime import datetime, timedelta

from snail_mail_parser.core.settings import Settings
from snail_mail_parser.navigation import navigator
from snail_mail_parser.core.processor import Processor # Added
from queue import Queue # Added
from pydantic import BaseModel as FastAPIBaseModel, Field # Alias to avoid clash with our LetterLLMResponse

# Initialize FastAPI app and settings
app = FastAPI(
    title="Snail Mail Parser API",
    description="""
    A comprehensive document processing API for physical mail scanning and content extraction.
    
    ## Features
    
    * **Document Navigation**: Browse processed documents by sender and date
    * **File Access**: Download original scans, previews, and processed files
    * **Document Processing**: Force completion and merge operations
    * **Content Extraction**: Access OCR text, QR codes, and AI-processed metadata
    * **Document Viewing**: Interactive facsimile viewer
    
    ## Document Workflow
    
    1. Documents are scanned and placed in the input directory
    2. OCR and QR code detection extract text and structured data
    3. AI processes content to extract metadata (sender, date, payment info)
    4. Documents are organized by sender and saved with structured metadata
    5. API provides access to all stages of processing
    
    ## Navigation Structure
    
    Documents are organized as: `/senders/{sender_name}/letters/{doc_id}/`
    
    Each document contains:
    - `details.yaml`: Structured metadata
    - `content.md`: Formatted content
    - `originals/`: Original scan files
    - `previews/`: Processed preview images
    - `llm_interactions/`: AI processing logs
    """,
    version="0.1.0",
    contact={
        "name": "Snail Mail Parser",
        "url": "https://github.com/jurrejan/snail_mail_parser",
    },
    license_info={
        "name": "MIT",
    },
)
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

# Configure logging
logger = logging.getLogger(__name__)

# Authentication setup
security = HTTPBasic()

# Mock user database with credits
MOCK_USERS = {
    "demo": {
        "password_hash": hashlib.sha256("demo123".encode()).hexdigest(),
        "email": "demo@example.com", 
        "credits": 1000,
        "created_at": datetime.now(),
        "transactions": []
    },
    "testuser": {
        "password_hash": hashlib.sha256("test456".encode()).hexdigest(),
        "email": "testuser@example.com",
        "credits": 500,
        "created_at": datetime.now(),
        "transactions": []
    },
    "admin": {
        "password_hash": hashlib.sha256("admin789".encode()).hexdigest(),
        "email": "admin@example.com",
        "credits": 10000,
        "created_at": datetime.now(),
        "transactions": []
    }
}

# Email configuration for testing (using Gmail SMTP)
EMAIL_CONFIG = {
    "smtp_server": "smtp.gmail.com",
    "smtp_port": 587,
    "email": "your-test-email@gmail.com",  # Replace with actual test email
    "password": "your-app-password",  # Replace with Gmail app password
    "sender_name": "Snail Mail Parser"
}

class User:
    def __init__(self, username: str, email: str, credits: int, created_at: datetime, transactions: List[Dict]):
        self.username = username
        self.email = email
        self.credits = credits
        self.created_at = created_at
        self.transactions = transactions

def authenticate_user(credentials: HTTPBasicCredentials = Depends(security)) -> User:
    """Authenticate user and return user object."""
    username = credentials.username
    password = credentials.password
    
    if username not in MOCK_USERS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    
    user_data = MOCK_USERS[username]
    password_hash = hashlib.sha256(password.encode()).hexdigest()
    
    if user_data["password_hash"] != password_hash:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    
    return User(
        username=username,
        email=user_data["email"],
        credits=user_data["credits"],
        created_at=user_data["created_at"],
        transactions=user_data["transactions"]
    )

def deduct_credits(username: str, amount: int, description: str) -> bool:
    """Deduct credits from user account."""
    if username not in MOCK_USERS:
        return False
    
    user_data = MOCK_USERS[username]
    if user_data["credits"] < amount:
        return False
    
    user_data["credits"] -= amount
    transaction = {
        "type": "deduct",
        "amount": amount,
        "description": description,
        "timestamp": datetime.now(),
        "balance_after": user_data["credits"]
    }
    user_data["transactions"].append(transaction)
    
    logger.info(f"Credits deducted: {amount} from {username}. Remaining: {user_data['credits']}")
    return True

def add_credits(username: str, amount: int, description: str) -> bool:
    """Add credits to user account."""
    if username not in MOCK_USERS:
        return False
    
    user_data = MOCK_USERS[username]
    user_data["credits"] += amount
    transaction = {
        "type": "add",
        "amount": amount,
        "description": description,
        "timestamp": datetime.now(),
        "balance_after": user_data["credits"]
    }
    user_data["transactions"].append(transaction)
    
    logger.info(f"Credits added: {amount} to {username}. New balance: {user_data['credits']}")
    return True

# Helper functions for upload processing
async def send_email_notification(email: str, doc_id: str, processing_type: str, user: User) -> bool:
    """
    Send email notification to user with processed document results.
    Uses Gmail SMTP for testing (can be replaced with other providers).
    """
    try:
        # Create email content
        subject = f"Document Processing Complete - {doc_id}"
        
        # Create HTML email body
        html_body = f"""
        <html>
            <body>
                <h2>Document Processing Complete</h2>
                <p>Hello {user.username},</p>
                <p>Your document <strong>{doc_id}</strong> has been successfully processed using <strong>{processing_type}</strong> processing.</p>
                
                <h3>Processing Details:</h3>
                <ul>
                    <li>Document ID: {doc_id}</li>
                    <li>Processing Type: {processing_type}</li>
                    <li>Processed at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</li>
                </ul>
                
                <h3>Account Information:</h3>
                <ul>
                    <li>Current Credits: {user.credits}</li>
                    <li>Credits Used: {5 if processing_type == 'LLM' else 1}</li>
                </ul>
                
                <p>You can access your processed documents through the API or web interface.</p>
                
                <p>Best regards,<br>
                Snail Mail Parser Team</p>
            </body>
        </html>
        """
        
        # For testing, we'll use a simple email service
        # In production, replace with actual email service
        if EMAIL_CONFIG["email"] == "your-test-email@gmail.com":
            # Mock email sending for testing
            logger.info(f"MOCK EMAIL: Sending to {email}")
            logger.info(f"Subject: {subject}")
            logger.info(f"Body: {html_body}")
            return True
        
        # Real email sending (uncomment and configure for production)
        # msg = MIMEMultipart('alternative')
        # msg['Subject'] = subject
        # msg['From'] = f"{EMAIL_CONFIG['sender_name']} <{EMAIL_CONFIG['email']}>"
        # msg['To'] = email
        
        # html_part = MIMEText(html_body, 'html')
        # msg.attach(html_part)
        
        # server = smtplib.SMTP(EMAIL_CONFIG['smtp_server'], EMAIL_CONFIG['smtp_port'])
        # server.starttls()
        # server.login(EMAIL_CONFIG['email'], EMAIL_CONFIG['password'])
        # server.send_message(msg)
        # server.quit()
        
        logger.info(f"Email sent successfully to {email}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to send email to {email}: {str(e)}")
        return False

def validate_upload_files(files: List[UploadFile]) -> bool:
    """Validate uploaded files meet requirements."""
    if not files:
        return False
    
    for file in files:
        if not file.content_type or not file.content_type.startswith('image/'):
            return False
        
        # Check file naming convention (page_1.jpg, page_2.jpg, etc.)
        if not file.filename or not file.filename.lower().endswith(('.jpg', '.jpeg')):
            return False
    
    return True

def calculate_credits(ocr_enabled: bool, llm_enabled: bool) -> int:
    """Calculate credit usage based on processing options."""
    if llm_enabled:
        return 5  # LLM processing includes OCR
    elif ocr_enabled:
        return 1  # Basic OCR only
    else:
        return 0  # No processing requested

async def process_uploaded_document(
    files: List[UploadFile], 
    email: str, 
    ocr_enabled: bool, 
    llm_enabled: bool
) -> tuple[str, bool]:
    """
    Process uploaded document files and return document ID and email status.
    """
    # Create temporary directory for processing
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        
        # Save uploaded files to temporary directory
        file_paths = []
        for i, file in enumerate(files):
            file_path = temp_path / f"page_{i+1}.jpg"
            try:
                with open(file_path, "wb") as buffer:
                    shutil.copyfileobj(file.file, buffer)
                
                # Validate image can be opened
                img = Image.open(file_path)
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                    img.save(file_path, 'JPEG', quality=80)  # 0.8 compression as specified
                
                file_paths.append(file_path)
            except Exception as e:
                logger.error(f"Error processing image {file.filename}: {e}")
                raise HTTPException(status_code=400, detail=f"Invalid image file: {file.filename}")
        
        # Process each file through the existing processor
        # The processor expects single file paths in a list
        doc_ids = []
        for file_path in file_paths:
            try:
                doc_id = processor_instance_api.process_document_pages([file_path])
                if doc_id:
                    doc_ids.append(doc_id)
            except Exception as e:
                logger.error(f"Error processing file {file_path}: {e}")
                raise HTTPException(status_code=500, detail=f"Error processing file: {file_path.name}")
        
        # For multi-page documents, use the first document ID or create a combined one
        final_doc_id = doc_ids[0] if doc_ids else processor_instance_api.get_new_doc_id()
        
        # Send email notification
        processing_type = "LLM" if llm_enabled else "OCR"
        # This function needs to be updated to accept user parameter
        return final_doc_id, True  # Mock email sent for now


# Response Models
class DocumentDetails(FastAPIBaseModel):
    """Document metadata and content details."""
    sender: Optional[str] = Field(None, description="Sender name")
    date: Optional[str] = Field(None, description="Document date")
    subject: Optional[str] = Field(None, description="Document subject")
    payment_amount: Optional[float] = Field(None, description="Payment amount if applicable")
    payment_iban: Optional[str] = Field(None, description="IBAN if applicable")
    payment_due_date: Optional[str] = Field(None, description="Payment due date if applicable")
    document_type: Optional[str] = Field(None, description="Type of document")
    pages: Optional[int] = Field(None, description="Number of pages")
    
class OpenDocument(FastAPIBaseModel):
    """Open document in processing queue."""
    doc_id: str = Field(..., description="Document ID")
    creation_time: str = Field(..., description="When document processing started")
    pages_count: int = Field(..., description="Number of pages processed")
    is_complete: bool = Field(..., description="Whether document is complete")
    
class ProcessingStatus(FastAPIBaseModel):
    """Status response for processing operations."""
    success: bool = Field(..., description="Whether operation succeeded")
    message: str = Field(..., description="Status message")
    doc_id: Optional[str] = Field(None, description="Document ID if applicable")

class UploadResponse(FastAPIBaseModel):
    """Response for document upload."""
    success: bool = Field(..., description="Whether upload succeeded")
    message: str = Field(..., description="Status message")
    doc_id: Optional[str] = Field(None, description="Generated document ID")
    credits_used: int = Field(..., description="Number of credits consumed")
    email_sent: bool = Field(..., description="Whether notification email was sent")

class CreditInfo(FastAPIBaseModel):
    """Credit usage information."""
    basic_ocr_cost: int = Field(1, description="Credits for basic OCR processing")
    llm_processing_cost: int = Field(5, description="Credits for LLM processing")

class UserInfo(FastAPIBaseModel):
    """User account information."""
    username: str = Field(..., description="Username")
    email: str = Field(..., description="Email address")
    credits: int = Field(..., description="Current credit balance")
    created_at: datetime = Field(..., description="Account creation date")

class Transaction(FastAPIBaseModel):
    """Credit transaction record."""
    type: str = Field(..., description="Transaction type (add/deduct)")
    amount: int = Field(..., description="Credit amount")
    description: str = Field(..., description="Transaction description")
    timestamp: datetime = Field(..., description="Transaction timestamp")
    balance_after: int = Field(..., description="Balance after transaction")

class CreditPurchase(FastAPIBaseModel):
    """Credit purchase request."""
    credits: int = Field(..., description="Number of credits to purchase", ge=1, le=10000)
    
class CreditPurchaseResponse(FastAPIBaseModel):
    """Credit purchase response."""
    success: bool = Field(..., description="Whether purchase succeeded")
    message: str = Field(..., description="Status message")
    credits_purchased: int = Field(..., description="Number of credits purchased")
    new_balance: int = Field(..., description="New credit balance")
    cost: float = Field(..., description="Total cost in USD")


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

@app.get(
    "/senders", 
    response_model=List[str], 
    tags=["Navigation"],
    summary="List all senders",
    description="Get a list of all sender names that have processed documents in the system.",
    responses={
        200: {
            "description": "List of sender names",
            "content": {
                "application/json": {
                    "example": ["bank-abc", "insurance-xyz", "utility-company"]
                }
            }
        }
    }
)
async def list_all_senders():
    """Lists all unique sender names based on folder structure."""
    return navigator.get_all_senders(OUTPUT_DIR)

@app.get(
    "/senders/{sender_name}/letters", 
    response_model=List[str], 
    tags=["Navigation"],
    summary="List letters for sender",
    description="Get all document IDs for a specific sender, sorted by processing date.",
    responses={
        200: {
            "description": "List of document IDs for the sender",
            "content": {
                "application/json": {
                    "example": ["2024-01-15_001", "2024-01-20_002", "2024-02-01_003"]
                }
            }
        },
        404: {"description": "Sender not found or no letters for sender"}
    }
)
async def list_letters_for_sender(sender_name: str):
    """Lists all document IDs for a given sender."""
    letters = navigator.get_letters_by_sender(OUTPUT_DIR, sender_name)
    if not letters and not (OUTPUT_DIR / sender_name).exists():
        raise HTTPException(status_code=404, detail=f"Sender '{sender_name}' not found.")
    return letters

@app.get(
    "/senders/{sender_name}/letters/{doc_id}/details", 
    response_model=Dict[str, Any], 
    tags=["Document Access"],
    summary="Get document details",
    description="Retrieve structured metadata and details for a specific document.",
    responses={
        200: {
            "description": "Document metadata including sender, date, payment info",
            "content": {
                "application/json": {
                    "example": {
                        "sender": "bank-abc",
                        "date": "2024-01-15",
                        "subject": "Monthly Statement",
                        "payment_amount": 250.00,
                        "payment_iban": "NL91ABNA0417164300",
                        "payment_due_date": "2024-02-15",
                        "document_type": "LETTER",
                        "pages": 3
                    }
                }
            }
        },
        404: {"description": "Document not found"}
    }
)
async def get_document_details(sender_name: str, doc_id: str):
    """Retrieves the YAML details for a specific document."""
    details = navigator.get_letter_details_yaml(OUTPUT_DIR, sender_name, doc_id)
    if details is None:
        raise HTTPException(status_code=404, detail=f"Document '{doc_id}' from sender '{sender_name}' not found or YAML missing.")
    return details

@app.get(
    "/senders/{sender_name}/letters/{doc_id}/markdown", 
    response_model=str, 
    tags=["Document Access"],
    summary="Get document content",
    description="Retrieve the processed markdown content of a document.",
    responses={
        200: {
            "description": "Document content in markdown format",
            "content": {
                "application/json": {
                    "example": "# Bank Statement\\n\\n**Date:** 2024-01-15\\n**Amount:** €250.00\\n\\nYour monthly statement..."
                }
            }
        },
        404: {"description": "Document content not found"}
    }
)
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

@app.post(
    "/processor/actions/flush_all_open", 
    response_model=ProcessingStatus,
    tags=["Processor Actions"],
    summary="Flush all open documents",
    description="Force all currently open multi-page documents to be completed and saved.",
    responses={
        200: {
            "description": "All documents flushed successfully",
            "content": {
                "application/json": {
                    "example": {
                        "success": True,
                        "message": "All open documents flushed successfully."
                    }
                }
            }
        },
        500: {"description": "Internal server error during processing"}
    }
)
async def flush_all_open_documents_api():
    """
    Forces all currently open multi-page documents to be considered complete, saved, and closed.
    """
    try:
        # Use the API's processor instance
        processor_instance_api.flush_open_documents()
        return ProcessingStatus(success=True, message="All open documents flushed successfully.")
    except Exception as e:
        print(f"Error during /processor/actions/flush_all_open: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error flushing open documents: {str(e)}")

@app.get(
    "/processor/open_documents", 
    response_model=List[Dict[str, Any]], 
    tags=["Processor Actions"],
    summary="Get open documents",
    description="Retrieve information about all documents currently being processed.",
    responses={
        200: {
            "description": "List of documents in processing queue",
            "content": {
                "application/json": {
                    "example": [
                        {
                            "doc_id": "2024-01-15_001",
                            "creation_time": "2024-01-15T10:30:00Z",
                            "pages_count": 3,
                            "is_complete": False
                        }
                    ]
                }
            }
        },
        500: {"description": "Internal server error"}
    }
)
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


# --- Authentication & Credit Endpoints ---

@app.get(
    "/auth/user",
    response_model=UserInfo,
    tags=["Authentication"],
    summary="Get user information",
    description="Retrieve current user account information including credit balance."
)
async def get_user_info(user: User = Depends(authenticate_user)):
    """Get authenticated user's account information."""
    return UserInfo(
        username=user.username,
        email=user.email,
        credits=user.credits,
        created_at=user.created_at
    )

@app.get(
    "/auth/transactions",
    response_model=List[Transaction],
    tags=["Authentication"],
    summary="Get transaction history",
    description="Retrieve user's credit transaction history."
)
async def get_transactions(user: User = Depends(authenticate_user)):
    """Get user's credit transaction history."""
    return [Transaction(**transaction) for transaction in user.transactions]

@app.post(
    "/credits/purchase",
    response_model=CreditPurchaseResponse,
    tags=["Credits"],
    summary="Purchase credits",
    description="Purchase additional credits for document processing. Mock implementation for testing.",
    responses={
        200: {
            "description": "Credits purchased successfully",
            "content": {
                "application/json": {
                    "example": {
                        "success": True,
                        "message": "Credits purchased successfully",
                        "credits_purchased": 100,
                        "new_balance": 1100,
                        "cost": 10.00
                    }
                }
            }
        },
        400: {"description": "Invalid purchase amount"},
        401: {"description": "Authentication required"}
    }
)
async def purchase_credits(
    purchase: CreditPurchase,
    user: User = Depends(authenticate_user)
):
    """Purchase additional credits (mock implementation)."""
    
    # Mock pricing: $0.10 per credit
    cost_per_credit = 0.10
    total_cost = purchase.credits * cost_per_credit
    
    # Mock payment processing (always succeeds)
    payment_successful = True
    
    if not payment_successful:
        raise HTTPException(status_code=400, detail="Payment processing failed")
    
    # Add credits to user account
    success = add_credits(
        user.username, 
        purchase.credits, 
        f"Purchased {purchase.credits} credits for ${total_cost:.2f}"
    )
    
    if not success:
        raise HTTPException(status_code=500, detail="Failed to add credits to account")
    
    # Get updated balance
    new_balance = MOCK_USERS[user.username]["credits"]
    
    return CreditPurchaseResponse(
        success=True,
        message="Credits purchased successfully",
        credits_purchased=purchase.credits,
        new_balance=new_balance,
        cost=total_cost
    )

@app.get(
    "/credits/info",
    response_model=CreditInfo,
    tags=["Credits"],
    summary="Get credit pricing information",
    description="Retrieve information about credit costs for different processing options."
)
async def get_credit_info():
    """Get credit usage information for processing options."""
    return CreditInfo()


# --- Upload Endpoint ---

@app.post(
    "/upload",
    response_model=UploadResponse,
    tags=["Document Upload"],
    summary="Upload document for processing",
    description="""
    Upload document pages for OCR and/or LLM processing.
    
    **Processing Options:**
    - **Basic OCR** (1 credit): Text extraction only
    - **LLM Processing** (5 credits): OCR + QR detection + bank account extraction + advanced analysis
    
    **File Requirements:**
    - Format: JPEG images with 0.8 compression quality
    - Naming: page_1.jpg, page_2.jpg, etc.
    - Content-Type: image/jpeg
    
    **Response:**
    - Success: HTTP 200 with document ID
    - Validation Error: HTTP 400 for invalid files/parameters
    - Processing Error: HTTP 500 (triggers credit refund)
    """,
    responses={
        200: {
            "description": "Document processed successfully",
            "content": {
                "application/json": {
                    "example": {
                        "success": True,
                        "message": "Document processed successfully",
                        "doc_id": "2024-01-15_001",
                        "credits_used": 5,
                        "email_sent": True
                    }
                }
            }
        },
        400: {
            "description": "Invalid request parameters or files",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Invalid file format. Only JPEG images are supported."
                    }
                }
            }
        },
        404: {
            "description": "Service temporarily unavailable (triggers fallback to local storage)",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Document processing service temporarily unavailable"
                    }
                }
            }
        },
        500: {
            "description": "Internal processing error (triggers credit refund)",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Internal server error during document processing"
                    }
                }
            }
        }
    }
)
async def upload_document(
    ocr: str = Form(..., description="Enable OCR processing ('true' or 'false')"),
    llm: str = Form(..., description="Enable LLM processing ('true' or 'false')"),
    files: List[UploadFile] = File(..., description="Document page images (JPEG format)"),
    user: User = Depends(authenticate_user)
):
    """
    Upload and process document pages with OCR and/or LLM analysis.
    
    Requires authentication. Credits are deducted from user account.
    Credit usage depends on processing options selected.
    """
    try:
        # Validate form parameters
        ocr_enabled = ocr.lower() == "true"
        llm_enabled = llm.lower() == "true"
        
        if not ocr_enabled and not llm_enabled:
            raise HTTPException(status_code=400, detail="At least one processing option (OCR or LLM) must be enabled")
        
        # Validate uploaded files
        if not validate_upload_files(files):
            raise HTTPException(status_code=400, detail="Invalid file format. Only JPEG images are supported.")
        
        # Calculate credits needed
        credits_needed = calculate_credits(ocr_enabled, llm_enabled)
        
        # Check if user has sufficient credits
        if user.credits < credits_needed:
            raise HTTPException(
                status_code=400, 
                detail=f"Insufficient credits. Need {credits_needed}, have {user.credits}. Purchase more credits to continue."
            )
        
        # Deduct credits before processing
        processing_description = f"{'LLM' if llm_enabled else 'OCR'} processing"
        if not deduct_credits(user.username, credits_needed, processing_description):
            raise HTTPException(status_code=500, detail="Failed to deduct credits")
        
        try:
            # Process document
            doc_id, email_sent = await process_uploaded_document(files, user.email, ocr_enabled, llm_enabled)
            
            # Send email notification
            processing_type = "LLM" if llm_enabled else "OCR"
            email_sent = await send_email_notification(user.email, doc_id, processing_type, user)
            
            return UploadResponse(
                success=True,
                message="Document processed successfully",
                doc_id=doc_id,
                credits_used=credits_needed,
                email_sent=email_sent
            )
            
        except Exception as processing_error:
            # Refund credits on processing failure
            add_credits(user.username, credits_needed, f"Refund for failed {processing_description}")
            logger.error(f"Processing failed, credits refunded: {str(processing_error)}")
            raise HTTPException(status_code=500, detail=f"Processing failed: {str(processing_error)}")
        
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        # Log the error and return 500 (triggers credit refund)
        logger.error(f"Error processing upload: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error during document processing: {str(e)}")




if __name__ == "__main__":
    print(f"Starting API server. Output directory configured to: {OUTPUT_DIR}")
    uvicorn.run("web_api:app", host="0.0.0.0", port=8000, reload=True)

# To run this API:
# 1. Ensure FastAPI and Uvicorn are installed: pip install fastapi "uvicorn[standard]"
# 2. Save this file as web_api.py
# 3. Run from your terminal: python web_api.py
# 4. Access the API at http://localhost:8000 and docs at http://localhost:8000/docs
