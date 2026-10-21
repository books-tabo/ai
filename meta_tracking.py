"""Public Meta configuration and minimal, authenticated conversion payloads."""
import hashlib
from catalog import BOOKS
import os
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

PIXEL_ID = '1371060818078535'
# Do not backfill old purchases when installing the pixel.
PURCHASE_SINCE = datetime(2026, 10, 19, 17, 23, 29, tzinfo=timezone.utc)


def pixel_id():
    value = os.environ.get('TABO_META_PIXEL_ID', PIXEL_ID).strip()
    return value if re.fullmatch(r'[0-9]{5,25}', value) else ''


def purchase(order, current=None):
    """Caller must first authenticate the buyer's active download session.

    Never expose customer information, provider IDs, order links or credentials.
    Browser delivery is best effort; this is not a Conversions API integration.
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
                order.get('currency') not in ('EGP', 'USD')):
            return None
        ids = [item['id'] for item in order['items']]
        if not ids or any(item not in BOOKS for item in ids):
            return None
        digest = hashlib.sha256(('tabo-meta-purchase-v1|' + pixel_id() + '|' +
                                 order['id'] + '|' + str(order['transactionId'])).encode()).hexdigest()
        return {'eventID': digest, 'value': float(total), 'currency': order['currency'],
                'content_ids': ids, 'content_type': 'product', 'num_items': len(ids)}
    except (ValueError, TypeError, KeyError, InvalidOperation):
        return None
