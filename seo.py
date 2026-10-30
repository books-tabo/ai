"""Public, server-rendered catalog. Never reads orders, statistics or PDF originals."""
import hashlib
import html
import json
import os
from pathlib import Path
import re
from xml.etree.ElementTree import Element, SubElement, tostring

import catalog

ROOT = Path(__file__).resolve().parent
ORIGIN = 'https://tabobooks.com'
BOOKS = json.loads((ROOT / 'content/books.json').read_text())
PAGES = json.loads((ROOT / 'content/pages.json').read_text())
CATEGORIES = json.loads((ROOT / 'content/categories.json').read_text())
ARTICLES = json.loads((ROOT / 'content/articles.json').read_text())
ASSET_VERSIONS = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()[:12] for name in ('seo.js', 'seo.css')}
AUTHOR = {'ar': 'تابو نور', 'en': 'TABO NOOR'}
AUTHOR_BIO = {
    'ar': 'تابو نور كاتب ومؤلف يهتم بالوعي وتطوير الذات والمال والطموح وفهم الإنسان لنفسه والعالم من حوله. تتناول كتبه السلوك والعلاقات والاختيار والمسؤولية، وتربط التأمل بخطوات عملية.',
    'en': 'TABO NOOR writes about awareness, personal growth, money, ambition and understanding human nature. His books explore behavior, relationships, choice and responsibility, connecting reflection with practical action.'}


def current_books():
    return catalog.store_catalog.book_data(BOOKS)


def review_mode():
    return os.environ.get('TABO_SITE_ENV', 'production') != 'production'


def indexable(host):
    return not review_mode() and host.lower().split(':')[0] in ('tabobooks.com', 'www.tabobooks.com')


def escape(value):
    return html.escape(str(value), quote=True)


def safe_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')


def text(book, field, language):
    return book[field if language == 'ar' else 'en' + field[0].upper() + field[1:]]


def book_name(book, language):
    return book['name' if language == 'ar' else 'enName']


def link(path, label):
    return '<a href="' + escape(path) + '">' + escape(label) + '</a>'


def paragraphs(value):
    return ''.join('<p>' + escape(p) + '</p>' for p in value.split('\n\n') if p.strip())


def categories_for(book_id):
    matches = [(key, cat) for key, cat in CATEGORIES.items() if book_id in cat['books']]
    return sorted(matches, key=lambda item: item[0] != current_books()[book_id].get('primaryCategory'))


def book_card(book_id, language='ar', home=False, position=0):
    book = current_books()[book_id]
    name = book_name(book, language)
    image = book['image']
    loading = 'eager' if position < 2 else 'lazy'
    if home:
        preview = ('<a class="book-sample-link" href="/previews/' + book_id + '" target="_blank" rel="noopener">اقرأ عينة مجانية</a>') if catalog.preview_enabled(book_id) else '<p class="fixed-price-note">سعر مستقل — خارج خصومات عدد الكتب</p>'
        return f'''<div class="book-card"><a class="book-cover-link" href="/books/{book_id}"><img class="book-image" src="{image}" alt="{escape(name)}" width="{book['imageWidth']}" height="{book['imageHeight']}" loading="{loading}" decoding="async"><span class="pdf-badge">PDF</span></a><div class="book-content"><div class="book-title"><span>{escape(name)}</span><span class="title-pdf">PDF</span></div><div class="book-description">{escape(book['short'])}</div><a class="read-more-btn" href="/books/{book_id}">اقرأ المزيد</a><div class="book-bottom">{preview}<div class="book-price">السعر بحسب الدولة</div><div class="book-format">نسخة PDF</div><button class="add-cart-btn" id="addBtn-{position}" data-book-id="{book_id}">أضف للسلة</button></div></div></div>'''
    return f'''<article class="seo-book"><a href="/books/{book_id}"><img src="{image}" alt="{escape(name)}" width="{book['imageWidth']}" height="{book['imageHeight']}" loading="lazy" decoding="async"><h3>{escape(name)}</h3></a><p>{escape(text(book,'short',language))}</p></article>'''


def book_list(ids, language):
    return '<div class="seo-book-grid">' + ''.join(book_card(key, language) for key in ids if key in catalog.BOOKS) + '</div>'


