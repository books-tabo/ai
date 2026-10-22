"""TABO BOOKS backend. Python 3.11+, pypdf, reportlab, Pillow."""
import io, json, os, re, secrets, hmac, hashlib, ipaddress, threading, time, unicodedata
from collections import deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote, urlencode
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.utils import ImageReader
from PIL import Image, ImageDraw, ImageFont
import supabase_store as store
import localization
import proxy_identity
import book_previews
import pdf_limits
import meta_tracking
import checkout_pricing
import catalog
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
    '/api/ratings': (12, 600),
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
def make_stamp(name, phone, order_id, width):
    font = ImageFont.truetype(FONT, 26)
    canvas = Image.new('RGBA',(1800,125),(255,255,255,0))
    draw = ImageDraw.Draw(canvas)
    draw.text((900,10), f'نسخة مرخصة إلى: {name}', fill=(105,105,105,255), font=font, anchor='mt', direction='rtl')
    draw.text((900,52), f'هاتف: {phone}  |  طلب: {order_id}', fill=(105,105,105,255), font=font, anchor='mt', direction='rtl')
    buf=io.BytesIO(); canvas.save(buf,format='PNG'); buf.seek(0)
    return buf

def _watermarked(src, order):
    pdf_limits.check_source(src)
    reader=PdfReader(io.BytesIO(src) if isinstance(src, bytes) else str(src)); writer=PdfWriter()
    if len(reader.pages)>pdf_limits.MAX_PAGES:raise ValueError('PDF page limit')
    for page in reader.pages:
        width=float(page.mediabox.width); height=float(page.mediabox.height)
        overlay=io.BytesIO(); c=Canvas(overlay,pagesize=(width,height))
        stamp=make_stamp(order['buyer']['name'],order['buyer']['phone'],order['id'],width)
        c.drawImage(ImageReader(stamp), width*.08, max(10,height*.025), width=width*.84, height=40, mask='auto')
        c.save(); overlay.seek(0)
        page.merge_page(PdfReader(overlay).pages[0])
        writer.add_page(page)
        # merge_page creates a combined, uncompressed content stream. Reapply
        # lossless Flate compression so stamping does not inflate the PDF.
        writer.pages[-1].compress_content_streams()
    out=pdf_limits.LimitedBuffer(); writer.write(out); return out.getvalue(),len(reader.pages)

def watermarked(src,order):
    with pdf_limits.work_slot():
        return _watermarked(src,order)

