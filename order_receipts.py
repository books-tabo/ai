"""Paid-only transactional receipts. No PDF links or download authority in email."""
import html
import json
import os
import re
import secrets
import threading
from datetime import datetime, timezone
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

import checkout_fields
import supabase_store as store

ORIGIN = 'https://tabobooks.com'
SEND_URL = 'https://api.resend.com/emails'
MAX_AGE_SECONDS = 20 * 3600  # safely inside Resend's documented 24-hour deduplication window
WAKE = threading.Event()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPEN = build_opener(NoRedirect()).open


def enabled():
    sender = os.environ.get('TABO_RECEIPT_FROM', '')
    return (os.environ.get('TABO_SITE_ENV', 'production') == 'production'
            and os.environ.get('TABO_RECEIPTS_ENABLED') == '1'
            and os.environ.get('TABO_RECEIPTS_SCHEMA_READY') == '1'
            and os.environ.get('TABO_RECEIPT_SENDER_VERIFIED') == '1'
            and bool(re.fullmatch(r'[A-Za-z0-9._+\-]+@(?:[a-z0-9\-]+\.)*tabobooks\.com', sender))
            and os.environ.get('RESEND_API_KEY', '').startswith('re_')
            and len(os.environ.get('RESEND_API_KEY', '')) > 20)


class DeliveryError(Exception):
    def __init__(self, retryable, status=None, reason='transport'):
        self.retryable = retryable
        self.status = status if isinstance(status, int) and 100 <= status <= 599 else None
        self.reason = reason if reason in {'transport', 'api_key', 'sender_unverified', 'test_recipient', 'quota', 'rate_limit', 'provider_rejected'} else 'provider_rejected'
        super().__init__('Receipt delivery unavailable')


def provider_failure(error):
    """Reduce provider errors to fixed categories; never retain response text."""
    reason = 'provider_rejected'
    try:
        raw = error.read(8193)
        detail = json.loads(raw) if len(raw) <= 8192 else {}
        message = str(detail.get('message', '')).lower() if isinstance(detail, dict) else ''
        name = detail.get('name', '') if isinstance(detail, dict) else ''
        if 'api key' in message or name in ('missing_api_key', 'restricted_api_key', 'suspended_api_key'):
            reason = 'api_key'
        elif 'domain is not verified' in message or 'domain is not verified' in message.replace('`', ''):
            reason = 'sender_unverified'
        elif 'only send testing emails' in message:
            reason = 'test_recipient'
        elif name in ('daily_quota_exceeded', 'monthly_quota_exceeded'):
            reason = 'quota'
        elif name == 'rate_limit_exceeded':
            reason = 'rate_limit'
    except (ValueError, OSError, TypeError):
        pass
    return DeliveryError(error.code in (408, 429) or error.code >= 500, error.code, reason)


