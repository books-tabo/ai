"""Optional hosted PayPal Checkout. Never selected without independent readiness.

Only USD is supported here; the existing Egyptian-price/Kashier flow is untouched.
No secrets, response bodies or buyer details are logged or returned to browsers.
"""
import base64
import hashlib
import threading
import time
import json
import os
import re
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import NAMESPACE_URL, uuid5

BASES={'test':'https://api-m.sandbox.paypal.com','live':'https://api-m.paypal.com'}
BROWSER_HOSTS={'test':'www.sandbox.paypal.com','live':'www.paypal.com'}
IDS=re.compile(r'[A-Z0-9]{10,32}')
_TOKEN_LOCK=threading.Lock()
_TOKEN=None

class PayPalUnavailable(RuntimeError):
    def __init__(self,status=None):
        self.status=status if isinstance(status,int) else None
        super().__init__('PayPal temporarily unavailable')

class PaymentMismatch(ValueError):
    pass

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        return None


def mode():
    value=os.environ.get('PAYPAL_MODE','').strip()
    return value if value in BASES else ''


def credential(name):
    prefix='SANDBOX' if mode()=='test' else 'LIVE'
    return os.environ.get('PAYPAL_'+prefix+'_'+name,'').strip() if mode() else ''


def configured():
    if not mode() or os.environ.get('TABO_PAYPAL_SCHEMA_READY')!='1':return False
    if not all(16<=len(credential(name))<=512 and not any(c.isspace() for c in credential(name))
               for name in ('CLIENT_ID','CLIENT_SECRET')):return False
    if not re.fullmatch(r'[A-Z0-9]{8,32}',credential('MERCHANT_ID')):return False
    if not re.fullmatch(r'[A-Za-z0-9-]{8,100}',credential('WEBHOOK_ID')):return False
    site=urlparse(os.environ.get('TABO_PUBLIC_URL',''))
    if site.scheme!='https' or not site.hostname or site.username or site.password or site.query or site.fragment:return False
    # A test merchant must never receive production-store customers.
    if mode()=='test' and (os.environ.get('TABO_ENV')!='paypal-sandbox' or site.hostname in
                          ('tabobooks.com','www.tabobooks.com','tabo-books-preview.onrender.com')):return False
    return True


def available(currency):
    return (currency=='USD' and os.environ.get('TABO_PAYPAL_ENABLED')=='1' and
            os.environ.get('TABO_PAYPAL_CAPABILITY_VERIFIED')=='1' and configured())


def _transport(path,method,payload,headers):
    if not mode() or not path.startswith('/') or '?' in path or '..' in path:
        raise PayPalUnavailable()
    data=payload if isinstance(payload,bytes) else json.dumps(payload).encode() if payload is not None else None
    request=Request(BASES[mode()]+path,data=data,method=method,
                    headers={'Accept':'application/json',**headers})
    try:
        with build_opener(NoRedirect()).open(request,timeout=12) as response:
            raw=response.read(1_048_577)
        if len(raw)>1_048_576:raise ValueError()
        result=json.loads(raw)
        if not isinstance(result,dict):raise ValueError()
        return result
    except (HTTPError,URLError,TimeoutError,ConnectionError,OSError,ValueError) as error:
        raise PayPalUnavailable(getattr(error,'code',None)) from None


def _access_token():
    global _TOKEN
    if not configured():raise PayPalUnavailable()
    identity=hashlib.sha256((mode()+':'+credential('CLIENT_ID')+':'+credential('CLIENT_SECRET')).encode()).hexdigest()
    if not _TOKEN_LOCK.acquire(timeout=.1):raise PayPalUnavailable()
    try:
        current=time.monotonic()
        if _TOKEN and _TOKEN[0]==identity and _TOKEN[2]>current:return _TOKEN[1]
        basic=base64.b64encode((credential('CLIENT_ID')+':'+credential('CLIENT_SECRET')).encode()).decode()
        data=_transport('/v1/oauth2/token','POST',b'grant_type=client_credentials',
                        {'Authorization':'Basic '+basic,'Content-Type':'application/x-www-form-urlencoded'})
        token=data.get('access_token');expires=data.get('expires_in')
        if (not isinstance(token,str) or not 16<=len(token)<=8192 or data.get('token_type','').lower()!='bearer'
                or isinstance(expires,bool) or not isinstance(expires,(int,float)) or not 60<expires<86400):
            raise PayPalUnavailable()
        _TOKEN=(identity,token,current+min(3600,expires)-60)
        return token
    finally:
        _TOKEN_LOCK.release()


