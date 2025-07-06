# Document Processing Application

This Python application monitors a directory for incoming scanned documents (images or PDFs), performs OCR and QR-code scanning, classifies the document content using an LLM, and writes structured outputs (YAML and Markdown). It is organized into the following modules:

* **settings.py:** Loads configuration via Pydantic-Settings.
* **watcher.py:** Uses `watchdog` to watch a folder, group multi-page scans into sessions, and enqueue complete documents.
* **ocr.py:** Performs OCR on images or PDFs using `pytesseract` and `pdf2image`.
* **qr.py:** Scans images for QR codes with `pyzbar`.
* **llm.py:** Calls the OpenAI (via OpenRouter) API to classify the document into a JSON schema, validated by Pydantic models.
* **output.py:** Writes the classification result to YAML (with `ruamel.yaml`) and Markdown (via `markdown-it-py`).
* **processor.py:** Worker that pulls document pages from a queue, runs OCR/QR/LLM, and saves outputs, handling errors and concurrency.
* **main.py:** Entry point: initializes settings, starts the watcher and processor, handles graceful shutdown and an optional `--flush` flag.
* **requirements.txt:** Lists required Python packages.
* **setup.sh:** Shell script to install system deps, set up a virtualenv, install Python packages, and initialize a `.env` file template.

Below are the implementation details for each module.

## settings.py

The `Settings` class uses pydantic-settings to load configuration from environment variables or a `.env` file. All important paths and constants are configurable here.

```python
# settings.py
from pydantic_settings import BaseSettings
from pathlib import Path

class Settings(BaseSettings):
    scan_dir: Path             # Directory to monitor for new files
    output_dir: Path           # Directory to write YAML/Markdown outputs
    session_timeout_s: int = 15
    max_inflight: int = 1
    llm_api_key: str
    llm_model: str = "openai/gpt-4o"
    llm_base_url: str = "https://openrouter.ai/api/v1"

    class Config:
        env_file = ".env"
        env_file_encoding = 'utf-8'
```

* **scan\_dir/output\_dir:** Paths for input and output directories.
* **session\_timeout\_s:** Seconds to wait for more pages in a session before flushing (default 15s).
* **max\_inflight:** Maximum concurrent documents to process (default 1).
* **llm\_**\*: Configuration for the LLM API (API key, model name, base URL).

Settings are loaded from a `.env` file or environment variables, thanks to pydantic’s `BaseSettings`. Example `.env` keys are given in the setup script below.

## watcher.py

The `FolderWatcher` class uses `watchdog` to monitor `Settings.scan_dir`. It handles file creation/move events, groups multi-page scans, and enqueues completed documents.

Key behaviors:

* **Accept file types:** `.png`, `.jpg`, `.jpeg`, `.tif`, `.tiff`, `.pdf`.
* **PDFs:** Treated as standalone documents; immediately enqueued as single-page documents.
* **Image pages:** Filenames ending in `_<digits>` are grouped. E.g., `SCAN_20250527_142301_1.png` and `_2.png` belong to one session. Trailing `_<num>` is stripped for grouping.
* **Session tracking:** Maintains a dict of active sessions (`prefix -> pages, last_seen`). Each new page updates `last_seen`.
* **Timeout flushing:** A background thread checks sessions periodically. If `now - last_seen >= timeout`, the session is flushed: the pages (sorted by their trailing number) are enqueued and the session removed.
* **Manual flush:** The `--flush` CLI flag causes all open sessions to flush immediately.
* **Thread safety:** A lock protects the sessions dict.

