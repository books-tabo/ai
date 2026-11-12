"""A scheduled discount on current single-book prices.

Use ISO timestamps with explicit offsets. A promotion can specify a storewide
percent or a bookId, currency and exact price for an individual book.
"""
from datetime import datetime
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
import json
import os
from pathlib import Path

import catalog


def load_campaign(path):
    data = json.loads(path.read_text(encoding='utf-8'))
    if data is None:
        return None
    period = {'startsAt', 'endsAt'}
    individual = {'bookId', 'currency', 'price'}
    if not isinstance(data, dict) or set(data) not in (period | {'percent'}, period | individual):
        raise ValueError('A book promotion needs a time period and a percentage or an individual price.')
    boundaries = []
    for field in ('startsAt', 'endsAt'):
        try:
            timestamp = datetime.fromisoformat(data[field])
        except (TypeError, ValueError):
            raise ValueError('Invalid book promotion timestamp.') from None
        if timestamp.utcoffset() is None:
            raise ValueError('Book promotion timestamps must include their time zone.')
        boundaries.append(timestamp.timestamp())
    start, end = boundaries
    if start >= end:
        raise ValueError('The book promotion must end after it starts.')
    if 'percent' in data:
        percent = data['percent']
        if type(percent) is not int or not 1 <= percent <= 99:
            raise ValueError('The book promotion percentage must be an integer from 1 to 99.')
        return {'percent': percent, 'start': start, 'end': end}

    book_id, currency, value = (data[field] for field in ('bookId', 'currency', 'price'))
    if (not isinstance(book_id, str) or book_id not in catalog.BOOKS or
            currency not in ('USD', 'EGP', 'KWD') or type(value) not in (int, float)):
        raise ValueError('Invalid individual book promotion.')
    price = Decimal(str(value))
    unit = Decimal('0.001' if currency == 'KWD' else '0.01')
    if not price.is_finite() or not unit <= price <= 100_000 or price != price.quantize(unit):
        raise ValueError('Invalid individual book promotion price.')
    return {'bookId': book_id, 'currency': currency, 'price': float(price), 'start': start, 'end': end}


CAMPAIGN = load_campaign(Path(__file__).with_name('content') / 'book-promotion.json')
if os.environ.get('TABO_CHECKOUT_ENABLED') != '1' or os.environ.get('TABO_SITE_ENV', 'production') != 'production':
    CAMPAIGN = None


def active(at):
    if CAMPAIGN and CAMPAIGN['start'] <= at < CAMPAIGN['end']:
        return CAMPAIGN
    return None


def revision(base_revision, at):
    campaign = active(at)
    if campaign is None:
        return base_revision
    period = f"{base_revision}:promotion:{campaign['start']}:{campaign['end']}"
    if 'bookId' in campaign:
        return f"{period}:{campaign['bookId']}:{campaign['currency']}:{campaign['price']}"
    return f"{period}:{campaign['percent']}"


def apply(config, at):
    campaign = active(at)
    if campaign is None:
        return config

    remaining = max(1, int((campaign['end'] - at) * 1000))
    if 'bookId' in campaign:
        book_id, currency, price = (campaign[field] for field in ('bookId', 'currency', 'price'))
        regular = catalog.current_fixed_prices().get(book_id, {}).get(currency)
        if regular is None or price >= regular:
            return config
        regular_amount = Decimal(str(regular))
        percent = (1 - Decimal(str(price)) / regular_amount) * 100
        return {**config, 'bookPromotion': {
            'bookId': book_id, 'currency': currency, 'price': price, 'regularPrice': regular,
            'percent': int(percent.quantize(Decimal('1'), rounding=ROUND_HALF_UP)),
            'expiresInMs': remaining,
        }}

    unit = Decimal('0.001' if config['currency'] == 'KWD' else '0.01')
    multiplier = Decimal(100 - campaign['percent']) / 100

    def discount(price):
        return max(unit, (Decimal(str(price)) * multiplier).quantize(unit, rounding=ROUND_DOWN))

    prices = config['prices']
    single = discount(prices['1'])
    # Keep any existing quantity total that is already cheaper than the sale copies.
    ladder = {count: float(min(Decimal(str(total)), single * int(count)))
              for count, total in prices.items()}
    policies = {}
    for book_id, policy in config['bookPolicies'].items():
        regular = policy['price']
        sale = float(discount(regular))
        policies[book_id] = {**policy, 'price': sale, 'regularPrice': regular}

    return {**config, 'prices': ladder, 'bookPolicies': policies, 'compareAtPrice': prices['1'],
            'bookPromotion': {'percent': campaign['percent'], 'expiresInMs': remaining}}