def crumbs(items, language):
    label = 'مسار التنقل' if language == 'ar' else 'Breadcrumb'
    return '<nav class="seo-crumbs" aria-label="' + label + '"><ol>' + ''.join('<li>' + (link(path, name) if path else '<span aria-current="page">' + escape(name) + '</span>') + '</li>' for path, name in items) + '</ol></nav>'


def related(book_id):
    # Editorial relations are explicit: a shared category alone is not enough.
    return current_books()[book_id]['related']


def book_extra(book_id, language):
    book = current_books()[book_id]
    ar = language == 'ar'
    section = '<section class="section"><h2>' + ('لمن يناسب الكتاب؟' if ar else 'Who is this book for?') + '</h2>' + paragraphs(book['audience'][language])
    section += '<h2>' + ('موضوعات يناقشها الكتاب' if ar else 'Topics explored') + '</h2><ul>' + ''.join('<li>' + escape(s) + '</li>' for s in book['topics'][language]) + '</ul></section>'
    facts = [('اللغة' if ar else 'Book language', 'العربية' if ar else 'Arabic'), ('الصيغة' if ar else 'Format', 'PDF')]
    if book.get('pageCount'):
        facts.append(('عدد صفحات ملف PDF' if ar else 'PDF page count', str(book['pageCount'])))
    if book.get('edition'):
        facts.append(('الإصدار' if ar else 'Edition', book['edition'][language]))
    section += '<section class="section"><h2>' + ('معلومات الكتاب' if ar else 'Book details') + '</h2><dl class="seo-facts">' + ''.join('<dt>' + escape(k) + '</dt><dd>' + escape(v) + '</dd>' for k, v in facts) + '</dl></section>'
    if book.get('chapters'):
        section += '<section class="section"><h2>' + ('فهرس الفصول' if ar else 'Chapter contents') + '</h2><ol lang="ar" dir="rtl">' + ''.join('<li>' + escape(ch) + '</li>' for ch in book['chapters']) + '</ol></section>'
    section += '<section class="section"><h2>' + ('أسئلة عن الكتاب' if ar else 'Questions about this book') + '</h2>'
    section += ''.join('<details><summary>' + escape(q) + '</summary><p>' + escape(a) + '</p></details>' for q, a in book['faq'][language]) + '</section>'
    section += '<section class="section"><h2>' + ('كتب مرتبطة بالموضوع' if ar else 'Related reading') + '</h2>' + book_list(related(book_id), language) + '</section>'
    for offer_id, offer in catalog.current_offers().items():
        if offer.get('fixedPriceBundle') and book_id in offer['books']:
            section += '<section class="section"><h2>' + ('عرض كتابَي السيجما' if ar else 'Sigma pair offer') + '</h2>' + paragraphs(offer['description'][language]) + link('/offers/' + offer_id, 'عرض الباقة والكتابين' if ar else 'View the pack and both books') + '</section>'
    section += '<nav class="seo-categories" aria-label="' + ('أقسام الكتاب' if ar else 'Book categories') + '">' + ''.join(link('/categories/' + key, cat[language]) for key, cat in categories_for(book_id)) + '</nav>'
    return section