```python
# watcher.py
import time
import re
import threading
from pathlib import Path
from queue import Queue
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class FolderWatcher(FileSystemEventHandler):
    allowed_exts = {'.png', '.jpg', '.jpeg', '.tif', '.tiff', '.pdf'}

    def __init__(self, scan_dir: Path, queue: Queue, timeout: int):
        self.scan_dir = scan_dir
        self.queue = queue
        self.timeout = timeout
        self.sessions = {}  # prefix -> {'pages': [(num, Path)], 'last_seen': timestamp}
        self.observer = Observer()
        self.pattern = re.compile(r"(.+)_([0-9]+)$")
        self._lock = threading.Lock()
        self._running = False

    def start(self):
        self.observer.schedule(self, str(self.scan_dir), recursive=False)
        self._running = True
        self.observer.start()
        # Start session timeout checker thread
        threading.Thread(target=self._session_checker, daemon=True).start()

    def stop(self):
        self._running = False
        self.observer.stop()
        self.observer.join()

    def on_created(self, event):
        self._handle_event(event)

    def on_moved(self, event):
        self._handle_event(event)

    def _handle_event(self, event):
        if event.is_directory:
            return
        # Determine file path for created or moved
        path = Path(event.dest_path) if hasattr(event, 'dest_path') else Path(event.src_path)
        ext = path.suffix.lower()
        if ext not in self.allowed_exts:
            return
        if ext == '.pdf':
            # PDF is standalone - enqueue immediately
            self._flush_pages([path])
            return

        # Handle image page for aggregation
        stem = path.stem
        m = self.pattern.match(stem)
        if m:
            prefix = m.group(1)
            page_num = int(m.group(2))
        else:
            prefix = stem
            page_num = 0
        with self._lock:
            if prefix not in self.sessions:
                self.sessions[prefix] = {'pages': [], 'last_seen': time.time()}
            session = self.sessions[prefix]
            session['pages'].append((page_num, path))
            session['last_seen'] = time.time()

    def _session_checker(self):
        # Periodically flush sessions that have timed out
        while self._running:
            now = time.time()
            to_flush = []
            with self._lock:
                for prefix, sess in list(self.sessions.items()):
                    if now - sess['last_seen'] >= self.timeout:
                        to_flush.append(prefix)
                for prefix in to_flush:
                    sess = self.sessions.pop(prefix, None)
                    if sess:
                        # Sort pages by the trailing number and enqueue
                        pages = [p for _, p in sorted(sess['pages'], key=lambda x: x[0])]
                        self._flush_pages(pages)
            time.sleep(1)

    def _flush_pages(self, pages):
        if pages:
            self.queue.put(pages)

    def flush_all(self):
        """Manually flush all sessions (used by --flush flag)."""
        with self._lock:
            for prefix, sess in list(self.sessions.items()):
                pages = [p for _, p in sorted(sess['pages'], key=lambda x: x[0])]
                self.queue.put(pages)
                self.sessions.pop(prefix, None)
```

## ocr.py

The `ocr.py` module provides OCR functionality:

* **ocr\_image(path):** Uses Pillow to open an image and `pytesseract.image_to_string` to get text. It also calls `pytesseract.image_to_data` to compute a mean confidence (optional).
* **ocr\_pdf(path):** Converts each page of a PDF to an image with `pdf2image.convert_from_path`, then runs OCR on each page. Returns the combined text and average confidence across pages.

```python
# ocr.py
from pathlib import Path
from typing import Tuple
from PIL import Image
import pytesseract
from pdf2image import convert_from_path

def ocr_image(path: Path) -> Tuple[str, float]:
    """
    Perform OCR on an image file.
    Returns extracted text and mean confidence.
    """
    image = Image.open(path)
    text = pytesseract.image_to_string(image)
    # Get OCR data to compute confidence scores
    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
    confs = [int(c) for c in data.get('conf', []) if c != '-1']
    mean_conf = sum(confs) / len(confs) if confs else 0.0
    return text, mean_conf

def ocr_pdf(path: Path) -> Tuple[str, float]:
    """
    Convert PDF to images and perform OCR on each page.
    Returns combined text and mean confidence across all pages.
    """
    text_all = ""
    all_confs = []
    images = convert_from_path(str(path))
    for image in images:
        text = pytesseract.image_to_string(image)
        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
        confs = [int(c) for c in data.get('conf', []) if c != '-1']
        if confs:
            all_confs.extend(confs)
        text_all += text + "\n"
    mean_conf = sum(all_confs) / len(all_confs) if all_confs else 0.0
    return text_all, mean_conf
```

* **Dependencies:** Requires `poppler-utils` installed on the system for `pdf2image`.

## qr.py

The `qr.py` module scans an image for QR codes using `pyzbar`:

```python
# qr.py
from pathlib import Path
from typing import List
from pyzbar.pyzbar import decode
from PIL import Image

def scan_qr(path: Path) -> List[str]:
    """
    Decode all QR codes in the image file and return list of decoded strings.
    """
    image = Image.open(path)
    decoded_objects = decode(image)
    return [obj.data.decode('utf-8') for obj in decoded_objects if obj.data]
```

* This returns all QR payloads found in the image.

## llm.py

The `llm.py` module interacts with the OpenAI API (via OpenRouter) to classify the document. It defines Pydantic models and a function `classify_document`:

* **Pydantic Models:** `Payment` and `LetterLLMResponse` reflect the expected JSON schema.
* **classify\_document:** Sends a prompt containing the OCR text and QR payloads to the LLM. The prompt instructs the model to output a JSON object with fields: `id, sender, date_sent, subject, type, content, qr_payloads (list), payment {iban, amount, due_date}`. The code uses `response_format={"type": "json_object"}` (supported by GPT-4o) so the API returns JSON. The JSON is validated against `LetterLLMResponse`. The document ID is generated by us and enforced in the result.

