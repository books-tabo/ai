"""Bound expensive PDF work before reading originals; never queue unbounded copies."""
import io
import threading
from contextlib import contextmanager

MAX_SOURCE_BYTES=50*1024*1024
MAX_OUTPUT_BYTES=100*1024*1024
MAX_PAGES=2000
_WORK=threading.RLock()

class PDFBusy(RuntimeError): pass

@contextmanager
def work_slot():
    if not _WORK.acquire(blocking=False):raise PDFBusy('PDF worker busy')
    try:yield
    finally:_WORK.release()

class LimitedBuffer(io.BytesIO):
    def write(self,value):
        if self.tell()+len(value)>MAX_OUTPUT_BYTES:raise ValueError('PDF output size limit')
        return super().write(value)

def check_source(value):
    if not isinstance(value,bytes) or not value.startswith(b'%PDF-') or len(value)>MAX_SOURCE_BYTES:
        raise ValueError('Invalid or oversized PDF')

def check_page_size(width,height):
    import math
    if not all(math.isfinite(value) and 0 < value <= 14400 for value in (width,height)):
        raise ValueError('Invalid PDF page dimensions')