def offer_card(offer_id, language='ar', home=False):
    offer = catalog.current_offers()[offer_id]
    ar = language == 'ar'
    names = ''.join('<li>' + link('/books/'+bid,book_name(current_books()[bid],language)).replace('<a ', '<a data-offer-book-name="'+bid+'" ',1) + '</li>' for bid in offer['books'])
    images = ''.join('<img src="'+current_books()[bid]['image']+'" alt="'+escape(book_name(current_books()[bid],language))+'" width="'+str(current_books()[bid]['imageWidth'])+'" height="'+str(current_books()[bid]['imageHeight'])+'" loading="lazy" decoding="async">' for bid in offer['books'])
    action = ('<button type="button" data-add-offer="'+offer_id+'"' if home else '<a data-offer-buy href="/?offer='+offer_id+'&amp;cart=1"') + ' class="offer-button" data-offer-button-label>' + ('أضف الباقة للسلة' if ar else 'Add pack to cart') + ('</button>' if home else '</a>')
    heading = 'h3' if home else 'h2'
    prices = ' / '.join((str(value)+' جنيه' if currency=='EGP' and ar else 'EGP '+str(value) if currency=='EGP' else '$'+str(value)) for currency,value in offer['prices'].items())
    availability = ('هذا العرض متاح عند التسعير بالدولار فقط. داخل مصر يظل سعر كل كتاب 500 جنيه.' if ar else 'This offer applies only to USD pricing. In Egypt each book remains EGP 500.') if set(offer['prices'])=={'USD'} else ''
    return '<article class="curated-offer" data-offer-id="'+offer_id+'"><div class="offer-covers">'+images+'</div><p data-offer-count>'+str(len(offer['books']))+(' كتب PDF' if ar else ' PDF books')+'</p><'+heading+' data-offer-title>'+escape(offer['title'][language])+'</'+heading+'><p data-offer-description>'+escape(offer['description'][language])+'</p><ul>'+names+'</ul><div class="offer-price" data-offer-price>'+prices+'</div><p class="offer-original" data-offer-original hidden></p><p data-offer-availability>'+availability+'</p>'+action+'<a class="offer-details" href="/offers/'+offer_id+'" data-offer-details>'+('تفاصيل الباقة والكتب' if ar else 'Pack details and books')+'</a></article>'


def offers_section(language='ar', home=False):
    return '<section class="curated-offers" id="curatedOffers"><h2 data-offer-section-title>'+('باقات كتب مختارة' if language=='ar' else 'Curated book packs')+'</h2><div class="curated-offer-grid">'+''.join(offer_card(key,language,home) for key in catalog.current_offers())+'</div></section>'


def listed_articles(include_drafts=False):
    return {key: value for key, value in ARTICLES.items() if value['status'] == 'published' or include_drafts}


def public_paths():
    paths = ['/'] + ['/books/' + book_id for book_id in catalog.BOOKS]
    paths += ['/' + key for key in PAGES] + ['/categories', '/authors/tabo-noor']
    paths += ['/offers'] + ['/offers/' + key for key in catalog.current_offers()]
    paths += ['/categories/' + key for key, cat in CATEGORIES.items() if cat['books']]
    articles = listed_articles()
    if articles:
        paths += ['/articles'] + ['/articles/' + key for key in articles]
    return paths


def sitemap():
    root = Element('urlset', xmlns='http://www.sitemaps.org/schemas/sitemap/0.9')
    for path in public_paths():
        SubElement(SubElement(root, 'url'), 'loc').text = ORIGIN + path
    return tostring(root, encoding='utf-8', xml_declaration=True)


def robots(host):
    # Preview pages remain crawlable so crawlers can see their noindex headers.
    source = 'User-agent: *\nAllow: /\n'
    return source + ('Sitemap: ' + ORIGIN + '/sitemap.xml\n' if indexable(host) else '# Review/alternate origin: noindex is set in HTTP responses.\n')


def graph(path, title, description, image, trail, book_id=None):
    org_id, site_id, author_id = ORIGIN + '/#organization', ORIGIN + '/#website', ORIGIN + '/authors/tabo-noor#person'
    nodes = [
        {'@type': 'Organization', '@id': org_id, 'name': 'TABO BOOKS', 'url': ORIGIN + '/', 'logo': ORIGIN + '/tabo-books-logo.webp'},
        {'@type': 'WebSite', '@id': site_id, 'name': 'TABO BOOKS', 'url': ORIGIN + '/', 'publisher': {'@id': org_id}, 'inLanguage': 'ar'},
        {'@type': 'Person', '@id': author_id, 'name': AUTHOR['ar'], 'alternateName': AUTHOR['en'], 'url': ORIGIN + '/authors/tabo-noor', 'description': AUTHOR_BIO['ar']},
        {'@type': 'WebPage', '@id': ORIGIN + path + '#webpage', 'url': ORIGIN + path, 'name': title, 'description': description, 'isPartOf': {'@id': site_id}, 'inLanguage': 'ar'}]
    if trail and len(trail) > 1:
        nodes.append({'@type': 'BreadcrumbList', '@id': ORIGIN + path + '#breadcrumb', 'itemListElement': [
            {'@type': 'ListItem', 'position': i + 1, 'name': name, 'item': ORIGIN + (url or path)} for i, (url, name) in enumerate(trail)]})
        nodes[3]['breadcrumb'] = {'@id': ORIGIN + path + '#breadcrumb'}
    if book_id:
        book = current_books()[book_id]
        node = {'@type': 'Book', '@id': ORIGIN + path + '#book', 'name': book['name'], 'alternateName': book['enName'], 'url': ORIGIN + path,
                'description': book['short'], 'image': ORIGIN + image, 'author': {'@id': author_id}, 'publisher': {'@id': org_id},
                'bookFormat': 'https://schema.org/EBook', 'inLanguage': 'ar', 'encodingFormat': 'application/pdf', 'isAccessibleForFree': False}
        if book.get('pageCount'):
            node['numberOfPages'] = book['pageCount']
        if book.get('edition'):
            node['bookEdition'] = book['edition']['ar']
        nodes.append(node)
        nodes[3]['mainEntity'] = {'@id': node['@id']}
    # No Offer/Product/review: one country-dependent URL, no invented static price.
    return {'@context': 'https://schema.org', '@graph': nodes}


