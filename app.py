"""TABO BOOKS backend. Python 3.11+, pypdf, reportlab, Pillow."""
import io, json, os, re, secrets, hmac, hashlib, ipaddress, threading, time
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

ROOT = Path(__file__).resolve().parent
BOOKS = {'book1': ('هرمون','book1.pdf'), 'book2': ('نحو القمة','book2.pdf'),
         'book3': ('تمرد','book3.pdf'), 'book4': ('كيف تصنع المليون الأول','book4.pdf'),
         'book5': ('شهوات','book5.pdf'), 'book6': ('دليل السمو','book6.pdf'),
         'book7': ('إلى كل بنت','book7.pdf'), 'book8': ('أصنام','book8.pdf'),
         'book9': ('تأثير الكوبرا','book9.pdf'),
         'book10': ('دليل التحرر من العادة السرية','book10.pdf'),
         'book11': ('الأفعى والمنشار','book11.pdf'),
         'book12': ('جسد','book12.pdf'),
         'book13': ('بنت تخطت الثلاثين','book13.pdf'),
         'book14': ('أنت','book14.pdf'),
         'book15': ('شفرة الحياة','book15.pdf'),
         'book16': ('شفرة قلب الأنثى','book16.pdf'),
         'book17': ('عجلة الفئران','book17.pdf')}
