"""Read-only audit of public HTML; never creates orders or tracking events."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from xml.etree import ElementTree


class Page(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.title = self.h1 = ''
        self.meta, self.links, self.anchors, self.graphs = {}, [], [], []
        self.active = None
        self.ld = None
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
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
            return response.status, dict(response.headers), response.read().decode('utf-8')
    except HTTPError as error:
        return error.code, dict(error.headers), error.read().decode('utf-8', errors='replace')


def audit(base):
    base = base.rstrip('/')
    sitemap_status, _, sitemap = fetch(base + '/sitemap.xml')
    paths = ['/'] + ['/books/book' + str(n) for n in range(1, 21)]
    paths += ['/authors', '/about', '/contact', '/privacy', '/refund', '/delivery',
              '/books/book999', '/books/book0', '/book.html?id=book999', '/robots.txt']
    if sitemap_status == 200:
        urls = [x.text for x in ElementTree.fromstring(sitemap).iter() if x.tag.endswith('}loc')]
    else:
        urls = []

    def one(path):
        status, headers, source = fetch(base + path)
        page = Page(source)
        return {'path': path, 'status': status, 'bytes': len(source.encode()), 'title': page.title,
                'h1': page.h1, 'description': page.meta.get('description'),
                'canonical': [x.get('href') for x in page.links if x.get('rel') == 'canonical'],
                'graphs': len(page.graphs), 'bookLinks': sorted(set(x for x in page.anchors if x.startswith('/books/'))),
                'robotsHeader': headers.get('X-Robots-Tag'), 'robotsMeta': page.meta.get('robots')}
    with ThreadPoolExecutor(max_workers=4) as pool:
        pages = list(pool.map(one, paths))
    return {'base': base, 'sitemapStatus': sitemap_status, 'sitemapURLs': urls, 'pages': pages}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('base')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = audit(args.base)
    with open(args.output, 'w') as target:
        json.dump(result, target, ensure_ascii=False, indent=2)
    print(json.dumps({'pages': len(result['pages']), 'sitemapURLs': len(result['sitemapURLs']),
                      'books200': sum(p['status'] == 200 for p in result['pages'] if p['path'] in ['/books/book'+str(n) for n in range(1,21)])}))