def page_data(path, include_drafts=False):
    """Return both UI languages. URL identity and actual Arabic edition stay fixed."""
    book_id = path.removeprefix('/books/') if path.startswith('/books/') else None
    if book_id not in catalog.BOOKS:
        book_id = None
    result = {}
    for language in ('ar', 'en'):
        ar = language == 'ar'
        home = 'الرئيسية' if ar else 'Home'
        trail = [('/', home)]
        image = '/tabo-books-logo.webp'
        body, extra = '', ''
        if path == '/':
            title = 'TABO BOOKS | كتب تابو نور العربية PDF' if ar else 'TABO BOOKS | Arabic PDF books by TABO NOOR'
            description = 'اكتشف كتب تابو نور العربية في تطوير الذات والمال وفهم السلوك والعلاقات. تصفح نبذات الكتب والأقسام، واختر نسختك الرقمية بصيغة PDF.' if ar else 'Discover original Arabic PDF books by TABO NOOR on personal growth, money, behavior and relationships. Explore book descriptions and subject collections.'
            body = '<nav class="seo-category-links" aria-label="' + ('تصفح الأقسام' if ar else 'Browse categories') + '">' + ''.join(link('/categories/' + key, cat[language]) for key, cat in CATEGORIES.items() if cat['books']) + '</nav>'
        elif book_id:
            book = current_books()[book_id]
            name = book_name(book, language)
            title = name + (' — كتاب تابو نور PDF | TABO BOOKS' if ar else ' — Arabic PDF by TABO NOOR | TABO BOOKS')
            description = text(book, 'short', language)
            primary, category = categories_for(book_id)[0]
            trail += [('/categories/' + primary, category[language]), (None, name)]
            image = book['image']
            extra = book_extra(book_id, language)
        elif path == '/offers':
            title = 'عروض باقات الكتب' if ar else 'Book pack offers'
            description = 'باقات مختارة من كتب تابو نور العربية PDF في التغيير والمال وتطوير الذات، وعرض خاص لكتابَي السيجما معًا بالدولار. تصفح كتب كل باقة وأسعارها.' if ar else 'Curated Arabic PDF book packs by TABO NOOR on change, money and personal growth, plus a dedicated USD offer for the two Sigma books.'
            trail += [(None,title)]
            body = paragraphs(description) + offers_section(language)
            body += paragraphs('يُطبق سعر الباقة عند اكتمال كتبها في السلة وفي العملة المحددة للعرض، ويُختار أقل إجمالي مستحق عند جمع العروض. حذف كتاب قد يغيّر العرض المستحق. كتابا السيجما خارج خصومات العدد، ولهما باقة مستقلة بالدولار فقط.' if ar else 'A pack price applies when all its books are in the cart and the offer supports the current currency. The lowest eligible total is used when packs are combined. Removing a book may change eligibility. The Sigma books do not count toward quantity discounts and have a dedicated USD-only pack.')
        elif path.startswith('/offers/') and path.rsplit('/',1)[-1] in catalog.current_offers():
            key = path.rsplit('/',1)[-1]
            offer = catalog.current_offers()[key]
            title,description = offer['title'][language],offer['description'][language]
            image = current_books()[offer['books'][0]]['image']
            trail += [('/offers','عروض الباقات' if ar else 'Book pack offers'),(None,title)]
            body = offer_card(key,language) + '<h2>'+('الكتب المشمولة في الباقة' if ar else 'Books included in this pack')+'</h2>'+book_list(offer['books'],language)
            body += '<section class="seo-section"><h2>'+('كيف تحصل على العرض؟' if ar else 'How does the offer work?')+'</h2>'+paragraphs('اضغط «أضف الباقة للسلة» لإضافة الكتب المحددة دون تكرار كتب موجودة بالفعل. يُحسب أقل سعر مستحق تلقائيًا. إذا حذفت كتابًا من الباقة يُعاد حساب الإجمالي والعروض المتاحة. الكتب باللغة العربية وبصيغة PDF، وتظهر روابطها في طلبك بعد إتمام الدفع وفق سياسة المتجر.' if ar else 'Add the pack to your cart to add its selected books without duplicating titles already there. The lowest eligible total is calculated automatically. Removing a book recalculates the available offers. These are Arabic PDF books; access follows the store delivery policy after successful payment.')+'</section>'
        elif path == '/categories':
            title = 'أقسام الكتب' if ar else 'Book categories'
            description = 'تصفح أقسام كتب تابو نور حسب موضوعها: تطوير الذات، علم النفس، المال والأعمال، الثروة، ريادة الأعمال والعلاقات الزوجية والأسرية.' if ar else 'Browse TABO NOOR books by subject: personal growth, psychology, money and business, wealth, entrepreneurship, and marriage and family relationships.'
            trail += [(None, title)]
            body = ''.join('<section class="seo-section"><h2>' + link('/categories/' + key, cat[language]) + '</h2>' + paragraphs(cat['description'][language]) + '</section>' for key, cat in CATEGORIES.items() if cat['books'])
        elif path.startswith('/categories/') and path.rsplit('/', 1)[-1] in CATEGORIES:
            cat = CATEGORIES[path.rsplit('/', 1)[-1]]
            if not cat['books']:
                return None
            title, description = cat[language], cat['description'][language]
            trail += [('/categories', 'الأقسام' if ar else 'Categories'), (None, title)]
            body = paragraphs(description) + '<h2>' + ('كتب هذا القسم' if ar else 'Books in this category') + '</h2>' + book_list(cat['books'], language)
            body += '<p>' + link('/?category=' + path.rsplit('/', 1)[-1] + '#booksSection', 'تصفية هذه الكتب داخل المتجر' if ar else 'Filter these books in the store') + '</p>'
        elif path == '/authors/tabo-noor':
            title, description = AUTHOR[language], AUTHOR_BIO[language]
            trail += [('/authors', 'المؤلفون' if ar else 'Authors'), (None, title)]
            body = paragraphs(description) + '<h2>' + ('كتب المؤلف' if ar else 'Books by the author') + '</h2>' + book_list(catalog.BOOKS, language)
        elif path.removeprefix('/') in PAGES:
            key = path.removeprefix('/')
            page = PAGES[key][language]
            title, body = page['title'], page['body']
            description = re.sub(r'<[^>]+>', ' ', body).strip()
            description = re.sub(r'\s+', ' ', html.unescape(description))[:170]
            trail += [(None, title)]
            if key == 'authors':
                body = '<section id="tabo-noor"><h2>' + link('/authors/tabo-noor', AUTHOR[language]) + '</h2>' + paragraphs(AUTHOR_BIO[language]) + book_list(catalog.BOOKS, language) + '</section>' + re.sub(r'^.*?(?=<section id=)', '', body, flags=re.S)
        elif path == '/articles' and listed_articles(include_drafts):
            title = 'مقالات القراءة والتطبيق' if ar else 'Reading and practice'
            description = 'أفكار عملية عن التعلم والعادات والمال، مع قراءات مرتبطة من كتب تابو نور.' if ar else 'Practical reflections on learning, habits and money, with related TABO NOOR books.'
            trail += [(None, title)]
            body = paragraphs(description) + ''.join('<section class="seo-section"><h2>' + link('/articles/' + key, article['title'][language]) + '</h2><p>' + escape(article['description'][language]) + '</p></section>' for key, article in listed_articles(include_drafts).items())
        elif path.startswith('/articles/') and path.rsplit('/', 1)[-1] in listed_articles(include_drafts):
            article = listed_articles(include_drafts)[path.rsplit('/', 1)[-1]]
            title, description = article['title'][language], article['description'][language]
            trail += [('/articles', 'المقالات' if ar else 'Articles'), (None, title)]
            if article['status'] != 'published':
                body = '<p class="seo-draft">' + ('مسودة للمراجعة — لم تُنشر في المتجر' if ar else 'Review draft — not published in the store') + '</p>'
            body += paragraphs(description)
            for section in article['sections'][language]:
                body += '<section class="seo-section"><h2>' + escape(section['heading']) + '</h2>' + paragraphs(section['text']) + '</section>'
            body += '<h2>' + ('قراءة مرتبطة' if ar else 'Related reading') + '</h2>' + book_list(article['books'], language)
        else:
            return None
        if path != '/' and not book_id:
            title += ' | TABO BOOKS'
        result[language] = {'title': title, 'description': description, 'image': ORIGIN + image, 'body': body,
                            'extra': extra, 'trail':trail, 'breadcrumb': crumbs(trail, language), 'heading': (book_name(current_books()[book_id], language) if book_id else title.removesuffix(' | TABO BOOKS'))}
    result['graph'] = graph(path, result['ar']['title'], result['ar']['description'], result['ar']['image'].removeprefix(ORIGIN),
                            result['ar']['trail'], book_id)
    result['canonical'] = ORIGIN + path
    return result


