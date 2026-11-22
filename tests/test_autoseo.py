"""Synthetic publishing/HTTP checks. SQL concurrency is tested separately on PostgreSQL."""
import base64
import copy
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request

from PIL import Image
import autoseo
from support import HTTPCase
from tools.audit_seo import Page

SECRET = 'synthetic-autoseo-token-for-tests-only'
AUTH = {'Authorization': 'Bearer ' + SECRET}


def article(**changes):
    result = {'event': 'article.published', 'id': 123, 'slug': 'example-article',
              'title': 'عنوان المقال', 'metaDescription': 'وصف المقال', 'languageCode': 'ar',
              'content_html': '<h2>محتوى</h2><p>نص مفيد</p>', 'content_markdown': 'نص مفيد',
              'status': 'published', 'publishedAt': '2026-10-26T12:00:00Z',
              'updatedAt': '2026-10-26T12:00:00Z', 'keywords': ['قراءة'],
              'faqSchema': [{'question': 'سؤال؟', 'answer': 'إجابة'}]}
    result.update(changes)
    return result


class AutoSEOHTTPTests(HTTPCase):
    def setUp(self):
        super().setUp()
        self.config = patch.dict(os.environ, {'AUTOSEO_WEBHOOK_SECRET': SECRET,
            'TABO_PUBLIC_URL': 'https://tabobooks.com'})
        self.config.start()
        self.addCleanup(self.config.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        env = patch.dict(os.environ, {'AUTOSEO_IMAGE_CACHE_DIR': self.directory.name})
        env.start()
        self.addCleanup(env.stop)
        self.rows, self.media, self.calls = {}, {}, []
        mock = patch.object(autoseo.store, 'request', side_effect=self.request_store)
        mock.start()
        self.addCleanup(mock.stop)

    def request_store(self, path, method='GET', payload=None, params=None, headers=None):
        self.calls.append((path, method))
        if path == '/rest/v1/rpc/tabo_autoseo_publish':
            post = copy.deepcopy(payload['p_post'])
            for asset in payload['p_media']:
                self.media[asset['digest']] = asset
            previous = self.rows.get(post['id'])
            if previous and previous['updated_at'] > post['updated_at']:
                return {'slug': previous['slug']}
            row = {key: post[key] for key in autoseo.SUMMARY_FIELDS.split(',')}
            row['slug'] = previous['slug'] if previous else post['slug']
            row['post'] = post
            self.rows[post['id']] = row
            return {'slug': row['slug']}
        if path == '/rest/v1/tabo_autoseo_posts':
            rows = sorted(self.rows.values(), key=lambda r: (r['published_at'], r['id']), reverse=True)
            if params.get('slug'):
                rows = [r for r in rows if r['slug'] == params['slug'][3:]]
            return rows[params.get('offset', 0):params.get('offset', 0) + params['limit']]
        if path == '/rest/v1/tabo_autoseo_media':
            digest = params['digest'][3:]
            return [self.media[digest]] if digest in self.media else []
        raise AssertionError('AutoSEO accessed an unrelated store object')

    def get(self, path, host='tabobooks.com'):
        request = Request(self.base + path, headers={'Host': host})
        try:
            response = self.opener.open(request, timeout=10)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, response.headers, response.read()

    def raw_post(self, raw, headers):
        request = Request(self.base + autoseo.ENDPOINT, data=raw,
                          headers={'Content-Type': 'application/json', **headers})
        try:
            response = self.opener.open(request, timeout=10)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.loads(response.read())

    def test_missing_or_wrong_bearer_is_401_before_any_storage(self):
        for headers in ({}, {'Authorization': 'Bearer wrong'}, {'Authorization': 'bearer ' + SECRET}):
            status, _, _ = self.post(autoseo.ENDPOINT, article(), headers)
            self.assertEqual(status, 401)
        self.assertFalse(self.calls)

    def test_test_event_returns_public_url_without_writing(self):
        status, response, _ = self.post(autoseo.ENDPOINT, {'event': 'test'}, AUTH)
        self.assertEqual((status, response), (200, {'url': 'https://tabobooks.com/test'}))
        self.assertFalse(self.calls)

    def test_diagnostics_require_bearer_and_only_count_validation_reasons(self):
        autoseo._REJECTIONS.clear()
        self.assertEqual(self.get('/api/autoseo/status')[0], 401)
        self.post(autoseo.ENDPOINT, article(title=''), AUTH)
        request = Request(self.base + '/api/autoseo/status', headers=AUTH)
        with self.opener.open(request, timeout=10) as response:
            result = json.loads(response.read())
        self.assertEqual(result, {'rejections': {'Invalid title': 1}})
        self.assertNotIn(SECRET, json.dumps(result))

    def test_raw_body_signature_and_required_signature_mode(self):
        raw = json.dumps(article(), ensure_ascii=False, indent=3).encode() + b'\n'
        signature = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
        self.assertEqual(self.raw_post(raw, {**AUTH, 'X-AutoSEO-Signature': signature})[0], 200)
        self.assertEqual(self.raw_post(raw + b' ', {**AUTH, 'X-AutoSEO-Signature': signature})[0], 401)
        self.assertEqual(self.raw_post(raw, {**AUTH, 'X-AutoSEO-Signature': 'invalid'})[0], 401)
        with patch.dict(os.environ, {'AUTOSEO_REQUIRE_SIGNATURE': '1'}):
            self.assertEqual(self.raw_post(raw, AUTH)[0], 401)

    def test_duplicate_update_and_stale_delivery_keep_one_stable_url(self):
        self.assertEqual(self.post(autoseo.ENDPOINT, article(), AUTH)[0], 200)
        status, response, _ = self.post(autoseo.ENDPOINT, article(event='article.updated',
            title='عنوان جديد', slug='changed-slug', updatedAt='2026-10-27T12:00:00Z'), AUTH)
        self.assertEqual(status, 200)
        self.assertEqual(response['url'], 'https://tabobooks.com/blog/example-article')
        self.post(autoseo.ENDPOINT, article(), AUTH)
        self.assertEqual(len(self.rows), 1)
        self.assertEqual(self.rows[123]['title'], 'عنوان جديد')

    def test_images_are_downloaded_sanitized_durable_and_served_locally(self):
        source = io.BytesIO()
        Image.new('RGB', (80, 60), 'gold').save(source, 'PNG')
        hero, infographic = 'https://cdn.example/hero.png', 'https://cdn.example/info.png'
        content = '<p>نص</p><img src="' + hero + '" alt="صورة"><script>alert(1)</script><img src="https://tracker.example/a"><a href="javascript:alert(1)" onclick="alert(1)">رابط</a>'
        with patch.object(autoseo, 'fetch_image', return_value=source.getvalue()) as fetch:
            status, _, _ = self.post(autoseo.ENDPOINT, article(heroImageUrl=hero, heroImageAlt='غلاف',
                infographicImageUrl=infographic, content_html=content), AUTH)
        self.assertEqual(status, 200)
        self.assertEqual(fetch.call_count, 2)
        post = self.rows[123]['post']
        self.assertNotIn('https://', post['content_html'])
        self.assertNotIn('script', post['content_html'])
        self.assertNotIn('onclick', post['content_html'])
        self.assertNotIn('javascript', post['content_html'])
        self.assertTrue(post['hero_image'].startswith('/blog/media/'))
        # Clear local cache to prove a cold instance restores bytes from durable storage.
        for path in Path(self.directory.name).glob('*.webp'):
            path.unlink()
        status, headers, payload = self.get(post['hero_image'])
        self.assertEqual(status, 200)
        self.assertEqual(headers.get_content_type(), 'image/webp')
        self.assertEqual(payload[:4], b'RIFF')
        stored = next(iter(self.media.values()))
        self.assertEqual(payload, base64.b64decode(stored['content_base64']))
        self.assertTrue(list(Path(self.directory.name).glob('*.webp')))

    def test_image_or_database_failure_is_retryable_500_without_acknowledgment(self):
        with patch.object(autoseo, 'download_media', side_effect=RuntimeError('synthetic outage')):
            self.assertEqual(self.post(autoseo.ENDPOINT, article(heroImageUrl='https://cdn.example/a.png'), AUTH)[0], 500)
        self.assertFalse(self.rows)
        with patch.object(autoseo.store, 'request', side_effect=RuntimeError('synthetic outage')):
            self.assertEqual(self.post(autoseo.ENDPOINT, article(), AUTH)[0], 500)

    def test_invalid_payloads_do_not_write(self):
        for changes in ({'id': True}, {'id': -1}, {'slug': '../orders'}, {'slug': 'media'},
                        {'slug': 'x%2f..'}, {'title': ''}, {'event': 'delete'},
                        {'updatedAt': 'invalid'}, {'status': 'draft'}, {'languageCode': '"><script>'}):
            with self.subTest(changes=changes):
                self.assertEqual(self.post(autoseo.ENDPOINT, article(**changes), AUTH)[0], 400)
        self.assertFalse(self.calls)
        self.assertEqual(self.raw_post(b'[]', AUTH)[0], 400)
        self.assertEqual(self.raw_post(b'{invalid', AUTH)[0], 400)
        self.assertEqual(self.raw_post(b'x', {**AUTH, 'Content-Length': str(autoseo.MAX_BODY + 1)})[0], 413)

    def test_public_page_metadata_and_sitemap_are_server_rendered(self):
        self.post(autoseo.ENDPOINT, article(), AUTH)
        status, headers, payload = self.get('/blog/example-article')
        source = payload.decode()
        self.assertEqual(status, 200)
        self.assertIn('<h1>عنوان المقال</h1>', source)
        self.assertIn('dir="rtl"', source)
        self.assertIn('https://tabobooks.com/blog/example-article', source)
        self.assertIn('BlogPosting', source)
        self.assertIn('FAQPage', source)
        self.assertIn('nonce=', source)
        self.assertIn('nonce-', headers['Content-Security-Policy'])
        self.assertNotIn(SECRET, source)
        self.assertEqual(self.get('/blog/missing')[0], 404)
        self.assertIn(b'/blog/example-article', self.get('/blog')[2])
        self.assertIn(b'/blog/example-article', self.get('/blog/sitemap.xml')[2])
        self.assertIn(b'/blog/sitemap.xml', self.get('/robots.txt')[2])
        self.assertIn(b'href="/blog"', self.get('/')[2])

    def test_arabic_slug_is_percent_encoded_in_returned_url(self):
        status, response, _ = self.post(autoseo.ENDPOINT, article(slug='حب-القراءة'), AUTH)
        self.assertEqual(status, 200)
        self.assertIn('%D8', response['url'])
        self.assertEqual(self.get(response['url'].replace('https://tabobooks.com', ''))[0], 200)

    def test_pagination_has_no_skipped_article(self):
        for identifier in range(1, 23):
            self.post(autoseo.ENDPOINT, article(id=identifier, slug='post-' + str(identifier)), AUTH)
        first = self.get('/blog')[2].decode()
        second = self.get('/blog?page=2')[2].decode()
        self.assertIn('/blog/post-3"', first)
        self.assertNotIn('/blog/post-2"', first)
        self.assertIn('/blog/post-2"', second)
        self.assertIn('/blog/post-1"', second)

    def test_existing_toc_targets_and_duplicate_headings_are_repaired_without_republishing(self):
        content = '<a href="https://tabobooks.com/blog/example-article#1-أساسيات-التمويل">انتقل</a>'
        content += '<a href="https://other.example/post#1-أساسيات-التمويل">مصدر آخر</a>'
        content += '<h2>1. أسَاسيات <strong>التمويل</strong></h2><p>نص</p><h2>1. أسَاسيات التمويل</h2>'
        self.post(autoseo.ENDPOINT, article(content_html=content), AUTH)
        stored = copy.deepcopy(self.rows[123])
        source = self.get('/blog/example-article')[2].decode()
        target = re.search(r'href="#([^"]+)"', source)[1]
        self.assertIn('id="' + target + '"', source)
        self.assertIn('id="' + target + '-2"', source)
        self.assertIn('https://other.example/post#1-أساسيات-التمويل', source)
        self.assertEqual(self.rows[123], stored)

    def test_article_navigation_dates_and_schema_describe_visible_public_content(self):
        self.post(autoseo.ENDPOINT, article(title='كتب إدارة المال', updatedAt='2026-10-27T12:00:00Z'), AUTH)
        self.post(autoseo.ENDPOINT, article(id=124, slug='money-reading', title='الثقافة المالية'), AUTH)
        self.post(autoseo.ENDPOINT, article(id=125, slug='learning', title='مهارة جديدة'), AUTH)
        status, _, payload = self.get('/blog/example-article?utm_source=synthetic')
        source = payload.decode()
        page = Page(source)
        self.assertEqual(status, 200)
        self.assertIn('/books/book4', page.anchors)
        self.assertIn('/categories/business', page.anchors)
        self.assertIn('https://tabobooks.com/blog/money-reading', page.anchors)
        self.assertNotIn('https://tabobooks.com/blog/learning', page.anchors)
        self.assertIn('/about', page.anchors)
        self.assertIn('2026-10-27', source)
        self.assertIn('آخر تحديث', source)
        self.assertEqual(page.meta['og:type'], 'article')
        graph = page.graphs[0]['@graph']
        posting = next(n for n in graph if n['@type'] == 'BlogPosting')
        self.assertEqual(posting['dateModified'], self.rows[123]['updated_at'])
        self.assertNotIn('author', posting)  # No invented human author or expert review.
        self.assertEqual(posting['mainEntityOfPage']['@id'], 'https://tabobooks.com/blog/example-article#webpage')
        self.assertIn('BreadcrumbList', [n['@type'] for n in graph])
        self.assertIn('noindex', self.get('/blog/example-article', host='alternate.invalid')[1]['X-Robots-Tag'])
        # These are navigation links, not paid assets or a checkout action.
        self.assertNotIn('/api/orders', source)
        self.assertNotIn('/originals/', source)

    def test_optional_related_or_catalog_outage_does_not_hide_published_article(self):
        self.post(autoseo.ENDPOINT, article(title='الثقافة المالية'), AUTH)
        with patch.object(autoseo, 'list_posts', side_effect=RuntimeError('synthetic outage')), \
                patch.object(autoseo.seo.catalog.store_catalog, 'published', side_effect=RuntimeError('synthetic outage')):
            self.assertEqual(self.get('/blog/example-article')[0], 200)

    def test_explicit_editorial_book_wins_over_title_keywords_and_pins_related_articles(self):
        self.post(autoseo.ENDPOINT, article(title='تعلم اتخاذ القرارات'), AUTH)
        self.post(autoseo.ENDPOINT, article(id=124, slug='same-book', title='اختبار قرار'), AUTH)
        self.post(autoseo.ENDPOINT, article(id=125, slug='different-book', title='التعلم الذاتي'), AUTH)
        self.rows[123]['post']['editorial_book_id']='book9'
        self.rows[124]['post']['editorial_book_id']='book9'
        self.rows[125]['post']['editorial_book_id']='book19'
        page=Page(self.get('/blog/example-article')[2].decode())
        self.assertIn('/books/book9',page.anchors)
        self.assertNotIn('/books/book19',page.anchors)
        self.assertIn('https://tabobooks.com/blog/same-book',page.anchors)
        self.assertNotIn('https://tabobooks.com/blog/different-book',page.anchors)
        self.assertEqual(autoseo.editorial_book({'editorial_book_id':'book18'}),'book18')
        self.assertIsNone(autoseo.editorial_book({'editorial_book_id':'book999'}))

    def test_blog_index_pagination_metadata_and_drawer_link_are_crawlable(self):
        for identifier in range(1, 23):
            self.post(autoseo.ENDPOINT, article(id=identifier, slug='post-' + str(identifier)), AUTH)
        first = Page(self.get('/blog')[2].decode())
        second_source = self.get('/blog?page=2')[2].decode()
        second = Page(second_source)
        self.assertEqual(first.meta['og:type'], 'website')
        self.assertIn('الثقافة المالية', first.meta['description'])
        collection = next(n for n in second.graphs[0]['@graph'] if n['@type'] == 'CollectionPage')
        self.assertEqual(collection['mainEntity']['numberOfItems'], 2)
        self.assertEqual(collection['mainEntity']['itemListElement'][0]['position'], 21)
        self.assertIn('href="/blog"', second_source)
        self.assertIn('https://tabobooks.com/blog?page=2', second_source)
        for route in ('/', '/books/book4', '/categories/business', '/offers'):
            source = self.get(route)[2].decode()
            self.assertIn('href="/blog" data-seo-label="blog"', source)
        home = self.get('/')[2].decode()
        categories = json.loads(re.search(r'const BOOK_CATEGORIES = (\[.*?\]);', home)[1])
        self.assertEqual(next(c for c in categories if c['id'] == 'articles')['href'], '/blog')

    def test_readable_content_only_retargets_same_article_fragments(self):
        canonical = autoseo.post_url('حب-القراءة')
        source = '<a href="' + canonical + '#القراءة">هنا</a><h2>القراءة</h2>'
        result = autoseo.readable_content(source, canonical)
        self.assertIn('href="#القراءة"', result)
        self.assertIn('id="القراءة"', result)


class ImageSafetyTests(unittest.TestCase):
    def test_non_https_and_credentials_are_not_downloaded(self):
        for url in ('http://example.com/a.png', 'file:///etc/passwd', 'https://user:pass@example.com/a', 'https://example.com:8080/a'):
            with self.assertRaises(autoseo.InvalidArticle):
                autoseo.fetch_image(url, autoseo.time.monotonic() + 10)

    def test_private_or_mixed_dns_is_blocked_before_connection(self):
        for addresses in (['127.0.0.1'], ['169.254.169.254'], ['::1'], ['10.0.0.1'], ['93.184.216.34', '192.168.0.1']):
            answers = [(2, 1, 6, '', (address, 443)) for address in addresses]
            with patch.object(autoseo.socket, 'getaddrinfo', return_value=answers), patch.object(autoseo.socket, 'create_connection') as connect:
                with self.assertRaises(RuntimeError):
                    autoseo.PinnedHTTPS('example.com').connect()
                connect.assert_not_called()

    def test_html_allowlist_blocks_active_markup(self):
        source = '<svg><script>bad</script></svg><iframe>bad</iframe><form>bad</form><h1>Hello</h1><a href="//bad.example/x">link</a><p style="background:url(https://bad.example)">safe</p>'
        result = autoseo.sanitize(source, {})
        self.assertEqual(result, '<h2>Hello</h2><a>link</a><p>safe</p>')
