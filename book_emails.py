"""One privately stamped PDF email per purchased book; no download claims."""
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
import secrets
import threading

import catalog
import checkout_fields
import media_sandbox
import order_receipts
import pdf_limits
import supabase_store as store

# Base64 + MIME overhead remain below Resend's 40 MB total message limit.
MAX_PDF_BYTES = 27 * 1024 * 1024
WAKE = threading.Event()


def enabled():
    return (order_receipts.enabled() and store.ready()
            and os.environ.get('TABO_BOOK_EMAILS_ENABLED') == '1'
            and os.environ.get('TABO_BOOK_EMAILS_SCHEMA_READY') == '1')


def eligible(order, book_id):
    if not isinstance(order, dict) or book_id not in catalog.BOOKS:
        raise ValueError('Ineligible book delivery')
    buyer = order.get('buyer') or {}
    if buyer.get('bookEmailRequested') is not True or not any(
            isinstance(item, dict) and item.get('id') == book_id for item in order.get('items', [])):
        raise ValueError('Ineligible book delivery')
    # Share the existing persisted PAID/live/validated-contact checks.
    order_receipts.message(order)
    clean, errors = checkout_fields.customer(buyer)
    if errors:
        raise ValueError('Ineligible buyer')
    return {'id': order['id'], 'buyer': {'name': clean['name'], 'phone': clean['phone']}}


def attachment(order, book_id):
    stamp_order = eligible(order, book_id)
    # Use the same bounded child and work slot as interactive downloads. Never
    # call claim_download, alter a session, or write a stamped file to public storage.
    with pdf_limits.work_slot():
        source = store.original(catalog.BOOKS[book_id][1])
        payload, result = media_sandbox.run('watermark', source,
            {'order': stamp_order, 'max_pages': pdf_limits.MAX_PAGES,
             'font': os.environ.get('TABO_ARABIC_FONT', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')},
            input_limit=pdf_limits.MAX_SOURCE_BYTES, output_limit=MAX_PDF_BYTES)
    if not payload.startswith(b'%PDF-') or not 0 < result.get('pages', 0) <= pdf_limits.MAX_PAGES:
        raise ValueError('Invalid stamped PDF')
    return payload


def message(order, book_id, pdf):
    eligible(order, book_id)
    if not isinstance(pdf, bytes) or not pdf.startswith(b'%PDF-') or len(pdf) > MAX_PDF_BYTES:
        raise ValueError('Invalid book attachment')
    payload = order_receipts.message(order, attachment_book=book_id)
    payload['attachments'] = [{'filename': 'TABO-' + book_id + '-' + order['id'] + '.pdf',
                               'content': base64.b64encode(pdf).decode('ascii')}]
    return payload


def process_one():
    if not enabled():
        return False
    lease = secrets.token_hex(32)
    claim = store.claim_book_email(lease)
    if not claim:
        return False
    oid, bid = claim['order_id'], claim['book_id']
    state, provider_id, reason = 'skipped', None, 'ineligible'
    try:
        created = datetime.fromisoformat(claim['created_at'].replace('Z', '+00:00'))
        if (datetime.now(timezone.utc) - created).total_seconds() >= order_receipts.MAX_AGE_SECONDS:
            reason = 'expired'
        else:
            order = store.find('id', oid)
            pdf = attachment(order, bid)
            # Re-read after potentially expensive stamping. Never send if payment,
            # recipient or stamping identity changed while preparing the file.
            current = store.find('id', oid)
            if eligible(current, bid) != eligible(order, bid) or current['buyer'] != order['buyer'] or current['items'] != order['items']:
                raise ValueError('Order changed during delivery')
            payload = message(current, bid, pdf)
            digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            # A stable hash prevents submitting different content under the same
            # provider idempotency key across retries, source changes or redeploys.
            prepared = store.prepare_book_email(oid, bid, lease, digest)
            if prepared == 'changed':
                state, reason = 'failed', 'payload_changed'
            elif prepared == 'ineligible':
                state, reason = 'skipped', 'ineligible'
            elif prepared == 'ready' and enabled():
                provider_id = order_receipts.send(payload, oid + '/' + bid, namespace='paid-book')
                state, reason = 'sent', None
            elif prepared == 'ready':
                state, reason = 'retry', 'transient'
            else:
                # Lease lost or payment revoked: do not send or clobber a new lease.
                return True
    except order_receipts.DeliveryError as exc:
        state = 'retry' if exc.retryable and claim['attempts'] < 8 else 'failed'
        reason = 'transient' if exc.retryable else 'provider_rejected'
    except (pdf_limits.PDFBusy, media_sandbox.MediaError, store.StoreError, OSError, TimeoutError):
        state, reason = ('retry' if claim['attempts'] < 8 else 'failed'), 'transient'
    except (ValueError, KeyError, TypeError, ArithmeticError):
        pass
    delay = min(3600, 30 * 2 ** min(claim['attempts'], 7))
    store.finish_book_email(oid, bid, lease, state, provider_id, delay, reason)
    return True


def start_worker():
    if not enabled():
        return

    def run():
        while True:
            try:
                # One attachment at a time; no unbounded in-memory book batch.
                process_one()
            except Exception:
                print('Book email worker: temporary failure', flush=True)
            WAKE.wait(20)
            WAKE.clear()

    threading.Thread(target=run, daemon=True, name='book-emails').start()
