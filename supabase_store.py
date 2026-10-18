"""Private Supabase REST adapter. The secret key must never reach the browser."""
import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen

JSON_RESPONSE_LIMIT = 2 * 1024 * 1024
from pdf_limits import MAX_SOURCE_BYTES
ORIGINAL_PDF_LIMIT = MAX_SOURCE_BYTES

class StoreError(RuntimeError):
    """Sanitized classification only; never retain a database response body."""
    def __init__(self, status=None, code=None):
        self.status = status
        self.code = code if isinstance(code,str) and code.isalnum() and len(code)<=8 else None
        super().__init__('Private storage request failed')

class StoreUnavailable(StoreError):
    pass

class UniqueConflict(StoreError):
    pass

def auth_headers(key):
    headers={'apikey':key}
    # Opaque sb_secret keys are not JWTs. Let the API gateway map apikey to service_role.
    if key.startswith('eyJ'):
        headers['Authorization']='Bearer '+key
    return headers

def ready():
    return bool(os.environ.get('SUPABASE_URL') and os.environ.get('SUPABASE_SECRET_KEY'))

def request(path, method='GET', payload=None, params=None, headers=None):
    base = os.environ['SUPABASE_URL'].rstrip('/')
    key = os.environ['SUPABASE_SECRET_KEY']
    url = base + path + (('?' + urlencode(params)) if params else '')
    body = json.dumps(payload).encode('utf-8') if payload is not None else None
    h = {**auth_headers(key), 'Content-Type': 'application/json'}
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
        code=None
        try:
            detail=json.loads(error.read(8192))
            if isinstance(detail,dict): code=detail.get('code')
        except (ValueError,OSError):
            pass
        # A 409 is not sufficient: only SQLSTATE 23505 proves a unique conflict.
        kind=UniqueConflict if error.code==409 and code=='23505' else StoreUnavailable if error.code>=500 or error.code==429 else StoreError
        raise kind(error.code,code) from None
    except (URLError,TimeoutError,ConnectionError,OSError):
        raise StoreUnavailable() from None
    except (ValueError,TypeError):
        raise StoreUnavailable() from None

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
        'checkoutSessionHash': row.get('checkout_session_hash'),
        'downloadSessionHash': row.get('download_session_hash'),
        'downloadDeviceHash': row.get('download_device_hash'),
        'downloadSessionExpiresAt': row.get('download_session_expires_at'),
        'downloadTokenExpiresAt': row.get('download_token_expires_at'),
        'downloadTokenUsedAt': row.get('download_token_used_at'),
        'paymentMode':row.get('payment_mode','legacy'),
    }

def find(field, value):
    rows = request('/rest/v1/orders', params={field: 'eq.' + value, 'select': '*', 'limit': 1})
    return order(rows[0]) if rows else None

def insert(o):
    request('/rest/v1/orders', 'POST', {
        'id': o['id'], 'status': 'PENDING', 'items': o['items'], 'total': o['total'],
        'currency': o['currency'], 'payment_method': o['paymentMethod'],
        'status_key': o['statusKey'], 'country': o.get('country','EG'),
        'paymob_method_id': o.get('paymobMethodId'), 'buyer': o.get('buyer'),
        'checkout_session_hash': o.get('checkoutSessionHash')
        ,'payment_mode':o.get('paymentMode','test')
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
                  headers=auth_headers(key))
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

def apply_payment_event(order_id,provider,mode,transaction,kind,amount,currency,provider_order_id,digest):
    result=request('/rest/v1/rpc/apply_payment_event','POST',{
        'p_order_id':order_id,'p_provider':provider,'p_mode':mode,
        'p_transaction_id':transaction,'p_kind':kind,'p_amount':str(amount),
        'p_currency':currency,'p_provider_order_id':provider_order_id,'p_digest':digest})
    if not isinstance(result,dict): raise StoreUnavailable()
    return result


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

def download_usage(order_id):
    rows=request('/rest/v1/order_downloads',params={
        'order_id':'eq.'+order_id,'select':'book_id,download_count'
    })
    return {row['book_id']:int(row.get('download_count') or 0) for row in rows or []}

def claim_download(order_id, book_id, session_hash, device_hash, page_count):
    result=request('/rest/v1/rpc/claim_order_download','POST',{
        'p_order_id':order_id,'p_book_id':book_id,
        'p_session_hash':session_hash,'p_device_hash':device_hash,
        'p_page_count':int(page_count)
    })
    if not isinstance(result,dict): raise RuntimeError('Invalid download claim response')
    return result

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
