"""Synthetic HTTP fixtures. MemoryStore is for HTTP tests, NOT evidence of SQL atomicity."""
import copy
import hashlib
import hmac
import json
import os
import threading
import unittest
from datetime import datetime,timedelta,timezone
from urllib.request import Request,urlopen,build_opener,ProxyHandler
from urllib.error import HTTPError
from unittest.mock import patch
import app

ENV={'TABO_CHECKOUT_ENABLED':'0','SUPABASE_URL':'https://synthetic.invalid',
 'SUPABASE_SECRET_KEY':'sb_secret_synthetic_not_a_real_key',
 'TABO_SESSION_BINDING_KEY':'synthetic-session-binding-key-32-bytes',
 'TABO_PAYMENT_PROVIDER':'kashier','KASHIER_MODE':'test','PAYMOB_MODE':'test',
 'KASHIER_PAYMENT_API_KEY':'synthetic-payment-api-key','KASHIER_SECRET_KEY':'synthetic-secret-key',
 'KASHIER_MERCHANT_ID':'MID-TEST-000','KASHIER_VERIFIED_CURRENCIES':'EGP',
 'KASHIER_VERIFIED_METHODS':'card','KASHIER_ALLOWED_METHODS':'card',
 'PAYMOB_HMAC_SECRET':'synthetic-hmac-secret-at-least-32-bytes',
 'TABO_TRUSTED_PROXY_CIDRS':''}

def order(provider='Kashier',number=1):
    return {'id':f'TABO-{number:016X}','status':'PENDING','items':[{'id':'book1','name':'Test'}],
      'total':199,'currency':'EGP','paymentMethod':provider,'paymentMode':'test',
      'paymobOrderId':'session-synthetic' if provider=='Kashier' else str(7000+number),
      'paymobMethodId':71,'statusKey':f'{number:064x}','transactionId':None,
      'buyer':{'name':'مشتري اختباري','phone':'01000000000'},'token':None}

class MemoryStore:
    def __init__(self,*orders):
        self.orders={o['id']:copy.deepcopy(o) for o in orders};self.lock=threading.RLock();self.events={};self.usage={}
        self.fail_next=False
    def find(self,field,value):
        key={'status_key':'statusKey','download_token':'token','paymob_order_id':'paymobOrderId'}.get(field,field)
        with self.lock:return next((copy.deepcopy(o) for o in self.orders.values() if str(o.get(key))==value),None)
    def patch(self,oid,changes,conditions=None):
        keys={'checkout_session_hash':'checkoutSessionHash','download_token':'token','download_session_hash':'downloadSessionHash','download_token_used_at':'downloadTokenUsedAt','download_token_expires_at':'downloadTokenExpiresAt'}
        with self.lock:
            o=self.orders[oid]
            for key,val in (conditions or {}).items():
                actual=o.get(keys.get(key,key));operation,_,value=val.partition('.')
                if operation=='eq' and str(actual)!=value:return None
                if operation=='is' and actual is not None:return None
                if operation=='gt' and (not actual or actual<=value):return None
            for key,value in changes.items():
                target=keys.get(key,{'download_device_hash':'downloadDeviceHash','download_session_expires_at':'downloadSessionExpiresAt'}.get(key,key));o[target]=value
            return copy.deepcopy(o)
    def event(self,oid,provider,mode,transaction,kind,amount,currency,provider_id,digest):
        with self.lock:
            if self.fail_next:self.fail_next=False;raise app.store.StoreUnavailable(503)
            o=self.orders[oid];key=(provider,mode,transaction,kind)
            if key in self.events:return {'ok':True,'status':o['status'],'duplicate':True}
            if any(k[:3]==key[:3] and v!=oid for k,v in self.events.items()):return {'ok':False}
            self.events[key]=oid
            if kind=='paid':
                if o['status']=='PENDING':o.update(status='PAID',transactionId=transaction)
            else:
                o.update(status={'refund':'REFUNDED','void':'VOIDED','reversal':'REVERSED','partial_refund':'REVIEW','review_notice':'REVIEW'}[kind],downloadSessionHash=None,checkoutSessionHash=None,token=None)
            return {'ok':True,'status':o['status'],'duplicate':False}
    def download_usage(self,oid):
        return {b:n for (o,b),n in self.usage.items() if o==oid}
    def claim(self,oid,bid,session,device,pages):
        with self.lock:
            key=(oid,bid);used=self.usage.get(key,0)
            if used>=2:return {'allowed':False,'reason':'limit_reached'}
            self.usage[key]=used+1;return {'allowed':True,'remaining':1-used}

class HTTPCase(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,ENV,clear=True);self.env.start();self.addCleanup(self.env.stop)
        app.RATE_LIMITER=app.SlidingWindowLimiter()
        self.server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
        self.base='http://127.0.0.1:'+str(self.server.server_port)
        self.opener=build_opener(ProxyHandler({}))
    def install(self,*orders):
        self.db=MemoryStore(*orders)
        for name,func in [('ready',lambda:True),('find',self.db.find),('patch',self.db.patch),('apply_payment_event',self.db.event),('download_usage',self.db.download_usage),('claim_download',self.db.claim)]:
            p=patch.object(app.store,name,func);p.start();self.addCleanup(p.stop)
    def post(self,path,data,headers=None):
        r=Request(self.base+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json','User-Agent':'SyntheticBrowser/1',**(headers or {})},method='POST')
        try:response=self.opener.open(r,timeout=10)
        except HTTPError as e:response=e
        payload=response.read();status=response.status
        return status,json.loads(payload) if response.headers.get_content_type()=='application/json' else payload,response.headers

def kashier_payload(o,**changes):
    d={'amount':o['total'],'currency':o['currency'],'merchantOrderId':o['id'],
       'orderReference':'REFERENCE-TEST','kashierOrderId':'order-synthetic',
       'transactionId':'TX-SYNTHETIC-1','status':'SUCCESS','transactionResponseCode':'00'}
    d.update(changes);d['signatureKeys']=list(d)
    payload='&'.join(k+'='+app.quote(app.kashier_signature_value(d[k]),safe='') for k in sorted(d['signatureKeys']))
    signature=hmac.new(ENV['KASHIER_PAYMENT_API_KEY'].encode(),payload.encode(),hashlib.sha256).hexdigest()
    return {'event':'pay','data':d},{'x-kashier-signature':signature}

def paymob_payload(o,**changes):
    d={k:False for k in ('error_occured','has_parent_transaction','is_3d_secure','is_auth','is_capture','is_refunded','is_voided','pending')}
    d.update(amount_cents=19900,created_at='2026-01-01T00:00:00Z',currency='EGP',id=8001,integration_id=71,
             is_standalone_payment=True,success=True,order={'id':int(o['paymobOrderId']),'merchant_order_id':o['id']},
             owner=91,source_data={'pan':'0000','sub_type':'Test','type':'card'})
    d.update(changes);values=[]
    for field in app.HMAC_FIELDS:
        v=d
        for key in field.split('.'):v=v[key]
        values.append(str(v).lower() if isinstance(v,bool) else str(v))
    sig=hmac.new(ENV['PAYMOB_HMAC_SECRET'].encode(),''.join(values).encode(),hashlib.sha512).hexdigest()
    return '/api/paymob/webhook?hmac='+sig,{'type':'TRANSACTION','obj':d}
