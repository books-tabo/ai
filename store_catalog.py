"""Versioned owner catalog edits, with one immutable snapshot per HTTP request."""
import base64
import copy
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import os
import re
import threading
import time

import media_sandbox
import supabase_store as store

_DOCUMENT = ContextVar('store_catalog_document', default=None)
_REVISION = ContextVar('store_catalog_revision', default=0)
_CACHE = None
_LOCK = threading.Lock()
TEXT_FIELDS = {'name':200,'enName':200,'short':1000,'enShort':1000,'about':12000,'enAbout':12000,
               'adds':4000,'enAdds':4000,'idea':1000,'enIdea':1000}


def enabled():
    return os.environ.get('TABO_STORE_ADMIN_ENABLED') == '1' and store.ready()


def rpc(name, **params):
    return store.request('/rest/v1/rpc/'+name, 'POST', {'p_'+k:v for k,v in params.items()})


def document():
    return _DOCUMENT.get() or {}


def invalidate():
    global _CACHE
    with _LOCK:
        _CACHE = None


def published(with_revision=False):
    global _CACHE
    if not enabled():
        return ({},0) if with_revision else {}
    if not _LOCK.acquire(timeout=.2):
        raise store.StoreUnavailable()
    try:
        if _CACHE and time.monotonic()-_CACHE[0] < 5:
            return (_CACHE[1],_CACHE[3]) if with_revision else _CACHE[1]
        rows = store.request('/rest/v1/store_catalog_state', params={'id':'eq.true','select':'published,reminders_enabled,revision','limit':1})
        if not rows:
            raise store.StoreUnavailable()
        value = validate(rows[0]['published']) if rows else {}
        _CACHE = (time.monotonic(), value, rows[0]['reminders_enabled'] if rows else False,rows[0]['revision'] if rows else 0)
        return (value,_CACHE[3]) if with_revision else value
    finally:
        _LOCK.release()


def reminders_enabled():
    if not enabled():return False
    published()
    return bool(_CACHE and _CACHE[2])


def revision():
    return _REVISION.get()


@contextmanager
def context(value=None):
    value,number=published(with_revision=True) if value is None else (value,0)
    token = _DOCUMENT.set(value)
    version = _REVISION.set(number)
    try:
        yield
    finally:
        _DOCUMENT.reset(token)
        _REVISION.reset(version)


def book_data(base):
    value = document()
    books = {bid:{**base[bid],**value.get('books',{}).get(bid,{})} for bid in value.get('order',list(base))}
    import catalog
    independent = {**catalog.FIXED_PRICES,**value.get('independentPrices',{})}
    for bid, prices in independent.items():
        books[bid] = {**books[bid], 'fixedPrices':prices,'bundleEligible':False}
    return books


def money(value):
    try:
        result = Decimal(str(value))
        if isinstance(value,bool) or not result.is_finite() or not Decimal('0.01') <= result <= 1_000_000 or result != result.quantize(Decimal('.01')):
            raise ValueError()
        return float(result)
    except (InvalidOperation,TypeError):
        raise ValueError('السعر يجب أن يكون موجبًا وبحد أقصى منزلتين عشريتين.') from None