```python
# llm.py
import openai
from typing import List
from pydantic import BaseModel, ValidationError

class Payment(BaseModel):
    iban: str
    amount: float
    due_date: str

class LetterLLMResponse(BaseModel):
    id: str
    sender: str
    date_sent: str
    subject: str
    type: str
    content: str
    qr_payloads: List[str]
    payment: Payment

def classify_document(text: str, qr_payloads: List[str], doc_id: str, settings) -> LetterLLMResponse:
    """
    Send OCR text and QR payloads to LLM and parse the JSON response into a LetterLLMResponse.
    """
    # Configure OpenAI (OpenRouter) API credentials
    openai.api_base = settings.llm_base_url
    openai.api_key = settings.llm_api_key

    # Build prompt for classification
    prompt = (
        "You are an AI assistant that classifies documents. "
        "Given the OCR-extracted text of a document, extract relevant information into a JSON object "
        "with the following fields: id, sender, date_sent, subject, type, content, qr_payloads (list of strings), "
        "and payment (an object with iban, amount, due_date). "
        "The qr_payloads should include any QR code content found in the document.\n\n"
        f"Document ID: {doc_id}\n"
        f"OCR Text: \"\"\"\n{text}\n\"\"\"\n"
        f"QR Payloads: {qr_payloads}\n\n"
        "Return a JSON object."
    )
    response = openai.ChatCompletion.create(
        model=settings.llm_model,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"}
    )
    content = response["choices"][0]["message"]["content"]
    # Parse JSON (it may come as a dict already, or a string)
    if isinstance(content, str):
        import json
        content = json.loads(content)

    try:
        result = LetterLLMResponse.parse_obj(content)
    except ValidationError:
        # If id was missing or invalid, insert our doc_id and retry parsing
        content['id'] = doc_id
        result = LetterLLMResponse.parse_obj(content)

    # Override any ID with our generated ID (ensuring consistency)
    result.id = doc_id
    return result
```

* **Note:** `doc_id` is an argument we generate (see `processor.py` below) and enforce in the result.

## output.py

This module takes a `LetterLLMResponse` and writes it to YAML and Markdown in `Settings.output_dir`:

* **YAML:** Uses `ruamel.yaml` to dump the data.
* **Markdown:** Constructs a Markdown string with a YAML front-matter style header listing metadata fields, then the content.

```python
# output.py
from ruamel.yaml import YAML
from markdown_it import MarkdownIt

def save_output(letter, settings):
    """
    Save the LLM response as YAML and Markdown in the output directory.
    """
    out_dir = settings.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # Prepare data dict for YAML
    data = {
        "id": letter.id,
        "sender": letter.sender,
        "date_sent": letter.date_sent,
        "subject": letter.subject,
        "type": letter.type,
        "content": letter.content,
        "qr_payloads": letter.qr_payloads,
        "payment": {
            "iban": letter.payment.iban,
            "amount": letter.payment.amount,
            "due_date": letter.payment.due_date
        }
    }

    # Write YAML file
    yaml_path = out_dir / f"{letter.id}.yaml"
    yaml = YAML()
    with open(yaml_path, 'w') as yf:
        yaml.dump(data, yf)

    # Prepare Markdown content with metadata header
    md_lines = []
    md_lines.append("---")
    md_lines.append(f"id: {letter.id}")
    md_lines.append(f"sender: {letter.sender}")
    md_lines.append(f"date_sent: {letter.date_sent}")
    md_lines.append(f"subject: {letter.subject}")
    md_lines.append(f"type: {letter.type}")
    md_lines.append("qr_payloads:")
    for payload in letter.qr_payloads:
        md_lines.append(f"  - {payload}")
    md_lines.append("payment:")
    md_lines.append(f"  iban: {letter.payment.iban}")
    md_lines.append(f"  amount: {letter.payment.amount}")
    md_lines.append(f"  due_date: {letter.payment.due_date}")
    md_lines.append("---\n")
    md_lines.append(letter.content)

    md_text = "\n".join(md_lines)
    # Optionally render with markdown-it-py (not strictly required)
    md = MarkdownIt()
    _ = md.render(md_text)  # Ensures formatting is valid

    # Write Markdown file
    md_path = out_dir / f"{letter.id}.md"
    with open(md_path, 'w') as mf:
        mf.write(md_text)
```

