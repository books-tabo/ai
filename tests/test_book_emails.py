"""Synthetic outbox/provider tests; no customer messages or production orders."""
import base64
import copy
from datetime import datetime, timezone
import io
import json
import os
import time
import unittest
from unittest.mock import patch

import app
import book_emails as emails
import media_sandbox
import order_receipts
import pdf_limits
from pypdf import PdfReader, PdfWriter
from support import HTTPCase
from test_order_receipts import MAIL_ENV, paid

ENV = {**MAIL_ENV, 'TABO_BOOK_EMAILS_ENABLED':'1','TABO_BOOK_EMAILS_SCHEMA_READY':'1',
       'SUPABASE_URL':'https://synthetic.invalid','SUPABASE_SECRET_KEY':'synthetic'}


def purchase():
    value = paid()
    value['buyer']['bookEmailRequested'] = True
    value['items'] = [{'id':'book20','name':'Synthetic book'}, {'id':'book22','name':'Another book'}]
    return value


class BookEmailTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, ENV, clear=True)
        env.start();self.addCleanup(env.stop)

    def test_activation_requires_verified_sender_storage_and_explicit_flags(self):
        self.assertTrue(emails.enabled())
        for change in ({'TABO_BOOK_EMAILS_ENABLED':'0'}, {'TABO_BOOK_EMAILS_SCHEMA_READY':'0'},
                       {'TABO_SITE_ENV':'review'}, {'TABO_RECEIPTS_ENABLED':'0'}, {'SUPABASE_SECRET_KEY':''}):
            with patch.dict(os.environ,change), patch.object(emails.store,'claim_book_email') as claim:
                self.assertFalse(emails.process_one());claim.assert_not_called()

    def test_only_persisted_paid_live_purchased_books_can_produce_attachments(self):
        for update in ({'status':'PENDING'}, {'status':'REFUNDED'}, {'status':'REVERSED'}, {'status':'REVIEW'}, {'paymentMode':'test'}):
            value=purchase();value.update(update)
            with patch.object(emails.store,'original') as original, self.assertRaises(ValueError):
                emails.attachment(value,'book20')
            original.assert_not_called()
        value=purchase()
        for bid in ('book1','../book20','book9999'):
            with self.assertRaises(ValueError):emails.eligible(value,bid)
        value['buyer']['bookEmailRequested']=False
        with self.assertRaises(ValueError):emails.eligible(value,'book20')

    def test_bilingual_message_contains_only_the_purchased_pdf_no_original_url(self):
        value=purchase();value['buyer']['name']='<Synthetic & Reader>'
        for language in ('ar','en'):
            value['buyer']['language']=language
            payload=emails.message(value,'book20',b'%PDF-synthetic-stamped')
            self.assertEqual(payload['to'],['reader@example.invalid'])
            self.assertEqual(len(payload['attachments']),1)
            self.assertEqual(base64.b64decode(payload['attachments'][0]['content']),b'%PDF-synthetic-stamped')
            self.assertIn('book20',payload['attachments'][0]['filename'])
            self.assertNotIn('01000000000',payload['attachments'][0]['filename'])
            self.assertIn('&lt;Synthetic &amp; Reader&gt;',payload['html'])
            self.assertIn('560.59 EGP',payload['text']);self.assertIn('9.99 USD',payload['text'])
            self.assertNotIn('Another book',payload['text'])
            self.assertNotIn('/storage/',json.dumps(payload));self.assertNotIn('/api/download/',json.dumps(payload))
        for pdf in (b'not a PDF', b'%PDF-'+b'x'*100):
            with patch.object(emails,'MAX_PDF_BYTES',100),self.assertRaises(ValueError):emails.message(value,'book20',pdf)

    def worker(self, value=None, current=None, prepared='ready', attempts=1, error=None):
        value=value or purchase()
        claim={'order_id':value['id'],'book_id':'book20','created_at':datetime.now(timezone.utc).isoformat(),'attempts':attempts}
        with patch.object(emails.store,'claim_book_email',return_value=claim), \
             patch.object(emails.store,'find',side_effect=[value,current if current is not None else copy.deepcopy(value)]), \
             patch.object(emails,'attachment',return_value=b'%PDF-stamped') as attachment, \
             patch.object(emails.store,'prepare_book_email',return_value=prepared) as prepare, \
             patch.object(emails.store,'finish_book_email') as finish, \
             patch.object(order_receipts,'send',return_value='synthetic-message',side_effect=error) as send, \
             patch.object(emails.store,'claim_download',side_effect=AssertionError('Download claim spent')) as download:
            self.assertTrue(emails.process_one())
            download.assert_not_called()
            return attachment,prepare,finish,send

    def test_worker_rechecks_payment_and_identity_after_stamping_then_uses_distinct_key(self):
        _,prepare,finish,send=self.worker()
        self.assertEqual(len(prepare.call_args.args[-1]),64)
        self.assertEqual(finish.call_args.args[3:5],('sent','synthetic-message'))
        self.assertEqual(send.call_args.kwargs,{'namespace':'paid-book'})
        self.assertEqual(send.call_args.args[1],purchase()['id']+'/book20')
        for change in ('refund','contact'):
            current=purchase()
            if change=='refund':current['status']='REFUNDED'
            else:current['buyer']['phone']='01011111111'
            _,prepare,finish,send=self.worker(current=current)
            prepare.assert_not_called();send.assert_not_called()
            self.assertEqual(finish.call_args.args[3],'skipped')

    def test_lost_lease_changed_payload_and_revoked_payment_never_send(self):
        for state in (None,'changed','ineligible'):
            _,_,finish,send=self.worker(prepared=state)
            send.assert_not_called()
            if state is None:finish.assert_not_called()
            elif state=='changed':self.assertEqual(finish.call_args.args[-1],'payload_changed')
            else:self.assertEqual(finish.call_args.args[3],'skipped')

    def test_retryable_and_permanent_failures_do_not_spend_downloads(self):
        for attempts,retryable,state in [(1,True,'retry'),(8,True,'failed'),(1,False,'failed')]:
            _,_,finish,_=self.worker(attempts=attempts,error=order_receipts.DeliveryError(retryable))
            self.assertEqual(finish.call_args.args[3],state)

    def test_busy_pdf_slot_defers_email_without_charging_a_download(self):
        value=purchase();claim={'order_id':value['id'],'book_id':'book20','created_at':datetime.now(timezone.utc).isoformat(),'attempts':1}
        with patch.object(emails.store,'claim_book_email',return_value=claim), patch.object(emails.store,'find',return_value=value), \
             patch.object(emails,'attachment',side_effect=pdf_limits.PDFBusy()), patch.object(emails.store,'finish_book_email') as finish, \
             patch.object(order_receipts,'send') as send:
            self.assertTrue(emails.process_one());send.assert_not_called()
            self.assertEqual(finish.call_args.args[3],'retry')

    def test_real_pdf_stamping_is_byte_stable_and_preserves_all_pages_and_contact_stamp(self):
        source=PdfWriter()
        for _ in range(2):source.add_blank_page(width=595,height=842)
        buf=io.BytesIO();source.write(buf)
        value=purchase();value['buyer']['name']='قارئ اختباري';value['buyer']['phone']='٠١٠٠٠٠٠٠٠٠٠'
        with patch.object(emails.store,'original',return_value=buf.getvalue()), \
             patch.object(emails.store,'claim_download',side_effect=AssertionError('Download claim spent')), \
             patch.object(media_sandbox,'run',wraps=media_sandbox.run) as sandbox:
            first=emails.attachment(value,'book20');time.sleep(1.1);second=emails.attachment(value,'book20')
            self.assertEqual(first,second)
            stamp=sandbox.call_args.args[2]['order']
            self.assertEqual(stamp,{'id':value['id'],'buyer':{'name':'قارئ اختباري','phone':'01000000000'}})
            self.assertEqual(len(PdfReader(io.BytesIO(first)).pages),2)

    def test_attachment_transport_preserves_idempotency_and_content_on_retry(self):
        class Response:
            status=200
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,limit):return b'{"id":"synthetic-message"}'
        payload=emails.message(purchase(),'book20',b'%PDF-stamped')
        with patch.object(order_receipts,'OPEN',return_value=Response()) as transport:
            for _ in range(2):order_receipts.send(payload,purchase()['id']+'/book20',namespace='paid-book')
            requests=[call.args[0] for call in transport.call_args_list]
            self.assertEqual(requests[0].data,requests[1].data)
            self.assertEqual(requests[0].get_header('Idempotency-key'),'paid-book/'+purchase()['id']+'/book20')
            self.assertEqual(transport.call_args.kwargs['timeout'],30)


class BookEmailCheckoutHTTPTests(HTTPCase):
    def test_client_cannot_opt_in_when_disabled_and_server_sets_policy_when_enabled(self):
        for active in (False,True):
            captured=[]
            with patch.object(app.book_emails,'enabled',return_value=active), \
                 patch.object(app.order_receipts,'enabled',return_value=True), \
                 patch.object(app.Handler,'pricing_country',return_value='EG'), \
                 patch.object(app,'checkout_ready',return_value=True), \
                 patch.object(app.store,'insert',side_effect=lambda order:captured.append(copy.deepcopy(order))), \
                 patch.object(app.store,'patch',return_value={'id':'synthetic'}), \
                 patch.object(app,'kashier_checkout',return_value=('https://payments.kashier.io/synthetic','synthetic')):
                status,payload,_=self.post('/api/orders',{'items':[{'id':'book20'}],'country':'EG','bookEmailRequested':not active,
                    'customer':{'name':'قارئ اختباري','email':'reader@example.invalid','phone':'01000000000'}})
            self.assertEqual(status,201)
            self.assertIs(captured[0]['buyer']['bookEmailRequested'],active)
            self.assertNotIn('buyer',payload['order'])
