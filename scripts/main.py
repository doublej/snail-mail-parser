import argparse
import logging
# import threading # Removed
import time
from queue import Queue

from snail_mail_parser.core.settings import Settings
from snail_mail_parser.utils.watcher import FolderWatcher
from snail_mail_parser.core.processor import Processor

def main():
    # Configure logging
    logging.basicConfig(level=logging.INFO, # Changed to INFO for less verbosity
                        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    parser = argparse.ArgumentParser()
    parser.add_argument('--flush', action='store_true', help='Flush all open sessions immediately')
    args = parser.parse_args()

    settings = Settings()

    # Create a queue for document sessions
    q = Queue()

    # Initialize FolderWatcher and Processor
    watcher = FolderWatcher(settings.scan_dir, q)
    processor = Processor(settings, q)

    # watcher.start() # Call start if it performs necessary setup, otherwise it can be removed
                      # In the new watcher, start() is mostly a placeholder.

    # If flush flag is set, call the processor's flush_open_documents method.
    if args.flush:
        print("Processor: --flush argument provided. Flushing all open documents...")
        processor.flush_open_documents() 
        print("Processor: Flushing of open documents by --flush argument is complete.")

    print(f"Application started successfully. Monitoring {settings.scan_dir} for new files.")
    print(f"Scan interval: {settings.scan_interval_s}s.")
    print("Press Ctrl+C to stop.")

    try:
        while True:
            # 1. Scan for new files (watcher will add them to the queue individually)
            watcher.scan_for_new_files()

            # 2. The call to watcher.check_session_timeouts() is removed as the method no longer exists.
            
            # 3. Process one item from the queue if available
            processed_item = processor.process_next_item_from_queue()

            # Sleep a bit to avoid busy-waiting if there's nothing to do.
            # If an item was processed, we might want a shorter or no sleep
            # to quickly process subsequent items. If not, sleep for the scan_interval.
            if not processed_item and q.empty(): # Only sleep full interval if no work was done AND queue is empty
                time.sleep(settings.scan_interval_s)
            else:
                # If work was done or queue still has items, sleep very briefly to yield
                # or allow quick succession of processing.
                time.sleep(0.1) 

    except KeyboardInterrupt:
        print("\nCtrl+C pressed. Shutting down gracefully...")
        
        print("Stopping file watcher to prevent new items from being queued...")
        watcher.stop() 
        
        print("Processing any remaining items already in the queue...")
        while not q.empty():
            processor.process_next_item_from_queue()
        print("Queue processing complete.")

        print("Flushing all open document sessions (saving them as complete)...")
        processor.flush_open_documents() 
        
        print("Application shutdown complete. All open work has been saved.")

if __name__ == '__main__':
    main()
