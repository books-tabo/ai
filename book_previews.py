"""Build bounded public samples without publishing the private originals."""
import io
import threading
import time
from collections import OrderedDict

from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject

SAMPLE_PAGE_LIMIT = 16
_CACHE = OrderedDict()
_LOCK = threading.Lock()
_CACHE_TTL = 3600
_CACHE_BYTES = 48 * 1024 * 1024


def make_sample(source, title):
    reader = PdfReader(io.BytesIO(source))
    total = len(reader.pages)
    if total < 2:
        raise ValueError('A public sample must not contain the complete book')
    count = min(SAMPLE_PAGE_LIMIT, max(1, total // 10), total - 1)
    writer = PdfWriter()
    for index in range(count):
        writer.add_page(reader.pages[index])
        # Do not carry document actions, file attachments, or external links.
        writer.pages[-1].pop(NameObject('/Annots'), None)
    writer.add_metadata({'/Title': title + ' — عينة مجانية', '/Author': 'TABO NOOR'})
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue(), count


def get_sample(book_id, books, fetch_original):
    """Allowlisted IDs only; one generation at a time bounds memory usage."""
    if book_id not in books:
        raise KeyError(book_id)
    with _LOCK:
        cached = _CACHE.get(book_id)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL:
            _CACHE.move_to_end(book_id)
            return cached[1], cached[2]
        title, filename = books[book_id]
        payload, count = make_sample(fetch_original(filename), title)
        _CACHE[book_id] = (time.monotonic(), payload, count)
        _CACHE.move_to_end(book_id)
        while sum(len(value[1]) for value in _CACHE.values()) > _CACHE_BYTES:
            _CACHE.popitem(last=False)
        return payload, count
