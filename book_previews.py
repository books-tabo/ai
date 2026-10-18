"""Render limited reading samples. PDF bytes remain on the server."""
import hashlib
import hmac
import io
import re
import secrets
import threading
import time
from collections import OrderedDict

import pypdfium2 as pdfium
from PIL import ImageDraw, ImageFont
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject
from pdf_limits import work_slot,check_source,MAX_PAGES,LimitedBuffer

SAMPLE_PAGE_LIMIT = 16
_CACHE = OrderedDict()
_LOCK = threading.Lock()
_CACHE_TTL = 3600
_CACHE_BYTES = 48 * 1024 * 1024
_RENDER_LOCK = threading.Lock()  # PDFium calls must never run concurrently.
_IMAGE_CACHE = OrderedDict()
_IMAGE_CACHE_BYTES = 24 * 1024 * 1024
SESSION_SECONDS = 1800
_SESSION_KEY = secrets.token_bytes(32)


def _signature(book_id, expiry, nonce, user_agent):
    agent = hashlib.sha256(user_agent[:512].encode()).hexdigest()
    payload = f'{book_id}|{expiry}|{nonce}|{agent}'.encode()
    return hmac.new(_SESSION_KEY, payload, hashlib.sha256).hexdigest()


def issue_session(book_id, user_agent):
    expiry = str(int(time.time()) + SESSION_SECONDS)
    nonce = secrets.token_hex(16)
    return '.'.join((expiry, nonce, _signature(book_id, expiry, nonce, user_agent)))


def valid_session(token, book_id, user_agent):
    if not isinstance(token, str) or not re.fullmatch(r'[0-9]{10,12}\.[a-f0-9]{32}\.[a-f0-9]{64}', token):
        return False
    expiry, nonce, signature = token.split('.')
    if not int(time.time()) < int(expiry) <= int(time.time()) + SESSION_SECONDS:
        return False
    return hmac.compare_digest(signature, _signature(book_id, expiry, nonce, user_agent))


def make_sample(source, title):
    check_source(source)
    reader = PdfReader(io.BytesIO(source))
    total = len(reader.pages)
    if total>MAX_PAGES: raise ValueError('PDF page limit')
    if total < 2:
        raise ValueError('A public sample must not contain the complete book')
    count = min(SAMPLE_PAGE_LIMIT, max(1, total // 10), total - 1)
    writer = PdfWriter()
    for index in range(count):
        writer.add_page(reader.pages[index])
        # Do not carry document actions, file attachments, or external links.
        writer.pages[-1].pop(NameObject('/Annots'), None)
    writer.add_metadata({'/Title': title + ' — عينة مجانية', '/Author': 'TABO NOOR'})
    output = LimitedBuffer()
    writer.write(output)
    return output.getvalue(), count


def get_sample(book_id, books, fetch_original):
    """Server-internal PDF subset. Never return these bytes from an HTTP route."""
    if book_id not in books:
        raise KeyError(book_id)
    with work_slot(), _LOCK:
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


def render_page(book_id, page_number, books, fetch_original):
    """One bounded raster page only; the complete PDF never leaves the server."""
    if book_id not in books:
        raise KeyError(book_id)
    if isinstance(page_number, bool) or not isinstance(page_number, int) or not 1 <= page_number <= SAMPLE_PAGE_LIMIT:
        raise IndexError('Page outside sample')
    with work_slot(), _RENDER_LOCK:
        key = (book_id, page_number)
        cached = _IMAGE_CACHE.get(key)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL:
            _IMAGE_CACHE.move_to_end(key)
            return cached[1]
        sample, count = get_sample(book_id, books, fetch_original)
        if page_number > count:
            raise IndexError('Page outside sample')
        with pdfium.PdfDocument(sample) as document:
            page = document[page_number - 1]
            try:
                width, height = page.get_size()
                if min(width, height) <= 0:
                    raise ValueError('Invalid page size')
                scale = min(1400 / width, 2000 / height, 2.5)
                bitmap = page.render(scale=scale)
                try:
                    image = bitmap.to_pil().convert('RGB')
                finally:
                    bitmap.close()
            finally:
                page.close()
        try:
            draw = ImageDraw.Draw(image)
            font = ImageFont.load_default(size=max(12, image.width // 64))
            text = f'TABO BOOKS | PREVIEW {page_number}'
            box = draw.textbbox((0, 0), text, font=font)
            padding = max(8, image.width // 100)
            x = (image.width - (box[2] - box[0])) // 2
            y = image.height - (box[3] - box[1]) - padding * 2
            draw.rounded_rectangle((x - padding, y - padding, x + box[2] + padding, y + box[3] + padding), radius=4, fill='white')
            draw.text((x, y), text, font=font, fill='#8b7949')
            output = io.BytesIO()
            image.save(output, format='JPEG', quality=86, optimize=True)
            payload = output.getvalue()
        finally:
            image.close()
        _IMAGE_CACHE[key] = (time.monotonic(), payload)
        _IMAGE_CACHE.move_to_end(key)
        while sum(len(value[1]) for value in _IMAGE_CACHE.values()) > _IMAGE_CACHE_BYTES:
            _IMAGE_CACHE.popitem(last=False)
        return payload

