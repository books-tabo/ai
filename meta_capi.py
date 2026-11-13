"""Optional, backend-only Meta CAPI for one dataset. Never logs buyer fields/tokens."""
import hashlib
import ipaddress
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler

import catalog
import localization
import meta_tracking
import supabase_store as store

GRAPH_VERSION = 'v26.0'
PUBLIC_BASE = 'https://tabobooks.com'
WAKE = threading.Event()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward the access token to a redirect destination.


OPEN = build_opener(NoRedirect()).open


class DeliveryError(RuntimeError):
    def __init__(self, retryable):
        self.retryable = retryable
        super().__init__('Meta conversion delivery failed')


def dataset_id():
    value = os.environ.get('TABO_META_CAPI_PIXEL_ID', '').strip()
    return value if value and value in (meta_tracking.pixel_id(), meta_tracking.additional_pixel_id()) else ''


def enabled():
    token = os.environ.get('TABO_META_CAPI_ACCESS_TOKEN', '')
    return bool(os.environ.get('TABO_META_CAPI_ENABLED') == '1' and dataset_id()
                and os.environ.get('TABO_SITE_ENV', 'production') == 'production'
                and os.environ.get('TABO_PUBLIC_URL', '').rstrip('/') == PUBLIC_BASE
                and 32 <= len(token) <= 2048 and not any(c.isspace() for c in token))


def user_data(address, agent, fbp=None, fbc=None):
    """Use connection/click metadata only; no name, phone, email or form fields."""
    result = {}
    try:
        ip = ipaddress.ip_address(address)
        if ip.is_global:
            result['client_ip_address'] = ip.compressed
    except (ValueError, TypeError):
        pass
    if isinstance(agent, str) and 1 <= len(agent) <= 512 and not re.search(r'[\x00-\x1f\x7f]', agent):
        result['client_user_agent'] = agent
    for name, value in (('fbp', fbp), ('fbc', fbc)):
        if isinstance(value, str) and re.fullmatch(r'fb\.[0-9]{1,2}\.[0-9]{10,16}\.[A-Za-z0-9_.-]{1,300}', value):
            result[name] = value
    if not (result.get('fbp') or result.get('fbc') or
            (result.get('client_ip_address') and result.get('client_user_agent'))):
        return None
    return result


def safe_path(value):
    if value in ('/', '/checkout') or re.fullmatch(r'/books/book[1-9][0-9]*', str(value)):
        return value
    if re.fullmatch(r'/offers(?:/[a-z0-9-]{1,80})?', str(value)):
        return value
    raise ValueError('Invalid conversion page')


def event(name, identifier, custom=None, path='/', occurred=None):
    if not re.fullmatch(r'[0-9a-f]{64}', str(identifier)):
        raise ValueError('Invalid event identifier')
    result = {'event_name': name, 'event_id': identifier,
              'event_time': int(occurred if occurred is not None else time.time()),
              'action_source': 'website', 'event_source_url': PUBLIC_BASE + path}
    if custom:
        result['custom_data'] = custom
    return result


def outbox_row(payload, matching, order_id=None):
    key = hashlib.sha256(('meta-capi|' + dataset_id() + '|' + payload['event_name'] + '|' +
                          (order_id or payload['event_id'])).encode()).hexdigest()
    return {
        'event_key': key, 'pixel_id': dataset_id(), 'event_name': payload['event_name'],
        'order_id': order_id, 'payload': payload, 'user_data': matching,
    }


def enqueue(payload, matching, *, order_id=None):
    if not enabled() or not matching or not store.ready():
        return False
    store.request('/rest/v1/meta_conversion_outbox', 'POST', outbox_row(payload, matching, order_id),
                  headers={'Prefer': 'resolution=ignore-duplicates,return=minimal'})
    WAKE.set()
    return True


def browser_event(data, matching, country):
    """Prices come from the catalogue. Browsers cannot submit Purchase or checkout."""
    name = data.get('event_name')
    if name not in ('PageView', 'ViewContent', 'AddToCart'):
        raise ValueError('Invalid conversion event')
    path = safe_path(data.get('path'))
    custom = None
    if name != 'PageView':
        ids = data.get('content_ids')
        if (not isinstance(ids, list) or not 1 <= len(ids) <= len(catalog.BOOKS) or
                any(not isinstance(i, str) or i not in catalog.BOOKS for i in ids) or len(set(ids)) != len(ids)):
            raise ValueError('Invalid conversion products')
        config = localization.pricing_for_country(catalog.PRICES, country)
        value = catalog.total_for_items(ids, config)
        # A bundle delta is not equivalent to pricing the added products alone.
        # Send monetary parameters only when they match the authoritative catalogue.
        if data.get('currency') == config['currency'] and data.get('value') == value:
            custom = {'content_ids': ids, 'content_type': 'product', 'num_items': len(ids),
                      'value': value, 'currency': config['currency']}
        else:
            custom = {'content_ids': ids, 'content_type': 'product', 'num_items': len(ids)}
    return enqueue(event(name, data.get('event_id'), custom, path), matching)


