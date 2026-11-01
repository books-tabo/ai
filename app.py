"""TABO BOOKS backend. Python 3.11+, pypdf, reportlab, Pillow."""
import json, os, re, secrets, hmac, hashlib, ipaddress, threading, time, unicodedata
import media_sandbox
import reader_comments
import paypal_checkout
import checkout_fields
import order_receipts
import book_emails
import order_help
import owner_dashboard
import store_catalog
import checkout_recovery
import store_tools
from collections import deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote, unquote, urlencode
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import supabase_store as store
import localization
import proxy_identity
import book_previews
import pdf_limits
import meta_tracking
import google_ads_tracking
import journey_tracking
import snap_tracking
import checkout_pricing
import catalog
import seo
import autoseo
from catalog import BOOKS, PRICES

ROOT = Path(__file__).resolve().parent
PAYMOB_BASE = 'https://accept.paymob.com'
KASHIER_API_BASES = {
    'test': 'https://test-api.kashier.io',
    'live': 'https://api.kashier.io',
}
FONT = os.environ.get('TABO_ARABIC_FONT', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
DOWNLOAD_LIMIT = 2
DOWNLOAD_SESSION_DAYS = 30


class SlidingWindowLimiter:
    """Small in-process abuse guard; Render/Cloudflare remain the DDoS boundary."""
    def __init__(self):
        self._events = {}
        self._lock = threading.Lock()

    def allow(self, key, limit, window_seconds):
        current = time.monotonic()
        cutoff = current - window_seconds
        with self._lock:
            if key not in self._events and len(self._events)>=10000:
                return False,60
            events = self._events.setdefault(key, deque())
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                retry_after = max(1, int(window_seconds - (current - events[0])) + 1)
                return False, retry_after
            events.append(current)
            if len(self._events) > 5000:
                for stale_key in list(self._events)[:1000]:
                    stale = self._events[stale_key]
                    while stale and stale[0] <= cutoff:
                        stale.popleft()
                    if not stale:
                        self._events.pop(stale_key, None)
            return True, 0


RATE_LIMITER = SlidingWindowLimiter()
POST_LIMITS = {
    '/api/admin-dashboard/catalog/draft': (20, 600),
    '/api/admin-dashboard/catalog/publish': (10, 600),
    '/api/admin-dashboard/catalog/cover': (5, 600),
    '/api/admin-dashboard/recovery': (10, 600),
    '/api/checkout-reminders/unsubscribe': (10, 600),
    '/api/admin-dashboard/request': (5, 600),
    '/api/admin-dashboard/verify': (15, 600),
    '/api/admin-dashboard/logout': (20, 600),
    '/api/store-insights/session': (120, 600),
    '/api/store-insights/event': (180, 600),
    '/api/order-help/request': (5, 600),
    '/api/order-help/verify': (15, 600),
    '/api/order-help/resend': (5, 600),
    '/api/autoseo/webhook': (60, 60),
    '/api/paypal/capture': (12, 600),
    '/api/paypal/webhook': (180, 60),
    '/api/ratings': (12, 600),
    '/api/book-comments': (5, 600),
    '/api/orders': (6, 600),
    '/api/checkout-quote': (120, 600),
    '/api/download-session': (12, 600),
    # Admission guard for 20 full-catalog bundles behind one NAT per minute.
    # Per-session work limits and durable per-book entitlements are separate below.
    '/api/download': (len(BOOKS)*DOWNLOAD_LIMIT*20, 60),
    '/api/admin/confirm-payment': (10, 600),
    '/api/paymob/webhook': (180, 60),
    '/api/kashier/webhook': (180, 60),
}
GET_LIMITS = {
    '/api/admin-dashboard/catalog': (30, 600),
    '/api/admin-dashboard/recovery': (30, 600),
    '/api/admin-dashboard/session': (30, 600),
    '/api/admin-dashboard/report': (30, 600),
    '/api/order-help/status': (60, 600),
    '/api/book-comments': (120, 600),
    '/previews/view': (30, 600),
    '/previews/read': (180, 600),
    '/api/order-status': (120, 600),
    '/api/downloads': (len(BOOKS)*DOWNLOAD_LIMIT*20, 60),
}


def valid_https_base(value):
    parsed = urlparse(str(value or '').strip())
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        return None
    if parsed.query or parsed.fragment:
        return None
    return parsed._replace(path=parsed.path.rstrip('/'), params='', query='', fragment='').geturl()

def valid_paymob_test_secret(value):
    """Accept Paymob's global and Egypt-prefixed test secret keys."""
    return str(value or '').startswith(('sk_test_', 'egy_sk_test_'))

def valid_paymob_test_public(value):
    """Accept Paymob's global and Egypt-prefixed test public keys."""
    return str(value or '').startswith(('pk_test_', 'egy_pk_test_'))

def payment_provider():
    """Select one server-side provider without exposing credentials to clients."""
    provider=os.environ.get('TABO_PAYMENT_PROVIDER','paymob').strip().lower()
    return provider if provider in ('paymob','kashier') else ''

def kashier_mode():
    mode=os.environ.get('KASHIER_MODE','test').strip().lower()
    return mode if mode in KASHIER_API_BASES else ''

def valid_kashier_mid(value):
    return bool(re.fullmatch(r'MID-[A-Za-z0-9]+-[A-Za-z0-9]+',str(value or '').strip()))

def valid_kashier_credential(value):
    value=str(value or '').strip()
    return 16 <= len(value) <= 512 and not any(character.isspace() for character in value)

def kashier_key(name):
    mode=kashier_mode()
    key=os.environ.get('KASHIER_'+mode.upper()+'_'+name,'') if mode else ''
    # Generic credentials are accepted only for the explicitly test-only legacy setup.
    return key or (os.environ.get('KASHIER_'+name,'') if mode=='test' else '')

def paymob_mode():
    # Live Paymob is intentionally unsupported until a separate certified integration.
    return 'test' if os.environ.get('PAYMOB_MODE','test')=='test' else ''

def paymob_key(name):
    if not paymob_mode():return ''
    return os.environ.get('PAYMOB_TEST_'+name,'') or os.environ.get('PAYMOB_'+name,'')

def payment_mode():
    return kashier_mode() if payment_provider()=='kashier' else paymob_mode()

def confirmed_currencies(provider):
    declared=set(os.environ.get(provider+'_CURRENCIES','EGP').upper().split(','))
    verified=set(os.environ.get(provider+'_VERIFIED_CURRENCIES','').upper().split(','))
    result=declared & verified & {'EGP','USD'}
    if os.environ.get('TABO_USD_CAPABILITY_VERIFIED')!='1':result.discard('USD')
    return result

def kashier_currencies():
    return confirmed_currencies('KASHIER')

def payment_currency_supported(currency):
    currency=str(currency or '').upper()
    if payment_provider()=='kashier':
        # USD display prices are converted to an actual EGP charge for new orders.
        return ('EGP' if currency=='USD' else currency) in kashier_currencies()
    if payment_provider()=='paymob':
        return bool(paymob_mode() and currency in confirmed_currencies('PAYMOB') and localization.payment_method_for(currency))
    return False

def checkout_readiness():
    """Return non-secret readiness checks for safe operational diagnostics."""
    checks={
        'checkout_enabled': os.environ.get('TABO_CHECKOUT_ENABLED') == '1',
        'persistent_storage_confirmed': os.environ.get('TABO_PERSISTENT_STORAGE_READY') == '1',
        'supabase_configured': store.ready(),
        'originals_verified': os.environ.get('TABO_ORIGINALS_VERIFIED') == '1',
        'payment_provider_valid': bool(payment_provider()),
        'session_binding_key_configured':len(os.environ.get('TABO_SESSION_BINDING_KEY',''))>=32,
        'public_url_valid': bool(valid_https_base(os.environ.get('TABO_PUBLIC_URL'))),
    }
    if payment_provider()=='kashier':
        checks.update({
            'kashier_mode_valid': bool(kashier_mode()),
            'kashier_merchant_id_valid': valid_kashier_mid(os.environ.get('KASHIER_MERCHANT_ID')),
            'kashier_payment_key_configured': valid_kashier_credential(kashier_key('PAYMENT_API_KEY')),
            'kashier_secret_key_configured': valid_kashier_credential(kashier_key('SECRET_KEY')),
            'kashier_currency_configured': bool(kashier_currencies()),
        })
    elif payment_provider()=='paymob':
        checks.update({
            'paymob_test_mode':bool(paymob_mode()),
            'paymob_secret_test': valid_paymob_test_secret(os.environ.get('PAYMOB_SECRET_KEY')),
            'paymob_public_test': valid_paymob_test_public(os.environ.get('PAYMOB_PUBLIC_KEY')),
            'paymob_currency_verified':bool(confirmed_currencies('PAYMOB')),
            'paymob_hmac_configured': len(paymob_key('HMAC_SECRET')) >= 32,
        })
    return checks

def checkout_ready():
    """Never accept payment unless originals and durable order storage are ready."""
    return all(checkout_readiness().values())

def paypal_ready(currency):
    checks=checkout_readiness()
    required=('checkout_enabled','persistent_storage_confirmed','supabase_configured',
              'originals_verified','session_binding_key_configured','public_url_valid')
    return all(checks[key] for key in required) and paypal_checkout.available(currency)


def record_paypal_event(order,event,proof):
    result=store.apply_paypal_event(order,event,hash_secret(proof))
    if result.get('ok') is not True:
        raise store.StoreUnavailable()
    return result.get('status')=='PAID'


def now(): return datetime.now(timezone.utc).isoformat()

def hash_secret(value):
    """Hash a random bearer secret before durable storage."""
    return hashlib.sha256(str(value).encode()).hexdigest()

def normalized_name(value):
    return ' '.join(unicodedata.normalize('NFC',str(value or '')).split()).casefold()

def order_cookie_suffix(order_id):
    match=re.fullmatch(r'TABO-([A-F0-9]{16})',str(order_id or ''))
    if not match: raise ValueError('Invalid order id')
    return match.group(1)

def checkout_cookie_name(order_id):
    return '__Host-tabo_checkout_'+order_cookie_suffix(order_id)

def download_cookie_name(order_id):
    return '__Host-tabo_download_'+order_cookie_suffix(order_id)

def secure_cookie(name, value, max_age):
    return (f'{name}={value}; Path=/; Max-Age={int(max_age)}; HttpOnly; Secure; '
            'SameSite=Strict; Priority=High')

def timestamp(value):
    if not value: return None
    try:
        parsed=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError,ValueError):
        return None

def public(order):
    keys=('id','status','items','total','currency','paymentMethod','country')
    return {k:order[k] for k in keys if k in order}

def read_payment_response(response):
    raw=response.read(1_048_577)
    if len(raw)>1_048_576:
        raise ValueError('Payment response too large')
    reply=json.loads(raw)
    if not isinstance(reply,dict):
        raise ValueError('Invalid payment response')
    return reply

def log_payment_gateway_error(provider, exc):
    # Gateway messages may contain customer details. Log only a bounded status/type.
    status=getattr(exc,'code',None)
    print(f'{provider} checkout failure type={type(exc).__name__} status={status if isinstance(status,int) else "unknown"}',flush=True)

