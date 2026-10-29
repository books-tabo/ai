"""GA4 configuration and paid-only, privacy-minimized purchase parameters."""
import hashlib
import math
import os
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from catalog import BOOKS


def tag_id():
    value=os.environ.get('TABO_GA4_ID','').strip()
    return value if re.fullmatch(r'G-[A-Z0-9]{6,24}',value) else ''


def purchase(order,current=None):
    if (not tag_id() or order.get('status')!='PAID' or order.get('paymentMode')!='live'
            or not order.get('transactionId')):
        return None
    try:
        cutoff=datetime.fromisoformat(os.environ['TABO_GA4_PURCHASE_SINCE'].replace('Z','+00:00'))
        paid=datetime.fromisoformat(str(order.get('paidAt','')).replace('Z','+00:00'))
        current=current or datetime.now(timezone.utc)
        amount=Decimal(str(order['total']))
        ids=[i['id'] for i in order['items']]
        if (cutoff.tzinfo is None or paid.tzinfo is None or paid<cutoff or paid>current
            or current-paid>timedelta(hours=24) or not amount.is_finite() or amount<=0
            or not math.isfinite(float(amount)) or order.get('currency') not in ('EGP','USD')
            or not ids or len(ids)!=len(set(ids)) or any(i not in BOOKS for i in ids)
            or not re.fullmatch(r'TABO-[A-F0-9]{16}',order['id'])):
            return None
        return {'transaction_id':hashlib.sha256(('tabo-ga4-v1|'+tag_id()+'|'+order['id']).encode()).hexdigest(),
                'currency':order['currency'],'value':float(amount),
                'items':[{'item_id':i,'quantity':1} for i in ids]}
    except (KeyError,ValueError,TypeError,InvalidOperation,OverflowError):
        return None
