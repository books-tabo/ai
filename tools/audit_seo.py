"""Read-only audit of public HTML; never creates orders or tracking events."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree


class Page(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.title = self.h1 = ''
        self.meta, self.links, self.anchors, self.graphs = {}, [], [], []
        self.ids, self.images, self.h1_count = [], [], 0
        self.active = None
        self.ld = None
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get('id'):
            self.ids.append(attrs['id'])
        if tag == 'h1':
            self.h1_count += 1
        if tag == 'img':
            self.images.append(attrs)
        if tag in ('title', 'h1'):
            self.active = tag
        if tag == 'meta':
            self.meta[attrs.get('name', attrs.get('property', ''))] = attrs.get('content', '')
        if tag == 'link':
            self.links.append(attrs)
        if tag == 'a' and 'href' in attrs:
            self.anchors.append(attrs['href'])
        if tag == 'script' and attrs.get('type') == 'application/ld+json':
            self.ld = ''

    def handle_data(self, data):
        if self.active:
            setattr(self, self.active, getattr(self, self.active) + data)
        if self.ld is not None:
            self.ld += data

    def handle_endtag(self, tag):
        if tag == self.active:
            self.active = None
        if tag == 'script' and self.ld is not None:
            self.graphs.append(json.loads(self.ld))
            self.ld = None


def fetch(url):
    try:
        with urlopen(Request(url, headers={'User-Agent': 'TABO-SEO-ReadOnly-Audit/1.0'}), timeout=30) as response:
            return response.status, {k.lower():v for k,v in response.headers.items()}, response.read().decode('utf-8')
    except HTTPError as error:
        return error.code, {k.lower():v for k,v in error.headers.items()}, error.read().decode('utf-8', errors='replace')
    except OSError:
        return 0, {}, ''


def audit(base):
    base = base.rstrip('/')
    sitemap_status, _, sitemap = fetch(base + '/sitemap.xml')
    blog_status, _, blog_sitemap = fetch(base + '/blog/sitemap.xml')
    paths = ['/']
    if sitemap_status == 200:
        urls = [x.text for x in ElementTree.fromstring(sitemap).iter() if x.tag.endswith('}loc')]
    else:
        urls = []
    blog_urls = [x.text for x in ElementTree.fromstring(blog_sitemap).iter() if x.tag.endswith('}loc')] if blog_status == 200 else []
    urls += blog_urls
    # Follow public sitemap entries only, on the same origin. Never crawl APIs,
    # checkout, private downloads or third-party links found inside an article.
    allowed_hosts = {urlsplit(base).netloc, 'tabobooks.com', 'www.tabobooks.com'}
    for url in urls:
        parsed = urlsplit(url)
        if parsed.netloc in allowed_hosts and not parsed.path.startswith(('/api/', '/checkout', '/originals/', '/downloads/')):
            paths.append(parsed.path + ('?' + parsed.query if parsed.query else ''))
    paths += ['/books/book999', '/books/book0', '/book.html?id=book999', '/robots.txt']
    paths = list(dict.fromkeys(paths))

    def one(path):
        status, headers, source = fetch(base + path)
        page = Page(source)
        broken_fragments = []
        for href in page.anchors:
            parsed = urlsplit(href)
            if parsed.fragment and parsed.netloc in ('', *allowed_hosts) and parsed.path in ('', urlsplit(path).path):
                if unquote(parsed.fragment) not in page.ids:
                    broken_fragments.append(href)
        issues = []
        expected = 404 if path in ('/books/book999', '/books/book0', '/book.html?id=book999') else 200
        if status != expected:
            issues.append('http_' + str(status) + '_expected_' + str(expected))
        if status == 200 and headers.get('content-type', '').startswith('text/html'):
            if not page.title.strip(): issues.append('missing_title')
            if page.h1_count != 1: issues.append('h1_count_' + str(page.h1_count))
            if not page.meta.get('description', '').strip(): issues.append('missing_description')
            if len([x for x in page.links if x.get('rel') == 'canonical']) != 1: issues.append('canonical_count')
            if broken_fragments: issues.append('broken_fragment_links')
            if len(page.ids) != len(set(page.ids)): issues.append('duplicate_ids')
            if 'noindex' in headers.get('x-robots-tag', '') + page.meta.get('robots', ''): issues.append('noindex')
        return {'path': path, 'status': status, 'bytes': len(source.encode()), 'title': page.title,
                'h1': page.h1, 'description': page.meta.get('description'),
                'canonical': [x.get('href') for x in page.links if x.get('rel') == 'canonical'],
                'graphs': len(page.graphs), 'bookLinks': sorted(set(x for x in page.anchors if x.startswith('/books/'))),
                'robotsHeader': headers.get('x-robots-tag'), 'robotsMeta': page.meta.get('robots'),
                'h1Count': page.h1_count, 'brokenFragments': broken_fragments, 'issues': issues}
    with ThreadPoolExecutor(max_workers=4) as pool:
        pages = list(pool.map(one, paths))
    return {'base': base, 'sitemapStatus': sitemap_status, 'blogSitemapStatus': blog_status,
            'sitemapURLs': urls, 'pages': pages}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('base')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = audit(args.base)
    with open(args.output, 'w') as target:
        json.dump(result, target, ensure_ascii=False, indent=2)
    print(json.dumps({'pages': len(result['pages']), 'sitemapURLs': len(result['sitemapURLs']),
                      'books200': sum(p['status'] == 200 for p in result['pages'] if p['path'].startswith('/books/') and p['status'] == 200),
                      'issues': [{'path':p['path'],'issues':p['issues']} for p in result['pages'] if p['issues']]}))