def kashier_allowed_methods():
    """Allow only hosted-checkout methods documented by Kashier."""
    raw=os.environ.get('KASHIER_ALLOWED_METHODS','card').strip().lower()
    verified=set(os.environ.get('KASHIER_VERIFIED_METHODS','').split(','))
    methods=[]
    for item in raw.split(','):
        item=item.strip()
        if re.fullmatch(r'(?:card|wallet|bank_installments|bnpl)(?:\[[a-z0-9_-]+\])?',item):
            methods.append(item)
    if not methods or any(item not in verified for item in methods):
        raise ValueError('Payment methods have not been verified for this merchant')
    return ','.join(dict.fromkeys(methods))

def payment_price_summary(order):
    """Expose display/charge amounts only, bound to the stored actual charge."""
    try:
        amount=Decimal(str(order['total']))
        currency=order['currency']
        if not amount.is_finite() or amount<=0 or currency not in ('EGP','USD'):
            return None
        summary={'displayCurrency':currency,'displayTotal':f'{amount:.2f}',
                 'chargeCurrency':currency,'chargeTotal':f'{amount:.2f}'}
        price=order.get('pricingQuote')
        if isinstance(price,dict) and currency=='EGP':
            display=Decimal(str(price.get('displayTotal','NaN')))
            charge=Decimal(str(price.get('chargeTotal','NaN')))
            if (price.get('displayCurrency')=='USD' and price.get('chargeCurrency')=='EGP'
                    and display.is_finite() and display>0 and charge.is_finite() and charge==amount):
                summary.update(displayCurrency='USD',displayTotal=f'{display:.2f}')
        return summary
    except (KeyError,TypeError,ValueError,InvalidOperation):
        return None

def kashier_checkout(order, customer):
    mode=kashier_mode()
    merchant=os.environ.get('KASHIER_MERCHANT_ID','').strip()
    payment_key=kashier_key('PAYMENT_API_KEY').strip()
    secret=kashier_key('SECRET_KEY').strip()
    site=valid_https_base(os.environ.get('TABO_PUBLIC_URL'))
    if (not mode or not valid_kashier_mid(merchant) or
            not valid_kashier_credential(payment_key) or
            not valid_kashier_credential(secret) or not site):
        raise ValueError('Kashier credentials and public HTTPS URL are not configured')
    if order['currency'] not in kashier_currencies():
        raise ValueError('Kashier is not configured for '+order['currency'])
    payload={
        'expireAt':(datetime.now(timezone.utc)+timedelta(minutes=30)).isoformat(timespec='milliseconds').replace('+00:00','Z'),
        'maxFailureAttempts':3,
        'paymentType':'credit',
        'amount':f"{Decimal(str(order['total'])):.2f}",
        'currency':order['currency'],
        'order':order['id'],
        'merchantRedirect':site+'/pending.html?key='+order['statusKey'],
        'display':order.get('displayLanguage','ar'),
        'type':'one-time',
        'allowedMethods':kashier_allowed_methods(),
        'redirectMethod':'get',
        'iframeBackgroundColor':'#FFFFFF',
        'merchantId':merchant,
        'failureRedirect':False,
        'brandColor':'#b88a18',
        'defaultMethod':'card',
        'description':'TABO BOOKS order '+order['id'],
        'manualCapture':False,
        'customer':{'email':customer['email'],'reference':order['id']},
        'interactionSource':'ECOMMERCE',
        'enable3DS':True,
        'serverWebhook':site+'/api/kashier/webhook',
    }
    price=payment_price_summary(order)
    if price and price['displayCurrency']=='USD' and price['chargeCurrency']=='EGP':
        labels=('Order price (USD)','Amount charged (EGP)') if order.get('displayLanguage')=='en' else ('سعر الطلب بالدولار الأمريكي','المبلغ المحصل بالجنيه المصري')
        payload['metaData']={'displayNotes':{
            labels[0]:price['displayTotal']+' USD',labels[1]:price['chargeTotal']+' EGP'}}
    request=Request(KASHIER_API_BASES[mode]+'/v3/payment/sessions',
                    data=json.dumps(payload,separators=(',',':')).encode(),
                    headers={'Authorization':secret,'api-key':payment_key,
                             'Content-Type':'application/json','Accept':'application/json'},
                    method='POST')
    with urlopen(request,timeout=15) as response:
        reply=read_payment_response(response)
    session_id=reply.get('_id')
    payment_url=reply.get('sessionUrl')
    if not isinstance(session_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,128}',session_id):
        raise ValueError('Invalid Kashier session response')
    parsed=urlparse(payment_url if isinstance(payment_url,str) else '')
    returned_mode=parse_qs(parsed.query).get('mode',[mode])[0]
    if parsed.scheme!='https' or parsed.hostname!='payments.kashier.io' or not parsed.path.startswith('/session/') or returned_mode!=mode:
        raise ValueError('Invalid Kashier payment destination')
    return payment_url,session_id

def payment_checkout(order,customer):
    if payment_provider()=='kashier': return kashier_checkout(order,customer)
    if payment_provider()=='paymob': return paymob_checkout(order,customer)
    raise ValueError('Payment provider is not configured')
def paymob_checkout(order, customer):
    secret=paymob_key('SECRET_KEY')
    public=paymob_key('PUBLIC_KEY')
    site=valid_https_base(os.environ.get('TABO_PUBLIC_URL'))
    if not paymob_mode() or not valid_paymob_test_secret(secret) or not valid_paymob_test_public(public) or not site:
        raise ValueError('Paymob Test keys and public HTTPS URL are not configured')
    first,*rest=customer['name'].strip().split()
    details={key:'NA' for key in ('apartment','floor','street','building','shipping_method','postal_code','city','state')}
    method_id=localization.payment_method_for(order['currency'])
    if not payment_currency_supported(order['currency']) or not method_id:
        raise ValueError('Paymob is not configured for '+order['currency'])
    amount_minor=localization.amount_to_minor(order['total'],order['currency'])
    details.update(first_name=first,last_name=' '.join(rest) or first,email=customer['email'],phone_number=customer['phone'],country=order['country'])
    payload={'amount':amount_minor,'currency':order['currency'],'payment_methods':[method_id],
             'items':[{'name':'TABO BOOKS order '+order['id'],'amount':amount_minor,'quantity':1}],
             'billing_data':details,'special_reference':order['id'],
             'notification_url':site+'/api/paymob/webhook',
             'redirection_url':site+'/pending.html?key='+order['statusKey']}
    request=Request(PAYMOB_BASE+'/v1/intention/',data=json.dumps(payload).encode(),
                    headers={'Authorization':'Token '+secret,'Content-Type':'application/json'},method='POST')
    with urlopen(request,timeout=15) as response:
        raw=response.read(1_048_577)
    if len(raw)>1_048_576:
        raise ValueError('Payment response too large')
    reply=json.loads(raw)
    client=reply['client_secret']
    if not isinstance(client,str) or not 20 <= len(client) <= 4096:
        raise ValueError('Invalid payment response')
    payment_url=PAYMOB_BASE+'/unifiedcheckout/?'+urlencode({'publicKey':public,'clientSecret':client})
    parsed_payment=urlparse(payment_url)
    if parsed_payment.scheme!='https' or parsed_payment.netloc!='accept.paymob.com':
        raise ValueError('Invalid payment destination')
    return (payment_url,
            str(reply.get('intention_order_id') or reply.get('id') or ''))

HMAC_FIELDS=('amount_cents','created_at','currency','error_occured','has_parent_transaction','id',
             'integration_id','is_3d_secure','is_auth','is_capture','is_refunded',
             'is_standalone_payment','is_voided','order.id','owner','pending',
             'source_data.pan','source_data.sub_type','source_data.type','success')
def verify_paymob(obj, signature):
    secret=paymob_key('HMAC_SECRET')
    if not secret or not re.fullmatch(r'[0-9a-fA-F]{128}',signature): return False
    values=[]
    try:
        for field in HMAC_FIELDS:
            value=obj
            for part in field.split('.'): value=value[part]
            if value is None: raise ValueError('missing hmac value')
            values.append(str(value).lower() if isinstance(value,bool) else str(value))
    except (KeyError,TypeError,ValueError): return False
    expected=hmac.new(secret.encode(),''.join(values).encode(),hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected,signature.lower())

def kashier_signature_value(value):
    if isinstance(value,bool): return 'true' if value else 'false'
    if isinstance(value,int): return str(value)
    if isinstance(value,float):
        number=Decimal(str(value))
        if not number.is_finite(): raise ValueError('Invalid signature value')
        text=format(number,'f')
        return text.rstrip('0').rstrip('.') if '.' in text else text
    if isinstance(value,str): return value
    raise ValueError('Invalid signature value')

