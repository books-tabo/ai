"""Snap Pixel configuration and authenticated, paid-order conversion data."""
import hashlib
import math
import os
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from catalog import BOOKS

PIXEL_ID = 'ec1f266b-eb95-49e9-b53d-e6d5b38e1888'
# Do not count purchases made before installing this integration.
PURCHASE_SINCE = datetime(2026, 10, 25, 22, 0, tzinfo=timezone.utc)


def pixel_id():
    value = os.environ.get('TABO_SNAP_PIXEL_ID', PIXEL_ID).strip()
    return value if re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', value) else ''


def purchase(order, current=None):
    """Call only after authenticating the existing active paid download session.

    The charge value and book IDs come from the persisted order. No customer,
    payment-provider identifier, download token or raw order identifier is sent.
    """
    if (not pixel_id() or order.get('status') != 'PAID' or
            order.get('paymentMode') != 'live' or not order.get('transactionId')):
        return None
    try:
        paid = datetime.fromisoformat(str(order.get('paidAt', '')).replace('Z', '+00:00'))
        current = current or datetime.now(timezone.utc)
        total = Decimal(str(order.get('total')))
        if (paid.tzinfo is None or paid < PURCHASE_SINCE or paid > current or
                current - paid > timedelta(hours=24) or not total.is_finite() or total <= 0 or
                not math.isfinite(float(total)) or order.get('currency') not in ('EGP', 'USD') or
                not re.fullmatch(r'TABO-[A-F0-9]{16}', order['id'])):
            return None
        ids = [item['id'] for item in order['items']]
        if not ids or any(item not in BOOKS for item in ids):
            return None
        transaction = hashlib.sha256(('tabo-snap-purchase-v1|' + pixel_id() + '|' +
                                      order['id']).encode()).hexdigest()
        return {'transaction_id': transaction, 'value': float(total),
                'currency': order['currency'], 'content_ids': ids}
    except (ValueError, TypeError, KeyError, InvalidOperation, OverflowError):
        return None
