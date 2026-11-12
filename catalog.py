"""Authoritative book catalog, preview policy and independent-price checkout."""
from decimal import Decimal, ROUND_HALF_UP
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
 'book24': ('تحت المجهر', 'cover-edition-20261109/book24.pdf')}

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
FIXED_PRICES = {'book20': {'EGP': 500, 'USD': 19.99, 'KWD': 6.200},
                'book21': {'EGP': 500, 'USD': 19.99, 'KWD': 6.200},
                'book22': {'EGP': 500, 'USD': 19.99, 'KWD': 6.200},
                'book23': {'EGP': 500, 'USD': 19.99, 'KWD': 6.200},
                'book24': {'EGP': 500, 'USD': 19.99, 'KWD': 6.200}}
PREVIEW_DISABLED = frozenset({'book20', 'book21', 'book22', 'book23', 'book24'})

# Explicitly approved KWD offer prices, with the newest marriage pack rounded
# to the same 50-fils grid (USD 39 -> KWD 12.100 at the proposal exchange rate).
KWD_OFFER_PRICES = {
    'change-starter': 4.650,
    'money-and-wealth': 6.200,
    'self-growth': 7.150,
    'sigma-couple': 10.900,
    'sigma-trilogy': 15.250,
    'marriage-awareness': 12.100,
}


def kwd_from_custom_usd(value):
    """Fallback only for newly owner-customized independent book prices."""
    usd = Decimal(str(value))
    kwd = ((usd * Decimal('0.310823') / Decimal('0.05')).quantize(
        Decimal('1'), rounding=ROUND_HALF_UP) * Decimal('0.05'))
    return float(kwd.quantize(Decimal('0.001')))


def offer_price(offer_id, offer, currency):
    if currency == 'KWD':
        return KWD_OFFER_PRICES.get(offer_id) if 'USD' in offer['prices'] else None
    return offer['prices'].get(currency)
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
    def price(book_id):
        if book_id not in fixed:
            return ordinary_price
        prices = fixed[book_id]
        if currency in prices:
            return prices[currency]
        if currency == 'KWD' and 'USD' in prices:
            return kwd_from_custom_usd(prices['USD'])
        raise ValueError('Independent book price is missing')
    return {book_id: {
        'price': price(book_id),
        'bundleEligible': book_id not in fixed,
        'previewEnabled': preview_enabled(book_id),
    } for book_id in BOOKS}


def public_offers(currency):
    return [{'id':key,'books':offer['books'],'price':offer_price(key,offer,currency),
             'title':offer['title']} for key,offer in current_offers().items()
            if offer_price(key,offer,currency) is not None]


def pricing_breakdown(ids, price_config):
    """Choose disjoint packs, then price the ordinary and fixed-price remainder.

    Eligibility depends only on actual book IDs. A shared title is never counted
    twice. Fixed-price books only receive their explicitly configured special pack;
    they never contribute to the ordinary quantity discount.
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
    def remainder(used):
        fixed_total = sum((price for book_id, price in fixed.items() if book_id not in used), Decimal(0))
        return regular(len(ordinary - used)) + fixed_total
    baseline = remainder(set())
    best, applied, covered = baseline, [], set()
    eligible = [(key,offer) for key,offer in current_offers().items()
                if offer_price(key,offer,currency) is not None and set(offer['books']) <= set(ids)]
    for mask in range(1, 1 << len(eligible)):
        used, selected, cost = set(), [], Decimal(0)
        for i,(key,offer) in enumerate(eligible):
            if mask & (1 << i):
                books = set(offer['books'])
                if used & books:
                    break
                used |= books; selected.append(key)
                cost += Decimal(str(offer_price(key,offer,currency)))
        else:
            cost += remainder(used)
            # Prefer the fuller advertised pack when totals tie (overlapping fixed-price packs).
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
    unit = Decimal('0.001' if price_config['currency'] == 'KWD' else '0.01')
    total = pricing_breakdown(ids,price_config)['total'].quantize(unit)
    return int(total) if total == total.to_integral_value() else float(total)
