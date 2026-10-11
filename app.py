"""TABO BOOKS backend. Python 3.11+, pypdf, reportlab, Pillow."""
import io, json, os, re, secrets, hmac, hashlib, ipaddress, threading, time
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote, urlencode
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
         'book9': ('تأثير الكوبرا','book9.pdf')}
PRICES = {1:199,2:299,3:399,4:499,5:599,6:699,7:799,8:899,9:999}
PAYMOB_BASE = 'https://accept.paymob.com'
FONT = os.environ.get('TABO_ARABIC_FONT', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')


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
    '/api/buyer': (12, 600),
    '/api/admin/confirm-payment': (10, 600),
    '/api/paymob/webhook': (180, 60),
    '/api/private/upload-book': (3, 3600),
}
GET_LIMITS = {
    '/api/order-status': (120, 600),
    '/api/downloads': (60, 600),
    '/api/download': (12, 3600),
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

def checkout_readiness():
    """Return non-secret readiness checks for safe operational diagnostics."""
    return {
        'checkout_enabled': os.environ.get('TABO_CHECKOUT_ENABLED') == '1',
        'persistent_storage_confirmed': os.environ.get('TABO_PERSISTENT_STORAGE_READY') == '1',
        'supabase_configured': store.ready(),
        'originals_verified': os.environ.get('TABO_ORIGINALS_VERIFIED') == '1',
        'paymob_secret_test': valid_paymob_test_secret(os.environ.get('PAYMOB_SECRET_KEY')),
        'paymob_public_test': valid_paymob_test_public(os.environ.get('PAYMOB_PUBLIC_KEY')),
        'paymob_hmac_configured': len(os.environ.get('PAYMOB_HMAC_SECRET','')) >= 32,
        'public_url_valid': bool(valid_https_base(os.environ.get('TABO_PUBLIC_URL'))),
    }

def checkout_ready():
    """Never accept payment unless originals and durable order storage are ready."""
    return all(checkout_readiness().values())

def now(): return datetime.now(timezone.utc).isoformat()
def public(order):
    keys=('id','status','items','total','currency','paymentMethod','country')
    return {k:order[k] for k in keys if k in order}
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
        for name,value in (extra_headers or {}).items(): self.send_header(name,value)
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
        if edge_country and edge_country!='EG':
            return localization.pricing_country(selected,edge_country)
        verified_country=localization.country_from_ip(self.geo_client_ip())
        return localization.pricing_country(selected,verified_country)

    def enforce_rate_limit(self, route, method):
        lookup=POST_LIMITS if method=='POST' else GET_LIMITS
        bucket=route
        if method=='GET' and re.fullmatch(r'/api/download/book[1-9]',route):
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
            if route=='/api/private/upload-book':
                supplied=self.headers.get('X-Upload-Token','')
                supplied_digest=hashlib.sha256(supplied.encode()).hexdigest()
                if not hmac.compare_digest('60fd3e37cf5376e0e3cc30fa1c259285f2aea93be2c22e18f97460bb1655d52c',supplied_digest):
                    return self.respond(403,{'ok':False,'error':'Unauthorized'})
                if self.headers.get_content_type()!='application/pdf':
                    return self.respond(415,{'ok':False,'error':'PDF required'})
                length=int(self.headers.get('Content-Length','0'))
                if not 1<=length<=20*1024*1024:
                    return self.respond(413,{'ok':False,'error':'Invalid file size'})
                payload=self.rfile.read(length)
                if len(payload)!=length or not payload.startswith(b'%PDF-'):
                    return self.respond(400,{'ok':False,'error':'Invalid PDF'})
                if not store.ready():
                    return self.respond(503,{'ok':False,'error':'Storage unavailable'})
                store.upload_original('book9.pdf',payload)
                return self.respond(201,{'ok':True,'name':'book9.pdf','size':len(payload)})
            if route in ('/api/ratings','/api/orders','/api/buyer') and not self.same_origin_browser_request():
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
                paymob_method_id=localization.payment_method_for(currency)
                if not paymob_method_id:
                    return self.respond(503,{'ok':False,'error':'الدفع بهذه العملة سيُتاح بعد تفعيلها من Paymob.'})
                customer=data.get('customer')
                if not isinstance(customer,dict) or not isinstance(customer.get('name'),str) or not 2<=len(customer['name'].strip())<=100 or not isinstance(customer.get('phone'),str) or not re.fullmatch(r'\+?[0-9]{8,15}',customer['phone']) or not isinstance(customer.get('email'),str) or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',customer['email']):
                    return self.respond(400,{'ok':False,'error':'الاسم والبريد الإلكتروني ورقم الهاتف مطلوبة للدفع.'})
                order={'id':'TABO-'+secrets.token_hex(8).upper(),'status':'PENDING','items':[{'id':i,'name':BOOKS[i][0]} for i in ids],
                       'total':total,'currency':currency,'country':country,
                       'paymobMethodId':paymob_method_id,'paymentMethod':'Payment Link','createdAt':now(),
                       'token':None,'buyer':None,'transactionId':None,'statusKey':secrets.token_hex(32)}
                store.insert(order)
                try: link,paymob_id=paymob_checkout(order,customer)
                except Exception:
                    return self.respond(503,{'ok':False,'error':'تعذر إنشاء صفحة الدفع التجريبية. تحقق من إعدادات Paymob.'})
                store.patch(order['id'],{'paymob_order_id':paymob_id},{'status':'eq.PENDING'})
                return self.respond(201,{'ok':True,'order':public(order),'payment_url':link,
                                         'tracking_url':'/pending.html?key='+order['statusKey']})
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
                                   'paid_at':now(),'download_token':secrets.token_hex(32)},
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
                                    'paid_at':now(),'download_token':secrets.token_hex(32)},
                                    {'status':'eq.PENDING'})
                    except RuntimeError: return self.respond(409,{'ok':False,'error':'Transaction already used'})
                    if not order: return self.respond(409,{'ok':False,'error':'Order already paid'})
                return self.respond(200,{'ok':True,'order_id':oid,'success_url':'/success.html?token='+order['token']})
            if route=='/api/buyer':
                token=data.get('token',''); name=data.get('name',''); phone=data.get('phone','')
                if not isinstance(name,str) or not 2<=len(name.strip())<=100 or any(ord(c)<32 for c in name):
                    return self.respond(400,{'ok':False,'error':'اكتب الاسم الصحيح (من حرفين إلى 100).'})
                if not isinstance(phone,str) or not re.fullmatch(r'\+?[0-9]{8,15}',phone):
                    return self.respond(400,{'ok':False,'error':'اكتب رقم هاتف صحيح بالأرقام الإنجليزية.'})
                order=store.find('download_token',token) if store.ready() and isinstance(token,str) and re.fullmatch(r'[0-9a-f]{64}',token) else None
                if not order or order['status']!='PAID': return self.respond(403,{'ok':False,'error':'رابط غير صالح.'})
                if order['buyer'] and order['buyer']!={'name':name.strip(),'phone':phone}:
                    return self.respond(409,{'ok':False,'error':'تم تسجيل بيانات المشتري مسبقًا. تواصل مع الدعم لتصحيحها.'})
                if not order['buyer']:
                    updated=store.patch(order['id'],{'buyer':{'name':name.strip(),'phone':phone}},
                                        {'status':'eq.PAID','buyer':'is.null'})
                    if not updated and store.find('id',order['id'])['buyer']!={'name':name.strip(),'phone':phone}:
                        return self.respond(409,{'ok':False,'error':'تم تسجيل بيانات المشتري مسبقًا.'})
                return self.respond(200,{'ok':True})
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
        if re.fullmatch(r'/books/book[1-9]',route) or route=='/book.html': return self.html('book.html')
        if route=='/book7-cover.jpg': return self.asset('book7-cover.jpg','image/jpeg')
        if route=='/book8-cover.jpg': return self.asset('book8-cover.jpg','image/jpeg')
        if route=='/book9-cover.jpg': return self.asset('book9-cover.jpg','image/jpeg')
        if route in ('/about','/contact','/privacy','/refund','/delivery'): return self.html('info.html')
        if route=='/health': return self.respond(200,{'ok':True})
        if route=='/api/config': return self.respond(200,{'checkoutEnabled':checkout_ready()})
        if route=='/api/store-config':
            requested=parse_qs(parsed.query).get('country',[''])[0]
            country=self.pricing_country(requested)
            config=localization.pricing_for_country(PRICES,country)
            config['checkoutEnabled']=checkout_ready()
            config['checkoutSupported']=localization.payment_method_for(config['currency']) is not None
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
              'success_url':('/success.html?token='+order['token']) if order['status']=='PAID' else None})
        if route=='/api/downloads':
            order=store.find('download_token',token) if store.ready() and re.fullmatch(r'[0-9a-f]{64}',token) else None
            if order and order['status']!='PAID': order=None
            if not order: return self.respond(403,{'ok':False,'error':'رابط غير صالح أو الدفع غير مؤكد.'})
            return self.respond(200,{'ok':True,'needsBuyer':not bool(order['buyer']),
              'books':([{'id':i['id'],'name':i['name'],'url':'/api/download/'+i['id']+'?token='+quote(token)} for i in order['items']] if order['buyer'] else [])})
        match=re.fullmatch(r'/api/download/(book[1-9])',route)
        if match:
            order=store.find('download_token',token) if store.ready() and re.fullmatch(r'[0-9a-f]{64}',token) else None
            book_id=match.group(1)
            if not order or not order['buyer'] or book_id not in [i['id'] for i in order['items']]:
                return self.respond(403,{'ok':False,'error':'تحميل غير مصرح.'})
            try: payload,page_count=watermarked(store.original(BOOKS[book_id][1]),order)
            except Exception: return self.respond(500,{'ok':False,'error':'تعذر تجهيز الكتاب. تواصل مع الدعم.'})
            try: store.record_download(book_id,page_count)
            except Exception: pass
            self.send_response(200);self.send_header('Content-Type','application/pdf')
            self.send_header('Content-Disposition',f'attachment; filename="TABO-{book_id}-{order["id"]}.pdf"')
            self.send_header('Cache-Control','private, no-store');self.send_header('X-Robots-Tag','noindex, noarchive')
            self.send_header('Content-Length',str(len(payload)))
            self.end_headers();return self.wfile.write(payload)
        return self.respond(404,{'ok':False,'error':'Not found'})

if __name__=='__main__':
    host=os.environ.get('HOST','127.0.0.1');port=int(os.environ.get('PORT','3000'))
    print(f'TABO BOOKS listening on {host}:{port}',flush=True)
    missing=[name for name,ready in checkout_readiness().items() if not ready]
    print('Checkout readiness: '+('ready' if not missing else 'blocked: '+','.join(missing)),flush=True)
    ThreadingHTTPServer((host,port),Handler).serve_forever()
