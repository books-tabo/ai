"""Authoritative book catalog, preview policy and independent-price checkout."""
from decimal import Decimal
import json
import store_catalog
from pathlib import Path

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
 'book20': ('السيجما: الذكر الملكي', 'book20.pdf'),
 'book21': ('السيجما: أنوثة طاغية', 'book21.pdf')}

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
FIXED_PRICES = {'book20': {'EGP': 500, 'USD': 15.90},
                'book21': {'EGP': 500, 'USD': 15.99}}
PREVIEW_DISABLED = frozenset({'book20', 'book21'})
OFFERS = json.loads((Path(__file__).resolve().parent / 'content/offers.json').read_text())
for _offer in OFFERS.values():
    _fixed_bundle = _offer.get('fixedPriceBundle', False)
    if (len(_offer['books']) < 2 or len(_offer['books']) != len(set(_offer['books'])) or
            any(b not in BOOKS for b in _offer['books']) or
            any((b in FIXED_PRICES) != _fixed_bundle for b in _offer['books']) or
            not _offer['prices'] or set(_offer['prices']) - {'EGP', 'USD'} or
            any(Decimal(str(v)) <= 0 for v in _offer['prices'].values())):
        raise ValueError('Invalid curated offer')


def current_offers():
    offers=store_catalog.document().get('offers',OFFERS)
    return {key:value for key,value in offers.items() if value.get('active',True)}


def current_fixed_prices():
    return {**FIXED_PRICES,**store_catalog.document().get('independentPrices',{})}


def preview_enabled(book_id):
    return book_id in BOOKS and book_id not in PREVIEW_DISABLED


def public_policies(currency, ordinary_price):
    fixed=current_fixed_prices()
    return {book_id: {
        'price': fixed[book_id][currency] if book_id in fixed else ordinary_price,
        'bundleEligible': book_id not in fixed,
        'previewEnabled': preview_enabled(book_id),
    } for book_id in BOOKS}


def public_offers(currency):
    return [{'id':key,'books':offer['books'],'price':offer['prices'][currency],
             'title':offer['title']} for key,offer in current_offers().items() if currency in offer['prices']]


def pricing_breakdown(ids, price_config):
    """Choose disjoint packs, then price the ordinary and fixed-price remainder.

    Eligibility depends only on actual book IDs. A shared title is never counted
    twice. Fixed-price books only receive their explicitly configured special pack;
    they never contribute to the ordinary quantity discount.
    """
    if len(set(ids)) != len(ids) or any(book_id not in BOOKS for book_id in ids):
        raise ValueError('Invalid or duplicate book')
    fixed_prices=current_fixed_prices()
    ordinary = set(ids) - set(fixed_prices)
    currency = price_config['currency']
    def regular(count):
        return Decimal(str(price_config['prices'][str(count)])) if count else Decimal(0)
    def remainder(used):
        fixed = sum((Decimal(str(fixed_prices[b][currency])) for b in ids
                     if b in fixed_prices and b not in used), Decimal(0))
        return regular(len(ordinary - used)) + fixed
    baseline = remainder(set())
    best, applied = baseline, []
    eligible = [(key,offer) for key,offer in current_offers().items()
                if currency in offer['prices'] and set(offer['books']) <= set(ids)]
    for mask in range(1, 1 << len(eligible)):
        used, selected, cost = set(), [], Decimal(0)
        for i,(key,offer) in enumerate(eligible):
            if mask & (1 << i):
                books = set(offer['books'])
                if used & books:
                    break
                used |= books; selected.append(key)
                cost += Decimal(str(offer['prices'][currency]))
        else:
            cost += remainder(used)
            if cost < best:
                best, applied = cost, selected
    return {'total':best,'offerIds':applied,'offerSavings':baseline-best}


def total_for_items(ids, price_config):
    total = pricing_breakdown(ids,price_config)['total'].quantize(Decimal('0.01'))
    return int(total) if total == total.to_integral_value() else float(total)
