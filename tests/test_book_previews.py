import io
import time
import unittest
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen
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

    def test_reader_never_serves_pdf_and_requires_browser_session(self):
        server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with patch.object(app.store, 'original', return_value=fixture(200)) as fetch, \
             patch.object(app.store, 'claim_download') as claim:
            base = 'http://127.0.0.1:' + str(server.server_port)
            agent = 'TaboPreviewTest'
            with urlopen(Request(base + '/previews/book1', headers={'User-Agent':agent}), timeout=3) as response:
                self.assertIn('text/html', response.headers['Content-Type'])
                self.assertIn('noindex', response.headers['X-Robots-Tag'])
                cookie = response.headers['Set-Cookie']
                for value in ('HttpOnly', 'Secure', 'SameSite=Strict', 'Path=/previews/book1/'):
                    self.assertIn(value,cookie)
                cookie = cookie.split(';',1)[0]
            headers={'Cookie':cookie,'User-Agent':agent,'X-Tabo-Preview':'1','Sec-Fetch-Dest':'empty','Sec-Fetch-Site':'same-origin'}
            with urlopen(Request(base+'/previews/book1/info',headers=headers),timeout=3) as response:
                self.assertIn(b'"pages": 16',response.read())
            with urlopen(Request(base+'/previews/book1/page/1',headers=headers),timeout=3) as response:
                self.assertEqual(response.headers['Content-Type'],'image/jpeg')
                self.assertEqual(response.headers['Cache-Control'],'private, no-store')
                self.assertTrue(response.read().startswith(b'\xff\xd8'))
                self.assertIsNone(response.headers.get('Content-Disposition'))
            for change in ({'Cookie':''},{'X-Tabo-Preview':''},{'User-Agent':'other browser'},
                           {'Sec-Fetch-Dest':'document'},{'Sec-Fetch-Site':'cross-site'}):
                with self.assertRaises(HTTPError) as caught:
                    urlopen(Request(base+'/previews/book1/page/1',headers={**headers,**change}),timeout=3)
                self.assertEqual(caught.exception.code,403)
            for path,status in [('/previews/book1/page/17',404),('/previews/book1.pdf',410),
                                ('/previews/book1.pdf?pages=all',410),('/previews/book999.pdf',410)]:
                with self.assertRaises(HTTPError) as caught:
                    urlopen(Request(base+path,headers=headers),timeout=3)
                self.assertEqual(caught.exception.code,status)
                self.assertEqual(caught.exception.headers['Cache-Control'],'no-store')
            fetch.assert_called_once_with('book1.pdf')
            claim.assert_not_called()

    def test_session_is_expiring_and_bound_to_book_and_browser(self):
        token=book_previews.issue_session('book1','browser')
        self.assertTrue(book_previews.valid_session(token,'book1','browser'))
        self.assertFalse(book_previews.valid_session(token,'book2','browser'))
        self.assertFalse(book_previews.valid_session(token,'book1','other'))
        self.assertFalse(book_previews.valid_session(token[:-1]+'x','book1','browser'))
        self.assertFalse(book_previews.valid_session(None,'book1','browser'))
        with patch.object(book_previews.time,'time',return_value=time.time()+1801):
            self.assertFalse(book_previews.valid_session(token,'book1','browser'))

    def test_render_rejects_pages_outside_sample_before_fetch(self):
        fetch=Mock(return_value=fixture(200))
        for number in (0,17,-1,True):
            with self.assertRaises(IndexError):
                book_previews.render_page('book1',number,{'book1':('Title','book1.pdf')},fetch)
        fetch.assert_not_called()


if __name__ == '__main__':
    unittest.main()

