"""TABO BOOKS backend. Python 3.11+, pypdf, reportlab, Pillow."""
import io, json, os, re, secrets, hmac, hashlib
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

ROOT = Path(__file__).resolve().parent
BOOKS = {'book1': ('هرمون','book1.pdf'), 'book2': ('نحو القمة','book2.pdf'),
         'book3': ('تمرد','book3.pdf'), 'book4': ('كيف تصنع المليون الأول','book4.pdf'),
         'book5': ('شهوات','book5.pdf'), 'book6': ('دليل السمو','book6.pdf')}
PRICES = {1:150,2:250,3:300,4:350,5:420,6:500,7:550,8:620,9:700}
PAYMOB_ID = 5932821
PAYMOB_BASE = 'https://accept.paymob.com'
FONT = os.environ.get('TABO_ARABIC_FONT', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')

def checkout_ready():
    """Never accept payment unless originals and durable order storage are ready."""
    return (os.environ.get('TABO_CHECKOUT_ENABLED') == '1'
            and os.environ.get('TABO_PERSISTENT_STORAGE_READY') == '1'
            and store.ready()
            and os.environ.get('TABO_ORIGINALS_VERIFIED') == '1'
            and os.environ.get('PAYMOB_SECRET_KEY','').startswith('sk_test_')
            and bool(os.environ.get('PAYMOB_PUBLIC_KEY','').startswith('pk_test_'))
            and bool(os.environ.get('PAYMOB_HMAC_SECRET'))
            and os.environ.get('TABO_PUBLIC_URL','').startswith('https://'))

def now(): return datetime.now(timezone.utc).isoformat()
def public(order): return {k:order[k] for k in ('id','status','items','total','currency','paymentMethod')}
def paymob_checkout(order, customer):
    secret=os.environ.get('PAYMOB_SECRET_KEY','')
    public=os.environ.get('PAYMOB_PUBLIC_KEY','')
    site=os.environ.get('TABO_PUBLIC_URL','').rstrip('/')
    if not secret.startswith('sk_test_') or not public.startswith('pk_test_') or not site.startswith('https://'):
        raise ValueError('Paymob Test keys and public HTTPS URL are not configured')
    first,*rest=customer['name'].strip().split()
    details={key:'NA' for key in ('apartment','floor','street','building','shipping_method','postal_code','city','state')}
    details.update(first_name=first,last_name=' '.join(rest) or first,email=customer['email'],phone_number=customer['phone'],country='EG')
    payload={'amount':order['total']*100,'currency':'EGP','payment_methods':[PAYMOB_ID],
             'items':[{'name':'TABO BOOKS order '+order['id'],'amount':order['total']*100,'quantity':1}],
             'billing_data':details,'special_reference':order['id'],
             'notification_url':site+'/api/paymob/webhook',
             'redirection_url':site+'/pending.html?key='+order['statusKey']}
    request=Request(PAYMOB_BASE+'/v1/intention/',data=json.dumps(payload).encode(),
                    headers={'Authorization':'Token '+secret,'Content-Type':'application/json'},method='POST')
    with urlopen(request,timeout=15) as response: reply=json.load(response)
    client=reply['client_secret']
    return (PAYMOB_BASE+'/unifiedcheckout/?'+urlencode({'publicKey':public,'clientSecret':client}),
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
    out=io.BytesIO(); writer.write(out); return out.getvalue()

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt,*args):
        # Avoid recording buyer details or bearer download tokens in server logs.
        print('%s %s %s' % (self.address_string(), self.command, urlparse(self.path).path))
    def respond(self, code, obj):
        body=json.dumps(obj,ensure_ascii=False).encode()
        self.send_response(code); self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store'); self.send_header('Content-Length',str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def read_json(self):
        size=int(self.headers.get('Content-Length','0'))
        if size<1 or size>131_072: raise ValueError('Invalid request size')
        data=json.loads(self.rfile.read(size))
        if not isinstance(data,dict): raise ValueError('Invalid JSON object')
        return data
    def html(self, filename):
        payload=(ROOT/filename).read_bytes(); self.send_response(200)
        self.send_header('Content-Type','text/html; charset=utf-8')
        self.send_header('Content-Length',str(len(payload))); self.end_headers(); self.wfile.write(payload)
    def do_POST(self):
        route=urlparse(self.path).path
        try:
            data=self.read_json()
            if route=='/api/orders':
                if not checkout_ready():
                    return self.respond(503,{'ok':False,'error':'الشراء غير متاح حاليًا. يرجى العودة قريبًا.'})
                items=data.get('items')
                if not isinstance(items,list) or not 1<=len(items)<=len(BOOKS):
                    return self.respond(400,{'ok':False,'error':'الطلب أو طريقة الدفع غير صالحين.'})
                ids=[x.get('id') if isinstance(x,dict) else None for x in items]
                if any(i not in BOOKS for i in ids) or len(set(ids))!=len(ids):
                    return self.respond(400,{'ok':False,'error':'الكتب غير صالحة أو مكررة.'})
                total=PRICES[len(ids)]
                customer=data.get('customer')
                if not isinstance(customer,dict) or not isinstance(customer.get('name'),str) or not 2<=len(customer['name'].strip())<=100 or not isinstance(customer.get('phone'),str) or not re.fullmatch(r'\+?[0-9]{8,15}',customer['phone']) or not isinstance(customer.get('email'),str) or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',customer['email']):
                    return self.respond(400,{'ok':False,'error':'الاسم والبريد الإلكتروني ورقم الهاتف مطلوبة للدفع.'})
                order={'id':'TABO-'+secrets.token_hex(8).upper(),'status':'PENDING','items':[{'id':i,'name':BOOKS[i][0]} for i in ids],
                       'total':total,'currency':'EGP','paymentMethod':'Payment Link','createdAt':now(),
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
                if not order or obj.get('integration_id')!=PAYMOB_ID or obj.get('currency')!='EGP' or obj.get('amount_cents')!=order['total']*100 or str(order_obj.get('id'))!=str(order.get('paymobOrderId')):
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
        if route=='/': return self.html('index.html')
        if route=='/pending.html': return self.html('pending.html')
        if route=='/success.html': return self.html('success.html')
        if route=='/health': return self.respond(200,{'ok':True})
        if route=='/api/config': return self.respond(200,{'checkoutEnabled':checkout_ready()})
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
        match=re.fullmatch(r'/api/download/(book[1-6])',route)
        if match:
            order=store.find('download_token',token) if store.ready() and re.fullmatch(r'[0-9a-f]{64}',token) else None
            book_id=match.group(1)
            if not order or not order['buyer'] or book_id not in [i['id'] for i in order['items']]:
                return self.respond(403,{'ok':False,'error':'تحميل غير مصرح.'})
            try: payload=watermarked(store.original(BOOKS[book_id][1]),order)
            except Exception: return self.respond(500,{'ok':False,'error':'تعذر تجهيز الكتاب. تواصل مع الدعم.'})
            self.send_response(200);self.send_header('Content-Type','application/pdf')
            self.send_header('Content-Disposition',f'attachment; filename="TABO-{book_id}-{order["id"]}.pdf"')
            self.send_header('Cache-Control','private, no-store');self.send_header('Content-Length',str(len(payload)))
            self.end_headers();return self.wfile.write(payload)
        return self.respond(404,{'ok':False,'error':'Not found'})

if __name__=='__main__':
    host=os.environ.get('HOST','127.0.0.1');port=int(os.environ.get('PORT','3000'))
    print(f'TABO BOOKS listening on {host}:{port}',flush=True)
    ThreadingHTTPServer((host,port),Handler).serve_forever()
