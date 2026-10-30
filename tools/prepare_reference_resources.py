"""Validate original reusable worksheets; prepare data without database access."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.prepare_editorial_batch import to_html
import seo


def prepare():
    folder = ROOT / 'content/resources'
    manifest = json.loads((folder/'manifest.json').read_text())
    posts = []
    for item in manifest['resources']:
        slug, book = item['slug'], item['book_id']
        assert re.fullmatch(r'[a-z0-9-]+', slug) and book in seo.BOOKS
        source = (folder/(slug+'.md')).read_text()
        words = len(re.findall(r'\S+', source))
        assert 250 <= words <= 650, (slug, words)
        assert source.count('\n## ') >= 4
        assert 'https://tabobooks.com/books/'+book in source
        assert 'https://tabobooks.com/blog/'+slug in source
        assert 'اختيارية' in source and '................................' in source
        assert 70 <= len(item['description']) <= 220
        body = to_html(source)
        assert '<script' not in body and '<form' not in body
        number = 7_000_000_000_000_000_000 + int(hashlib.sha256(('tabo-editorial:'+slug).encode()).hexdigest()[:15],16)
        posts.append({'id':str(number),'slug':slug,'title':item['title'],
            'description':item['description'],'language':'ar','content_html':body,
            'content_markdown':source,'keywords':[seo.BOOKS[book]['name']],
            'meta_keywords':'','hero_image':None,'infographic_image':None,'hero_alt':'',
            'faq':[],'source_article_id':None,'editorial_book_id':book,
            'editorial_batch':manifest['batch'],'resource_kind':'worksheet','word_count':words})
    assert len(posts) == len({p['slug'] for p in posts}) == len({p['id'] for p in posts}) == 3
    return posts


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    posts = prepare()
    args.output.write_text(json.dumps(posts, ensure_ascii=False, indent=2))
    print(json.dumps({'ready':len(posts),'words':sum(p['word_count'] for p in posts)}))
