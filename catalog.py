"""Authoritative book catalog, preview policy and independent-price checkout."""
from decimal import Decimal
import json
import store_catalog
from pathlib import Path

BOOKS = {'book1': ('هرمون', 'cover-edition-20261109/book1.pdf'),
 'book2': ('نحو القمة', 'cover-edition-20261109/book2.pdf'),
 'book3': ('تمرد', 'cover-edition-20261109/book3.pdf'),
 'book4': ('كيف تصنع المليون الأول', 'cover-edition-20261109/book4.pdf'),
 'book5': ('شهوات', 'cover-edition-20261109/book5.pdf'),
 'book6': ('دليل السمو', 'cover-edition-20261109/book6.pdf'),
 'book7': ('إلى كل بنت', 'cover-edition-20261109/book7.pdf'),
 'book8': ('أصنام', 'cover-edition-20261109/book8.pdf'),
 'book9': ('تأثير الكوبرا', 'cover-edition-20261109/book9.pdf'),
 'book10': ('دليل التحرر من العادة السرية', 'cover-edition-20261109/book10.pdf'),
 'book11': ('الأفعى والمنشار', 'cover-edition-20261109/book11.pdf'),
 'book12': ('جسد', 'cover-edition-20261109/book12.pdf'),
 'book13': ('بنت تخطت الثلاثين', 'cover-edition-20261109/book13.pdf'),
 'book14': ('أنت', 'cover-edition-20261109/book14.pdf'),
 'book15': ('شفرة الحياة', 'cover-edition-20261109/book15.pdf'),
 'book16': ('شفرة قلب الأنثى', 'cover-edition-20261109/book16.pdf'),
 'book17': ('عجلة الفئران', 'cover-edition-20261109/book17.pdf'),
 'book18': ('كيف تغير حياتك', 'cover-edition-20261109/book18.pdf'),
 'book19': ('بدون شهادة جامعية', 'cover-edition-20261109/book19.pdf'),
 'book20': ('السيجما: الذكر الملكي', 'cover-edition-20261109/book20.pdf'),
 'book21': ('السيجما: أنوثة طاغية', 'cover-edition-20261109/book21.pdf'),
 'book22': ('محكمة الحب والجنس', 'cover-edition-20261109/book22.pdf'),
 'book23': ('هرمونات أنثى', 'cover-edition-20261109/book23.pdf'),
 'book24': ('تحت المجهر', 'cover-edition-20261109/book24.pdf'),
 'book25': ('رسائل الياسمين — الجزء الأول', 'book25.pdf')}

# All titles share the same unit prices and per-additional-book discount.
UNIT_PRICES = {'EGP': 149.99, 'USD': 4.99}
PRICING_VERSION = 'uniform-20261121'

def quantity_prices(currency):
    unit = Decimal(str(UNIT_PRICES[currency]))
    return {count: float(unit * count - (count - 1)) for count in range(1, len(BOOKS) + 1)}

PRICES = quantity_prices('EGP')
# Preview restrictions are independent of price and quantity-discount eligibility.
FIXED_PRICES = {}
PREVIEW_DISABLED = frozenset({'book20', 'book21', 'book22', 'book23', 'book24'})

OFFERS = json.loads((Path(__file__).resolve().parent / 'content/offers.json').read_text())
for _offer in OFFERS.values():
    if (len(_offer['books']) < 2 or len(_offer['books']) != len(set(_offer['books'])) or
            any(b not in BOOKS for b in _offer['books']) or
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
    def price(book_id):
        if book_id not in fixed:
            return ordinary_price
        return fixed[book_id][currency]
    return {book_id: {
        'price': price(book_id),
        'bundleEligible': book_id not in fixed,
        'previewEnabled': preview_enabled(book_id),
    } for book_id in BOOKS}


def public_offers(currency):
    return [{'id':key,'books':offer['books'],'price':offer['prices'][currency],
             'title':offer['title']} for key,offer in current_offers().items()
            if currency in offer['prices']]


def pricing_breakdown(ids, price_config):
    """Apply the quantity discount to the whole cart and disjoint pack savings.

    Each pack saves the difference between its standalone quantity total and
    its advertised price. This preserves the extra-book discount across packs
    and uncovered books without counting a shared title twice.
    """
    if len(set(ids)) != len(ids) or any(book_id not in BOOKS for book_id in ids):
        raise ValueError('Invalid or duplicate book')
    currency = price_config['currency']
    policies = price_config.get('bookPolicies')
    if policies is None:
        policies = public_policies(currency, price_config['prices'].get('1'))
    fixed = {book_id: Decimal(str(policies[book_id]['price'])) for book_id in ids
             if not policies[book_id]['bundleEligible']}
    ordinary = set(ids) - fixed.keys()
    def regular(count):
        return Decimal(str(price_config['prices'][str(count)])) if count else Decimal(0)
    def standalone(ids):
        return regular(len(ordinary.intersection(ids))) + sum((fixed.get(book_id, Decimal(0)) for book_id in ids), Decimal(0))
    baseline = standalone(ids)
    best, applied, covered = baseline, [], set()
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
                cost += standalone(books) - Decimal(str(offer['prices'][currency]))
        else:
            cost = baseline - cost
            # Never select a malformed combination with a non-positive total.
            if cost <= 0:
                continue
            # Prefer the fuller advertised pack when totals tie.
            if cost < best or (cost == best and applied and len(used) > len(covered)):
                best, applied, covered = cost, selected, used
    offer_savings = baseline - best
    # Select the existing packs first. The individual discount applies to the remainder.
    promotion = price_config.get('bookPromotion', {})
    promoted = promotion.get('bookId')
    if promotion.get('currency') == currency and promoted in fixed and promoted not in covered:
        best -= fixed[promoted] - Decimal(str(promotion['price']))
    return {'total':best,'offerIds':applied,'offerSavings':offer_savings}


def total_for_items(ids, price_config):
    total = pricing_breakdown(ids,price_config)['total'].quantize(Decimal('0.01'))
    return int(total) if total == total.to_integral_value() else float(total)
