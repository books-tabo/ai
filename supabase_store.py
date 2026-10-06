"""Private Supabase REST adapter. The secret key must never reach the browser."""
import json
import os
from urllib.error import HTTPError
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen

def ready():
    return bool(os.environ.get('SUPABASE_URL') and os.environ.get('SUPABASE_SECRET_KEY'))

def request(path, method='GET', payload=None, params=None, headers=None):
    base = os.environ['SUPABASE_URL'].rstrip('/')
    key = os.environ['SUPABASE_SECRET_KEY']
    url = base + path + (('?' + urlencode(params)) if params else '')
    body = json.dumps(payload).encode('utf-8') if payload is not None else None
    h = {'apikey': key, 'Content-Type': 'application/json'}
    if headers: h.update(headers)
    req = Request(url, data=body, headers=h, method=method)
    try:
        with urlopen(req, timeout=20) as response:
            content = response.read()
            return json.loads(content) if content else None
    except HTTPError as error:
        # Do not surface the response: it can contain identifiers or private data.
        raise RuntimeError('Supabase request failed: HTTP ' + str(error.code)) from error

def order(row):
    if not row: return None
    return {
        'id': row['id'], 'status': row['status'], 'items': row['items'],
        'total': row['total'], 'currency': row['currency'],
        'paymentMethod': row['payment_method'], 'statusKey': row['status_key'],
        'token': row['download_token'], 'buyer': row['buyer'],
        'transactionId': row['transaction_id'], 'paymobOrderId': row['paymob_order_id'],
        'country': row.get('country') or 'EG',
        'paymobMethodId': row.get('paymob_method_id'),
    }

def find(field, value):
    rows = request('/rest/v1/orders', params={field: 'eq.' + value, 'select': '*', 'limit': 1})
    return order(rows[0]) if rows else None

def insert(o):
    request('/rest/v1/orders', 'POST', {
        'id': o['id'], 'status': 'PENDING', 'items': o['items'], 'total': o['total'],
        'currency': o['currency'], 'payment_method': o['paymentMethod'],
        'status_key': o['statusKey'], 'country': o.get('country','EG'),
        'paymob_method_id': o.get('paymobMethodId')
    }, headers={'Prefer': 'return=minimal'})

def patch(oid, changes, conditions=None):
    params={'id': 'eq.' + oid}
    if conditions: params.update(conditions)
    rows=request('/rest/v1/orders', 'PATCH', changes, params,
                 headers={'Prefer': 'return=representation'})
    return order(rows[0]) if rows else None

def original(book_name):
    """Fetch one original from a private bucket using only the server secret."""
    base = os.environ['SUPABASE_URL'].rstrip('/')
    key = os.environ['SUPABASE_SECRET_KEY']
    req = Request(base + '/storage/v1/object/authenticated/tabo-originals/' + quote(book_name),
                  headers={'apikey': key, 'Authorization': 'Bearer ' + key})
    with urlopen(req, timeout=60) as response:
        return response.read()
