"""One consented reminder per email/day; recovery links restore only a cart."""
import html
import os
import re
import secrets
import threading

import order_receipts
import owner_dashboard
import store_catalog
import supabase_store as store

TOKEN=re.compile(r'[a-f0-9]{64}')


def enabled():
    return (os.environ.get('TABO_RECOVERY_ENABLED')=='1' and store_catalog.enabled()
            and owner_dashboard.enabled() and order_receipts.enabled())


def digest(kind,value):
    return owner_dashboard.digest('checkout-recovery-'+kind,value)


def token(order_id,kind='cart'):
    return digest(kind,order_id)


def enroll(order_id):
    if not enabled():return False
    return store_catalog.rpc('store_recovery_enroll',order=order_id,
        link=digest('link-hash',token(order_id)),unsubscribe=digest('unsubscribe-hash',token(order_id,'unsubscribe')))


def cart(raw):
    if not enabled() or not isinstance(raw,str) or not TOKEN.fullmatch(raw):return None
    return store_catalog.rpc('store_recovery_cart',link=digest('link-hash',raw))


def unsubscribe(raw):
    if not enabled() or not isinstance(raw,str) or not TOKEN.fullmatch(raw):return
    store_catalog.rpc('store_recovery_unsubscribe',unsubscribe=digest('unsubscribe-hash',raw))


def message(claim):
    en=claim['language']=='en'
    resume='https://tabobooks.com/resume-checkout?token='+token(claim['order_id'])
    stop='https://tabobooks.com/stop-reminders?token='+token(claim['order_id'],'unsubscribe')
    subject='Your books are waiting for you | TABO BOOKS' if en else 'كتبك في انتظارك | TABO BOOKS'
    text=('You asked us to remind you if checkout was not completed. You can restore your book selection and review current prices before paying. This link restores your cart only.\n\nContinue: '+resume+'\n\nStop reminders: '+stop if en else
          'اخترت تذكيرك إذا لم يكتمل الدفع. يمكنك استعادة الكتب التي اخترتها ومراجعة الأسعار الحالية قبل الدفع. هذا الرابط يستعيد السلة فقط.\n\nاستكمال الطلب: '+resume+'\n\nإيقاف التذكير: '+stop)
    label='Continue to your cart' if en else 'استكمال السلة'
    explanation='Review current prices and available payment methods before completing your purchase.' if en else 'راجع الأسعار الحالية ووسائل الدفع المتاحة قبل إتمام الشراء.'
    stop_label='Stop reminders' if en else 'إيقاف التذكير'
    body='<html lang="'+('en' if en else 'ar')+'" dir="'+('ltr' if en else 'rtl')+'"><body style="font-family:Tahoma,Arial,sans-serif;background:#f4f5f0;padding:24px"><div style="max-width:540px;margin:auto;background:white;padding:30px;border-radius:16px"><p>TABO BOOKS</p><h2>'+html.escape(subject)+'</h2><p>'+explanation+'</p><a style="display:inline-block;background:#17291e;color:white;padding:14px 24px;border-radius:8px" href="'+resume+'">'+label+'</a><p style="font-size:12px"><a href="'+stop+'">'+stop_label+'</a></p></div></body></html>'
    return {'from':'TABO BOOKS <'+os.environ['TABO_RECEIPT_FROM']+'>','to':[claim['email']],
            'reply_to':'taponoor@gmail.com','subject':subject,'text':text,'html':body}


def process_one():
    if not enabled():return False
    lease=secrets.token_hex(32)
    claim=store_catalog.rpc('store_recovery_claim',lease=lease)
    if not claim:return False
    # Read back immediately before transport; a paid, unsubscribed or expired
    # record is never deliberately sent based on a stale worker claim.
    if not store_catalog.rpc('store_recovery_sendable',order=claim['order_id'],lease=lease):
        state,reason='skipped','ineligible'
    else:
        try:
            order_receipts.send(message(claim),'checkout-recovery-'+claim['order_id'])
            state,reason='sent',None
        except order_receipts.DeliveryError as exc:
            state='retry' if exc.retryable and claim['attempts']<4 else 'failed'
            reason=exc.reason
    store_catalog.rpc('store_recovery_finish',order=claim['order_id'],lease=lease,state=state,reason=reason)
    return True


def start_worker():
    if not enabled():return
    def run():
        while True:
            try:
                # Consent is durable in the new orders' buyer JSON, allowing a
                # failed enrollment request to be retried after process restart.
                rows=store.request('/rest/v1/orders',params={'select':'id','status':'eq.PENDING','payment_mode':'eq.live',
                    'buyer->>recoveryConsent':'eq.true','order':'created_at.desc','limit':50})
                for row in rows:enroll(row['id'])
                for _ in range(5):
                    if not process_one():break
            except Exception:
                print('Checkout reminders: temporary failure',flush=True)
            threading.Event().wait(60)
    threading.Thread(target=run,daemon=True,name='checkout-reminders').start()


def admin(auth,data=None):
    args=store_catalog.auth_args(auth)
    if data is not None:
        if not isinstance(data.get('enabled'),bool):raise ValueError('حالة التذكير غير صالحة.')
        result=store_catalog.rpc('store_admin_recovery_toggle',**args,enabled=data['enabled'])
        store_catalog.invalidate()
    else:result=store_catalog.rpc('store_admin_recovery_report',**args)
    if result is None:raise PermissionError()
    return result