def api(path,method='GET',payload=None,request_key=None):
    headers={'Authorization':'Bearer '+_access_token(),'Content-Type':'application/json'}
    if request_key:
        headers.update({'PayPal-Request-Id':str(uuid5(NAMESPACE_URL,'tabo-paypal-v1:'+request_key)),
                        'Prefer':'return=representation'})
    return _transport(path,method,payload,headers)


def amount_matches(data,total):
    try:
        if not isinstance(data,dict):return False
        value=Decimal(str(data['value']))
        return data.get('currency_code')=='USD' and value.is_finite() and value>0 and value==Decimal(str(total))
    except (KeyError,TypeError,ValueError,InvalidOperation):return False


def bound_unit(order,data,expected_id=True):
    if (order.get('paymentMethod')!='PayPal' or order.get('paymentMode')!=mode() or order.get('currency')!='USD'
            or (expected_id and data.get('id')!=order.get('paymobOrderId'))):raise PaymentMismatch('Payment binding mismatch')
    units=data.get('purchase_units')
    if not isinstance(units,list) or len(units)!=1 or not isinstance(units[0],dict):raise PaymentMismatch('Invalid purchase units')
    unit=units[0]
    if (unit.get('reference_id')!=order['id'] or unit.get('custom_id')!=order['id'] or
            unit.get('invoice_id')!=order['id'] or not amount_matches(unit.get('amount',{}),order['total']) or
            unit.get('payee',{}).get('merchant_id')!=credential('MERCHANT_ID')):
        raise PaymentMismatch('Payment binding mismatch')
    return unit


def create(order):
    if not available(order.get('currency')):raise PayPalUnavailable()
    if order.get('paymentMode')!=mode() or not re.fullmatch(r'TABO-[A-F0-9]{16}',order.get('id','')):
        raise PaymentMismatch('Invalid merchant order')
    site=os.environ['TABO_PUBLIC_URL'].rstrip('/')
    query=urlencode({'key':order['statusKey']})
    total=f"{Decimal(str(order['total'])):.2f}"
    payload={'intent':'CAPTURE','purchase_units':[{'reference_id':order['id'],'custom_id':order['id'],
        'invoice_id':order['id'],'description':'TABO BOOKS — digital books',
        'amount':{'currency_code':'USD','value':total},'payee':{'merchant_id':credential('MERCHANT_ID')}}],
        'payment_source':{'paypal':{'experience_context':{'brand_name':'TABO BOOKS',
        'shipping_preference':'NO_SHIPPING','user_action':'PAY_NOW',
        'return_url':site+'/paypal-return.html?'+query,
        'cancel_url':site+'/pending.html?'+query}}}}
    data=api('/v2/checkout/orders','POST',payload,'create:'+order['id'])
    identifier=data.get('id','')
    if not isinstance(identifier,str) or not IDS.fullmatch(identifier):raise PaymentMismatch('Invalid provider order')
    # Some create responses are deliberately minimal. Full binding is always
    # required from a server GET before capture/fulfilment.
    urls=[link.get('href') for link in data.get('links',[]) if isinstance(link,dict) and link.get('rel') in ('payer-action','approve')]
    if len(urls)!=1 or not isinstance(urls[0],str):raise PaymentMismatch('Invalid approval link')
    parsed=urlparse(urls[0])
    if (parsed.scheme!='https' or parsed.hostname!=BROWSER_HOSTS[mode()] or parsed.port not in (None,443)
            or parsed.username or parsed.password or parsed.path!='/checkoutnow' or parsed.fragment):
        raise PaymentMismatch('Invalid approval link')
    from urllib.parse import parse_qs
    if parse_qs(parsed.query).get('token')!=[identifier]:raise PaymentMismatch('Invalid approval token')
    return urls[0],identifier