def validate(value):
    import catalog
    if not isinstance(value,dict) or set(value)-{'books','order','independentPrices','offers','prices'}:
        raise ValueError('بيانات الكتالوج غير صالحة.')
    value = copy.deepcopy(value)
    if len(json.dumps(value,ensure_ascii=False).encode()) > 110_000:
        raise ValueError('بيانات الكتالوج أكبر من الحد المسموح.')
    edits = value.get('books',{})
    if not isinstance(edits,dict) or set(edits)-set(catalog.BOOKS):
        raise ValueError('الكتب غير صالحة.')
    for bid, fields in edits.items():
        if not isinstance(fields,dict) or set(fields)-set(TEXT_FIELDS)-{'image','imageWidth','imageHeight'}:
            raise ValueError('يمكن تعديل بيانات العرض فقط؛ ملفات الكتب وروابطها ثابتة.')
        for field, limit in TEXT_FIELDS.items():
            if field in fields and (not isinstance(fields[field],str) or not fields[field].strip() or len(fields[field])>limit or any(ord(c)<32 and c not in '\n\t' for c in fields[field])):
                raise ValueError('راجع النصوص؛ بعض الحقول فارغة أو أطول من المسموح.')
        if set(fields)&{'imageWidth','imageHeight'} and 'image' not in fields:
            raise ValueError('أبعاد الغلاف تُحفظ مع صورة مرفوعة فقط.')
        if 'image' in fields:
            if not re.fullmatch(r'/catalog-covers/[a-f0-9]{64}\.webp',str(fields['image'])):
                raise ValueError('ارفع الغلاف من زر رفع الغلاف.')
            if any(isinstance(fields.get(k),bool) or not isinstance(fields.get(k),int) or not 1<=fields[k]<=1800 for k in ('imageWidth','imageHeight')):
                raise ValueError('أبعاد الغلاف غير صالحة.')
    order = value.get('order',list(catalog.BOOKS))
    if not isinstance(order,list) or len(order)!=len(catalog.BOOKS) or set(order)!=set(catalog.BOOKS):
        raise ValueError('ترتيب الكتب يجب أن يحتوي كل كتاب مرة واحدة.')
    custom = value.get('independentPrices',{})
    if not isinstance(custom,dict) or set(custom)-set(catalog.BOOKS) or set(custom)&set(catalog.FIXED_PRICES):
        raise ValueError('أسعار السيجما وسياسة الخصومات الخاصة به محفوظة.')
    for bid, prices in custom.items():
        if not isinstance(prices,dict) or set(prices)!={'EGP','USD'}:
            raise ValueError('أدخل السعر المستقل بالجنيه والدولار.')
        custom[bid] = {c:money(v) for c,v in prices.items()}
    if 'prices' in value:
        import localization
        if not isinstance(value['prices'],dict) or set(value['prices'])!={'EGP','USD'}:
            raise ValueError('جدول الأسعار غير صالح.')
        for currency, ladder in value['prices'].items():
            if not isinstance(ladder,dict) or set(ladder)!=set(localization.USD_PRICES):
                raise ValueError('أدخل أسعار عدد الكتب من 1 إلى 19.')
            value['prices'][currency] = {str(i):money(ladder[str(i)]) for i in range(1,20)}
            if any(value['prices'][currency][str(i)]<value['prices'][currency][str(i-1)] for i in range(2,20)):
                raise ValueError('إجمالي السعر لا ينقص عند إضافة كتاب.')
    if 'offers' in value:
        offers = value['offers']
        if not isinstance(offers,dict) or set(offers)!=set(catalog.OFFERS):
            raise ValueError('عدّل العروض الموجودة باستخدام معرفاتها الثابتة.')
        for oid, offer in offers.items():
            base = catalog.OFFERS[oid]
            if not isinstance(offer,dict) or set(offer)-{'title','description','books','prices','fixedPriceBundle','active'}:
                raise ValueError('العرض غير صالح.')
            if 'active' in offer and not isinstance(offer['active'],bool):
                raise ValueError('حالة العرض غير صالحة.')
            for field in ('title','description'):
                text = offer.get(field)
                if not isinstance(text,dict) or set(text)!={'ar','en'} or any(not isinstance(v,str) or not v.strip() or len(v)>3000 for v in text.values()):
                    raise ValueError('أدخل اسم العرض ووصفه بالعربية والإنجليزية.')
            ids = offer.get('books')
            if not isinstance(ids,list) or not 2<=len(ids)<=5 or len(set(ids))!=len(ids) or set(ids)-set(catalog.BOOKS):
                raise ValueError('الباقة يجب أن تحتوي من كتابين إلى خمسة كتب مختلفة.')
            if oid=='sigma-couple':
                if offer!=base:
                    raise ValueError('عرض السيجما المعتمد محفوظ بسعره وكتبه وشروطه.')
            elif set(ids)&set(catalog.FIXED_PRICES) or offer.get('fixedPriceBundle'):
                raise ValueError('كتب السيجما مستثناة من الباقات العادية.')
            if not isinstance(offer.get('prices'),dict) or set(offer['prices'])!=set(base['prices']):
                raise ValueError('عملات العرض غير صالحة.')
            offer['prices'] = {c:money(v) for c,v in offer['prices'].items()}
    return value


