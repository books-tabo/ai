"""Google Ads configuration and authenticated, paid-order conversion data."""
import hashlib
import math
import os
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from catalog import BOOKS

TAG_ID = 'AW-18456253761'
CONVERSION_LABEL = 'Z30jCMCguqYdEMGq0OBE'
# Installation cutoff: do not backfill earlier sales when a receipt is reopened.
PURCHASE_SINCE = datetime(2026, 10, 24, 14, 33, tzinfo=timezone.utc)


def tag_id():
    value = os.environ.get('TABO_GOOGLE_ADS_ID', TAG_ID).strip()
    return value if re.fullmatch(r'AW-[0-9]{5,25}', value) else ''


def purchase(order, current=None):
    """Caller must authenticate the active paid download session first.

    Use the stored charge total/currency, never a client price or a redirect flag.
    The stable opaque transaction ID contains no buyer or payment-provider data.
    """
    if (not tag_id() or order.get('status') != 'PAID' or
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
        transaction = hashlib.sha256(('tabo-google-ads-purchase-v1|' + tag_id() + '|' +
                                      CONVERSION_LABEL + '|' + order['id']).encode()).hexdigest()
        return {'transaction_id': transaction, 'value': float(total), 'currency': order['currency']}
    except (ValueError, TypeError, KeyError, InvalidOperation, OverflowError):
        return None