def reconcile(order):
    identifier=order.get('paymobOrderId','')
    if not isinstance(identifier,str) or not IDS.fullmatch(identifier):raise PaymentMismatch('Invalid provider order')
    data=api('/v2/checkout/orders/'+identifier)
    unit=bound_unit(order,data)
    captures=unit.get('payments',{}).get('captures',[])
    if not captures:return None
    if not isinstance(captures,list) or len(captures)!=1:raise PaymentMismatch('Unexpected capture count')
    capture=captures[0];capture_id=capture.get('id','')
    if not isinstance(capture_id,str) or not IDS.fullmatch(capture_id):raise PaymentMismatch('Invalid capture')
    current=api('/v2/payments/captures/'+capture_id)
    if current.get('id')!=capture_id or not amount_matches(current.get('amount',{}),order['total']):
        raise PaymentMismatch('Capture amount mismatch')
    # Bind capture to the order returned by PayPal, never to a browser flag.
    linked=current.get('supplementary_data',{}).get('related_ids',{}).get('order_id')
    if linked is not None and linked!=identifier:raise PaymentMismatch('Capture order mismatch')
    if not amount_matches(capture.get('amount',{}),order['total']):raise PaymentMismatch('Capture amount mismatch')
    status=current.get('status')
    if status=='COMPLETED' and data.get('status')=='COMPLETED' and capture.get('final_capture') is True:
        return {'transaction':capture_id,'kind':'paid','amount':order['total']}
    if status in ('REFUNDED','PARTIALLY_REFUNDED','REVERSED'):
        return {'transaction':capture_id,'kind':'refund' if status=='REFUNDED' else 'review_notice','amount':order['total']}
    return None


def capture(order):
    data=api('/v2/checkout/orders/'+order['paymobOrderId'])
    unit=bound_unit(order,data)
    if not unit.get('payments',{}).get('captures'):
        if data.get('status')!='APPROVED':return None
        api('/v2/checkout/orders/'+order['paymobOrderId']+'/capture','POST',{},'capture:'+order['id'])
    return reconcile(order)


def verify_webhook(event,headers):
    fields={'auth_algo':'PAYPAL-AUTH-ALGO','cert_url':'PAYPAL-CERT-URL','transmission_id':'PAYPAL-TRANSMISSION-ID',
            'transmission_sig':'PAYPAL-TRANSMISSION-SIG','transmission_time':'PAYPAL-TRANSMISSION-TIME'}
    supplied={key:headers.get(value,'') for key,value in fields.items()}
    if any(not isinstance(value,str) or not 1<=len(value)<=4096 for value in supplied.values()):return False
    if supplied['auth_algo']!='SHA256withRSA':return False
    cert=urlparse(supplied['cert_url']);hosts={'api-m.paypal.com','api.paypal.com'} if mode()=='live' else {'api-m.sandbox.paypal.com','api.sandbox.paypal.com'}
    if (cert.scheme!='https' or cert.hostname not in hosts or cert.port not in (None,443) or cert.username
            or cert.password or cert.query or cert.fragment or not cert.path.startswith('/v1/notifications/certs/')):return False
    result=api('/v1/notifications/verify-webhook-signature','POST',
               {**supplied,'webhook_id':credential('WEBHOOK_ID'),'webhook_event':event})
    return result.get('verification_status')=='SUCCESS'


def webhook_capture_id(event):
    if event.get('event_type') in ('PAYMENT.CAPTURE.COMPLETED','PAYMENT.CAPTURE.REVERSED'):
        identifier=event.get('resource',{}).get('id','')
    elif event.get('event_type')=='PAYMENT.CAPTURE.REFUNDED':
        resource=event.get('resource',{})
        identifier=resource.get('supplementary_data',{}).get('related_ids',{}).get('capture_id','')
        if not identifier:
            for link in resource.get('links',[]):
                if not isinstance(link,dict) or link.get('rel')!='up':continue
                parsed=urlparse(str(link.get('href','')))
                if (parsed.scheme=='https' and parsed.hostname==urlparse(BASES[mode()]).hostname and
                        not parsed.username and not parsed.password and parsed.port in (None,443) and not parsed.query and not parsed.fragment):
                    match=re.fullmatch(r'/v2/payments/captures/([A-Z0-9]{10,32})',parsed.path)
                    if match:identifier=match.group(1)
    else:return None
    return identifier if isinstance(identifier,str) and IDS.fullmatch(identifier) else None