PRICES = {1:199,2:299,3:399,4:499,5:599,6:699,7:799,8:899,9:999,10:1099,11:1199,12:1299,13:1399,14:1499,15:1599,16:1699,17:1799}
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
    '/api/download-session': (12, 600),
    '/api/download': (6, 3600),
    '/api/admin/confirm-payment': (10, 600),
    '/api/paymob/webhook': (180, 60),
    '/api/kashier/webhook': (180, 60),

}
GET_LIMITS = {
    '/api/order-status': (120, 600),
    '/api/downloads': (60, 600),
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
    mode=os.environ.get('KASHIER_MODE','live').strip().lower()
    return mode if mode in KASHIER_API_BASES else ''

def valid_kashier_mid(value):
    return bool(re.fullmatch(r'MID-[A-Za-z0-9]+-[A-Za-z0-9]+',str(value or '').strip()))

def valid_kashier_credential(value):
    value=str(value or '').strip()
    return 16 <= len(value) <= 512 and not any(character.isspace() for character in value)

def kashier_currencies():
    raw=os.environ.get('KASHIER_CURRENCIES','EGP')
    currencies={item.strip().upper() for item in raw.split(',')}
    return currencies.intersection({'EGP','USD','GBP','EUR'})

def payment_currency_supported(currency):
    currency=str(currency or '').upper()
    provider=payment_provider()
    if provider=='kashier': return currency in kashier_currencies()
    if provider=='paymob': return localization.payment_method_for(currency) is not None
    return False

def checkout_readiness():
    """Return non-secret readiness checks for safe operational diagnostics."""
    checks={
        'checkout_enabled': os.environ.get('TABO_CHECKOUT_ENABLED') == '1',
        'persistent_storage_confirmed': os.environ.get('TABO_PERSISTENT_STORAGE_READY') == '1',
        'supabase_configured': store.ready(),
        'originals_verified': os.environ.get('TABO_ORIGINALS_VERIFIED') == '1',
        'payment_provider_valid': bool(payment_provider()),
        'public_url_valid': bool(valid_https_base(os.environ.get('TABO_PUBLIC_URL'))),
    }
    if payment_provider()=='kashier':
        checks.update({
            'kashier_mode_valid': bool(kashier_mode()),
            'kashier_merchant_id_valid': valid_kashier_mid(os.environ.get('KASHIER_MERCHANT_ID')),
            'kashier_payment_key_configured': valid_kashier_credential(os.environ.get('KASHIER_PAYMENT_API_KEY')),
            'kashier_secret_key_configured': valid_kashier_credential(os.environ.get('KASHIER_SECRET_KEY')),
            'kashier_currency_configured': bool(kashier_currencies()),
        })
    elif payment_provider()=='paymob':
        checks.update({
            'paymob_secret_test': valid_paymob_test_secret(os.environ.get('PAYMOB_SECRET_KEY')),
            'paymob_public_test': valid_paymob_test_public(os.environ.get('PAYMOB_PUBLIC_KEY')),
            'paymob_hmac_configured': len(os.environ.get('PAYMOB_HMAC_SECRET','')) >= 32,
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
    return ' '.join(str(value or '').split()).casefold()

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
    """Log only a bounded provider error code/message; never credentials or buyer data."""
    status=getattr(exc,'code',None)
    code=''; message=''
    if isinstance(exc,HTTPError):
        try:
            raw=exc.read(8192)
            payload=json.loads(raw.decode('utf-8','replace'))
            if isinstance(payload,dict):
                for key in ('code','errorCode','reasonCode','statusCode'):
                    value=payload.get(key)
                    if isinstance(value,(str,int,float)):
                        code=str(value)[:80];break
                for key in ('message','error','description','reason'):
                    value=payload.get(key)
                    if isinstance(value,str):
                        message=re.sub(r'[^A-Za-z0-9 _.,:;()/-]','?',value)[:240];break
        except Exception:
            pass
    elif isinstance(exc,URLError):
        message='network_error'
    elif isinstance(exc,(ValueError,TimeoutError)):
        message=type(exc).__name__
    print(f'{provider} checkout error status={status or "none"} code={code or "none"} message={message or "none"}',flush=True)

def kashier_allowed_methods():
    """Allow only hosted-checkout methods documented by Kashier."""
    raw=os.environ.get('KASHIER_ALLOWED_METHODS','card,wallet').strip().lower()
    methods=[]
    for item in raw.split(','):
        item=item.strip()
        if re.fullmatch(r'(?:card|wallet|bank_installments|bnpl)(?:\[[a-z0-9_-]+\])?',item):
            methods.append(item)
    return ','.join(dict.fromkeys(methods)) or 'card,wallet'

def kashier_checkout(order, customer):
    mode=kashier_mode()
    merchant=os.environ.get('KASHIER_MERCHANT_ID','').strip()
    payment_key=os.environ.get('KASHIER_PAYMENT_API_KEY','').strip()
    secret=os.environ.get('KASHIER_SECRET_KEY','').strip()
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
    if parsed.scheme!='https' or parsed.hostname!='payments.kashier.io' or not parsed.path.startswith('/session/'):
        raise ValueError('Invalid Kashier payment destination')
    return payment_url,session_id

def payment_checkout(order,customer):
    if payment_provider()=='kashier': return kashier_checkout(order,customer)
    if payment_provider()=='paymob': return paymob_checkout(order,customer)
    raise ValueError('Payment provider is not configured')
def paymob_checkout(order, customer):
    secret=os.environ.get('PAYMOB_SECRET_KEY','')
    public=os.environ.get('PAYMOB_PUBLIC_KEY','')
    site=valid_https_base(os.environ.get('TABO_PUBLIC_URL'))
    if not valid_paymob_test_secret(secret) or not valid_paymob_test_public(public) or not site:
        raise ValueError('Paymob Test keys and public HTTPS URL are not configured')
    first,*rest=customer['name'].strip().split()
    details={key:'NA' for key in ('apartment','floor','street','building','shipping_method','postal_code','city','state')}
    method_id=localization.payment_method_for(order['currency'])
    if not method_id:
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
    secret=os.environ.get('PAYMOB_HMAC_SECRET','')
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
    payment_key=os.environ.get('KASHIER_PAYMENT_API_KEY','').strip()
    if not payment_key or not isinstance(data,dict) or not re.fullmatch(r'[0-9a-fA-F]{64}',str(signature or '')):
        return False
    keys=data.get('signatureKeys')
    if (not isinstance(keys,list) or not 1<=len(keys)<=64 or len(keys)!=len(set(keys)) or
            any(not isinstance(key,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}',key) for key in keys)):
        return False
    try:
        payload='&'.join(key+'='+quote(kashier_signature_value(data[key]),safe='')
                         for key in sorted(keys))
    except (KeyError,ValueError,TypeError,InvalidOperation):
        return False
    expected=hmac.new(payment_key.encode(),payload.encode(),hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected,str(signature).lower())

def kashier_reference(data):
    for key in ('merchantOrderId','orderReference'):
        value=data.get(key)
        if isinstance(value,str) and re.fullmatch(r'TABO-[A-F0-9]{16}',value):
            return value
    return None

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

def watermarked(src, order):
    reader=PdfReader(io.BytesIO(src) if isinstance(src, bytes) else str(src)); writer=PdfWriter()
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
    out=io.BytesIO(); writer.write(out); return out.getvalue(),len(reader.pages)

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
            self.send_header('Content-Security-Policy',
                "default-src 'self'; script-src 'nonce-"+nonce+"'; "
                "style-src 'self' 'unsafe-inline'; img-src 'self' https://i.ibb.co data:; "
                "connect-src 'self'; font-src 'self' data:; object-src 'none'; base-uri 'none'; "
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
        candidates=[]
        for name in ('CF-Connecting-IP','True-Client-IP'):
            if self.headers.get(name): candidates.append(self.headers[name].strip())
        if self.headers.get('X-Forwarded-For'):
            candidates.append(self.headers['X-Forwarded-For'].split(',',1)[0].strip())
        candidates.append(self.client_address[0])
        for candidate in candidates:
            try: return ipaddress.ip_address(candidate).compressed
            except ValueError: continue
        return 'unknown'

    def cookie(self, name):
        try:
            parsed=SimpleCookie(); parsed.load(self.headers.get('Cookie',''))
            morsel=parsed.get(name)
            return morsel.value if morsel else None
        except CookieError:
            return None

    def device_hash(self):
        """Bind a download session to the browser without storing its user agent."""
        secret=os.environ.get('SUPABASE_SECRET_KEY','')
        user_agent=self.headers.get('User-Agent','')[:512]
        if not secret or not user_agent: return None
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
        if self.headers.get('CF-Ray') and self.headers.get('CF-Connecting-IP'):
            candidate=self.headers['CF-Connecting-IP'].strip()
            try: return ipaddress.ip_address(candidate).compressed
            except ValueError: pass
        try: return ipaddress.ip_address(self.client_address[0]).compressed
        except ValueError: return 'unknown'

    def pricing_country(self, requested=None):
        selected=localization.normalize_country(requested)
        if selected!='EG': return selected
        edge_country=localization.country_from_proxy_headers(self.headers)
        if edge_country:
            return localization.pricing_country(selected,edge_country)
        verified_country=localization.country_from_ip(self.geo_client_ip())
        return localization.pricing_country(selected,verified_country)

    def enforce_rate_limit(self, route, method):
        lookup=POST_LIMITS if method=='POST' else GET_LIMITS
        bucket=route
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
    def html(self, filename):
        nonce=secrets.token_urlsafe(24)
        source=(ROOT/filename).read_text(encoding='utf-8')
        source=re.sub(r'<(script|style)(\b[^>]*)>',
                      lambda match:'<'+match.group(1)+' nonce="'+nonce+'"'+match.group(2)+'>',source)
        payload=source.encode('utf-8'); self._csp_nonce=nonce; self.send_response(200)
        self.send_header('Content-Type','text/html; charset=utf-8')
        self.send_header('Cache-Control','no-store')
        if filename in ('pending.html','success.html'):
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

            if (route in ('/api/ratings','/api/orders','/api/download-session') or
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
            if route=='/api/orders':
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
                total=price_config['prices'][str(len(ids))]
                currency=price_config['currency']
                if not payment_currency_supported(currency):
                    return self.respond(503,{'ok':False,'error':'الدفع بهذه العملة غير متاح حاليًا.'})
                paymob_method_id=(localization.payment_method_for(currency)
                                  if payment_provider()=='paymob' else None)
                customer=data.get('customer')
                if not isinstance(customer,dict) or not isinstance(customer.get('name'),str) or not 2<=len(customer['name'].strip())<=100 or not isinstance(customer.get('phone'),str) or not re.fullmatch(r'\+?[0-9]{8,15}',customer['phone']) or not isinstance(customer.get('email'),str) or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',customer['email']):
                    return self.respond(400,{'ok':False,'error':'الاسم والبريد الإلكتروني ورقم الهاتف مطلوبة للدفع.'})
                checkout_secret=secrets.token_urlsafe(32)
                order={'id':'TABO-'+secrets.token_hex(8).upper(),'status':'PENDING','items':[{'id':i,'name':BOOKS[i][0]} for i in ids],
                       'total':total,'currency':currency,'country':country,
                       'paymobMethodId':paymob_method_id,
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
                store.patch(order['id'],{'paymob_order_id':provider_order_id},{'status':'eq.PENDING'})
                cookie=secure_cookie(checkout_cookie_name(order['id']),checkout_secret,3600)
                return self.respond(201,{'ok':True,'order':public(order),'payment_url':link,
                                         'tracking_url':'/pending.html?key='+order['statusKey']},
                                    {'Set-Cookie':cookie})
            if route=='/api/kashier/webhook':
                event=data.get('event'); transaction_data=data.get('data')
                signature=self.headers.get('x-kashier-signature','')
                if not isinstance(transaction_data,dict) or not verify_kashier(transaction_data,signature):
                    return self.respond(401,{'ok':False,'error':'Invalid signature'})
                if event!='pay' or transaction_data.get('status')!='SUCCESS':
                    return self.respond(200,{'ok':True,'paid':False})
                reference=kashier_reference(transaction_data)
                transaction=str(transaction_data.get('transactionId',''))
                if not reference or not re.fullmatch(r'[A-Za-z0-9_.:-]{6,128}',transaction):
                    return self.respond(200,{'ok':True,'paid':False})
                order=store.find('id',reference) if store.ready() else None
                if not order or not kashier_payment_matches(order,transaction_data):
                    return self.respond(200,{'ok':True,'paid':False})
                if order['status']=='PAID':
                    if order['transactionId']==transaction:
                        return self.respond(200,{'ok':True,'paid':True})
                    return self.respond(409,{'ok':False,'error':'Order already paid'})
                if order['status']!='PENDING':
                    return self.respond(200,{'ok':True,'paid':False})
                try:
                    paid=store.patch(reference,{'status':'PAID','transaction_id':transaction,
                               'paid_at':now()},
                               {'status':'eq.PENDING'})
                except RuntimeError:
                    return self.respond(409,{'ok':False,'error':'Duplicate transaction'})
                if not paid:
                    latest=store.find('id',reference)
                    if latest and latest['status']=='PAID' and latest['transactionId']==transaction:
                        return self.respond(200,{'ok':True,'paid':True})
                    return self.respond(409,{'ok':False,'error':'Order already processed'})
                return self.respond(200,{'ok':True,'paid':True})
            if route=='/api/paymob/webhook':
                signature=parse_qs(urlparse(self.path).query).get('hmac',[''])[0]
                obj=data.get('obj')
                if data.get('type')!='TRANSACTION' or not isinstance(obj,dict) or not verify_paymob(obj,signature):
                    return self.respond(401,{'ok':False,'error':'Invalid signature'})
                if obj.get('success') is not True or obj.get('pending') is not False or obj.get('is_refunded') is True or obj.get('is_voided') is True:
                    return self.respond(200,{'ok':True,'paid':False})
                order_obj=obj.get('order') or {}
                reference=order_obj.get('merchant_order_id')
                transaction=str(obj.get('id',''))
                if not isinstance(reference,str) or not transaction:
                    return self.respond(200,{'ok':True,'paid':False})
                order=store.find('id',reference) if store.ready() and re.fullmatch(r'TABO-[A-F0-9]{16}',reference) else None
                expected_amount=localization.amount_to_minor(order['total'],order['currency']) if order else None
                if not order or obj.get('integration_id')!=order.get('paymobMethodId') or obj.get('currency')!=order['currency'] or obj.get('amount_cents')!=expected_amount or str(order_obj.get('id'))!=str(order.get('paymobOrderId')):
                    return self.respond(200,{'ok':True,'paid':False})
                if order['status']=='PENDING':
                    try:
                        paid=store.patch(reference,{'status':'PAID','transaction_id':transaction,
                                   'paid_at':now()},
                                   {'status':'eq.PENDING'})
                    except RuntimeError:
                        return self.respond(409,{'ok':False,'error':'Duplicate transaction'})
                    if not paid and store.find('id',reference)['transactionId']!=transaction:
                        return self.respond(409,{'ok':False,'error':'Order already paid'})
                return self.respond(200,{'ok':True,'paid':True})
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
                    except RuntimeError: return self.respond(409,{'ok':False,'error':'Transaction already used'})
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
                    if expiry and expiry<=datetime.now(timezone.utc):
                        return self.respond(403,{'ok':False,'error':'رابط التفعيل انتهت صلاحيته.'})
                    name=data.get('name'); phone=data.get('phone')
                    buyer=order.get('buyer')
                    if (not buyer or not isinstance(name,str) or not isinstance(phone,str) or
                            not hmac.compare_digest(normalized_name(name),normalized_name(buyer.get('name'))) or
                            not hmac.compare_digest(phone.strip(),str(buyer.get('phone') or '').strip())):
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
                try:
                    usage=store.download_usage(order_id).get(book_id,0)
                except Exception:
                    return self.respond(503,{'ok':False,'error':'تعذر التحقق من حد التحميل الآن.'})
                if usage>=DOWNLOAD_LIMIT:
                    return self.respond(403,{'ok':False,'error':'تم استهلاك محاولتي التحميل لهذا الكتاب.'})
                try: payload,page_count=watermarked(store.original(BOOKS[book_id][1]),order)
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
        except (ValueError,json.JSONDecodeError,TypeError) as e:
            return self.respond(400,{'ok':False,'error':str(e)})
        except Exception:
            self.log_error('Internal error');return self.respond(500,{'ok':False,'error':'Server error'})
    def do_GET(self):
        parsed=urlparse(self.path); route=parsed.path; token=parse_qs(parsed.query).get('token',[''])[0]
        if not self.enforce_rate_limit(route,'GET'): return
        if route=='/': return self.html('index.html')
        if route=='/pending.html': return self.html('pending.html')
        if route=='/success.html': return self.html('success.html')
        detail_match=re.fullmatch(r'/books/(book[1-9][0-9]*)',route)
        if (detail_match and detail_match.group(1) in BOOKS) or route=='/book.html': return self.html('book.html')
        cover_match=re.fullmatch(r'/(book[1-9][0-9]*)-cover\.jpg',route)
        if cover_match and cover_match.group(1) in BOOKS and (ROOT/(cover_match.group(1)+'-cover.jpg')).is_file():
            return self.asset(cover_match.group(1)+'-cover.jpg','image/jpeg')
        if route=='/tabo-books-logo.webp' and (ROOT/'tabo-books-logo.webp').is_file():
            return self.asset('tabo-books-logo.webp','image/webp')
        if route in ('/about','/contact','/privacy','/refund','/delivery'): return self.html('info.html')
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
              'ready':order['status']=='PAID'})
        if route=='/api/downloads':
            order_id=parse_qs(parsed.query).get('order',[''])[0]
            order=self.authorized_download_order(order_id)
            if not order: return self.respond(403,{'ok':False,'error':'هذه الصفحة مرتبطة بجهاز المشتري ولا تعمل من رابط مشترك.'})
            try: usage=store.download_usage(order_id)
            except Exception: return self.respond(503,{'ok':False,'error':'تعذر قراءة حد التحميل الآن.'})
            books=[]
            for item in order['items']:
                used=max(0,min(DOWNLOAD_LIMIT,int(usage.get(item['id'],0))))
                books.append({'id':item['id'],'name':item['name'],'remaining':DOWNLOAD_LIMIT-used})
            return self.respond(200,{'ok':True,'downloadLimit':DOWNLOAD_LIMIT,'books':books})
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
