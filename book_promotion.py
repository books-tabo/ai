"""A scheduled discount on current single-book prices.

Set content/book-promotion.json to null or to an object with percent,
startsAt, and endsAt. Use ISO timestamps with explicit offsets.
"""
from datetime import datetime
from decimal import Decimal, ROUND_DOWN
import json
import os
from pathlib import Path


def load_campaign(path):
    data = json.loads(path.read_text(encoding='utf-8'))
    if data is None:
        return None
    if not isinstance(data, dict) or set(data) != {'percent', 'startsAt', 'endsAt'}:
        raise ValueError('A book promotion needs a percentage, start and end.')
    percent = data['percent']
    if type(percent) is not int or not 1 <= percent <= 99:
        raise ValueError('The book promotion percentage must be an integer from 1 to 99.')
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
    return {'percent': percent, 'start': start, 'end': end}


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
    return f"{base_revision}:promotion:{campaign['start']}:{campaign['end']}:{campaign['percent']}"


def apply(config, at):
    campaign = active(at)
    if campaign is None:
        return config

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
            'bookPromotion': {'percent': campaign['percent'],
                              'expiresInMs': max(1, int((campaign['end'] - at) * 1000))}}
