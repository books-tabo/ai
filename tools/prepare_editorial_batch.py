"""Validate original editorial sources and prepare insert-only publishing payloads.

No credentials, network access or database writes. IDs are decimal strings to
preserve bigint precision across tooling. Production publishing is a separate,
authorized, atomic INSERT; never upsert an existing article.
"""
import argparse
from collections import Counter
import csv
import hashlib
import html
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import autoseo
import seo


def inline(value):
    out, start = [], 0
    for match in re.finditer(r'\[([^\]]+)\]\(([^)]+)\)', value):
        out.append(html.escape(value[start:match.start()]))
        target = autoseo.safe_link(match[2])
        if not target:
            raise ValueError('Unsafe editorial link')
        out.append('<a href="' + html.escape(target, quote=True) + '">' + html.escape(match[1]) + '</a>')
        start = match.end()
    out.append(html.escape(value[start:]))
    return ''.join(out)


def to_html(source):
    out = []
    for block in re.split(r'\n\s*\n', source.strip()):
        if block.startswith('## '):
            if '\n' in block:
                raise ValueError('Heading must be its own block')
            out.append('<h2>' + inline(block[3:]) + '</h2>')
        elif block.startswith('- '):
            lines = block.splitlines()
            if any(not line.startswith('- ') for line in lines):
                raise ValueError('Invalid editorial list')
            out.append('<ul>' + ''.join('<li>' + inline(line[2:]) + '</li>' for line in lines) + '</ul>')
        else:
            out.append('<p>' + inline(' '.join(block.splitlines())) + '</p>')
    return autoseo.sanitize(''.join(out), {})


def prepare(manifest_name='batch-one.json'):
    folder = ROOT / 'content/editorial'
    with (folder/'plan.tsv').open() as handle:
        rows = list(csv.DictReader(handle, delimiter='\t'))
    assert len(rows) == 84
    assert Counter(row['book_id'] for row in rows) == Counter({bid:4 for bid in seo.BOOKS})
    for key in ('slug','title','primary_keyword'):
        assert len({row[key] for row in rows}) == len(rows), key
    by_slug = {row['slug']:row for row in rows}
    assert re.fullmatch(r'batch-[a-z]+\.json', manifest_name), 'Invalid manifest name'
    manifests = [json.loads(path.read_text()) for path in sorted(folder.glob('batch-*.json'))]
    all_slugs = [item['slug'] for batch in manifests for item in batch['articles']]
    assert len(all_slugs) == len(set(all_slugs)), 'Article appears in multiple batches'
    assert len({batch['batch'] for batch in manifests}) == len(manifests), 'Duplicate batch ID'
    manifest = json.loads((folder/manifest_name).read_text())
    posts, repeated = [], set()
    for path in folder.glob('*.md'):
        if path.stem not in {entry['slug'] for entry in manifest['articles']}:
            repeated.update(paragraph for paragraph in path.read_text().split('\n\n')
                            if len(paragraph) > 160 and not paragraph.startswith('##'))
    for entry in manifest['articles']:
        row = by_slug[entry['slug']]
        source = (folder/(entry['slug']+'.md')).read_text()
        words = len(re.findall(r'\S+', source))
        assert 400 <= words <= 900, (entry['slug'], words)
        assert source.count('\n## ') >= 4, entry['slug']
        assert '/books/'+row['book_id'] in source, entry['slug']
        assert 70 <= len(entry['description']) <= 220
        for paragraph in source.split('\n\n'):
            if len(paragraph) > 160 and not paragraph.startswith('##'):
                assert paragraph not in repeated, 'Repeated editorial paragraph'
                repeated.add(paragraph)
        body = to_html(source)
        for target in re.findall(r'href="([^"]+)"', body):
            target = html.unescape(target)
            if target.startswith('/blog/'):
                assert target.removeprefix('/blog/') in all_slugs, target
            if target.startswith('/books/'):
                assert target.removeprefix('/books/') in seo.BOOKS, target
        number = 7_000_000_000_000_000_000 + int(hashlib.sha256(('tabo-editorial:'+entry['slug']).encode()).hexdigest()[:15],16)
        posts.append({'id':str(number),'slug':row['slug'],'title':row['title'],
            'description':entry['description'],'language':'ar','content_html':body,
            'content_markdown':source,'keywords':[row['primary_keyword'],seo.BOOKS[row['book_id']]['name']],
            'meta_keywords':row['primary_keyword'],'hero_image':None,'infographic_image':None,
            'hero_alt':'','faq':[],'source_article_id':None,'editorial_book_id':row['book_id'],
            'editorial_batch':manifest['batch'],'word_count':words})
    assert len(posts) == 12 and len({p['id'] for p in posts}) == 12
    counts = Counter(p['editorial_book_id'] for p in posts)
    assert len(counts) == 3 and set(counts.values()) == {4}, counts
    return rows, posts


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', default='batch-one.json')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    rows, posts = prepare(args.manifest)
    args.output.write_text(json.dumps(posts, ensure_ascii=False, indent=2))
    print(json.dumps({'planned':len(rows),'ready':len(posts),'words':sum(p['word_count'] for p in posts),
        'byBook':dict(Counter(p['editorial_book_id'] for p in posts))}))