def verify_kashier(data, signature):
    """Verify Kashier's webhook HMAC exactly over its declared signature keys."""
    payment_key=kashier_key('PAYMENT_API_KEY').strip()
    if not payment_key or not isinstance(data,dict) or not re.fullmatch(r'[0-9a-fA-F]{64}',str(signature or '')):
        return False
    keys=data.get('signatureKeys')
    if (not isinstance(keys,list) or not 1<=len(keys)<=64 or
            any(not isinstance(key,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}',key) for key in keys) or len(keys)!=len(set(keys))):
        return False
    required={'amount','currency','kashierOrderId','merchantOrderId','orderReference','status','transactionId','transactionResponseCode'}
    if not required.issubset(keys):return False
    try:
        payload='&'.join(key+'='+quote(kashier_signature_value(data[key]),safe='')
                         for key in sorted(keys))
    except (KeyError,ValueError,TypeError,InvalidOperation):
        return False
    expected=hmac.new(payment_key.encode(),payload.encode(),hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected,str(signature).lower())

def kashier_reference(data):
    value=data.get('merchantOrderId')
    return value if isinstance(value,str) and re.fullmatch(r'TABO-[A-F0-9]{16}',value) else None


def reconcile_kashier(order,data):
    """The unsigned event name is a hint only; confirm operation and session via API."""
    mode=kashier_mode()
    request=Request(KASHIER_API_BASES[mode]+'/v3/payment/orders?'+urlencode({'search':order['id'],'limit':20}),
                    headers={'Authorization':kashier_key('SECRET_KEY'),'Accept':'application/json'})
    try:
        with urlopen(request,timeout=10) as response: reply=read_payment_response(response)
    except (HTTPError,URLError,TimeoutError,ValueError,OSError):
        raise store.StoreUnavailable() from None
    rows=reply.get('data')
    if not isinstance(rows,list):raise store.StoreUnavailable()
    for row in rows:
        if not isinstance(row,dict) or row.get('merchantOrderId')!=order['id']:continue
        if row.get('sessionId')!=order.get('paymobOrderId') or row.get('orderId')!=data.get('kashierOrderId'):continue
        original=row.get('order') or {}
        if str(original.get('currency'))!=order['currency'] or Decimal(str(original.get('amount',-1)))!=Decimal(str(order['total'])):continue
        for transaction in row.get('transactions',[]):
            if (transaction.get('transactionId')==data['transactionId'] and
                    transaction.get('status')=='SUCCESS' and
                    str(transaction.get('transactionResponseCode'))=='00' and
                    transaction.get('currency')==order['currency'] and
                    Decimal(str(transaction.get('amount',-1)))==Decimal(str(data['amount']))):
                kinds={'pay':'paid','capture':'paid','refund':'refund','partial_refund':'partial_refund','void':'void','reversal':'reversal'}
                return kinds.get(transaction.get('operation'))
    # Provider state may lag the webhook. Do not acknowledge until it can be reconciled.
    raise store.StoreUnavailable()


def apply_verified_event(order,provider,transaction,kind,amount,signature):
    result=store.apply_payment_event(order['id'],provider,kashier_mode() if provider=='kashier' else paymob_mode(),transaction,kind,amount,
                                     order['currency'],str(order['paymobOrderId']),hash_secret(signature))
    if result.get('ok') is not True:
        if result.get('reason')=='transaction_conflict':
            return 409,{'ok':False,'error':'Transaction already durably recorded'}
        raise store.StoreUnavailable()
    return 200,{'ok':True,'paid':result.get('status')=='PAID'}

def kashier_payment_matches(order,data):
    try:
        actual=Decimal(str(data.get('amount')))
        expected=Decimal(str(order['total']))
    except (InvalidOperation,ValueError,TypeError):
        return False
    return (order.get('paymentMethod')=='Kashier' and data.get('status')=='SUCCESS' and
            str(data.get('transactionResponseCode',''))=='00' and
            data.get('currency')==order['currency'] and actual==expected)
def watermarked(src,order):
    pdf_limits.check_source(src)
    with pdf_limits.work_slot():
        stamp_order={'id':order['id'],'buyer':{key:order['buyer'][key] for key in ('name','phone')}}
        payload,result=media_sandbox.run('watermark',src,{'order':stamp_order,'max_pages':pdf_limits.MAX_PAGES,'font':FONT},
            input_limit=pdf_limits.MAX_SOURCE_BYTES,output_limit=pdf_limits.MAX_OUTPUT_BYTES)
        return payload,result['pages']

class Handler(BaseHTTPRequestHandler):
    server_version = 'TABO'
    sys_version = ''

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def end_headers(self):
        if not seo.indexable(self.headers.get('Host','')):
            self.send_header('X-Robots-Tag','noindex, nofollow, noarchive')
        elif urlparse(self.path).path.startswith('/api/'):
            self.send_header('X-Robots-Tag','noindex, noarchive')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Permissions-Policy','camera=(), microphone=(), geolocation=(), payment=()')
        self.send_header('Cross-Origin-Opener-Policy','same-origin')
        self.send_header('Cross-Origin-Resource-Policy','same-origin')
        self.send_header('Strict-Transport-Security','max-age=31536000; includeSubDomains')
        nonce=getattr(self,'_csp_nonce',None)
        if nonce:
            meta_enabled=getattr(self,'_meta_enabled',False)
            meta_scripts=' https://connect.facebook.net' if meta_enabled else ''
            meta_images=' https://www.facebook.com https://facebook.com' if meta_enabled else ''
            meta_connect=' https://www.facebook.com https://connect.facebook.net' if meta_enabled else ''
            google_enabled=getattr(self,'_google_enabled',False) or getattr(self,'_ga4_enabled',False)
            google_scripts=' https://www.googletagmanager.com https://www.googleadservices.com https://www.google.com' if google_enabled else ''
            google_images=(google_scripts+' https://googleads.g.doubleclick.net https://pagead2.googlesyndication.com'
                           ' https://www.google.com.eg https://www.google.com.sa https://www.google.com.kw') if google_enabled else ''
            analytics_endpoints=' https://www.google-analytics.com https://region1.google-analytics.com https://analytics.google.com' if getattr(self,'_ga4_enabled',False) else ''
            google_images+=analytics_endpoints
            google_connect=(google_images+' https://ad.doubleclick.net') if google_enabled else ''
            google_frames='https://www.googletagmanager.com' if google_enabled else "'none'"
            snap_enabled=getattr(self,'_snap_enabled',False)
            snap_scripts=' https://sc-static.net' if snap_enabled else ''
            snap_endpoints=' https://tr.snapchat.com https://tr6.snapchat.com' if snap_enabled else ''
            sentry_config=getattr(self,'_sentry_config',None)
            sentry_scripts=' https://browser.sentry-cdn.com' if sentry_config else ''
            sentry_connect=' '+sentry_config['ingest'] if sentry_config else ''
            self.send_header('Content-Security-Policy',
                "default-src 'self'; script-src 'nonce-"+nonce+"'"+meta_scripts+google_scripts+snap_scripts+sentry_scripts+"; "
                "style-src 'self' 'unsafe-inline'; img-src 'self' https://i.ibb.co data:"+meta_images+google_images+snap_endpoints+"; "
                "connect-src 'self'"+meta_connect+google_connect+snap_endpoints+sentry_connect+"; font-src 'self' data:; object-src 'none'; base-uri 'none'; "
                "frame-ancestors 'none'; frame-src "+google_frames+"; form-action 'self'; worker-src 'none'; "
                "upgrade-insecure-requests")
        else:
            self.send_header('Content-Security-Policy',"default-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        super().end_headers()

    def log_message(self, fmt,*args):
        # Avoid recording buyer details or bearer download tokens in server logs.
        print('%s %s %s' % (self.address_string(), self.command, urlparse(self.path).path))
    def respond(self, code, obj, extra_headers=None):
        if code == 400 and urlparse(self.path).path == autoseo.ENDPOINT:
            autoseo.record_rejection(obj.get('error', 'Invalid request'))
        body=json.dumps(obj,ensure_ascii=False).encode()
        self.send_response(code); self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store'); self.send_header('Content-Length',str(len(body)))
        headers=(extra_headers or {}).items() if isinstance(extra_headers,dict) else (extra_headers or [])
        for name,value in headers: self.send_header(name,value)
        self.end_headers(); self.wfile.write(body)

    def client_id(self):
        return proxy_identity.client_ip(self.client_address[0],self.headers)

    def cookie(self, name):
        try:
            parsed=SimpleCookie(); parsed.load(self.headers.get('Cookie',''))
            morsel=parsed.get(name)
            return morsel.value if morsel else None
        except CookieError:
            return None

    def device_hash(self):
        """Bind a download session to the browser without storing its user agent."""
        secret=os.environ.get('TABO_SESSION_BINDING_KEY','')
        user_agent=self.headers.get('User-Agent','')[:512]
        if len(secret)<32 or not user_agent: return None
        return hmac.new(secret.encode(),user_agent.encode(),hashlib.sha256).hexdigest()

    def authorized_download_order(self, order_id):
        if not isinstance(order_id,str) or not re.fullmatch(r'TABO-[A-F0-9]{16}',order_id):
            return None
        order=store.find('id',order_id) if store.ready() else None
        if not order or order['status']!='PAID' or not order['buyer']:
            return None
        raw=self.cookie(download_cookie_name(order_id))
        device=self.device_hash()
        expiry=timestamp(order.get('downloadSessionExpiresAt'))
        if (not raw or not device or not expiry or expiry<=datetime.now(timezone.utc) or
                not order.get('downloadSessionHash') or not order.get('downloadDeviceHash')):
            return None
        if not hmac.compare_digest(hash_secret(raw),order['downloadSessionHash']):
            return None
        if not hmac.compare_digest(device,order['downloadDeviceHash']):
            return None
        return order

    def geo_client_ip(self):
        return self.client_id()

    def pricing_country(self, requested=None):
        selected=localization.normalize_country(requested)
        if selected!='EG': return selected
        return localization.pricing_country(selected,localization.country_from_ip(self.client_id()))

    def enforce_rate_limit(self, route, method):
        lookup=POST_LIMITS if method=='POST' else GET_LIMITS
        bucket=route
        if method=='GET' and re.fullmatch(r'/previews/book[1-9][0-9]*',route):
            bucket='/previews/view'
        if method=='GET' and re.fullmatch(r'/previews/book[1-9][0-9]*/(?:info|page/[1-9][0-9]*)',route):
            bucket='/previews/read'
        if method=='GET' and re.fullmatch(r'/api/download/book[1-9][0-9]*',route):
            bucket='/api/download'
        if method=='POST' and re.fullmatch(r'/api/download/book[1-9][0-9]*',route):
            bucket='/api/download'
        limits=lookup.get(bucket)
        if not limits: return True
        allowed,retry=RATE_LIMITER.allow(method+':'+bucket+':'+self.client_id(),*limits)
        if allowed: return True
        self.respond(429,{'ok':False,'error':'طلبات كثيرة جدًا. حاول مرة أخرى بعد قليل.'},
                     {'Retry-After':str(retry)})
        return False

    def same_origin_browser_request(self):
        if self.headers.get('Sec-Fetch-Site','').lower()=='cross-site':
            return False
        origin=self.headers.get('Origin')
        if not origin: return True
        parsed=urlparse(origin)
        host=self.headers.get('Host','').lower()
        return parsed.netloc.lower()==host and parsed.scheme in ('https','http')

    def read_json(self, maximum=131_072):
        if self.headers.get_content_type()!='application/json':
            raise ValueError('Content-Type must be application/json')
        size=int(self.headers.get('Content-Length','0'))
        if size<1 or size>maximum: raise ValueError('Invalid request size')
        data=json.loads(self.rfile.read(size))
        if not isinstance(data,dict): raise ValueError('Invalid JSON object')
        return data
    def html(self, filename, extra_headers=None, public_path=None):
        nonce=secrets.token_urlsafe(24)
        source=(ROOT/filename).read_text(encoding='utf-8')
        if filename == 'admin-dashboard.html':
            version=hashlib.sha256((ROOT/'admin-dashboard.css').read_bytes()).hexdigest()[:12]
            source=source.replace('href="/admin-dashboard.css"','href="/admin-dashboard.css?v='+version+'"')
        if filename == 'checkout-recovery.html':
            source=source.replace('__RECOVERY_DATA__',seo.safe_json(self._recovery_data))
        if filename == 'checkout.html':
            books=[{'id':bid,'name':book['name'],'enName':book['enName'],
                    **{key:book[key] for key in ('fixedPrices','bundleEligible','previewEnabled') if key in book}}
                   for bid,book in seo.current_books().items()]
            source=source.replace('__CHECKOUT_BOOKS__',seo.safe_json(books))
        for asset in ('checkout-core.js','checkout-page.js','offer-pricing.js','order-help.js','admin-dashboard.js','admin-store.js','checkout-recovery.js','book-sharing.js','download-progress.js','admin-tools.js'):
            if 'src="/'+asset+'"' in source:
                version=hashlib.sha256((ROOT/asset).read_bytes()).hexdigest()[:12]
                source=source.replace('src="/'+asset+'"','src="/'+asset+'?v='+version+'"')
        if filename in ('index.html','book.html','info.html','offers.html'):
            source=seo.render(source,public_path or urlparse(self.path).path,self.headers.get('Host',''),filename,blog_enabled=autoseo.enabled())
            if source is None:
                return self.respond(404,{'ok':False,'error':'Not found'})
        if filename in ('index.html','book.html','info.html','offers.html') and autoseo.enabled():
            source = source.replace('<a href="/offers" data-offers-label>',
                '<a href="/blog" data-seo-label="blog">المقالات · Blog</a><a href="/offers" data-offers-label>', 1)
        self._sentry_config=store_tools.sentry_config() if seo.indexable(self.headers.get('Host','')) and filename in ('index.html','book.html','checkout.html','info.html','offers.html','success.html') else None
        if self._sentry_config:
            config=self._sentry_config
            version=hashlib.sha256((ROOT/'error-monitoring.js').read_bytes()).hexdigest()[:12]
            tag='<script src="/error-monitoring.js?v='+version+'" data-sentry-dsn="'+seo.escape(config['dsn'])+'" data-release="'+config['release']+'"></script>'
            source=source.replace('</head>',tag+'</head>',1)
        pixel_id=meta_tracking.pixel_id()
        self._meta_enabled=bool(pixel_id and not seo.review_mode() and filename in ('index.html','book.html','checkout.html','success.html','offers.html'))
        if self._meta_enabled:
            version=hashlib.sha256((ROOT/'meta-tracking.js').read_bytes()).hexdigest()[:12]
            source=source.replace('</head>', '<script src="/meta-tracking.js?v='+version+'" data-pixel-id="'+pixel_id+'"></script></head>',1)
        google_id=google_ads_tracking.tag_id()
        self._google_enabled=bool(google_id and not seo.review_mode() and filename in ('index.html','book.html','checkout.html','info.html','offers.html','success.html'))
        if self._google_enabled:
            version=hashlib.sha256((ROOT/'google-ads-tracking.js').read_bytes()).hexdigest()[:12]
            source=source.replace('</head>', '<script src="/google-ads-tracking.js?v='+version+'" data-google-tag-id="'+google_id+'" data-google-conversion-label="'+google_ads_tracking.CONVERSION_LABEL+'"></script></head>',1)
        ga4_id=journey_tracking.tag_id()
        self._ga4_enabled=bool(ga4_id and not seo.review_mode() and filename in ('index.html','book.html','checkout.html','info.html','offers.html','success.html'))
        if self._ga4_enabled:
            version=hashlib.sha256((ROOT/'ga4-tracking.js').read_bytes()).hexdigest()[:12]
            names={bid:book['name'] for bid,book in seo.current_books().items()}
            names_attr=__import__('html').escape(json.dumps(names,ensure_ascii=False),quote=True)
            source=source.replace('</head>','<script src="/ga4-tracking.js?v='+version+'" data-ga4-id="'+ga4_id+'" data-ga4-catalog="'+names_attr+'"></script></head>',1)
        if owner_dashboard.enabled() and not seo.review_mode() and filename in ('index.html','book.html','checkout.html','info.html','offers.html'):
            version=hashlib.sha256((ROOT/'store-insights.js').read_bytes()).hexdigest()[:12]
            source=source.replace('</head>','<script src="/store-insights.js?v='+version+'"></script></head>',1)
        snap_id=snap_tracking.pixel_id()
        self._snap_enabled=bool(snap_id and not seo.review_mode() and filename in ('index.html','book.html','checkout.html','offers.html','success.html'))
        if self._snap_enabled:
            version=hashlib.sha256((ROOT/'snap-tracking.js').read_bytes()).hexdigest()[:12]
            source=source.replace('</head>', '<script src="/snap-tracking.js?v='+version+'" data-snap-pixel-id="'+snap_id+'"></script></head>',1)
        source=re.sub(r'<(script|style)(\b[^>]*)>',
                      lambda match:'<'+match.group(1)+' nonce="'+nonce+'"'+match.group(2)+'>',source)
        payload=source.encode('utf-8'); self._csp_nonce=nonce; self.send_response(200)
        self.send_header('Content-Type','text/html; charset=utf-8')
        self.send_header('Cache-Control','no-store')
        for name,value in (extra_headers or {}).items():
            self.send_header(name,value)
        if filename in ('checkout.html','pending.html','success.html','preview.html','paypal-return.html','order-help.html','admin-dashboard.html','checkout-recovery.html'):
            self.send_header('X-Robots-Tag','noindex, nofollow, noarchive')
        self.send_header('Content-Length',str(len(payload))); self.end_headers(); self.wfile.write(payload)

    def asset(self, filename, content_type):
        payload=(ROOT/filename).read_bytes()
        self.send_response(200); self.send_header('Content-Type',content_type)
        self.send_header('Cache-Control','public, max-age=86400')
        self.send_header('Content-Length',str(len(payload))); self.end_headers(); self.wfile.write(payload)

    def unsupported(self):
        return self.respond(405,{'ok':False,'error':'Method not allowed'},{'Allow':'GET, POST'})
    do_OPTIONS=unsupported
    do_TRACE=unsupported
    do_PUT=unsupported
    do_PATCH=unsupported
    do_DELETE=unsupported
    do_HEAD=unsupported

    def do_POST(self):
        route=urlparse(self.path).path
        try:
            if route in ('/api/orders','/api/checkout-quote'):
                with store_catalog.context():return self._do_POST()
            return self._do_POST()
        except Exception:
            return self.respond(503,{'error':'تعذر قراءة إعدادات المتجر الآن. حاول مرة أخرى.'})

    def _do_POST(self):
        route=urlparse(self.path).path
        try:
            if seo.review_mode():
                return self.respond(503,{'ok':False,'error':'Read-only review environment'})
            if not self.enforce_rate_limit(route,'POST'): return
            if route == autoseo.ENDPOINT:
                return self.autoseo_webhook()
            if (route in ('/api/paypal/capture','/api/book-comments','/api/ratings','/api/orders','/api/checkout-quote','/api/download-session') or route.startswith(('/api/order-help/','/api/admin-dashboard/','/api/store-insights/','/api/checkout-reminders/')) or
                    re.fullmatch(r'/api/download/book[1-9][0-9]*',route)) and not self.same_origin_browser_request():
                return self.respond(403,{'ok':False,'error':'Cross-site request blocked'})
            if route=='/api/admin-dashboard/catalog/cover':
                if not owner_dashboard.enabled() or not store_catalog.enabled() or not owner_dashboard.authenticated(self.cookie(owner_dashboard.COOKIE),self.device_hash()):
                    return self.respond(403,{'error':'سجّل الدخول أولًا.'})
            data=self.read_json(2_200_000 if route=='/api/admin-dashboard/catalog/cover' else 131_072)
            if route=='/api/checkout-reminders/unsubscribe':
                checkout_recovery.unsubscribe(data.get('token'))
                return self.respond(200,{'ok':True})
            if route.startswith('/api/admin-dashboard/'):
                if not owner_dashboard.enabled():return self.respond(503,{'error':'لوحة الإدارة غير متاحة مؤقتًا.'})
                if route.startswith('/api/admin-dashboard/catalog/') or route=='/api/admin-dashboard/recovery':
                    auth=owner_dashboard.authenticated(self.cookie(owner_dashboard.COOKIE),self.device_hash())
                    if not auth:return self.respond(403,{'error':'سجّل الدخول أولًا.'})
                    if not store_catalog.enabled():return self.respond(503,{'error':'إدارة المتجر غير متاحة مؤقتًا.'})
                    try:
                        if route=='/api/admin-dashboard/catalog/cover':return self.respond(200,store_catalog.upload(data))
                        if route=='/api/admin-dashboard/catalog/draft':return self.respond(200,store_catalog.save(auth,data))
                        if route=='/api/admin-dashboard/catalog/publish':return self.respond(200,store_catalog.save(auth,data,publish=True))
                        if route=='/api/admin-dashboard/recovery':return self.respond(200,checkout_recovery.admin(auth,data))
                    except FileExistsError as exc:return self.respond(409,{'error':str(exc)})
                    except ValueError as exc:return self.respond(400,{'error':str(exc)})
                    except PermissionError:return self.respond(403,{'error':'انتهت جلسة الدخول.'})
                    return self.respond(404,{'error':'Not found'})
                if route=='/api/admin-dashboard/request':
                    challenge=owner_dashboard.login_request(data,self.client_id(),self.device_hash())
                    return self.respond(202,{'challenge':challenge})
                if route=='/api/admin-dashboard/verify':
                    cookie=owner_dashboard.login_verify(data,self.device_hash())
                    if not cookie:return self.respond(403,{'error':'رمز التحقق غير صحيح أو انتهت صلاحيته.'})
                    return self.respond(200,{'ok':True},{'Set-Cookie':secure_cookie(owner_dashboard.COOKIE,cookie,28800)})
                if route=='/api/admin-dashboard/logout':
                    owner_dashboard.logout(self.cookie(owner_dashboard.COOKIE),self.device_hash())
                    return self.respond(200,{'ok':True},{'Set-Cookie':secure_cookie(owner_dashboard.COOKIE,'',0)})
                return self.respond(404,{'error':'Not found'})
            if route.startswith('/api/store-insights/'):
                if not owner_dashboard.enabled():return self.respond(503,{'error':'Unavailable'})
                if route=='/api/store-insights/session':
                    result=owner_dashboard.insight_start(data,self.cookie(owner_dashboard.INSIGHT_COOKIE),self.device_hash(),self.client_id(),self.pricing_country(data.get('country')))
                    if not result:return self.respond(429,{'error':'Please try later'})
                    token,ttl=result
                    return self.respond(200,{'ok':True},{'Set-Cookie':secure_cookie(owner_dashboard.INSIGHT_COOKIE,token,ttl)})
                if route=='/api/store-insights/event':
                    owner_dashboard.insight_event(data,self.cookie(owner_dashboard.INSIGHT_COOKIE),self.device_hash())
                    return self.respond(200,{'ok':True})
                return self.respond(404,{'error':'Not found'})
            if route=='/api/paypal/capture':
                key=data.get('key')
                if not isinstance(key,str) or not re.fullmatch(r'[0-9a-f]{64}',key):
                    return self.respond(403,{'ok':False,'error':'Invalid payment session'})
                order=store.find('status_key',key) if store.ready() else None
                if (not order or order.get('paymentMethod')!='PayPal' or order.get('paymentMode')!=paypal_checkout.mode()
                        or not order.get('checkoutSessionHash') or not paypal_checkout.configured()):
                    return self.respond(403,{'ok':False,'error':'Invalid payment session'})
                cookie=self.cookie(checkout_cookie_name(order['id']))
                if not cookie or not hmac.compare_digest(hash_secret(cookie),order['checkoutSessionHash']):
                    return self.respond(403,{'ok':False,'error':'Return to the original purchasing browser'})
                if data.get('token')!=order.get('paymobOrderId'):
                    return self.respond(400,{'ok':False,'error':'Payment reference mismatch'})
                if order['status']!='PENDING':
                    return self.respond(200,{'ok':True,'paid':order['status']=='PAID'})
                try:
                    event=paypal_checkout.capture(order)
                    paid=record_paypal_event(order,event,json.dumps(event,sort_keys=True)) if event else False
                    return self.respond(200,{'ok':True,'paid':paid})
                except paypal_checkout.PaymentMismatch:
                    return self.respond(409,{'ok':False,'error':'Payment verification failed'})
                except paypal_checkout.PayPalUnavailable:
                    return self.respond(503,{'ok':False,'error':'PayPal confirmation temporarily unavailable'})
            if route=='/api/paypal/webhook':
                if not store.ready() or not paypal_checkout.configured():
                    return self.respond(503,{'ok':False,'error':'PayPal reconciliation unavailable'})
                try:
                    if not paypal_checkout.verify_webhook(data,self.headers):
                        return self.respond(401,{'ok':False,'error':'Invalid PayPal signature'})
                    capture_id=paypal_checkout.webhook_capture_id(data)
                    if not capture_id:return self.respond(200,{'ok':True,'ignored':True})
                    captured=paypal_checkout.api('/v2/payments/captures/'+capture_id)
                    provider_id=captured.get('supplementary_data',{}).get('related_ids',{}).get('order_id')
                    reference=captured.get('custom_id') or captured.get('invoice_id')
                    if isinstance(reference,str) and re.fullmatch(r'TABO-[A-F0-9]{16}',reference):
                        order=store.find('id',reference)
                    elif isinstance(provider_id,str) and paypal_checkout.IDS.fullmatch(provider_id):
                        order=store.find('paymob_order_id',provider_id)
                    else:raise store.StoreUnavailable()
                    if not order or order.get('paymentMethod')!='PayPal' or order.get('paymentMode')!=paypal_checkout.mode():
                        return self.respond(200,{'ok':True,'ignored':True})
                    event=paypal_checkout.reconcile(order)
                    if not event or event['transaction']!=capture_id:raise store.StoreUnavailable()
                    if data.get('event_type') in ('PAYMENT.CAPTURE.REFUNDED','PAYMENT.CAPTURE.REVERSED') and event['kind']=='paid':
                        raise store.StoreUnavailable()
                    paid=record_paypal_event(order,event,json.dumps(data,sort_keys=True))
                    return self.respond(200,{'ok':True,'paid':paid})
                except paypal_checkout.PaymentMismatch:
                    return self.respond(409,{'ok':False,'error':'Payment verification failed'})
                except paypal_checkout.PayPalUnavailable:
                    return self.respond(503,{'ok':False,'error':'PayPal reconciliation temporarily unavailable'})
            if route.startswith('/api/order-help/'):
                if not order_help.enabled():
                    return self.respond(503,{'ok':False,'error':'Order assistance temporarily unavailable'})
                if route=='/api/order-help/request':
                    challenge=order_help.request(data,self.client_id(),self.device_hash())
                    return self.respond(202,{'ok':True,'challenge':challenge})
                if route=='/api/order-help/verify':
                    cookie=order_help.verify(data,self.device_hash())
                    if not cookie:
                        return self.respond(403,{'ok':False,'error':'Verification code is invalid or expired'})
                    return self.respond(200,{'ok':True},{'Set-Cookie':secure_cookie(order_help.COOKIE,cookie,900)})
                if route=='/api/order-help/resend':
                    result=order_help.resend(self.cookie(order_help.COOKIE),self.device_hash())
                    if not result:
                        return self.respond(403,{'ok':False,'error':'Verify your email first'})
                    return self.respond(200,{'ok':True,**result})
                return self.respond(404,{'ok':False,'error':'Not found'})
            if route=='/api/book-comments':
                if not store.ready():
                    return self.respond(503,{'ok':False,'error':'Comments temporarily unavailable'})
                if data.get('website'):
                    return self.respond(202,{'ok':True,'status':'pending'})
                comment=reader_comments.validate(data,BOOKS)
                verified_hash=None
                order_id=data.get('order_id')
                if (isinstance(order_id,str) and re.fullmatch(r'TABO-[A-F0-9]{16}',order_id)
                        and self.cookie(download_cookie_name(order_id))):
                    order=self.authorized_download_order(order_id)
                    if order and comment['book_id'] in [item.get('id') for item in order['items'] if isinstance(item,dict)]:
                        verified_hash=reader_comments.fingerprint('order',order['id'])
                store.submit_comment(comment,verified_hash)
                return self.respond(202,{'ok':True,'status':'pending'})
            if route=='/api/ratings':
                book_id=data.get('book_id'); rating=data.get('rating')
                if book_id not in BOOKS or isinstance(rating,bool) or not isinstance(rating,int) or not 1<=rating<=5:
                    return self.respond(400,{'ok':False,'error':'اختر تقييمًا صحيحًا من نجمة إلى خمس نجوم.'})
                if not store.ready():
                    return self.respond(503,{'ok':False,'error':'تعذر حفظ التقييم الآن. حاول مرة أخرى.'})
                cookie=self.headers.get('Cookie','')
                match=re.search(r'(?:^|;\s*)(?:__Host-)?tabo_rating_id=([0-9a-f]{64})(?:;|$)',cookie)
                rating_id=match.group(1) if match else secrets.token_hex(32)
                secret=os.environ.get('SUPABASE_SECRET_KEY','')
                voter_hash=hmac.new(secret.encode(),rating_id.encode(),hashlib.sha256).hexdigest()
                stats=store.submit_rating(book_id,voter_hash,rating)
                headers={'Set-Cookie':f'__Host-tabo_rating_id={rating_id}; Path=/; Max-Age=31536000; HttpOnly; Secure; SameSite=Strict; Priority=High'}
                return self.respond(200,{'ok':True,'rating':rating,'stats':stats},headers)
            if route in ('/api/orders','/api/checkout-quote'):
                if store_catalog.enabled() and data.get('catalogRevision')!=store_catalog.revision():
                    return self.respond(409,{'ok':False,'code':'CATALOG_REFRESH_REQUIRED','error':'تغيرت الأسعار أو العروض. حدّث السلة وراجع الإجمالي قبل الدفع.'})
                selected=data.get('paymentProvider') or payment_provider()
                if selected not in (payment_provider(),'paypal'):
                    return self.respond(400,{'ok':False,'error':'Invalid payment provider'})
                using_paypal=selected=='paypal'
                if not (paypal_ready('USD') if using_paypal else checkout_ready()):
                    return self.respond(503,{'ok':False,'error':'الشراء غير متاح حاليًا. يرجى العودة قريبًا.'})
                items=data.get('items')
                if not isinstance(items,list) or not 1<=len(items)<=len(BOOKS):
                    return self.respond(400,{'ok':False,'error':'الطلب أو طريقة الدفع غير صالحين.'})
                ids=[x.get('id') if isinstance(x,dict) else None for x in items]
                if any(i not in BOOKS for i in ids) or len(set(ids))!=len(ids):
                    return self.respond(400,{'ok':False,'error':'الكتب غير صالحة أو مكررة.'})
                country=self.pricing_country(data.get('country'))
                price_config=localization.pricing_for_country(PRICES,country)
                total=catalog.total_for_items(ids,price_config)
                currency=price_config['currency']
                if not (paypal_ready(currency) if using_paypal else payment_currency_supported(currency)):
                    return self.respond(503,{'ok':False,'error':'الدفع بهذه العملة غير متاح حاليًا.'})
                conversion=selected=='kashier' and currency=='USD'
                if route=='/api/checkout-quote':
                    if not conversion:
                        return self.respond(200,{'ok':True,'quote':None})
                    quote_data=checkout_pricing.issue(ids,country,total,payment_mode(),os.environ.get('TABO_SESSION_BINDING_KEY',''))
                    return self.respond(200,{'ok':True,'quote':quote_data})
                paymob_method_id=(localization.payment_method_for(currency)
                                  if selected=='paymob' else None)
                customer,field_errors=checkout_fields.customer(data.get('customer'))
                if field_errors:
                    return self.respond(400,{'ok':False,'code':'INVALID_CUSTOMER','fields':field_errors,
                                             'error':'راجع بيانات التواصل الموضحة قبل الدفع.'})
                pricing_quote=None
                if conversion:
                    pricing_quote=checkout_pricing.verify(data.get('quoteToken'),ids,country,total,payment_mode(),os.environ.get('TABO_SESSION_BINDING_KEY',''))
                    total=float(Decimal(pricing_quote['chargeTotal']))
                    currency='EGP'
                checkout_secret=secrets.token_urlsafe(32)
                order={'id':'TABO-'+secrets.token_hex(8).upper(),'status':'PENDING','items':[{'id':i,'name':seo.current_books()[i]['name']} for i in ids],
                       'total':total,'currency':currency,'country':country,'pricingQuote':pricing_quote,
                       'paymobMethodId':paymob_method_id,'paymentMode':paypal_checkout.mode() if using_paypal else payment_mode(),
                       'paymentMethod':'PayPal' if using_paypal else ('Kashier' if payment_provider()=='kashier' else 'Paymob'),
                       'displayLanguage':'en' if data.get('language')=='en' else 'ar','createdAt':now(),
                       'token':None,'buyer':{**customer,'language':'en' if data.get('language')=='en' else 'ar',
                                             'receiptRequested':order_receipts.enabled(),
                                             'bookEmailRequested':book_emails.enabled(),
                                             'recoveryConsent':checkout_recovery.enabled() and data.get('recoveryConsent') is True},
                       'transactionId':None,'statusKey':secrets.token_hex(32),
                       'checkoutSessionHash':hash_secret(checkout_secret)}
                store.insert(order)
                if order['buyer']['recoveryConsent']:
                    try:checkout_recovery.enroll(order['id'])
                    except Exception:pass  # Consent remains durable for worker enrollment retries.
                owner_dashboard.link_order_async(self.cookie(owner_dashboard.INSIGHT_COOKIE),self.device_hash(),order['id'])
                try: link,provider_order_id=paypal_checkout.create(order) if using_paypal else payment_checkout(order,customer)
                except Exception as exc:
                    log_payment_gateway_error(selected,exc)
                    return self.respond(503,{'ok':False,'error':'تعذر إنشاء صفحة الدفع الآن. حاول مرة أخرى بعد قليل.'})
                stored=store.patch(order['id'],{'paymob_order_id':provider_order_id},{'status':'eq.PENDING'})
                if not stored: raise store.StoreUnavailable()
                cookie=secure_cookie(checkout_cookie_name(order['id']),checkout_secret,3600)
                return self.respond(201,{'ok':True,'order':public(order),'payment_url':link,
                                         'tracking_url':'/pending.html?key='+order['statusKey']},
                                    {'Set-Cookie':cookie})
            if route=='/api/kashier/webhook':
                transaction_data=data.get('data')
                signature=self.headers.get('x-kashier-signature','')
                if not isinstance(transaction_data,dict) or not verify_kashier(transaction_data,signature):
                    return self.respond(401,{'ok':False,'error':'Invalid signature'})
                if transaction_data.get('status')!='SUCCESS':
                    return self.respond(200,{'ok':True,'paid':False})
                if not store.ready():raise store.StoreUnavailable()
                reference=kashier_reference(transaction_data)
                transaction=str(transaction_data.get('transactionId',''))
                if not reference or not re.fullmatch(r'[A-Za-z0-9_.:-]{6,128}',transaction):
                    return self.respond(400,{'ok':False,'error':'Invalid payment reference'})
                order=store.find('id',reference)
                if not order or order['paymentMethod']!='Kashier' or order.get('paymentMode')!=kashier_mode():
                    return self.respond(400,{'ok':False,'error':'Payment mode or order mismatch'})
                amount=Decimal(str(transaction_data['amount']))
                if (not amount.is_finite() or amount<=0 or amount>Decimal(str(order['total'])) or
                        transaction_data.get('currency')!=order['currency'] or
                        transaction_data.get('transactionResponseCode')!='00' or not order.get('paymobOrderId')):
                    return self.respond(400,{'ok':False,'error':'Payment mismatch'})
                kind=reconcile_kashier(order,transaction_data)
                if not kind:return self.respond(200,{'ok':True,'paid':False})
                if kind=='paid' and amount!=Decimal(str(order['total'])):
                    return self.respond(400,{'ok':False,'error':'Payment mismatch'})
                code,result=apply_verified_event(order,'kashier',transaction,kind,amount,signature)
                return self.respond(code,result)
            if route=='/api/paymob/webhook':
                signature=parse_qs(urlparse(self.path).query).get('hmac',[''])[0]
                obj=data.get('obj')
                if data.get('type')!='TRANSACTION' or not isinstance(obj,dict) or not verify_paymob(obj,signature):
                    return self.respond(401,{'ok':False,'error':'Invalid signature'})
                if not paymob_mode():return self.respond(503,{'ok':False,'error':'Paymob live integration is unavailable'})
                if not store.ready():raise store.StoreUnavailable()
                # order.id is signed; merchant_order_id is not included in Paymob's HMAC.
                provider_id=str((obj.get('order') or {}).get('id',''))
                order=store.find('paymob_order_id',provider_id) if re.fullmatch(r'[0-9]{1,30}',provider_id) else None
                if (not order or order['paymentMethod']!='Paymob' or order.get('paymentMode')!='test' or
                        obj.get('integration_id')!=order.get('paymobMethodId') or obj.get('currency')!=order['currency'] or
                        not isinstance(obj.get('amount_cents'),int) or isinstance(obj.get('amount_cents'),bool) or
                        not 0<obj['amount_cents']<=localization.amount_to_minor(order['total'],order['currency'])):
                    return self.respond(400,{'ok':False,'error':'Payment mismatch'})
                claimed_reference=(obj.get('order') or {}).get('merchant_order_id')
                if claimed_reference is not None and claimed_reference!=order['id']:
                    return self.respond(400,{'ok':False,'error':'Payment reference mismatch'})
                transaction=str(obj.get('id',''))
                if not re.fullmatch(r'[0-9]{1,30}',transaction):return self.respond(400,{'ok':False,'error':'Invalid transaction'})
                if obj.get('success') is not True or obj.get('pending') is not False or obj.get('error_occured') is not False:
                    return self.respond(200,{'ok':True,'paid':False})
                # Child-operation type and cumulative refunded amount are not signed.
                # A signed successful child or parent refund notice safely suspends access;
                # do not infer which books or how much was refunded from unsigned fields.
                if obj.get('has_parent_transaction') is True and obj.get('is_auth') is False:
                    kind='review_notice'
                elif obj.get('is_voided') is True or obj.get('is_refunded') is True:
                    if order.get('transactionId') and order['transactionId']!=transaction:
                        return self.respond(400,{'ok':False,'error':'Parent transaction mismatch'})
                    kind='void' if obj.get('is_voided') else 'review_notice'
                elif obj.get('is_auth') is False and obj.get('has_parent_transaction') is False and obj.get('is_standalone_payment') is True:
                    if obj['amount_cents']!=localization.amount_to_minor(order['total'],order['currency']):
                        return self.respond(400,{'ok':False,'error':'Payment mismatch'})
                    kind='paid'
                else:
                    return self.respond(200,{'ok':True,'paid':False})
                code,result=apply_verified_event(order,'paymob',transaction,kind,Decimal(obj['amount_cents'])/100,signature)
                return self.respond(code,result)
            if route=='/api/admin/confirm-payment':
                secret=os.environ.get('TABO_ADMIN_KEY','')
                supplied=self.headers.get('X-Admin-Key','')
                if len(secret)<32 or not hmac.compare_digest(secret,supplied):
                    return self.respond(403,{'ok':False,'error':'Unauthorized'})
                oid=data.get('order_id'); tid=data.get('transaction_id'); amount=data.get('amount'); currency=data.get('currency')
                if not isinstance(tid,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{6,100}',tid):
                    return self.respond(400,{'ok':False,'error':'Transaction reference required'})
                if not store.ready(): return self.respond(503,{'ok':False,'error':'Database unavailable'})
                order=store.find('id',oid) if isinstance(oid,str) and re.fullmatch(r'TABO-[A-F0-9]{16}',oid) else None
                if not order: return self.respond(404,{'ok':False,'error':'Order not found'})
                if isinstance(amount,bool) or amount!=order['total'] or currency!=order['currency']:
                    return self.respond(400,{'ok':False,'error':'Amount/currency mismatch'})
                if order['status']=='PAID' and order['transactionId']!=tid:
                    return self.respond(409,{'ok':False,'error':'Order already paid with another transaction'})
                if order['status']!='PAID':
                    try:
                        order=store.patch(oid,{'status':'PAID','transaction_id':tid,
                                    'paid_at':now()},
                                    {'status':'eq.PENDING'})
                    except store.UniqueConflict: return self.respond(409,{'ok':False,'error':'Transaction already used'})
                    if not order: return self.respond(409,{'ok':False,'error':'Order already paid'})
                return self.respond(200,{'ok':True,'order_id':oid,'tracking_url':'/pending.html?key='+order['statusKey']})
            if route=='/api/download-session':
                if not store.ready(): return self.respond(503,{'ok':False,'error':'الخدمة غير متاحة الآن.'})
                key=data.get('key'); legacy_token=data.get('token')
                order=None; claim_field=None; claim_value=None
                if isinstance(key,str) and re.fullmatch(r'[0-9a-f]{64}',key):
                    order=store.find('status_key',key)
                    if not order or order['status']!='PAID':
                        return self.respond(403,{'ok':False,'error':'الدفع غير مؤكد أو رابط المتابعة غير صالح.'})
                    existing=self.authorized_download_order(order['id'])
                    if existing:
                        return self.respond(200,{'ok':True,'order_id':order['id'],
                                                'success_url':'/success.html?order='+order['id']})
                    raw=self.cookie(checkout_cookie_name(order['id']))
                    expected=order.get('checkoutSessionHash')
                    if not raw or not expected or not hmac.compare_digest(hash_secret(raw),expected):
                        return self.respond(403,{'ok':False,'error':'تم ربط الطلب بجهاز الشراء. افتح صفحة المتابعة الأصلية على الجهاز نفسه.'})
                    claim_field='checkout_session_hash'; claim_value=expected
                elif isinstance(legacy_token,str) and re.fullmatch(r'[0-9a-f]{64}',legacy_token):
                    order=store.find('download_token',legacy_token)
                    if not order or order['status']!='PAID' or order.get('downloadTokenUsedAt'):
                        return self.respond(403,{'ok':False,'error':'رابط التحميل منتهي أو تم استخدامه.'})
                    expiry=timestamp(order.get('downloadTokenExpiresAt'))
                    if not expiry or expiry<=datetime.now(timezone.utc):
                        return self.respond(403,{'ok':False,'error':'رابط التفعيل انتهت صلاحيته.'})
                    name=data.get('name'); phone=data.get('phone')
                    buyer=order.get('buyer')
                    if (not isinstance(buyer,dict) or not normalized_name(buyer.get('name')) or not buyer.get('phone') or not isinstance(name,str) or not isinstance(phone,str) or
                            not hmac.compare_digest(normalized_name(name).encode('utf-8'),normalized_name(buyer.get('name')).encode('utf-8')) or
                            not hmac.compare_digest(phone.strip().encode('utf-8'),str(buyer.get('phone') or '').strip().encode('utf-8'))):
                        return self.respond(403,{'ok':False,'error':'بيانات المشتري غير مطابقة للطلب.'})
                    claim_field='download_token'; claim_value=legacy_token
                else:
                    return self.respond(400,{'ok':False,'error':'بيانات التفعيل غير صالحة.'})
                device=self.device_hash()
                if not device:
                    return self.respond(403,{'ok':False,'error':'تعذر تأمين هذا المتصفح.'})
                session_secret=secrets.token_urlsafe(32)
                expires=(datetime.now(timezone.utc)+timedelta(days=DOWNLOAD_SESSION_DAYS)).isoformat()
                conditions={'status':'eq.PAID',claim_field:'eq.'+claim_value,'download_session_hash':'is.null'}
                if claim_field=='download_token':
                    conditions.update(download_token_used_at='is.null',download_token_expires_at='gt.'+now())
                updated=store.patch(order['id'],{
                    'checkout_session_hash':None,'download_token':None,'download_token_used_at':now(),
                    'download_session_hash':hash_secret(session_secret),'download_device_hash':device,
                    'download_session_expires_at':expires},conditions)
                if not updated:
                    return self.respond(403,{'ok':False,'error':'تم استخدام رابط التفعيل بالفعل على جهاز آخر.'})
                cookie=secure_cookie(download_cookie_name(order['id']),session_secret,
                                     DOWNLOAD_SESSION_DAYS*86400)
                return self.respond(200,{'ok':True,'order_id':order['id'],
                    'success_url':'/success.html?order='+order['id']},{'Set-Cookie':cookie})
            download_match=re.fullmatch(r'/api/download/(book[1-9][0-9]*)',route)
            if download_match:
                order_id=data.get('order_id'); book_id=download_match.group(1)
                order=self.authorized_download_order(order_id)
                if (book_id not in BOOKS or not order or
                        book_id not in [item.get('id') for item in order['items'] if isinstance(item,dict)]):
                    return self.respond(403,{'ok':False,'error':'تحميل غير مصرح.'})
                allowed,retry=RATE_LIMITER.allow('download-session:'+order_id+':'+order['downloadSessionHash'],
                                                len(order['items'])*(DOWNLOAD_LIMIT+1),60)
                if not allowed:
                    return self.respond(429,{'ok':False,'error':'طلبات كثيرة جدًا. حاول بعد قليل.'},{'Retry-After':str(retry)})
                try:
                    usage=store.download_usage(order_id).get(book_id,0)
                except Exception:
                    return self.respond(503,{'ok':False,'error':'تعذر التحقق من حد التحميل الآن.'})
                if usage>=DOWNLOAD_LIMIT:
                    return self.respond(403,{'ok':False,'error':'تم استهلاك محاولتي التحميل لهذا الكتاب.'})
                try:
                    with pdf_limits.work_slot():
                        payload,page_count=watermarked(store.original(BOOKS[book_id][1]),order)
                except (pdf_limits.PDFBusy,media_sandbox.MediaBusy):
                    return self.respond(503,{'ok':False,'error':'يجري تجهيز كتاب آخر. حاول بعد قليل.'},{'Retry-After':'5'})
                except Exception: return self.respond(500,{'ok':False,'error':'تعذر تجهيز الكتاب. تواصل مع الدعم.'})
                try:
                    claimed=store.claim_download(order_id,book_id,order['downloadSessionHash'],
                                                 order['downloadDeviceHash'],page_count)
                except Exception:
                    return self.respond(503,{'ok':False,'error':'تعذر تسجيل محاولة التحميل بأمان.'})
                if not claimed.get('allowed'):
                    message=('تم استهلاك محاولتي التحميل لهذا الكتاب.'
                             if claimed.get('reason')=='limit_reached' else 'جلسة التحميل غير صالحة.')
                    return self.respond(403,{'ok':False,'error':message})
                self.send_response(200);self.send_header('Content-Type','application/pdf')
                self.send_header('Content-Disposition',f'attachment; filename="TABO-{book_id}-{order["id"]}.pdf"')
                self.send_header('Cache-Control','private, no-store');self.send_header('X-Robots-Tag','noindex, noarchive')
                self.send_header('X-Downloads-Remaining',str(claimed.get('remaining',0)))
                self.send_header('Content-Length',str(len(payload)))
                self.end_headers();return self.wfile.write(payload)
            return self.respond(404,{'ok':False,'error':'Not found'})
        except store.StoreError:
            return self.respond(503,{'ok':False,'error':'Temporary storage failure'},{'Retry-After':'30'})
        except checkout_pricing.RateUnavailable:
            return self.respond(503,{'ok':False,'error':'تعذر حساب المقابل بالجنيه الآن. يرجى المحاولة بعد قليل.'})
        except checkout_pricing.InvalidQuote:
            return self.respond(409,{'ok':False,'code':'QUOTE_REFRESH_REQUIRED','error':'يرجى تحديث مبلغ الدفع ومراجعته قبل تأكيد الطلب.'})
        except (ValueError,json.JSONDecodeError,TypeError) as e:
            return self.respond(400,{'ok':False,'error':'Invalid request'})
        except Exception:
            self.log_error('Internal error');return self.respond(500,{'ok':False,'error':'Server error'})
    def autoseo_webhook(self):
        try:
            # Authenticate before reading or parsing untrusted request bodies.
            autoseo.authenticate(self.headers)
            if self.headers.get('Transfer-Encoding'):
                return self.respond(400, {'error': 'Transfer-Encoding is unsupported'})
            lengths = self.headers.get_all('Content-Length', [])
            if len(lengths) != 1 or not re.fullmatch(r'[0-9]+', lengths[0]):
                return self.respond(400, {'error': 'A valid Content-Length is required'})
            size = int(lengths[0])
            if size > autoseo.MAX_BODY:
                return self.respond(413, {'error': 'Article payload too large'})
            if size < 1 or self.headers.get_content_type() != 'application/json':
                return self.respond(400, {'error': 'A JSON body is required'})
            self.connection.settimeout(15)
            raw = self.rfile.read(size)
            if len(raw) != size:
                return self.respond(400, {'error': 'Incomplete JSON body'})
            result = autoseo.publish(raw, self.headers)
            return self.respond(200, result)
        except autoseo.Unauthorized:
            return self.respond(401, {'error': 'Unauthorized'})
        except autoseo.InvalidArticle as exc:
            return self.respond(400, {'error': str(exc)})
        except Exception:
            # Do not log payloads, image URLs, credentials or database responses.
            self.log_error('AutoSEO publishing failed')
            return self.respond(500, {'error': 'Publishing failed; retry delivery'}, {'Retry-After': '30'})

    def autoseo_page(self, route, parsed):
        if not autoseo.enabled():
            return self.respond(404, {'error': 'Not found'})
        try:
            if route == '/blog/style.css':
                payload = autoseo.STYLE.encode()
                content_type = 'text/css; charset=utf-8'
            elif route == '/blog/sitemap.xml':
                payload = autoseo.sitemap()
                content_type = 'application/xml; charset=utf-8'
            elif route.startswith(autoseo.MEDIA_PATH):
                match = re.fullmatch(r'/blog/media/([0-9a-f]{64})\.webp', route)
                payload = autoseo.get_media(match[1]) if match else None
                if payload is None:
                    return self.respond(404, {'error': 'Not found'})
                content_type = 'image/webp'
            else:
                if route == '/blog':
                    value = parse_qs(parsed.query).get('page', ['1'])[0]
                    if not re.fullmatch(r'[1-9][0-9]{0,4}', value):
                        return self.respond(400, {'error': 'Invalid page'})
                    page = int(value)
                    rows = autoseo.list_posts(page, 21, offset=(page - 1) * 20)
                    if page > 1 and not rows:
                        return self.respond(404, {'error': 'Not found'})
                    source = autoseo.render_index(rows, page, self.headers.get('Host', ''))
                else:
                    slug = autoseo.article_slug(unquote(route.removeprefix('/blog/')), maximum=210)
                    row = autoseo.get_post(slug)
                    if not row:
                        return self.respond(404, {'error': 'Not found'})
                    try:
                        related = autoseo.list_posts(1, 40)
                    except Exception:
                        related = []  # Optional navigation must not hide a published article.
                    try:
                        catalog_document = store_catalog.published(public_read=True)
                    except Exception:
                        catalog_document = {}  # Book links can use the baseline during a catalog outage.
                    with store_catalog.context(catalog_document):
                        source = autoseo.render_post(row, self.headers.get('Host', ''), related)
                nonce = secrets.token_urlsafe(24)
                source = source.replace('<script ', '<script nonce="' + nonce + '" ')
                self._csp_nonce = nonce
                payload = source.encode()
                content_type = 'text/html; charset=utf-8'
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'public, max-age=31536000, immutable' if content_type == 'image/webp' else 'no-store')
            self.end_headers()
            self.wfile.write(payload)
        except autoseo.InvalidArticle:
            return self.respond(404, {'error': 'Not found'})
        except Exception:
            return self.respond(503, {'error': 'Blog temporarily unavailable'}, {'Retry-After': '30'})

    def do_GET(self):
        route=urlparse(self.path).path
        uses_catalog=(route in ('/','/index.html','/checkout','/checkout.html','/api/store-config','/offers','/categories','/authors') or route.startswith(('/books/','/offers/','/categories/','/authors/','/catalog-covers/')))
        try:
            if uses_catalog:
                public_read=not (route.startswith(('/api/','/catalog-covers/')) or route in ('/checkout','/checkout.html'))
                with store_catalog.context(public_read=public_read):return self._do_GET()
            return self._do_GET()
        except Exception:
            return self.respond(503,{'error':'تعذر قراءة إعدادات المتجر الآن. حاول مرة أخرى.'})

    def _do_GET(self):
        parsed=urlparse(self.path); route=parsed.path; token=parse_qs(parsed.query).get('token',[''])[0]
        if seo.review_mode() and route=='/api/book-stats':
            return self.respond(200,{key:{'rating':None,'reviews':0,'pageCount':value.get('pageCount')} for key,value in seo.current_books().items()})
        if seo.review_mode() and route.startswith('/api/') and route not in ('/api/config','/api/store-config','/api/book-comments'):
            return self.respond(503,{'ok':False,'error':'Read-only review environment'})
        if seo.review_mode() and route.startswith('/previews/'):
            book_id=route.split('/')[2]
            return self.respond(503 if catalog.preview_enabled(book_id) else 404,
                                {'ok':False,'error':'Preview originals are not connected to the review environment'})
        if not self.enforce_rate_limit(route,'GET'): return
        if route in ('/resume-checkout','/stop-reminders'):
            if not checkout_recovery.enabled():return self.respond(404,{'error':'Not found'})
            if route=='/stop-reminders':self._recovery_data={'mode':'unsubscribe','token':token if checkout_recovery.TOKEN.fullmatch(token) else ''}
            else:
                value=checkout_recovery.cart(token)
                with store_catalog.context():
                    self._recovery_data={'mode':'cart','cart':value,'names':{bid:b['name'] for bid,b in seo.current_books().items()}}
            return self.html('checkout-recovery.html')
        if route=='/checkout-recovery.js':return self.asset('checkout-recovery.js','text/javascript; charset=utf-8')
        if route=='/admin-store.js':return self.asset('admin-store.js','text/javascript; charset=utf-8')
        image=re.fullmatch(r'/catalog-covers/([a-f0-9]{64})\.webp',route)
        private_image=re.fullmatch(r'/api/admin-dashboard/cover/([a-f0-9]{64})',route)
        if image or private_image:
            if not store_catalog.enabled():return self.respond(404,{'error':'Not found'})
            if private_image and not owner_dashboard.authenticated(self.cookie(owner_dashboard.COOKIE),self.device_hash()):return self.respond(403,{'error':'سجّل الدخول أولًا.'})
            payload=store_catalog.cover((image or private_image).group(1),private=bool(private_image))
            if payload is None:return self.respond(404,{'error':'Not found'})
            self.send_response(200);self.send_header('Content-Type','image/webp');self.send_header('Cache-Control','private, no-store' if private_image else 'public, max-age=31536000, immutable');self.send_header('Content-Length',str(len(payload)));self.end_headers();return self.wfile.write(payload)
        if route in ('/admin','/admin/'):

            return self.html('admin-dashboard.html')
        if route=='/admin-dashboard.js':return self.asset('admin-dashboard.js','text/javascript; charset=utf-8')
        if route=='/admin-dashboard.css':return self.asset('admin-dashboard.css','text/css; charset=utf-8')
        if route=='/store-insights.js':return self.asset('store-insights.js','text/javascript; charset=utf-8')
        if route.startswith('/api/admin-dashboard/'):
            if not owner_dashboard.enabled():return self.respond(503,{'error':'لوحة الإدارة غير متاحة مؤقتًا.'})
            try:
                if route in ('/api/admin-dashboard/catalog','/api/admin-dashboard/recovery'):
                    auth=owner_dashboard.authenticated(self.cookie(owner_dashboard.COOKIE),self.device_hash())
                    if not auth:return self.respond(403,{'error':'سجّل الدخول أولًا.'})
                    if not store_catalog.enabled():return self.respond(503,{'error':'إدارة المتجر غير متاحة مؤقتًا.'})
                    return self.respond(200,store_catalog.read(auth) if route.endswith('/catalog') else checkout_recovery.admin(auth))
                if route=='/api/admin-dashboard/tools':
                    if not owner_dashboard.authenticated(self.cookie(owner_dashboard.COOKIE),self.device_hash()):return self.respond(403,{'error':'سجّل الدخول أولًا.'})
                    return self.respond(200,store_tools.dashboard_tools())
                if route=='/api/admin-dashboard/session':
                    if not owner_dashboard.authenticated(self.cookie(owner_dashboard.COOKIE),self.device_hash()):return self.respond(403,{'error':'سجّل الدخول أولًا.'})
                    return self.respond(200,{'ok':True})
                if route=='/api/admin-dashboard/report':
                    result=owner_dashboard.report(self.cookie(owner_dashboard.COOKIE),self.device_hash(),parse_qs(parsed.query,keep_blank_values=True))
                    if result is None:return self.respond(403,{'error':'انتهت جلسة الدخول. سجّل الدخول من جديد.'})
                    return self.respond(200,result)
                return self.respond(404,{'error':'Not found'})
            except ValueError:return self.respond(400,{'error':'راجع الفترة والفلاتر؛ أقصى فترة 93 يومًا.'})
            except Exception:return self.respond(503,{'error':'تعذر قراءة البيانات الآن. حاول مرة أخرى.'})
        if route=='/api/book-comments':
            book_id=parse_qs(parsed.query).get('book_id',[''])[0]
            raw_offset=parse_qs(parsed.query).get('offset',['0'])[0]
            if book_id not in BOOKS or not re.fullmatch(r'[0-9]{1,4}',raw_offset) or int(raw_offset)>1000:
                return self.respond(400,{'ok':False,'error':'Invalid comment request'})
            if seo.review_mode():
                return self.respond(200,{'comments':[],'total':0})
            try:
                if not store.ready():
                    raise store.StoreUnavailable()
                result=reader_comments.public_result(store.book_comments(book_id,int(raw_offset)))
                return self.respond(200,result)
            except Exception:
                return self.respond(503,{'ok':False,'error':'Comments temporarily unavailable'})
        if route == '/api/autoseo/status':
            try:
                autoseo.authenticate(self.headers)
            except autoseo.Unauthorized:
                return self.respond(401, {'error': 'Unauthorized'})
            return self.respond(200, autoseo.diagnostics())
        if route == '/blog/' and autoseo.enabled():
            return self.respond(301, {'url': autoseo.origin() + '/blog'}, {'Location': '/blog'})
        if route == '/blog' or route.startswith('/blog/'):
            return self.autoseo_page(route, parsed)
        viewer=re.fullmatch(r'/previews/(book[1-9][0-9]*)',route)
        if viewer and viewer.group(1) in BOOKS:
            book_id=viewer.group(1)
            if not catalog.preview_enabled(book_id):
                return self.respond(404,{'ok':False,'error':'المعاينة غير متاحة لهذا الكتاب.'})
            session=book_previews.issue_session(book_id,self.headers.get('User-Agent',''))
            cookie=(f'__Secure-tabo_preview_{book_id}={session}; Path=/previews/{book_id}/; '
                    f'Max-Age={book_previews.SESSION_SECONDS}; HttpOnly; Secure; SameSite=Strict')
            return self.html('preview.html',{'Set-Cookie':cookie})
        preview_resource=re.fullmatch(r'/previews/(book[1-9][0-9]*)/(info|page/([1-9][0-9]*))',route)
        if preview_resource:
            book_id=preview_resource.group(1)
            if not catalog.preview_enabled(book_id):
                return self.respond(404,{'ok':False,'error':'Not found'})
            # Images are issued only to same-origin viewer fetches in a short browser session.
            if (self.headers.get('X-Tabo-Preview')!='1' or
                    self.headers.get('Sec-Fetch-Dest','empty')!='empty' or
                    not self.same_origin_browser_request() or
                    not book_previews.valid_session(self.cookie('__Secure-tabo_preview_'+book_id),
                                                    book_id,self.headers.get('User-Agent',''))):
                return self.respond(403,{'ok':False,'error':'افتح المعاينة من صفحة الكتاب.'})
            try:
                if preview_resource.group(2)=='info':
                    _,count=book_previews.get_sample(book_id,BOOKS,store.original)
                    return self.respond(200,{'pages':count,'title':BOOKS[book_id][0]})
                payload=book_previews.render_page(book_id,int(preview_resource.group(3)),BOOKS,store.original)
            except (pdf_limits.PDFBusy,media_sandbox.MediaBusy):
                return self.respond(503,{'ok':False,'error':'يجري تجهيز معاينة أخرى. حاول بعد قليل.'},{'Retry-After':'5'})
            except IndexError:
                return self.respond(404,{'ok':False,'error':'Page not found'})
            except Exception:
                return self.respond(503,{'ok':False,'error':'تعذر عرض العينة الآن. حاول مرة أخرى.'})
            self.send_response(200)
            self.send_header('Content-Type','image/jpeg')
            self.send_header('Content-Length',str(len(payload)))
            self.send_header('Cache-Control','private, no-store')
            self.send_header('X-Robots-Tag','noindex, nofollow, noarchive')
            self.end_headers()
            self.wfile.write(payload)
            return
        # Keep every previous PDF URL closed. No public PDF bytes are sent by the viewer.
        if route=='/previews' or route.startswith('/previews/'):
            return self.respond(410,{'ok':False,'error':'رابط ملف العينة غير متاح.'})
        if route=='/': return self.html('index.html')
        if route=='/offers' or route.startswith('/offers/'):
            return self.html('offers.html')
        if route=='/robots.txt':
            robots=seo.robots(self.headers.get('Host',''))
            if autoseo.enabled() and seo.indexable(self.headers.get('Host','')):
                robots += 'Sitemap: ' + autoseo.origin() + '/blog/sitemap.xml\n'
            payload=robots.encode()
            self.send_response(200)
            self.send_header('Content-Type','text/plain; charset=utf-8')
            self.send_header('Content-Length',str(len(payload)))
            self.end_headers();self.wfile.write(payload);return
        if route=='/sitemap.xml':
            payload=seo.sitemap()
            self.send_response(200)
            self.send_header('Content-Type','application/xml; charset=utf-8')
            self.send_header('Cache-Control','public, max-age=300')
            self.send_header('Content-Length',str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if route=='/noto-kufi-arabic-bold.ttf':
            return self.asset('noto-kufi-arabic-bold.ttf','font/ttf')
        if route=='/book-comments.js': return self.asset('book-comments.js','text/javascript; charset=utf-8')
        if route=='/meta-tracking.js': return self.asset('meta-tracking.js','text/javascript; charset=utf-8')
        if route=='/google-ads-tracking.js': return self.asset('google-ads-tracking.js','text/javascript; charset=utf-8')
        if route=='/ga4-tracking.js': return self.asset('ga4-tracking.js','text/javascript; charset=utf-8')
        if route=='/snap-tracking.js': return self.asset('snap-tracking.js','text/javascript; charset=utf-8')
        if route=='/seo.js': return self.asset('seo.js','text/javascript; charset=utf-8')
        if route=='/seo.css': return self.asset('seo.css','text/css; charset=utf-8')
        if route in ('/checkout-core.js','/checkout-page.js','/offer-pricing.js','/book-sharing.js','/download-progress.js','/error-monitoring.js','/admin-tools.js'):
            return self.asset(route.lstrip('/'),'text/javascript; charset=utf-8')
        optimized_cover=re.fullmatch(r'/covers/(book[1-9][0-9]*)\.webp',route)
        if optimized_cover and optimized_cover.group(1) in BOOKS:
            return self.asset('covers/'+optimized_cover.group(1)+'.webp','image/webp')
        if route=='/paypal-return.html': return self.html('paypal-return.html')
        if route=='/order-help': return self.html('order-help.html')
        if route=='/order-help.js': return self.asset('order-help.js','text/javascript; charset=utf-8')
        if route=='/checkout': return self.html('checkout.html')
        if route=='/pending.html': return self.html('pending.html')
        if route=='/success.html': return self.html('success.html')
        detail_match=re.fullmatch(r'/books/(book[1-9][0-9]*)',route)
        if detail_match and detail_match.group(1) in BOOKS:
            return self.html('book.html')
        if route=='/book.html':
            book_id=parse_qs(parsed.query).get('id',[''])[0]
            if book_id not in BOOKS:return self.respond(404,{'ok':False,'error':'Not found'})
            return self.html('book.html',public_path='/books/'+book_id)
        if (route in seo.public_paths() or
                (seo.review_mode() and (route=='/articles' or route in ['/articles/'+key for key in seo.listed_articles(True)]))):
            return self.html('info.html')
        cover_match=re.fullmatch(r'/(book[1-9][0-9]*)-cover\.jpg',route)
        if cover_match and cover_match.group(1) in BOOKS and (ROOT/(cover_match.group(1)+'-cover.jpg')).is_file():
            return self.asset(cover_match.group(1)+'-cover.jpg','image/jpeg')
        if route=='/tabo-books-logo.webp' and (ROOT/'tabo-books-logo.webp').is_file():
            return self.asset('tabo-books-logo.webp','image/webp')
        if route in ('/authors','/about','/contact','/privacy','/refund','/delivery'): return self.html('info.html')
        if route=='/health': return self.respond(200,{'ok':True})
        if route=='/api/config': return self.respond(200,{
            'checkoutEnabled':checkout_ready(),'paymentProvider':payment_provider()})
        if route=='/api/store-config':
            requested=parse_qs(parsed.query).get('country',[''])[0]
            country=self.pricing_country(requested)
            config=localization.pricing_for_country(PRICES,country)
            config['checkoutEnabled']=checkout_ready()
            config['checkoutSupported']=payment_currency_supported(config['currency'])
            config['paypalAvailable']=paypal_ready(config['currency'])
            config['receiptEmailEnabled']=order_receipts.enabled()
            config['bookEmailEnabled']=book_emails.enabled()
            config['catalogRevision']=store_catalog.revision()
            config['recoveryEmailEnabled']=checkout_recovery.enabled() and store_catalog.reminders_enabled()
            config['paymentProvider']=payment_provider()
            config['chargeCurrency']='EGP' if payment_provider()=='kashier' else config['currency']
            return self.respond(200,config)
        if route=='/api/book-stats':
            defaults={book_id:{'rating':None,'reviews':0,'pageCount':None}
                      for book_id in BOOKS}
            if store.ready():
                try: defaults.update(store.book_stats())
                except Exception: pass
            return self.respond(200,defaults)
        if route=='/api/order-help/status':
            if not order_help.enabled():
                return self.respond(503,{'ok':False,'error':'Order assistance temporarily unavailable'})
            order=order_help.status(self.cookie(order_help.COOKIE),self.device_hash())
            if not order:
                return self.respond(403,{'ok':False,'error':'Verify your email first'})
            result=public(order)
            # Knowing the return link never replaces the existing download-device cookies.
            result['returnUrl']='/pending.html?key='+order['statusKey']
            return self.respond(200,{'ok':True,'order':result})
        if route=='/api/order-status':
            key=parse_qs(parsed.query).get('key',[''])[0]
            if not re.fullmatch(r'[0-9a-f]{64}',key): return self.respond(403,{'ok':False,'error':'رابط متابعة غير صالح.'})
            order=store.find('status_key',key) if store.ready() else None
            if not order: return self.respond(403,{'ok':False,'error':'رابط متابعة غير صالح.'})
            return self.respond(200,{'ok':True,'status':order['status'],'order_id':order['id'],
              'books':[i['name'] for i in order['items']],
              'pricing':payment_price_summary(order),
              'ready':order['status']=='PAID'})
        if route=='/api/downloads':
            order_id=parse_qs(parsed.query).get('order',[''])[0]
            order=self.authorized_download_order(order_id)
            if not order: return self.respond(403,{'ok':False,'error':'هذه الصفحة مرتبطة بجهاز المشتري ولا تعمل من رابط مشترك.'})
            allowed,retry=RATE_LIMITER.allow('download-manifest:'+order_id+':'+order['downloadSessionHash'],120,600)
            if not allowed:return self.respond(429,{'ok':False,'error':'طلبات كثيرة جدًا.'},{'Retry-After':str(retry)})
            try: usage=store.download_usage(order_id)
            except Exception: return self.respond(503,{'ok':False,'error':'تعذر قراءة حد التحميل الآن.'})
            books=[]
            for item in order['items']:
                used=max(0,min(DOWNLOAD_LIMIT,int(usage.get(item['id'],0))))
                books.append({'id':item['id'],'name':item['name'],'remaining':DOWNLOAD_LIMIT-used})
            return self.respond(200,{'ok':True,'downloadLimit':DOWNLOAD_LIMIT,'books':books,
                                     'metaPurchase':meta_tracking.purchase(order),
                                     'googleAdsPurchase':google_ads_tracking.purchase(order) if not seo.review_mode() else None,
                                     'snapPurchase':snap_tracking.purchase(order) if not seo.review_mode() else None,
                                     'ga4Purchase':journey_tracking.purchase(order) if not seo.review_mode() else None})
        match=re.fullmatch(r'/api/download/(book[1-9][0-9]*)',route)
        if match:
            return self.respond(405,{'ok':False,'error':'استخدم زر التحميل الآمن من صفحة الطلب.'},{'Allow':'POST'})
        return self.respond(404,{'ok':False,'error':'Not found'})

if __name__=='__main__':
    host=os.environ.get('HOST','127.0.0.1');port=int(os.environ.get('PORT','3000'))
    print(f'TABO BOOKS listening on {host}:{port}',flush=True)
    missing=[name for name,ready in checkout_readiness().items() if not ready]
    print('Checkout readiness: '+('ready' if not missing else 'blocked: '+','.join(missing)),flush=True)
    order_receipts.start_worker()
    book_emails.start_worker()
    order_help.start_worker()
    owner_dashboard.start_pruner()
    checkout_recovery.start_worker()
    ThreadingHTTPServer((host,port),Handler).serve_forever()