def message(order):
    """Only persisted, server-confirmed values; never caller-supplied prices/links."""
    buyer = order.get('buyer') or {}
    clean, errors = checkout_fields.customer(buyer)
    if (errors or order.get('status') != 'PAID' or order.get('paymentMode') != 'live'
            or buyer.get('receiptRequested') is not True
            or not re.fullmatch(r'TABO-[A-F0-9]{16}', order.get('id', ''))
            or not re.fullmatch(r'[0-9a-f]{64}', order.get('statusKey', ''))):
        raise ValueError('Ineligible receipt')
    en = buyer.get('language') == 'en'
    oid = order['id']
    resume = ORIGIN + '/pending.html?key=' + order['statusKey']
    support = ORIGIN + '/order-help'
    amount = f"{Decimal(str(order['total'])):.2f} {order['currency']}"
    titles = [str(item['name']) for item in order['items']]
    subject = ('Payment confirmed — ' if en else 'تم تأكيد شراء كتبك — ') + oid
    summary = ('Amount charged: ' if en else 'المبلغ المحصّل: ') + amount
    quote = order.get('pricingQuote')
    if quote:
        summary += ('\nDisplayed price: ' if en else '\nالسعر المعروض: ') + f"{Decimal(str(quote['displayTotal'])):.2f} {quote['displayCurrency']}"
    instruction = ('Open the order link in the same browser and device used for your purchase. '
                   'Your existing download protection and two-download limit per book still apply.' if en else
                   'افتح رابط طلبك من نفس المتصفح والجهاز المستخدمين للشراء. تظل حماية طلبك ومحاولتا تحميل كل كتاب كما هما.')
    action = 'Return to your books' if en else 'الرجوع لطلبك وتحميل كتبك'
    help_label = 'Help with my order' if en else 'مساعدة بخصوص طلبي'
    greeting = ('Thank you, ' if en else 'شكرًا لك، ') + clean['name']
    text = '\n\n'.join([greeting, subject, '\n'.join(titles), summary,
                          action + ': ' + resume, instruction,
                          help_label + ': ' + support, ('Order number: ' if en else 'رقم الطلب: ') + oid])
    escape = html.escape
    body = f'''<!doctype html><html lang="{'en' if en else 'ar'}" dir="{'ltr' if en else 'rtl'}"><body style="margin:0;background:#f6f5f2;font-family:Tahoma,Arial,sans-serif;color:#171717"><div style="max-width:580px;margin:24px auto;background:#fff;padding:28px;border-radius:14px;line-height:1.9"><p style="letter-spacing:3px;font-weight:bold">TABO BOOKS</p><h1 style="font-size:23px">{escape(subject)}</h1><p>{escape(greeting)}</p><ul>{''.join('<li>'+escape(t)+'</li>' for t in titles)}</ul><p style="white-space:pre-line">{escape(summary)}</p><p><a href="{escape(resume, quote=True)}" style="display:inline-block;background:#15803d;color:#fff;padding:12px 20px;border-radius:8px;text-decoration:none">{action}</a></p><p>{instruction}</p><p><a href="{support}" style="color:#775c17">{help_label}</a></p><p style="font-size:12px;color:#666">{oid}</p></div></body></html>'''
    return {'from': 'TABO BOOKS <' + os.environ.get('TABO_RECEIPT_FROM', '') + '>',
            'to': [clean['email']], 'reply_to': 'taponoor@gmail.com',
            'subject': subject, 'html': body, 'text': text}


def send(payload, order_id):
    req = Request(SEND_URL, method='POST', data=json.dumps(payload).encode(), headers={
        'Authorization': 'Bearer ' + os.environ['RESEND_API_KEY'],
        'User-Agent': 'TABO-Books/1.0 (+https://tabobooks.com)',
        'Content-Type': 'application/json', 'Idempotency-Key': 'paid-receipt/' + order_id})
    try:
        with OPEN(req, timeout=10) as response:
            raw = response.read(8193)
            if len(raw) > 8192:
                raise DeliveryError(True)
            data = json.loads(raw)
            if response.status not in (200, 201) or not isinstance(data.get('id'), str):
                raise DeliveryError(True)
            return data['id'][:100]
    except HTTPError as exc:
        raise provider_failure(exc) from None
    except (URLError, OSError, TimeoutError, ValueError):
        raise DeliveryError(True) from None


def process_one():
    if not enabled() or not store.ready():
        return False
    lease = secrets.token_hex(32)
    claim = store.claim_receipt(lease)
    if not claim:
        return False
    oid = claim['order_id']
    state, provider_id, reason = 'skipped', None, 'ineligible'
    try:
        created = datetime.fromisoformat(claim['created_at'].replace('Z', '+00:00'))
        if (datetime.now(timezone.utc) - created).total_seconds() >= MAX_AGE_SECONDS:
            reason = 'expired'
        else:
            order = store.find('id', oid)
            payload = message(order or {})  # re-check PAID immediately before provider request
            if not enabled():
                raise DeliveryError(True)
            provider_id = send(payload, oid)
            state, reason = 'sent', None
    except DeliveryError as exc:
        state = 'retry' if exc.retryable and claim['attempts'] < 8 else 'failed'
        reason = 'transient' if exc.retryable else 'provider_rejected'
    except (ValueError, KeyError, TypeError, ArithmeticError):
        pass
    except store.StoreError:
        state, reason = 'retry', 'transient'
    delay = min(3600, 30 * 2 ** min(claim['attempts'], 7))
    store.finish_receipt(oid, lease, state, provider_id, delay, reason)
    return True


def start_worker():
    """One bounded worker; database leases also protect overlapping instances."""
    if not enabled():
        return

    def run():
        while True:
            try:
                for _ in range(5):
                    if not process_one():
                        break
            except Exception:
                print('Receipt worker: temporary failure', flush=True)
            WAKE.wait(20)
            WAKE.clear()

    threading.Thread(target=run, daemon=True, name='order-receipts').start()
