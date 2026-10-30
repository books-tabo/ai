"""Authenticated AutoSEO publishing; independent of orders and private book assets."""
import base64
from collections import Counter
import hashlib
import hmac
import html
from html.parser import HTMLParser
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import ssl
import tempfile
import threading
import time
import unicodedata
from datetime import datetime, timezone
from urllib.parse import quote, unquote, urljoin, urlsplit
from xml.etree.ElementTree import Element, SubElement, tostring

import media_sandbox
import seo
import supabase_store as store

ENDPOINT = '/api/autoseo/webhook'
MAX_BODY = 1_048_576
MAX_IMAGE_DOWNLOAD = 10_485_760
MAX_IMAGE_STORED = 1_000_000
MEDIA_PATH = '/blog/media/'
SUMMARY_FIELDS = 'id,slug,title,description,language,published_at,updated_at'
_REJECTIONS = Counter()
_REJECTION_LOCK = threading.Lock()


def record_rejection(reason):
    """Count bounded, fixed validation reasons only; never retain article data."""
    reason = re.sub(r'[^A-Za-z0-9 ._-]', '', str(reason))[:80]
    with _REJECTION_LOCK:
        if reason in _REJECTIONS or len(_REJECTIONS) < 32:
            _REJECTIONS[reason] += 1


def diagnostics():
    with _REJECTION_LOCK:
        return {'rejections': dict(_REJECTIONS)}


class InvalidArticle(ValueError):
    pass


class Unauthorized(ValueError):
    pass


def enabled():
    return bool(os.environ.get('AUTOSEO_WEBHOOK_SECRET')) and not seo.review_mode()


def origin():
    value = os.environ.get('TABO_PUBLIC_URL', seo.ORIGIN).rstrip('/')
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or
            parsed.password or parsed.query or parsed.fragment or parsed.path):
        raise RuntimeError('AutoSEO requires the public HTTPS origin')
    return value


