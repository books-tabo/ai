import io
import time
import unittest
import threading
from urllib.error import HTTPError
from urllib.request import urlopen
from unittest.mock import Mock, patch

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, TextStringObject

import book_previews
import app


def fixture(pages=100):
    writer = PdfWriter()
    for index in range(pages):
        page = writer.add_blank_page(width=200 + index, height=300)
        page[NameObject('/Annots')] = ArrayObject([DictionaryObject({
            NameObject('/Type'): NameObject('/Annot'),
            NameObject('/Subtype'): NameObject('/Text'),
            NameObject('/Contents'): TextStringObject('private attachment or link'),
        })])
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class BookPreviewTests(unittest.TestCase):
    def setUp(self):
        book_previews._CACHE.clear()

    def test_sample_excludes_rest_of_book_and_document_actions(self):
        payload, count = book_previews.make_sample(fixture(200), 'Test book')
        reader = PdfReader(io.BytesIO(payload))
        self.assertEqual(count, 16)
        self.assertEqual(len(reader.pages), 16)
        self.assertEqual(float(reader.pages[-1].mediabox.width), 215)
        self.assertTrue(all('/Annots' not in page for page in reader.pages))
        self.assertNotIn('/EmbeddedFiles', reader.trailer['/Root'])
        self.assertNotIn('/OpenAction', reader.trailer['/Root'])

    def test_short_sample_never_returns_complete_original(self):
        payload, count = book_previews.make_sample(fixture(2), 'Short book')
        self.assertEqual(count, 1)
        self.assertEqual(len(PdfReader(io.BytesIO(payload)).pages), 1)
        with self.assertRaises(ValueError):
            book_previews.make_sample(fixture(1), 'One page')

    def test_allowlist_cache_and_expiry(self):
        fetch = Mock(return_value=fixture(200))
        books = {'book1': ('Title', 'book1.pdf')}
        first = book_previews.get_sample('book1', books, fetch)
        self.assertEqual(book_previews.get_sample('book1', books, fetch), first)
        fetch.assert_called_once_with('book1.pdf')
        with self.assertRaises(KeyError):
            book_previews.get_sample('../book1.pdf', books, fetch)
        with patch.object(book_previews.time, 'monotonic', return_value=time.monotonic() + 4000):
            book_previews.get_sample('book1', books, fetch)
        self.assertEqual(fetch.call_count, 2)

    def test_public_route_only_serves_sample_without_download_claim(self):
        server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with patch.object(app.store, 'original', return_value=fixture(200)) as fetch, \
             patch.object(app.store, 'claim_download') as claim:
            base = 'http://127.0.0.1:' + str(server.server_port)
            with urlopen(base + '/previews/book1.pdf?pages=all', timeout=3) as response:
                self.assertEqual(response.headers['Content-Type'], 'application/pdf')
                self.assertEqual(response.headers['X-Preview-Pages'], '16')
                self.assertIn('noindex', response.headers['X-Robots-Tag'])
                self.assertEqual(len(PdfReader(io.BytesIO(response.read())).pages), 16)
            with self.assertRaises(HTTPError) as caught:
                urlopen(base + '/previews/book999.pdf', timeout=3)
            self.assertEqual(caught.exception.code, 404)
            fetch.assert_called_once_with('book1.pdf')
            claim.assert_not_called()


if __name__ == '__main__':
    unittest.main()
