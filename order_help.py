"""Email-verified order assistance. Never grants or resets download authority."""
import hashlib
import hmac
import html
import os
import re
import secrets
import threading
from datetime import datetime, timezone

import order_receipts
import supabase_store as store

COOKIE = '__Host-tabo_order_help'
ID = re.compile(r'[0-9a-f]{64}')
ORDER = re.compile(r'TABO-[A-F0-9]{16}')
EMAIL = re.compile(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+')
WAKE = threading.Event()


def enabled():
    return (order_receipts.enabled() and store.ready()
            and os.environ.get('TABO_ORDER_HELP_ENABLED') == '1'
            and os.environ.get('TABO_ORDER_HELP_SCHEMA_READY') == '1'
            and len(os.environ.get('TABO_SESSION_BINDING_KEY', '')) >= 32)


def fingerprint(kind, value):
    return hmac.new(os.environ['TABO_SESSION_BINDING_KEY'].encode(),
                    ('order-help-v1|' + kind + '|' + str(value)).encode(), hashlib.sha256).hexdigest()


def email(value):
    value = value.strip().casefold() if isinstance(value, str) else ''
    return value if len(value) <= 254 and EMAIL.fullmatch(value) else ''


def code(challenge):
    return str(int(fingerprint('otp', challenge), 16) % 1_000_000).zfill(6)


def request(data, client, device):
    """Always return an indistinguishable handle, including for unknown orders."""
    challenge = secrets.token_hex(32)
    oid = str(data.get('order_id', '')).strip().upper()
    address = email(data.get('email'))
    if not ORDER.fullmatch(oid) or not address or not device:
        return challenge
    store.help_request(challenge, oid, address, fingerprint('email', address), fingerprint('client', client),
                       device, fingerprint('verify', challenge + '|' + code(challenge)),
                       'en' if data.get('language') == 'en' else 'ar')
    WAKE.set()
    return challenge


def verify(data, device):
    challenge, entered = data.get('challenge'), data.get('code')
    if not isinstance(challenge, str) or not ID.fullmatch(challenge) or not device:
        return None
    if isinstance(entered, str):
        entered = entered.translate(str.maketrans('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹', '01234567890123456789'))
    if not isinstance(entered, str) or not re.fullmatch(r'[0-9]{6}', entered):
        entered = 'invalid'
    token = secrets.token_hex(32)
    ok = store.help_verify(challenge, fingerprint('verify', challenge + '|' + entered),
                           fingerprint('session', token), device)
    return challenge + '.' + token if ok is True else None


def identity(cookie, device):
    if not isinstance(cookie, str) or not device:
        return None
    parts = cookie.split('.')
    if len(parts) != 2 or not all(ID.fullmatch(p) for p in parts):
        return None
    return parts[0], fingerprint('session', parts[1]), device


def status(cookie, device):
    credentials = identity(cookie, device)
    if not credentials:
        return None
    oid = store.help_order(*credentials)
    return store.find('id', oid) if isinstance(oid, str) and ORDER.fullmatch(oid) else None


def resend(cookie, device):
    credentials = identity(cookie, device)
    if not credentials:
        return None
    result = store.help_resend(*credentials)
    WAKE.set()
    return result


def otp_message(order, claim):
    en = claim['language'] == 'en'
    number = code(claim['challenge_id'])
    subject = 'Your TABO BOOKS verification code' if en else 'رمز التحقق من طلبك في TABO BOOKS'
    text = ('Your verification code is ' + number + '. It expires in 10 minutes. '
            'This code does not confirm a payment. Ignore this email if you did not request it.' if en else
            'رمز التحقق: ' + number + '. صلاحيته 10 دقائق. هذا الرمز لا يعني تأكيد الدفع. '
            'إذا لم تطلب متابعة طلبك فتجاهل الرسالة.')
    return {'from': 'TABO BOOKS <' + os.environ['TABO_RECEIPT_FROM'] + '>',
            'to': [order['buyer']['email']], 'reply_to': 'taponoor@gmail.com', 'subject': subject,
            'text': text, 'html': '<!doctype html><html><body dir="' + ('ltr' if en else 'rtl') +
            '"><p>TABO BOOKS</p><p>' + html.escape(text) + '</p></body></html>'}


def process_one():
    if not enabled():
        return False
    lease = secrets.token_hex(32)
    claim = store.help_claim(lease)
    if not claim:
        return False
    state, provider, reason = 'skipped', None, 'ineligible'
    try:
        order = store.find('id', claim['order_id'])
        address = email(((order or {}).get('buyer') or {}).get('email'))
        if not address or not hmac.compare_digest(fingerprint('email', address), claim['email_hash']):
            raise ValueError('Ineligible help message')
        created = datetime.fromisoformat(claim['created_at'].replace('Z', '+00:00'))
        age = (datetime.now(timezone.utc) - created).total_seconds()
        if age >= (600 if claim['kind'] == 'otp' else order_receipts.MAX_AGE_SECONDS):
            reason = 'expired'
        else:
            if claim['kind'] == 'otp':
                payload = otp_message(order, claim)
            else:
                # Explicit, verified transactional request; do not mutate the order/buyer.
                payload = order_receipts.message({**order, 'buyer': {**order['buyer'], 'receiptRequested': True}})
            if not enabled():
                raise order_receipts.DeliveryError(True)
            provider = order_receipts.send(payload, 'help-' + claim['kind'] + '-' + claim['challenge_id'])
            state, reason = 'sent', None
    except order_receipts.DeliveryError as exc:
        state = 'retry' if exc.retryable and claim['attempts'] < 5 else 'failed'
        reason = 'transient' if exc.retryable else 'provider_rejected'
    except (ValueError, KeyError, TypeError, ArithmeticError):
        pass
    except store.StoreError:
        state, reason = 'retry', 'transient'
    store.help_finish(claim['challenge_id'], claim['kind'], lease, state, provider,
                      min(3600, 30 * 2 ** min(claim['attempts'], 7)), reason)
    return True


def start_worker():
    if not enabled():
        return

    def run():
        while True:
            try:
                for _ in range(5):
                    if not process_one():
                        break
            except Exception:
                print('Order help worker: temporary failure', flush=True)
            WAKE.wait(20)
            WAKE.clear()
    threading.Thread(target=run, daemon=True, name='order-help').start()
