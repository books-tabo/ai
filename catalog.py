"""Authoritative book catalog, preview policy and independent-price checkout."""
from decimal import Decimal

BOOKS = {'book1': ('هرمون', 'book1.pdf'),
 'book2': ('نحو القمة', 'book2.pdf'),
 'book3': ('تمرد', 'book3.pdf'),
 'book4': ('كيف تصنع المليون الأول', 'book4.pdf'),
 'book5': ('شهوات', 'book5.pdf'),
 'book6': ('دليل السمو', 'book6.pdf'),
 'book7': ('إلى كل بنت', 'book7.pdf'),
 'book8': ('أصنام', 'book8.pdf'),
 'book9': ('تأثير الكوبرا', 'book9.pdf'),
 'book10': ('دليل التحرر من العادة السرية', 'book10.pdf'),
 'book11': ('الأفعى والمنشار', 'book11.pdf'),
 'book12': ('جسد', 'book12.pdf'),
 'book13': ('بنت تخطت الثلاثين', 'book13.pdf'),
 'book14': ('أنت', 'book14.pdf'),
 'book15': ('شفرة الحياة', 'book15.pdf'),
 'book16': ('شفرة قلب الأنثى', 'book16.pdf'),
 'book17': ('عجلة الفئران', 'book17.pdf'),
 'book18': ('كيف تغير حياتك', 'book18.pdf'),
 'book19': ('بدون شهادة جامعية', 'book19.pdf'),
 'book20': ('السيجما: الذكر الملكي', 'book20.pdf')}

PRICES = {1: 199,
 2: 299,
 3: 399,
 4: 499,
 5: 599,
 6: 699,
 7: 799,
 8: 899,
 9: 999,
 10: 1099,
 11: 1199,
 12: 1299,
 13: 1399,
 14: 1499,
 15: 1599,
 16: 1699,
 17: 1799,
 18: 1899,
 19: 1999}

# Fixed-price books never contribute to the ordinary bundle count.
FIXED_PRICES = {'book20': {'EGP': 500, 'USD': 15.90}}
PREVIEW_DISABLED = frozenset({'book20'})


def preview_enabled(book_id):
    return book_id in BOOKS and book_id not in PREVIEW_DISABLED


def public_policies(currency, ordinary_price):
    return {book_id: {
        'price': FIXED_PRICES[book_id][currency] if book_id in FIXED_PRICES else ordinary_price,
        'bundleEligible': book_id not in FIXED_PRICES,
        'previewEnabled': preview_enabled(book_id),
    } for book_id in BOOKS}


def total_for_items(ids, price_config):
    if len(set(ids)) != len(ids) or any(book_id not in BOOKS for book_id in ids):
        raise ValueError('Invalid or duplicate book')
    ordinary_count = sum(book_id not in FIXED_PRICES for book_id in ids)
    total = Decimal(str(price_config['prices'][str(ordinary_count)])) if ordinary_count else Decimal(0)
    currency = price_config['currency']
    total += sum((Decimal(str(FIXED_PRICES[book_id][currency]))
                  for book_id in ids if book_id in FIXED_PRICES), Decimal(0))
    total = total.quantize(Decimal('0.01'))
    return int(total) if total == total.to_integral_value() else float(total)