def authenticate(headers, raw=None):
    secret = os.environ.get('AUTOSEO_WEBHOOK_SECRET', '')
    values = headers.get_all('Authorization', []) if hasattr(headers, 'get_all') else [headers.get('Authorization', '')]
    if (not secret or len(values) != 1 or
            not hmac.compare_digest(values[0].encode(), ('Bearer ' + secret).encode())):
        raise Unauthorized('Unauthorized')
    if raw is None:
        return
    signature = headers.get('X-AutoSEO-Signature')
    if signature is not None or os.environ.get('AUTOSEO_REQUIRE_SIGNATURE') == '1':
        expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        if (not isinstance(signature, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', signature) or
                not hmac.compare_digest(signature.lower(), expected)):
            raise Unauthorized('Invalid signature')


def article_slug(value, maximum=180):
    if not isinstance(value, str):
        raise InvalidArticle('Invalid slug')
    slug = unicodedata.normalize('NFC', value).strip().lower()
    if (not 1 <= len(slug) <= maximum or slug in ('.', '..', 'media', 'sitemap.xml') or
            any(not (char.isalnum() or char in '-_') for char in slug)):
        raise InvalidArticle('Invalid slug')
    return slug


def post_url(slug):
    return origin() + '/blog/' + quote(slug, safe='-_')


def timestamp(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None:
            raise ValueError()
        return result.astimezone(timezone.utc).isoformat()
    except (AttributeError, TypeError, ValueError):
        raise InvalidArticle('Invalid timestamp') from None


def safe_link(value):
    if not isinstance(value, str) or any(ord(c) < 32 for c in value) or '\\' in value:
        return None
    parsed = urlsplit(value)
    if parsed.scheme in ('https', 'http') and parsed.hostname and not parsed.username and not parsed.password:
        return value
    if not parsed.scheme and not parsed.netloc and (value.startswith(('/', '#')) and not value.startswith('//')):
        return value
    return None


class ArticleHTML(HTMLParser):
    """Conservative HTML allowlist: no active content, CSS, embeds or remote images."""
    TAGS = frozenset('p h2 h3 h4 h5 h6 strong b em i u s ul ol li blockquote pre code br hr a table thead tbody tfoot tr th td figure figcaption details summary div span sub sup'.split())
    DROP = frozenset('script style iframe object embed svg math form textarea select template noscript xmp plaintext'.split())

    def __init__(self, images):
        super().__init__(convert_charrefs=True)
        self.images = images
        self.out = []
        self.blocked = []

    def handle_starttag(self, tag, attrs):
        if self.blocked:
            if tag in self.DROP:
                self.blocked.append(tag)
            return
        if tag in self.DROP:
            self.blocked.append(tag)
            return
        attrs = dict(attrs)
        if tag == 'img':
            src = self.images.get(attrs.get('src'))
            if src:
                self.out.append('<img src="' + src + '" alt="' + html.escape(attrs.get('alt') or '', quote=True) + '" loading="lazy" decoding="async">')
            return
        if tag == 'h1':
            tag = 'h2'
        if tag not in self.TAGS:
            return
        extra = ''
        if tag == 'a':
            href = safe_link(attrs.get('href'))
            if href:
                extra = ' href="' + html.escape(href, quote=True) + '" rel="noopener noreferrer"'
        if tag in ('td', 'th'):
            for name in ('colspan', 'rowspan'):
                if re.fullmatch(r'[1-9][0-9]?', attrs.get(name) or ''):
                    extra += ' ' + name + '="' + attrs[name] + '"'
        self.out.append('<' + tag + extra + '>')

    def handle_endtag(self, tag):
        if self.blocked:
            if tag == self.blocked[-1]:
                self.blocked.pop()
            return
        if tag == 'h1':
            tag = 'h2'
        if tag in self.TAGS and tag not in ('br', 'hr'):
            self.out.append('</' + tag + '>')

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in ('img', 'br', 'hr'):
            self.handle_endtag(tag)

    def handle_data(self, data):
        if not self.blocked:
            self.out.append(html.escape(data))


def sanitize(source, images):
    parser = ArticleHTML(images)
    parser.feed(source)
    parser.close()
    return ''.join(parser.out)


class PinnedHTTPS(http.client.HTTPSConnection):
    """Validate all DNS answers, pin a public address and retain TLS hostname checks."""
    def connect(self):
        answers = socket.getaddrinfo(self.host, 443, type=socket.SOCK_STREAM)
        addresses = list(dict.fromkeys(item[4][0] for item in answers))
        if not addresses or any(not ipaddress.ip_address(addr).is_global for addr in addresses):
            raise RuntimeError('Image destination is not public')
        raw = socket.create_connection((addresses[0], 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


def fetch_image(url, deadline):
    for _ in range(4):
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or
                parsed.port not in (None, 443) or any(ord(c) < 32 for c in url) or '\\' in url):
            raise InvalidArticle('Images require a public HTTPS URL')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError('Image download timed out')
        connection = PinnedHTTPS(parsed.hostname, timeout=min(8, remaining), context=ssl.create_default_context())
        try:
            connection.request('GET', (parsed.path or '/') + ('?' + parsed.query if parsed.query else ''),
                               headers={'User-Agent': 'TABO-Books-AutoSEO/1', 'Accept': 'image/*', 'Accept-Encoding': 'identity'})
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader('Location')
                if not location:
                    raise RuntimeError('Invalid image redirect')
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise RuntimeError('Image download failed')
            length = response.getheader('Content-Length')
            if length and int(length) > MAX_IMAGE_DOWNLOAD:
                raise InvalidArticle('Image too large')
            if not (response.getheader('Content-Type') or '').lower().startswith('image/'):
                raise InvalidArticle('Invalid image content type')
            chunks, size = [], 0
            while True:
                if time.monotonic() >= deadline:
                    raise RuntimeError('Image download timed out')
                connection.sock.settimeout(min(8, max(.1, deadline - time.monotonic()))) if connection.sock else None
                chunk = response.read(min(65536, MAX_IMAGE_DOWNLOAD + 1 - size))
                if not chunk:
                    return b''.join(chunks)
                size += len(chunk)
                if size > MAX_IMAGE_DOWNLOAD:
                    raise InvalidArticle('Image too large')
                chunks.append(chunk)
        finally:
            connection.close()
    raise RuntimeError('Too many image redirects')


def cache_dir():
    return Path(os.environ.get('AUTOSEO_IMAGE_CACHE_DIR', str(Path(tempfile.gettempdir()) / 'tabo-autoseo-images')))


def cache_media(digest, payload):
    # Only a cache: durable copies live in the dedicated media table, never in Git.
    directory = cache_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (digest + '.webp')
    if target.exists():
        return
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
    try:
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def download_media(url, deadline):
    raw = fetch_image(url, deadline)
    try:
        payload, _ = media_sandbox.run('image', raw, {}, input_limit=MAX_IMAGE_DOWNLOAD,
                                     output_limit=MAX_IMAGE_STORED,
                                     timeout=deadline - time.monotonic())
    except media_sandbox.MediaError:
        raise InvalidArticle('Image processing rejected') from None
    digest = hashlib.sha256(payload).hexdigest()
    cache_media(digest, payload)
    return {'digest': digest, 'content_base64': base64.b64encode(payload).decode('ascii')}


def require_text(data, key, maximum, required=False):
    value = data.get(key, '')
    if value is None and not required:
        return ''
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value or (required and not value.strip()):
        raise InvalidArticle('Invalid ' + key)
    return value.strip()


def publish(raw, headers):
    authenticate(headers, raw)
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError):
        raise InvalidArticle('Invalid JSON') from None
    if not isinstance(data, dict):
        raise InvalidArticle('Expected JSON object')
    event = data.get('event')
    if event == 'test':
        return {'url': origin() + '/test'}
    if event not in ('article.published', 'article.updated'):
        raise InvalidArticle('Unsupported event')
    identifier = data.get('id')
    if isinstance(identifier, bool) or not isinstance(identifier, int) or not 0 < identifier <= 9223372036854775807:
        raise InvalidArticle('Invalid article id')
    if data.get('status', 'published') != 'published':
        raise InvalidArticle('Only published articles are supported')
    language = require_text(data, 'languageCode', 35) or 'ar'
    if not re.fullmatch(r'[a-zA-Z]{2,8}(?:-[a-zA-Z0-9]{1,8})*', language):
        raise InvalidArticle('Invalid language')
    post = {
        'id': identifier, 'slug': article_slug(data.get('slug')),
        'title': require_text(data, 'title', 500, True),
        'description': require_text(data, 'metaDescription', 2000), 'language': language,
        'published_at': timestamp(data.get('publishedAt')),
        'updated_at': timestamp(data.get('updatedAt') or data.get('publishedAt')),
        'created_at': timestamp(data.get('createdAt') or data.get('publishedAt')),
        'content_markdown': require_text(data, 'content_markdown', MAX_BODY),
        'hero_alt': require_text(data, 'heroImageAlt', 1000),
        'meta_keywords': require_text(data, 'metaKeywords', 4000),
    }
    source_html = require_text(data, 'content_html', MAX_BODY, True)
    faq = data.get('faqSchema') or []
    if (not isinstance(faq, list) or len(faq) > 50 or any(not isinstance(item, dict) or
            not isinstance(item.get('question'), str) or not isinstance(item.get('answer'), str) or
            len(item['question']) > 1000 or len(item['answer']) > 10000 for item in faq)):
        raise InvalidArticle('Invalid FAQ')
    post['faq'] = [{'question': item['question'], 'answer': item['answer']} for item in faq]
    keywords = data.get('keywords') or []
    if not isinstance(keywords, list) or len(keywords) > 100 or any(not isinstance(x, str) or len(x) > 200 for x in keywords):
        raise InvalidArticle('Invalid keywords')
    post['keywords'] = keywords
    source_id = data.get('sourceArticleId')
    if source_id is not None and (isinstance(source_id, bool) or not isinstance(source_id, int) or not 0 < source_id <= 9223372036854775807):
        raise InvalidArticle('Invalid source article id')
    post['source_article_id'] = source_id
    images, media = {}, []
    deadline = time.monotonic() + 30
    for field, target in (('heroImageUrl', 'hero_image'), ('infographicImageUrl', 'infographic_image')):
        image_url = data.get(field)
        post[target] = None
        if image_url:
            if not isinstance(image_url, str) or len(image_url) > 4096:
                raise InvalidArticle('Invalid image URL')
            if image_url not in images:
                asset = download_media(image_url, deadline)
                media.append(asset)
                images[image_url] = MEDIA_PATH + asset['digest'] + '.webp'
            post[target] = images[image_url]
    post['content_html'] = sanitize(source_html, images)
    result = store.request('/rest/v1/rpc/tabo_autoseo_publish', 'POST', {'p_post': post, 'p_media': media})
    if not isinstance(result, dict) or not isinstance(result.get('slug'), str):
        raise RuntimeError('Invalid publishing response')
    return {'url': post_url(result['slug'])}


def list_posts(page=1, size=20, *, offset=None):
    return store.request('/rest/v1/tabo_autoseo_posts', params={
        'select': SUMMARY_FIELDS + ',editorial_book_id:post->>editorial_book_id', 'order': 'published_at.desc,id.desc', 'limit': size,
        'offset': (page - 1) * size if offset is None else offset}) or []


def get_post(slug):
    rows = store.request('/rest/v1/tabo_autoseo_posts', params={'select': '*', 'slug': 'eq.' + slug, 'limit': 1})
    return rows[0] if rows else None


def get_media(digest):
    if not re.fullmatch(r'[0-9a-f]{64}', digest):
        return None
    cached = cache_dir() / (digest + '.webp')
    if cached.is_file():
        return cached.read_bytes()
    rows = store.request('/rest/v1/tabo_autoseo_media', params={'select': 'content_base64', 'digest': 'eq.' + digest, 'limit': 1})
    if not rows:
        return None
    payload = base64.b64decode(rows[0]['content_base64'], validate=True)
    if len(payload) > MAX_IMAGE_STORED or hashlib.sha256(payload).hexdigest() != digest:
        raise RuntimeError('Invalid stored image')
    cache_media(digest, payload)
    return payload


class ArticleText(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.feed(source)

    def handle_data(self, value):
        self.parts.append(value)


def heading_key(value):
    value = unicodedata.normalize('NFKD', value).casefold()
    value = ''.join(c for c in value if not unicodedata.combining(c))
    return re.sub(r'[^\w-]+', '-', value).strip('-_')


def readable_content(source, canonical):
    """Restore heading targets stripped by ingestion, including already stored posts."""
    used, targets = set(), {}

    def heading(match):
        key = heading_key(''.join(ArticleText(match[2]).parts)) or 'section'
        identifier, suffix = key[:180], 1
        while identifier in used:
            suffix += 1
            identifier = key[:170] + '-' + str(suffix)
        used.add(identifier)
        targets.setdefault(key, identifier)
        return '<h' + match[1] + ' id="' + seo.escape(identifier) + '">' + match[2] + '</h' + match[1] + '>'

    # Stored markup has already passed ArticleHTML's allowlist. Only add escaped IDs.
    source = re.sub(r'<h([2-6])>([\s\S]*?)</h\1>', heading, source)
    destination = urlsplit(canonical)

    def anchor(match):
        href = html.unescape(match[1])
        parsed = urlsplit(href)
        same_page = (not parsed.scheme and not parsed.netloc and not parsed.path) or (
            parsed.scheme in ('', 'https') and parsed.netloc in ('', destination.netloc) and
            unquote(parsed.path) == unquote(destination.path))
        target = targets.get(heading_key(unquote(parsed.fragment))) if same_page else None
        return 'href="#' + seo.escape(target) + '"' if target else match[0]

    return re.sub(r'href="([^"]*)"', anchor, source)


def article_topic(row):
    """Conservative topic matching uses public titles, never visitor information."""
    book_id = editorial_book(row)
    if book_id:
        return seo.categories_for(book_id)[0][0], (book_id,)
    title = heading_key(row.get('title', '') + ' ' + row.get('description', ''))
    groups = (
        (('تعلم', 'مهارة', 'تعليم', 'learning', 'skill'), 'self-development', ('book19', 'book18', 'book2')),
        (('مال', 'ميزانية', 'ادخار', 'ثروة', 'money', 'financial', 'budget', 'wealth', 'business'), 'business', ('book4', 'book17', 'book9')),
        (('تطوير-الذات', 'عادات', 'عادة', 'انضباط', 'تغيير-حيات', 'habit', 'discipline'), 'self-development', ('book18', 'book14', 'book6')),
        (('زوج', 'زواج', 'marriage'), 'marriage-family', ('book20', 'book21')),
    )
    return next(((category, books) for terms, category, books in groups if any(heading_key(term) in title for term in terms)), None)


def editorial_book(row):
    book_id = row.get('editorial_book_id') or row.get('post', {}).get('editorial_book_id')
    return book_id if isinstance(book_id, str) and book_id in seo.BOOKS else None


def breadcrumbs(canonical, title, article=False):
    trail = [('/', 'الرئيسية'), ('/blog', 'المقالات')]
    if article:
        trail.append((canonical, title))
    items = [{'@type': 'ListItem', 'position': i + 1, 'name': label,
              'item': path if path.startswith('https://') else origin() + path} for i, (path, label) in enumerate(trail)]
    body = '<nav class="breadcrumbs" aria-label="مسار التصفح">'
    body += ' <span aria-hidden="true">›</span> '.join(
        seo.link(path, label) if i < len(trail) - 1 else '<span aria-current="page">' + seo.escape(label) + '</span>'
        for i, (path, label) in enumerate(trail)) + '</nav>'
    return body, {'@type': 'BreadcrumbList', '@id': canonical + '#breadcrumb', 'itemListElement': items}


def related_reading(row, candidates):
    topic = article_topic(row)
    if not topic:
        return ''
    category, books = topic
    language = 'ar' if row['language'].lower().startswith('ar') else 'en'
    ar = language == 'ar'
    body = '<aside class="related-reading"><h2>' + ('كتب مرتبطة بموضوع المقال' if ar else 'Books on this topic') + '</h2>'
    body += '<p>' + ('للتوسع في القراءة، تصفح نبذات هذه الكتب العربية بصيغة PDF واختر ما يناسب سؤالك.' if ar else 'Explore these Arabic PDF books and choose the reading that fits your question.') + '</p>'
    body += seo.book_list(books, language)
    body += '<p>' + seo.link('/categories/' + category, seo.CATEGORIES[category][language]) + '</p>'
    pinned_book = editorial_book(row)
    related = [item for item in candidates if item['slug'] != row['slug'] and
               item['language'].lower().split('-')[0] == row['language'].lower().split('-')[0] and
               ((editorial_book(item) == pinned_book) if pinned_book else
                (article_topic(item) and article_topic(item)[0] == category))][:3]
    if related:
        body += '<h2>' + ('مقالات تكمل قراءتك' if ar else 'Continue reading') + '</h2><ul>'
        body += ''.join('<li>' + seo.link(post_url(item['slug']), item['title']) + '</li>' for item in related) + '</ul>'
    return body + '</aside>'


def document(title, description, canonical, body, host, language='ar', graph=None, image=None):
    esc = seo.escape
    rtl = language.split('-')[0].lower() in ('ar', 'fa', 'he', 'ur')
    metadata = '<meta name="description" content="' + esc(description) + '"><link rel="canonical" href="' + esc(canonical) + '">'
    article = any(node.get('@type') == 'BlogPosting' for node in (graph or {}).get('@graph', []))
    share_image = origin() + (image or '/tabo-books-logo.webp')
    for key, value in {'og:type': 'article' if article else 'website', 'og:title': title, 'og:description': description, 'og:url': canonical, 'og:site_name': 'TABO BOOKS', 'og:image': share_image, 'og:locale': language.replace('-', '_')}.items():
        metadata += '<meta property="' + key + '" content="' + esc(value) + '">'
    metadata += '<meta name="twitter:card" content="' + ('summary_large_image' if image else 'summary') + '">'
    if not seo.indexable(host):
        metadata += '<meta name="robots" content="noindex,nofollow">'
    else:
        metadata += '<meta name="robots" content="max-image-preview:large">'
    if graph:
        metadata += '<script type="application/ld+json">' + seo.safe_json(graph) + '</script>'
    return ('<!doctype html><html lang="' + esc(language) + '" dir="' + ('rtl' if rtl else 'ltr') + '"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>' + esc(title) + ' | TABO BOOKS</title>' + metadata + '<link rel="stylesheet" href="/seo.css?v=' + seo.ASSET_VERSIONS['seo.css'] + '"><link rel="stylesheet" href="/blog/style.css?v=' + hashlib.sha256(STYLE.encode()).hexdigest()[:12] + '"></head><body>'
            '<header><a href="/">TABO BOOKS</a><nav aria-label="التنقل الرئيسي"><a href="/">المتجر · Store</a><a href="/blog">المقالات · Blog</a><a href="/categories">أقسام الكتب · Categories</a></nav></header><main>' + body + '</main><footer><nav><a href="/about">من نحن · About</a><a href="/contact">تواصل معنا · Contact</a><a href="/">اكتشف الكتب · Explore our books</a></nav></footer></body></html>')


def render_post(row, host, related=()):
    post = row['post']
    esc = seo.escape
    canonical = post_url(row['slug'])
    content = readable_content(post['content_html'], canonical)
    description = row['description'].strip() or ' '.join(ArticleText(content).parts)[:160]
    body, breadcrumb = breadcrumbs(canonical, row['title'], article=True)
    ar = row['language'].lower().startswith('ar')
    body += '<article><h1>' + esc(row['title']) + '</h1><p class="article-meta">' + ('نشر: ' if ar else 'Published: ')
    body += '<time datetime="' + esc(row['published_at']) + '">' + esc(row['published_at'][:10]) + '</time>'
    if row['updated_at'] != row['published_at']:
        body += ' · ' + ('آخر تحديث: ' if ar else 'Updated: ') + '<time datetime="' + esc(row['updated_at']) + '">' + esc(row['updated_at'][:10]) + '</time>'
    body += ' · ' + ('الناشر: ' if ar else 'Publisher: ') + seo.link('/about', 'TABO BOOKS') + '</p>'
    hero = post.get('hero_image')
    if hero:
        body += '<img class="hero" src="' + esc(hero) + '" alt="' + esc(post.get('hero_alt') or row['title']) + '" fetchpriority="high" decoding="async">'
    body += '<div class="article-body">' + content + '</div>'
    infographic = post.get('infographic_image')
    if infographic and infographic not in post['content_html']:
        body += '<figure><img src="' + esc(infographic) + '" alt="' + esc(row['title']) + '" loading="lazy"></figure>'
    faq = post.get('faq', [])
    if faq:
        body += '<section><h2>' + ('أسئلة شائعة' if row['language'].startswith('ar') else 'Frequently asked questions') + '</h2>'
        body += ''.join('<details><summary>' + esc(item['question']) + '</summary><p>' + esc(item['answer']) + '</p></details>' for item in faq) + '</section>'
    body += '</article>' + related_reading(row, related)
    article = {'@type': 'BlogPosting', '@id': canonical + '#article', 'headline': row['title'], 'description': description, 'url': canonical,
               'datePublished': row['published_at'], 'dateModified': row['updated_at'], 'inLanguage': row['language'],
               'mainEntityOfPage': {'@id': canonical + '#webpage'}, 'isAccessibleForFree': True,
               'publisher': {'@type': 'Organization', 'name': 'TABO BOOKS', 'url': origin()}}
    if hero:
        article['image'] = origin() + hero
    graph = {'@context': 'https://schema.org', '@graph': [article, breadcrumb,
        {'@type': 'WebPage', '@id': canonical + '#webpage', 'url': canonical, 'name': row['title'],
         'inLanguage': row['language'], 'mainEntity': {'@id': canonical + '#article'}, 'breadcrumb': {'@id': breadcrumb['@id']}}]}
    if faq:
        graph['@graph'].append({'@type': 'FAQPage', 'mainEntity': [{'@type': 'Question', 'name': item['question'], 'acceptedAnswer': {'@type': 'Answer', 'text': item['answer']}} for item in faq]})
    return document(row['title'], description, canonical, body, host, row['language'], graph, hero)


def render_index(rows, page, host):
    title = 'مقالات الكتب وأدلة القراءة' + (' — الصفحة ' + str(page) if page > 1 else '')
    description = 'اقرأ مقالات TABO BOOKS عن التعلم وتطوير الذات واتخاذ القرارات والثقافة المالية، مع أمثلة عملية وروابط للكتب والقراءات المرتبطة.'
    canonical = origin() + '/blog' + ('?page=' + str(page) if page > 1 else '')
    body, breadcrumb = breadcrumbs(canonical, title)
    body += '<h1>' + title + '</h1><p class="blog-intro">' + description + '</p>'
    for row in rows[:20]:
        body += '<article><h2><a href="' + seo.escape(post_url(row['slug'])) + '">' + seo.escape(row['title']) + '</a></h2><time datetime="' + seo.escape(row['published_at']) + '">' + seo.escape(row['published_at'][:10]) + '</time><p>' + seo.escape(row['description']) + '</p></article>'
    if not rows:
        body += '<p>ستظهر المقالات هنا بعد نشرها. Articles will appear here after publication.</p>'
    body += '<nav aria-label="صفحات المقالات">'
    if page > 1:
        body += '<a rel="prev" href="' + ('/blog' if page == 2 else '/blog?page=' + str(page - 1)) + '">السابق · Previous</a>'
    if len(rows) > 20:
        body += '<a rel="next" href="/blog?page=' + str(page + 1) + '">التالي · Next</a>'
    body += '</nav>'
    graph = {'@context': 'https://schema.org', '@graph': [breadcrumb,
        {'@type': 'CollectionPage', '@id': canonical + '#webpage', 'url': canonical, 'name': title,
         'description': description, 'inLanguage': 'ar', 'breadcrumb': {'@id': breadcrumb['@id']},
         'mainEntity': {'@type': 'ItemList', 'numberOfItems': len(rows[:20]), 'itemListElement': [
             {'@type': 'ListItem', 'position': (page - 1) * 20 + i + 1, 'url': post_url(row['slug']), 'name': row['title']}
             for i, row in enumerate(rows[:20])]}}]}
    return document(title, description, canonical, body, host, graph=graph)


def sitemap():
    root = Element('urlset', xmlns='http://www.sitemaps.org/schemas/sitemap/0.9')
    SubElement(SubElement(root, 'url'), 'loc').text = origin() + '/blog'
    for page in range(1, 101):
        rows = list_posts(page, 500)
        for row in rows:
            node = SubElement(root, 'url')
            SubElement(node, 'loc').text = post_url(row['slug'])
            SubElement(node, 'lastmod').text = row['updated_at']
        if len(rows) < 500:
            break
    return tostring(root, encoding='utf-8', xml_declaration=True)


STYLE = '''body{margin:0;background:#faf9f6;color:#242b35;font:18px/1.9 system-ui,sans-serif}header,main,footer{max-width:900px;margin:auto;padding:24px}header{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #ddd}header>a{font-weight:800;color:#9a7014}nav{display:flex;gap:24px}a{color:#795712;text-decoration-thickness:1px;text-underline-offset:4px}article{margin:30px 0;padding:24px;background:white;border:1px solid #e8e3d8;border-radius:14px}h1{font-size:clamp(28px,5vw,44px);line-height:1.5}h2,h3{line-height:1.5}img{max-width:100%;height:auto;border-radius:8px}.hero{display:block;margin:20px auto}time{font-size:14px;color:#647080}pre,table{overflow:auto;max-width:100%;display:block}pre{direction:ltr;background:#f2f4f6;padding:16px}blockquote{border-inline-start:3px solid #b88a18;margin:20px 0;padding:0 20px}td,th{padding:8px;border:1px solid #ddd}details{padding:15px;border-bottom:1px solid #ddd}summary{cursor:pointer;font-weight:600}footer{border-top:1px solid #ddd}@media(max-width:600px){header,main,footer{padding:16px}header{display:block}nav{font-size:15px}article{padding:16px}}'''
STYLE += '''*{box-sizing:border-box}nav{flex-wrap:wrap}a:focus-visible,summary:focus-visible{outline:3px solid #b88a18;outline-offset:4px}.breadcrumbs{font-size:14px;gap:8px;line-height:1.8}.breadcrumbs [aria-current]{color:#647080}.article-meta{font-size:14px;color:#647080}.article-body{overflow-wrap:anywhere}.article-body :is(h2,h3,h4,h5,h6){scroll-margin-top:24px}.related-reading{border-top:1px solid #dfd5b8;margin-top:36px;padding-top:24px}.related-reading .seo-book{font-size:16px;margin:0}.related-reading .seo-book img{height:190px}.related-reading li{margin:12px 0}.blog-intro{color:#525d68;max-width:70ch}@media(max-width:600px){.related-reading .seo-book-grid{grid-template-columns:1fr}.related-reading .seo-book img{height:210px}header nav{gap:12px}}'''
