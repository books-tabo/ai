"""Private Supabase REST adapter. The secret key must never reach the browser."""
import json
import os
from urllib.error import HTTPError
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen

JSON_RESPONSE_LIMIT = 2 * 1024 * 1024
ORIGINAL_PDF_LIMIT = 100 * 1024 * 1024

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
            content = response.read(JSON_RESPONSE_LIMIT + 1)
            if len(content) > JSON_RESPONSE_LIMIT:
                raise RuntimeError('Supabase response exceeded the safe size limit')
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
        length=response.headers.get('Content-Length')
        if length and int(length)>ORIGINAL_PDF_LIMIT:
            raise RuntimeError('Original PDF exceeded the safe size limit')
        payload=response.read(ORIGINAL_PDF_LIMIT + 1)
    if len(payload)>ORIGINAL_PDF_LIMIT:
        raise RuntimeError('Original PDF exceeded the safe size limit')
    if not payload.startswith(b'%PDF-'):
        raise RuntimeError('Original file is not a PDF')
    return payload

def upload_original(book_name, payload):
    """Upload one PDF to the private originals bucket using only the server secret."""
    if not isinstance(payload,bytes) or not payload.startswith(b'%PDF-') or len(payload)>ORIGINAL_PDF_LIMIT:
        raise RuntimeError('Invalid original PDF')
    base=os.environ['SUPABASE_URL'].rstrip('/')
    key=os.environ['SUPABASE_SECRET_KEY']
    req=Request(base+'/storage/v1/object/tabo-originals/'+quote(book_name),data=payload,method='POST',
                headers={'apikey':key,'Authorization':'Bearer '+key,'Content-Type':'application/pdf','x-upsert':'false'})
    try:
        with urlopen(req,timeout=90) as response:
            response.read(JSON_RESPONSE_LIMIT+1)
    except HTTPError as error:
        raise RuntimeError('Supabase upload failed: HTTP '+str(error.code)) from error

def book_stats():
    """Return only public-safe aggregate book metadata."""
    rows=request('/rest/v1/book_stats', params={
        'select':'book_id,rating_sum,review_count,download_count,page_count,file_size_bytes',
        'order':'book_id.asc'
    })
    result={}
    for row in rows or []:
        reviews=int(row.get('review_count') or 0)
        rating=(float(row.get('rating_sum') or 0)/reviews) if reviews else None
        result[row['book_id']]={
            'rating':round(rating,1) if rating is not None else None,
            'reviews':reviews,
            'downloads':int(row.get('download_count') or 0),
            'pageCount':row.get('page_count'),
            'fileSizeBytes':int(row.get('file_size_bytes') or 0)
        }
    return result

def record_download(book_id, page_count):
    request('/rest/v1/rpc/increment_book_download', 'POST', {
        'p_book_id':book_id,
        'p_page_count':int(page_count)
    })

def submit_rating(book_id, voter_hash, rating):
    """Create or update one browser's rating and return the fresh aggregate."""
    result=request('/rest/v1/rpc/submit_book_rating', 'POST', {
        'p_book_id':book_id,
        'p_voter_hash':voter_hash,
        'p_rating':int(rating)
    })
    if not isinstance(result,dict): raise RuntimeError('Invalid rating response')
    return {
        'rating':float(result['rating']),
        'reviews':int(result['reviews'])
    }