The Markdown file begins with a YAML-like header (delimited by `---`) containing all fields, followed by the `content` from the LLM.

## processor.py

The `Processor` pulls completed document sessions from a `Queue` and processes them:

Steps for each session (list of pages):

1. **OCR:** Run OCR on each page (image or PDF). Concatenate all page texts into one string.
2. **QR Scanning:** For each image page, decode QR codes and collect payloads.
3. **Generate ID:** Create a unique document ID (based on current date + a sequence number).
4. **LLM Classification:** Call `classify_document(text, qr_payloads, doc_id)`.
5. **Save Outputs:** On success, write YAML and Markdown via `output.save_output`.
6. **Error Handling:** On any exception, write a `.error` file in the output directory containing the full traceback.

Concurrency is controlled by a `Semaphore(Settings.max_inflight)`. The default is 1 (sequential processing). Each session is processed in a separate thread (respecting the semaphore).

```python
# processor.py
import threading
import traceback
from datetime import datetime
from queue import Queue

from ocr import ocr_image
from processor import convert_pdf_to_pil
from qr import scan_qr
from llm import classify_document
from output import save_output


class Processor:
    def __init__(self, settings, queue: Queue):
        self.settings = settings
        self.queue = queue
        self.doc_seq = 0
        self.lock = threading.Lock()
        self.semaphore = threading.Semaphore(settings.max_inflight)

    def get_new_doc_id(self) -> str:
        """Generate a new document ID: YYYYMMDD-XXXX sequence."""
        with self.lock:
            self.doc_seq += 1
            return f"{datetime.now().strftime('%Y%m%d')}-{self.doc_seq:04d}"

    def process_pages(self, pages):
        try:
            text_all = ""
            qr_payloads = []
            for page in pages:
                if page.suffix.lower() == '.pdf':
                    text, _ = convert_pdf_to_pil(page)
                    text_all += text + "\n"  # Optionally handle QR scanning inside PDFs if needed
                else:
                    text, _ = ocr_image(page)
                    text_all += text + "\n"
                    qr_payloads.extend(scan_qr(page))
            doc_id = self.get_new_doc_id()
            letter = classify_document(text_all, qr_payloads, doc_id, self.settings)
            save_output(letter, self.settings)
        except Exception:
            # On error, write a .error file with full traceback
            doc_id = locals().get('doc_id', 'unknown')
            error_path = self.settings.output_dir / f"{doc_id}.error"
            with open(error_path, 'w') as ef:
                ef.write(traceback.format_exc())

    def _worker(self, pages):
        try:
            self.process_pages(pages)
        finally:
            self.semaphore.release()

    def run(self):
        """
        Continuously consume page lists from the queue and process them.
        Use a semaphore to limit concurrency.
        """
        while True:
            pages = self.queue.get()
            if pages is None:  # Sentinel to stop processing
                break
            self.semaphore.acquire()
            thread = threading.Thread(target=self._worker, args=(pages,), daemon=True)
            thread.start()
```

**Usage:** Create a `Processor` with the shared queue, then call `run()` (usually in its own thread). Each `pages` item from the queue should be a list of `Path` objects (images or a single PDF).

## main.py

The entry point sets everything up:

1. Parse `--flush` flag.
2. Load `Settings`.
3. Create a `Queue` for communication.
4. Start the `FolderWatcher` on `Settings.scan_dir`.
5. Start the `Processor` (in a background thread).
6. If `--flush` is given, call `watcher.flush_all()` immediately.
7. Sleep forever (or until Ctrl+C).
8. On keyboard interrupt, gracefully stop the watcher and signal the processor to exit.

```python
# main.py
import argparse
import threading
import time
from queue import Queue

from settings import Settings
from watcher import FolderWatcher
from processor import Processor

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--flush', action='store_true', help='Flush all open sessions immediately')
    args = parser.parse_args()

    settings = Settings()

    # Create a queue for document sessions
    q = Queue()

    # Start folder watcher
    watcher = FolderWatcher(settings.scan_dir, q, settings.session_timeout_s)
    watcher.start()

    # Start processor in a separate thread
    processor = Processor(settings, q)
    proc_thread = threading.Thread(target=processor.run, daemon=True)
    proc_thread.start()

    # If flush flag is set, flush existing sessions
    if args.flush:
        watcher.flush_all()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Shutting down...")
        watcher.stop()
        # Signal processor to exit by sending None
        q.put(None)
        proc_thread.join()

if __name__ == '__main__':
    main()
```

* Running `python main.py` will start the service.
* Use `python main.py --flush` to flush any in-progress sessions immediately (after watcher and processor start).