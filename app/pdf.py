"""PyMuPDF isn't thread-safe, and figures are processed on a thread pool (app/pipeline.py), so
every use of it outside the single-threaded ingest/detect pass holds this lock."""

import threading

LOCK = threading.RLock()