class Handler(BaseHTTPRequestHandler):
    server_version = 'TABO'
    sys_version = ''

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def end_headers(self):
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
            self.send_header('Content-Security-Policy',
                "default-src 'self'; script-src 'nonce-"+nonce+"'"+meta_scripts+"; "
                "style-src 'self' 'unsafe-inline'; img-src 'self' https://i.ibb.co data:"+meta_images+"; "
                "connect-src 'self'"+meta_connect+"; font-src 'self' data:; object-src 'none'; base-uri 'none'; "
                "frame-ancestors 'none'; frame-src 'none'; form-action 'self'; worker-src 'none'; "
                "upgrade-insecure-requests")
        else:
            self.send_header('Content-Security-Policy',"default-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        super().end_headers()

    def log_message(self, fmt,*args):
        # Avoid recording buyer details or bearer download tokens in server logs.
        print('%s %s %s' % (self.address_string(), self.command, urlparse(self.path).path))
    def respond(self, code, obj, extra_headers=None):
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

    def read_json(self):
        if self.headers.get_content_type()!='application/json':
            raise ValueError('Content-Type must be application/json')
        size=int(self.headers.get('Content-Length','0'))
        if size<1 or size>131_072: raise ValueError('Invalid request size')
        data=json.loads(self.rfile.read(size))
        if not isinstance(data,dict): raise ValueError('Invalid JSON object')
        return data
    def html(self, filename, extra_headers=None):
        nonce=secrets.token_urlsafe(24)
        source=(ROOT/filename).read_text(encoding='utf-8')
        pixel_id=meta_tracking.pixel_id()
        self._meta_enabled=bool(pixel_id and filename in ('index.html','book.html','success.html'))
        if self._meta_enabled:
            version=hashlib.sha256((ROOT/'meta-tracking.js').read_bytes()).hexdigest()[:12]
            source=source.replace('</head>', '<script src="/meta-tracking.js?v='+version+'" data-pixel-id="'+pixel_id+'"></script></head>',1)
        source=re.sub(r'<(script|style)(\b[^>]*)>',
                      lambda match:'<'+match.group(1)+' nonce="'+nonce+'"'+match.group(2)+'>',source)
        payload=source.encode('utf-8'); self._csp_nonce=nonce; self.send_response(200)
        self.send_header('Content-Type','text/html; charset=utf-8')
        self.send_header('Cache-Control','no-store')
        for name,value in (extra_headers or {}).items():
            self.send_header(name,value)
        if filename in ('pending.html','success.html','preview.html'):
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
            if not self.enforce_rate_limit(route,'POST'): return
            if (route in ('/api/ratings','/api/orders','/api/checkout-quote','/api/download-session') or
                    re.fullmatch(r'/api/download/book[1-9][0-9]*',route)) and not self.same_origin_browser_request():
                return self.respond(403,{'ok':False,'error':'Cross-site request blocked'})
            data=self.read_json()
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
                if not checkout_ready():
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
                if not payment_currency_supported(currency):
                    return self.respond(503,{'ok':False,'error':'الدفع بهذه العملة غير متاح حاليًا.'})
                conversion=payment_provider()=='kashier' and currency=='USD'
                if route=='/api/checkout-quote':
                    if not conversion:
                        return self.respond(200,{'ok':True,'quote':None})
                    quote_data=checkout_pricing.issue(ids,country,total,payment_mode(),os.environ.get('TABO_SESSION_BINDING_KEY',''))
                    return self.respond(200,{'ok':True,'quote':quote_data})
                paymob_method_id=(localization.payment_method_for(currency)
                                  if payment_provider()=='paymob' else None)
                customer=data.get('customer')
                if not isinstance(customer,dict) or not isinstance(customer.get('name'),str) or not 2<=len(customer['name'].strip())<=100 or not isinstance(customer.get('phone'),str) or not re.fullmatch(r'\+?[0-9]{8,15}',customer['phone']) or not isinstance(customer.get('email'),str) or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',customer['email']):
                    return self.respond(400,{'ok':False,'error':'الاسم والبريد الإلكتروني ورقم الهاتف مطلوبة للدفع.'})
                pricing_quote=None
                if conversion:
                    pricing_quote=checkout_pricing.verify(data.get('quoteToken'),ids,country,total,payment_mode(),os.environ.get('TABO_SESSION_BINDING_KEY',''))
                    total=float(Decimal(pricing_quote['chargeTotal']))
                    currency='EGP'
                checkout_secret=secrets.token_urlsafe(32)
                order={'id':'TABO-'+secrets.token_hex(8).upper(),'status':'PENDING','items':[{'id':i,'name':BOOKS[i][0]} for i in ids],
                       'total':total,'currency':currency,'country':country,'pricingQuote':pricing_quote,
                       'paymobMethodId':paymob_method_id,'paymentMode':payment_mode(),
                       'paymentMethod':'Kashier' if payment_provider()=='kashier' else 'Paymob',
                       'displayLanguage':'en' if data.get('language')=='en' else 'ar','createdAt':now(),
                       'token':None,'buyer':{'name':customer['name'].strip(),'phone':customer['phone']},
                       'transactionId':None,'statusKey':secrets.token_hex(32),
                       'checkoutSessionHash':hash_secret(checkout_secret)}
                store.insert(order)
                try: link,provider_order_id=payment_checkout(order,customer)
                except Exception as exc:
                    log_payment_gateway_error(payment_provider(),exc)
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
                except pdf_limits.PDFBusy:
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
    def do_GET(self):
        parsed=urlparse(self.path); route=parsed.path; token=parse_qs(parsed.query).get('token',[''])[0]
        if not self.enforce_rate_limit(route,'GET'): return
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
            except pdf_limits.PDFBusy:
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
        if route=='/sitemap.xml':
            from xml.etree.ElementTree import Element, SubElement, tostring
            namespace='http://www.sitemaps.org/schemas/sitemap/0.9'
            root=Element('urlset', xmlns=namespace)
            paths=['/']+['/books/'+book_id for book_id in BOOKS]+[
                '/authors','/about','/contact','/privacy','/refund','/delivery']
            for path in paths:
                SubElement(SubElement(root,'url'),'loc').text='https://tabobooks.com'+path
            payload=tostring(root,encoding='utf-8',xml_declaration=True)
            self.send_response(200)
            self.send_header('Content-Type','application/xml; charset=utf-8')
            self.send_header('Cache-Control','public, max-age=300')
            self.send_header('Content-Length',str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if route=='/noto-kufi-arabic-bold.ttf':
            return self.asset('noto-kufi-arabic-bold.ttf','font/ttf')
        if route=='/meta-tracking.js': return self.asset('meta-tracking.js','text/javascript; charset=utf-8')
        if route=='/pending.html': return self.html('pending.html')
        if route=='/success.html': return self.html('success.html')
        detail_match=re.fullmatch(r'/books/(book[1-9][0-9]*)',route)
        if (detail_match and detail_match.group(1) in BOOKS) or route=='/book.html': return self.html('book.html')
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
            config['paymentProvider']=payment_provider()
            config['chargeCurrency']='EGP' if payment_provider()=='kashier' else config['currency']
            return self.respond(200,config)
        if route=='/api/book-stats':
            defaults={book_id:{'rating':None,'reviews':0,'downloads':0,'pageCount':None}
                      for book_id in BOOKS}
            if store.ready():
                try: defaults.update(store.book_stats())
                except Exception: pass
            return self.respond(200,defaults)
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
                                     'metaPurchase':meta_tracking.purchase(order)})
        match=re.fullmatch(r'/api/download/(book[1-9][0-9]*)',route)
        if match:
            return self.respond(405,{'ok':False,'error':'استخدم زر التحميل الآمن من صفحة الطلب.'},{'Allow':'POST'})
        return self.respond(404,{'ok':False,'error':'Not found'})

if __name__=='__main__':
    host=os.environ.get('HOST','127.0.0.1');port=int(os.environ.get('PORT','3000'))
    print(f'TABO BOOKS listening on {host}:{port}',flush=True)
    missing=[name for name,ready in checkout_readiness().items() if not ready]
    print('Checkout readiness: '+('ready' if not missing else 'blocked: '+','.join(missing)),flush=True)
    ThreadingHTTPServer((host,port),Handler).serve_forever()

