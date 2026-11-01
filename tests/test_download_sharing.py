"""Public sharing uses product metadata; delivery remains device-bound."""
from urllib.request import Request
from urllib.parse import urlparse, parse_qs
import html
import re

import app
import seo
from support import HTTPCase


class DownloadSharingHTTPTests(HTTPCase):
    def test_new_scripts_are_served_and_versioned_in_their_pages(self):
        for asset in ('download-progress.js', 'book-sharing.js'):
            with self.opener.open(self.base+'/'+asset) as response:
                self.assertEqual(response.status, 200)
                self.assertIn('javascript', response.headers['Content-Type'])
        for path, asset in [('/success.html', 'download-progress.js'), ('/books/book22', 'book-sharing.js')]:
            with self.opener.open(Request(self.base+path, headers={'Host':'tabobooks.com'})) as response:
                source=response.read().decode()
                self.assertRegex(source, re.escape('/'+asset)+r'\?v=[a-f0-9]{12}')

    def test_share_links_never_include_visit_or_private_parameters(self):
        for bid in ('book19', 'book20', 'book21', 'book22'):
            source=seo.render((app.ROOT/'book.html').read_text(), '/books/'+bid, 'tabobooks.com', 'book.html')
            target=html.unescape(re.search(r'id="shareWhatsApp" href="([^"]+)"', source)[1])
            self.assertEqual(urlparse(target).netloc, 'wa.me')
            self.assertEqual(parse_qs(urlparse(target).query)['text'], [seo.BOOKS[bid]['name']+'\nhttps://tabobooks.com/books/'+bid])
            self.assertIn('property="og:image" content="https://tabobooks.com'+seo.BOOKS[bid]['image']+'"', source)
            self.assertNotIn('id="downloads"', source)