def checkout_event_id(order):
    if not enabled() or order.get('paymentMode') != 'live':
        return None
    return hashlib.sha256(('tabo-meta-checkout-v1|' + order['id']).encode()).hexdigest()


def checkout(order, matching):
    """Enroll before redirect. The worker waits for durable PAID for Purchase."""
    if not enabled() or order.get('paymentMode') != 'live' or not matching:
        return None
    identifier = checkout_event_id(order)
    custom = {'content_ids': [item['id'] for item in order['items']], 'content_type': 'product',
              'num_items': len(order['items']), 'value': float(order['total']), 'currency': order['currency']}
    # This placeholder never goes to Meta; its confirmed event ID/time/value come
    # from meta_tracking.purchase(), exactly as for the authenticated browser event.
    rows = [outbox_row(event('InitiateCheckout', identifier, custom, '/checkout'), matching),
            outbox_row(event('Purchase', identifier), matching, order['id'])]
    # One database transaction prevents a partial checkout/purchase enrollment.
    store.request('/rest/v1/meta_conversion_outbox', 'POST', rows,
                  headers={'Prefer': 'resolution=ignore-duplicates,return=minimal'})
    WAKE.set()
    return identifier


def purchase_event(order):
    value = meta_tracking.purchase(order)
    if not value or dataset_id() not in [meta_tracking.pixel_id(), *value.get('additional_pixel_ids', [])]:
        return None
    paid = datetime.fromisoformat(order['paidAt'].replace('Z', '+00:00'))
    custom = {key: value[key] for key in ('value', 'currency', 'content_ids', 'content_type', 'num_items')}
    return event('Purchase', value['eventID'], custom, '/success.html', paid.timestamp())


def send(payload, matching, *, test_event_code=None):
    if not enabled():
        raise DeliveryError(False)
    body = {'data': [{**payload, 'user_data': matching}],
            'access_token': os.environ['TABO_META_CAPI_ACCESS_TOKEN']}
    if test_event_code is not None:
        if not re.fullmatch(r'TEST[0-9A-Z]{3,40}', test_event_code):
            raise ValueError('Invalid test event code')
        body['test_event_code'] = test_event_code
    req = Request('https://graph.facebook.com/' + GRAPH_VERSION + '/' + dataset_id() + '/events',
                  data=json.dumps(body).encode(), method='POST',
                  headers={'Content-Type': 'application/json', 'User-Agent': 'TABO-Books-CAPI/1.0'})
    try:
        with OPEN(req, timeout=8) as response:
            raw = response.read(16385)
            if len(raw) > 16384:
                raise DeliveryError(True)
            result = json.loads(raw)
        if not isinstance(result, dict) or result.get('events_received') != 1:
            raise DeliveryError(False)
        # Keep only an opaque provider trace, never request/response bodies.
        return str(result.get('fbtrace_id', ''))[:100]
    except HTTPError as exc:
        retryable = exc.code == 429 or exc.code >= 500
        try:
            error = json.loads(exc.read(16384)).get('error', {})
            retryable = retryable or error.get('is_transient') is True or error.get('code') in (1, 2, 4, 17, 32, 341, 613)
        except (ValueError, OSError, TypeError):
            pass
        raise DeliveryError(retryable) from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise DeliveryError(True) from None


def process_one():
    if not enabled() or not store.ready():
        return False
    lease = secrets.token_hex(32)
    claim = store.request('/rest/v1/rpc/claim_meta_conversion', 'POST',
                          {'p_pixel_id': dataset_id(), 'p_lease': lease})
    if not claim:
        return False
    state, reason, trace = 'skipped', 'ineligible', None
    try:
        payload = claim['payload']
        if claim['event_name'] == 'Purchase':
            payload = purchase_event(store.find('id', claim['order_id']) or {})
        if payload:
            trace = send(payload, claim['user_data'])
            state, reason = 'sent', None
    except DeliveryError as exc:
        state = 'retry' if exc.retryable and claim['attempts'] < 8 else 'failed'
        reason = 'transient' if exc.retryable else 'provider_rejected'
    except store.StoreError:
        state, reason = 'retry', 'transient'
    except (KeyError, ValueError, TypeError, ArithmeticError):
        pass
    store.request('/rest/v1/rpc/finish_meta_conversion', 'POST', {
        'p_event_key': claim['event_key'], 'p_lease': lease, 'p_state': state,
        'p_trace': trace, 'p_retry_seconds': min(3600, 30 * 2 ** min(claim['attempts'], 7)), 'p_reason': reason})
    return True


def start_worker():
    if not enabled():
        return

    def run():
        while True:
            try:
                for _ in range(20):
                    if not process_one():
                        break
            except Exception:
                print('Meta conversion worker: temporary failure', flush=True)
            WAKE.wait(10)
            WAKE.clear()

    threading.Thread(target=run, daemon=True, name='meta-conversions').start()
