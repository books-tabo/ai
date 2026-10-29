"""Owner-only reports and minimized first-party session counts; no payment mutations."""
import hashlib
import hmac
import os
import re
import secrets
import threading
from datetime import datetime,timedelta,timezone
from urllib.parse import urlparse
import order_receipts
import supabase_store as store
import catalog
import localization

COOKIE='__Host-tabo_dashboard'
INSIGHT_COOKIE='__Host-tabo_insight'
HEX=re.compile(r'[a-f0-9]{64}')
PUBLIC_PATH=re.compile(r'/(?:books/book[1-9][0-9]*|checkout|offers(?:/[a-z0-9-]+)?|categories(?:/[a-z0-9-]+)?|authors(?:/[a-z0-9-]+)?|about|contact|privacy|refund|delivery)?')
TZ=timezone(timedelta(hours=3))


def enabled():
    return (os.environ.get('TABO_DASHBOARD_ENABLED')=='1' and os.environ.get('TABO_DASHBOARD_SCHEMA_READY')=='1'
        and order_receipts.enabled() and store.ready() and len(os.environ.get('TABO_SESSION_BINDING_KEY',''))>=32
        and bool(re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+',os.environ.get('TABO_DASHBOARD_ADMIN_EMAIL',''))))


def digest(kind,value):
    return hmac.new(os.environ['TABO_SESSION_BINDING_KEY'].encode(),('owner-dashboard-v1|'+kind+'|'+str(value)).encode(),hashlib.sha256).hexdigest()


def owner():
    return os.environ.get('TABO_DASHBOARD_ADMIN_EMAIL','').strip().casefold()


def rpc(name,**params):
    return store.request('/rest/v1/rpc/'+name,'POST',{'p_'+k:v for k,v in params.items()})


def credentials(cookie,device):
    parts=cookie.split('.') if isinstance(cookie,str) else []
    if not device or len(parts)!=2 or not all(HEX.fullmatch(p) for p in parts):return None
    return {'id':parts[0],'owner':digest('owner',owner()),'device':device,'session':digest('session',parts[1])}


def login_request(data,client,device):
    challenge=secrets.token_hex(32)
    address=data.get('email')
    if not device or not isinstance(address,str) or not hmac.compare_digest(address.strip().casefold().encode(),owner().encode()):return challenge
    code=str(secrets.randbelow(1_000_000)).zfill(6)
    accepted=rpc('owner_login_request',id=challenge,owner=digest('owner',owner()),request=digest('client',client),device=device,code=digest('code',challenge+'|'+code))
    if accepted is True:
        text='رمز الدخول إلى لوحة إدارة TABO BOOKS: '+code+'\nصالح لمدة 10 دقائق. لا تشاركه مع أي شخص. إذا لم تطلب الدخول فتجاهل الرسالة.'
        payload={'from':'TABO BOOKS <'+os.environ['TABO_RECEIPT_FROM']+'>','to':[owner()],
                 'reply_to':'taponoor@gmail.com','subject':'رمز دخول لوحة إدارة TABO BOOKS','text':text,
                 'html':'<!doctype html><html lang="ar" dir="rtl"><body><h2>TABO BOOKS</h2><p>رمز دخول لوحة الإدارة</p><p style="font-size:32px;letter-spacing:6px">'+code+'</p><p>صالح لمدة 10 دقائق. لا تشاركه مع أي شخص.</p></body></html>'}
        try:order_receipts.send(payload,'owner-login-'+challenge)
        except order_receipts.DeliveryError:pass  # Same generic response; never log addresses/codes.
    return challenge


def login_verify(data,device):
    cid,code=data.get('challenge'),data.get('code')
    if not device or not isinstance(cid,str) or not HEX.fullmatch(cid):return None
    if isinstance(code,str):code=code.translate(str.maketrans('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹','01234567890123456789'))
    if not isinstance(code,str) or not re.fullmatch(r'[0-9]{6}',code):code='invalid'
    token=secrets.token_hex(32)
    if rpc('owner_login_verify',id=cid,owner=digest('owner',owner()),device=device,code=digest('code',cid+'|'+code),session=digest('session',token)) is True:
        return cid+'.'+token
    return None


def authenticated(cookie,device):
    value=credentials(cookie,device)
    return value if value and rpc('owner_session_valid',**value) is True else None


def logout(cookie,device):
    value=credentials(cookie,device)
    if value:rpc('owner_session_logout',**value)


def dates(params):
    today=datetime.now(TZ).date()
    start=datetime.fromisoformat(params.get('from',[(today-timedelta(days=29)).isoformat()])[0]).date()
    end=datetime.fromisoformat(params.get('to',[today.isoformat()])[0]).date()
    if end<start or (end-start).days>92 or end>today:raise ValueError('Invalid dates')
    return datetime.combine(start,datetime.min.time(),TZ).isoformat(),datetime.combine(end+timedelta(days=1),datetime.min.time(),TZ).isoformat()


def report(cookie,device,params):
    auth=credentials(cookie,device)
    if not auth:return None
    begin,end=dates(params)
    country=params.get('country',[''])[0] or None
    status=params.get('status',[''])[0] or None
    query=params.get('query',[''])[0].strip().upper() or None
    raw=params.get('offset',['0'])[0]
    if (country is not None and country not in localization.COUNTRIES or status is not None and status not in ('PAID','PENDING','REFUNDED','VOIDED','REVERSED','REVIEW')
        or query is not None and not re.fullmatch(r'TABO-[A-F0-9]{16}',query) or not re.fullmatch(r'[0-9]{1,5}',raw) or int(raw)>10000):raise ValueError('Invalid filters')
    args={'login':auth.pop('id'),**auth,'from':begin,'to':end,'country':country,'status':status,'query':query,'offset':int(raw)}
    result=rpc('owner_dashboard_report',**args)
    if result is None:return None
    if not isinstance(result,dict):raise store.StoreUnavailable()
    result['countrySales']=result.pop('countries',[])
    return {**result,'range':{'from':begin,'to':end},'timezone':'Asia/Kuwait','updatedAt':datetime.now(timezone.utc).isoformat(),
            'trackingSince':os.environ.get('TABO_INSIGHTS_SINCE'),'catalog':{bid:value[0] for bid,value in catalog.BOOKS.items()},
            'countries':{k:v['ar'] for k,v in localization.COUNTRIES.items()},'adminEmail':owner()}


def source(data):
    # Fixed categories only. Never retain referrer paths, query values or ad click IDs.
    if data.get('googleClick') is True:return 'google','paid'
    if data.get('snapClick') is True:return 'snapchat','paid'
    value=str(data.get('source','')).lower()[:40]
    medium=str(data.get('medium','')).lower()[:20]
    if data.get('metaClick') is True:return 'meta',('paid' if medium in ('cpc','ppc','paid','paid_social','ads') else 'unknown')
    groups={'google':'google','bing':'bing','facebook':'meta','fb':'meta','instagram':'meta','ig':'meta','meta':'meta','snapchat':'snapchat','snap':'snapchat'}
    if value in groups:return groups[value],('paid' if medium in ('cpc','ppc','paid','paid_social','paid_search','ads') else 'organic' if medium=='organic' else 'unknown')
    try:host=(urlparse(str(data.get('referrer',''))[:500]).hostname or '').lower()
    except ValueError:host=''
    if host in ('tabobooks.com','www.tabobooks.com',''):return 'direct','direct'
    if host=='google.com' or host.endswith('.google.com') or host.startswith('www.google.'):return 'google','organic'
    if host=='bing.com' or host.endswith('.bing.com'):return 'bing','organic'
    if any(host==h or host.endswith('.'+h) for h in ('facebook.com','instagram.com','fb.com')):return 'meta','referral'
    return 'other','referral'


def insight_start(data,cookie,device,client,country):
    path=data.get('path')
    if not device or not isinstance(path,str) or not PUBLIC_PATH.fullmatch(path):raise ValueError('Invalid page')
    if path.startswith('/books/') and path.split('/')[-1] not in catalog.BOOKS:raise ValueError('Invalid book')
    old=cookie if isinstance(cookie,str) and HEX.fullmatch(cookie) else None
    token=secrets.token_hex(32);src,medium=source(data)
    result=rpc('owner_insight_start',old=digest('insight',old) if old else None,new=digest('insight',token),device=device,
        request=digest('client',client),source=src,medium=medium,country=country,path=path)
    if not result:return None
    return (old if result['resumed'] else token),min(1800,max(1,int(result['ttl'])))


def insight_event(data,cookie,device):
    step={'ViewContent':'product','AddToCart':'cart','InitiateCheckout':'checkout'}.get(data.get('event'))
    if not step or not isinstance(cookie,str) or not HEX.fullmatch(cookie) or not device:return False
    return rpc('owner_insight_event',id=digest('insight',cookie),device=device,step=step)


def link_order(cookie,device,oid):
    if enabled() and isinstance(cookie,str) and HEX.fullmatch(cookie) and device:
        rpc('owner_insight_order',id=digest('insight',cookie),device=device,order=oid)


def start_pruner():
    if not enabled():return
    def work():
        while True:
            try:rpc('owner_dashboard_prune')
            except Exception:pass
            threading.Event().wait(3600)
    threading.Thread(target=work,daemon=True,name='owner-dashboard-maintenance').start()


_LINK_SLOTS=threading.BoundedSemaphore(2)
def link_order_async(cookie,device,oid):
    if not enabled() or not _LINK_SLOTS.acquire(blocking=False):return
    def run():
        try:link_order(cookie,device,oid)
        except Exception:pass
        finally:_LINK_SLOTS.release()
    threading.Thread(target=run,daemon=True,name='owner-order-source').start()
