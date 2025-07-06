# import threading # Removed
from pathlib import Path
from queue import Queue
# from watchdog.observers.polling import PollingObserver as Observer # Removed
# from watchdog.events import FileSystemEventHandler # Removed
import os # For manual scanning


class FolderWatcher: # No longer inherits from FileSystemEventHandler
    allowed_exts = {'.png', '.jpg', '.jpeg', '.tif', '.tiff', '.pdf'}

    def __init__(self, scan_dir: Path, queue: Queue):
        self.scan_dir = scan_dir
        self.queue = queue
        # self.sessions, self.pattern, self.timeout and related session logic removed
        self.known_files = set()

        # self.known_files is initialized as an empty set.
        # Files existing in scan_dir upon startup will be processed on the first scan.
        print(f"Watcher: Initializing. Existing files in {self.scan_dir} will be processed.")
        try:
            if not (self.scan_dir.exists() and self.scan_dir.is_dir()):
                print(f"Warning: Scan directory {self.scan_dir} does not exist or is not a directory.")
                # Optionally create it: self.scan_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            # This exception would likely be from self.scan_dir.exists() or .is_dir() if path is invalid
            print(f"Error checking scan directory {self.scan_dir}: {e}")
        print(f"Watcher: Initialized. known_files set is empty. All files in {self.scan_dir} will be scanned.")


    def start(self):
        # No observer to start, this method is for any initial setup if needed
        # or can be removed if __init__ handles all setup.
        print(f"Watcher: Manual scanning mode configured for {self.scan_dir}.")
        pass

    def stop(self):
        # No observer to stop
        print("Watcher: Stopped.")
        pass

    def _handle_new_file(self, path: Path):
        """
        Processes a single newly detected file and enqueues it immediately.
        All session-based grouping logic is removed.
        """
        print(f"Watcher: Handling new file: {path}")
        ext = path.suffix.lower() # Still useful for logging or potential future filtering

        # This check should ideally be redundant if scan_for_new_files filters correctly,
        # but it's a good safeguard.
        if ext not in self.allowed_exts:
            print(f"Watcher: File {path} with unsupported extension {ext} was handled. This should not happen if scan_for_new_files is correct.")
            return

        # All allowed files (PDFs and images) are now treated as individual documents
        # to be processed by the Processor. The Processor will use the LLM for multi-page determination.
        print(f"Watcher: File {path} detected. Enqueuing as a single-item list for processing.")
        self._flush_pages([path]) # Enqueue the file path as a list containing a single path

    def scan_for_new_files(self):
        """Scans the directory for new files and processes them."""
        # print(f"Watcher: Scanning {self.scan_dir} for new files...") # Can be noisy
        try:
            if not self.scan_dir.exists() or not self.scan_dir.is_dir():
                # print(f"Watcher: Scan directory {self.scan_dir} not found or not a directory. Skipping scan.")
                return

            current_files_on_disk = {self.scan_dir / item_name for item_name in os.listdir(self.scan_dir) if (self.scan_dir / item_name).is_file()}
        except Exception as e:
            print(f"Error listing files in {self.scan_dir}: {e}")
            return

        new_files = current_files_on_disk - self.known_files
        
        if new_files:
            # print(f"Watcher: Found {len(new_files)} new file(s).")
            for path in sorted(list(new_files)): # Sort for deterministic processing order
                if path.suffix.lower() in self.allowed_exts:
                    self._handle_new_file(path)
                # Add all new files to known_files, whether processed or not, to avoid re-evaluating
                self.known_files.add(path)
        # else:
            # print(f"Watcher: No new files found.") # Can be noisy

        # Optional: Clean up known_files if files are deleted from disk
        # self.known_files = self.known_files.intersection(current_files_on_disk) # Optional cleanup

    # Removed check_session_timeouts method as session logic is gone.

    def _flush_pages(self, pages): # pages is now expected to be a list with a single Path
        if pages:
            print(f"Watcher: Adding to queue: {pages}")
            self.queue.put(pages)
        # else: 
            # print(f"Watcher: _flush_pages called with no pages. Nothing to queue.") # Should not happen often now

    # Removed flush_all method as session logic is gone.
    # The --flush CLI argument in main.py should now target processor.flush_open_documents() instead.