def auth_args(auth):
    return {'login':auth['id'],'owner':auth['owner'],'device':auth['device'],'session':auth['session']}


def read(auth):
    state = rpc('store_admin_catalog_read',**auth_args(auth))
    if state is None:
        raise PermissionError()
    import seo, catalog, localization
    base = {'books':seo.BOOKS,'offers':catalog.OFFERS,'prices':{'EGP':{str(k):v for k,v in catalog.PRICES.items()},'USD':localization.USD_PRICES},'lockedBooks':list(catalog.FIXED_PRICES)}
    return {**state,'base':base}


def save(auth,data,publish=False):
    version = data.get('draftRevision')
    if isinstance(version,bool) or not isinstance(version,int) or version<0:
        raise ValueError('رقم المسودة غير صالح.')
    if publish:
        state = read(auth)
        doc = validate(state['draft'])
        verify_covers(doc)
        result = rpc('store_admin_catalog_publish',**auth_args(auth),expected=version)
    else:
        doc = validate(data.get('document'))
        verify_covers(doc)
        result = rpc('store_admin_catalog_save',**auth_args(auth),expected=version,document=doc)
    if result is None:
        raise PermissionError()
    if result.get('conflict'):
        raise FileExistsError('تغيرت المسودة في نافذة أخرى. حدّث البيانات قبل المتابعة.')
    if publish:
        invalidate()
    return result


def verify_covers(doc):
    for fields in doc.get('books',{}).values():
        if 'image' in fields:
            digest=fields['image'].rsplit('/',1)[-1][:-5]
            rows=store.request('/rest/v1/store_catalog_covers',params={'digest':'eq.'+digest,'select':'digest,width,height','limit':1})
            if not rows or rows[0]['width']!=fields['imageWidth'] or rows[0]['height']!=fields['imageHeight']:
                raise ValueError('الغلاف غير محفوظ. ارفعه من جديد.')


def upload(data):
    raw=data.get('imageData')
    if not isinstance(raw,str) or len(raw)>2_100_000:
        raise ValueError('الحد الأقصى للغلاف 1.5 ميجابايت.')
    try:
        source=base64.b64decode(raw,validate=True)
        payload,_=media_sandbox.run('image',source,{},input_limit=1_572_864,output_limit=media_sandbox.IMAGE_OUTPUT)
        from PIL import Image
        with Image.open(io.BytesIO(payload)) as image:width,height=image.size
    except (ValueError,OSError):
        raise ValueError('ارفع صورة JPG أو PNG أو WEBP صحيحة وأقل من 1.5 ميجابايت.') from None
    digest=hashlib.sha256(payload).hexdigest()
    store.request('/rest/v1/store_catalog_covers','POST',{'digest':digest,'data_base64':base64.b64encode(payload).decode(),'width':width,'height':height},headers={'Prefer':'resolution=ignore-duplicates,return=minimal'})
    return {'image':'/catalog-covers/'+digest+'.webp','imageWidth':width,'imageHeight':height}


def cover(digest,private=False):
    if not re.fullmatch(r'[a-f0-9]{64}',digest):return None
    path='/catalog-covers/'+digest+'.webp'
    if not private and not any(x.get('image')==path for x in document().get('books',{}).values()):return None
    rows=store.request('/rest/v1/store_catalog_covers',params={'digest':'eq.'+digest,'select':'data_base64','limit':1})
    return base64.b64decode(rows[0]['data_base64'],validate=True) if rows else None