def replace_id(source, node_id, content):
    pattern = r'(<(?P<tag>[a-z0-9]+)\b[^>]*\bid="' + re.escape(node_id) + r'"[^>]*>)[\s\S]*?(</(?P=tag)>)'
    return re.sub(pattern, lambda m: m[1] + content + m[3], source, count=1)


def render(source, path, host, filename, *, blog_enabled=False):
    data = page_data(path, include_drafts=review_mode())
    if data is None:
        return None
    ar = data['ar']
    source = re.sub(r'<title>[\s\S]*?</title>', '<title>' + escape(ar['title']) + '</title>', source, count=1)
    source = re.sub(r'<meta\b[^>]*\bname=["\']description["\'][^>]*>', '', source, flags=re.I)
    head = '<meta name="description" content="' + escape(ar['description']) + '">\n<link rel="canonical" href="' + data['canonical'] + '">\n'
    for key, value in {'og:type': 'book' if path.startswith('/books/') else 'website', 'og:title': ar['title'], 'og:description': ar['description'], 'og:url': data['canonical'], 'og:image': ar['image'], 'og:site_name': 'TABO BOOKS', 'og:locale': 'ar_AR'}.items():
        head += '<meta property="' + key + '" content="' + escape(value) + '">\n'
    head += '<meta name="twitter:card" content="summary_large_image">\n'
    if not indexable(host):
        head += '<meta name="robots" content="noindex, nofollow, noarchive">\n'
    head += '<link rel="stylesheet" href="/seo.css?v=' + ASSET_VERSIONS['seo.css'] + '">\n<script type="application/ld+json" id="seo-graph">' + safe_json(data['graph']) + '</script>\n'
    head += '<script type="application/json" id="seo-data">' + safe_json(data) + '</script>\n<script src="/seo.js?v=' + ASSET_VERSIONS['seo.js'] + '"></script>\n'
    if filename in ('index.html','offers.html'):
        names = {bid:{'ar':book['name'],'en':book['enName']} for bid,book in current_books().items()}
        code = 'var TABO_CURATED_OFFERS='+safe_json(catalog.current_offers())+';var TABO_OFFER_BOOK_NAMES='+safe_json(names)+';\n'
        code += (ROOT/'offer-pricing.js').read_text()+'\n'+(ROOT/'offer-ui.js').read_text()
        head += '<script id="curated-offer-code">'+code+'</script>\n'
    source = source.replace('</head>', head + '</head>', 1)
    if review_mode():
        notice = '<aside class="seo-review-banner">نسخة مراجعة غير منشورة — ' + link('/articles', 'مسودات المقالات') + '</aside>'
        source = source.replace('<body>', '<body>' + notice, 1)
    if filename == 'index.html':
        home_books = [{'id':key,'name':book['name'],'image':book['image'],'imageWidth':book['imageWidth'],'imageHeight':book['imageHeight'],
                       'shortDescription':book['short'],'about':book['about'],'adds':book['adds'],'idea':book['idea'],
                       **{field:book[field] for field in ('fixedPrices','bundleEligible','previewEnabled') if field in book}} for key,book in current_books().items()]
        translations = {key:{'name':book['enName'],'shortDescription':book['enShort'],'about':book['enAbout'],'adds':book['enAdds'],'idea':book['enIdea']} for key,book in current_books().items()}
        source = source.replace('const BOOKS = []; /* SSR_HOME_BOOKS */','const BOOKS = ' + safe_json(home_books) + ';')
        source = source.replace('const BOOK_TRANSLATIONS = {}; /* SSR_HOME_TRANSLATIONS */','const BOOK_TRANSLATIONS = ' + safe_json(translations) + ';')
        categories = [{'id':'all','ar':'كل الكتب','en':'All books'}] + [{'id':key,'ar':cat['ar'],'en':cat['en'],'books':cat['books']} for key,cat in CATEGORIES.items()]
        categories += [{'id':'novels','ar':'روايات','en':'Novels','books':[]},{'id':'articles','ar':'مقالات','en':'Articles','books':[], **({'href':'/blog'} if blog_enabled else {})},{'id':'courses','ar':'الكورسات','en':'Courses','books':[]}]
        source = source.replace('const BOOK_CATEGORIES = []; /* SSR_CATEGORIES */','const BOOK_CATEGORIES = ' + safe_json(categories) + ';')
        grid = re.compile(r'(<div\s+class="books-grid"\s+id="booksGrid"\s*>)[\s\S]*?</div>')
        cards = ''.join(book_card(key, home=True, position=i) for i, key in enumerate(current_books()))
        source = grid.sub(lambda m: m[1] + cards + '</div>', source, count=1)
        source = source.replace('<section class="book-panorama"', offers_section(home=True)+'<section class="book-panorama"',1)
        source = source.replace('<section class="book-panorama"', '<div id="seo-body" class="seo-home-nav">' + ar['body'] + '</div><section class="book-panorama"', 1)
    elif filename == 'book.html':
        book_id = path.rsplit('/', 1)[-1]
        book = current_books()[book_id]
        for key, value in {'title': book['name'], 'short': book['short'], 'about': book['about'], 'adds': book['adds'], 'idea': book['idea'], 'author': AUTHOR_BIO['ar']}.items():
            source = replace_id(source, key, escape(value))
        source = source.replace('id="cover" alt=""', 'id="cover" src="' + book['image'] + '" alt="غلاف كتاب ' + escape(book['name']) + '" width="' + str(book['imageWidth']) + '" height="' + str(book['imageHeight']) + '" fetchpriority="high"')
        source = source.replace('<article class="product">', '<div id="seo-breadcrumb">' + ar['breadcrumb'] + '</div><article class="product">', 1)
        source = source.replace('</main>', '<p class="seo-author-link">' + link('/authors/tabo-noor', 'كتب تابو نور ونبذة عن المؤلف') + '</p><div id="seo-extra">' + ar['extra'] + '</div></main>', 1)
        source = source.replace('const BOOKS={}; /* SSR_BOOKS */', 'const BOOKS=' + safe_json(current_books()) + ';')
        source = source.replace('const id=""; /* SSR_BOOK_ID */', 'const id=' + safe_json(book_id) + ';')
        if book.get('pageCount'):
            source = replace_id(source, 'pages', str(book['pageCount']))
        if catalog.preview_enabled(book_id):
            source = source.replace('id="previewLink" hidden', 'id="previewLink" href="/previews/' + book_id + '"')
    else:
        source = replace_id(source, 'title', escape(ar['heading']))
        source = replace_id(source, 'body', ar['body'])
        source = source.replace('<article class="card">', '<div id="seo-breadcrumb">' + ar['breadcrumb'] + '</div><article class="card">', 1)
    return source
