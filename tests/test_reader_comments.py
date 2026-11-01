import json
import os
from unittest.mock import patch
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import unittest
import app
import reader_comments
import supabase_store as store
from support import HTTPCase,ENV


def comment(**changes):
    return {'book_id':'book20','email':'Reader@EXAMPLE.COM','display_name':'قارئ اختباري',
            'body':'تعليق اختباري مستقل عن رأيي الحقيقي في الكتاب','consent':True,**changes}


class CommentValidationTests(unittest.TestCase):
    def test_email_private_normalized_and_purpose_separated(self):
        with patch.dict(os.environ,ENV):
            first=reader_comments.validate(comment(),app.BOOKS)
            second=reader_comments.validate(comment(email=' reader@example.com '),app.BOOKS)
            self.assertEqual(first['email_hash'],second['email_hash'])
            self.assertNotIn('email',first)
            self.assertEqual(len(first['email_hash']),64)
            self.assertNotEqual(first['email_hash'],reader_comments.fingerprint('order','reader@example.com'))
            self.assertNotIn('reader@example',json.dumps(first))

    def test_validation_rejects_invalid_email_book_body_and_missing_consent(self):
        with patch.dict(os.environ,ENV):
            for changes in ({'email':''},{'email':'bad'},{'email':'a\n@b.com'},{'email':'x'*255+'@e.com'},
                            {'book_id':'book999'},{'book_id':[]},{'body':'tiny'},{'body':'x'*2001},
                            {'display_name':'x'*81},{'body':'hello\x00there'},{'consent':False}):
                with self.subTest(changes=changes),self.assertRaises(ValueError):
                    reader_comments.validate(comment(**changes),app.BOOKS)
            self.assertEqual(reader_comments.validate(comment(display_name=''),app.BOOKS)['display_name'],'قارئ')

    def test_only_public_fields_reach_response(self):
        row={'id':'abc','display_name':'Reader','body':'Honest opinion','verified_purchase':False,
             'created_at':'2026-10-28T00:00:00Z','email_hash':'private','email':'PRIVATE','verified_order_hash':'private','status':'approved'}
        result=reader_comments.public_result({'total':1,'comments':[row]})
        self.assertEqual(set(result['comments'][0]),{'id','display_name','body','verified_purchase','created_at'})
        self.assertNotIn('private',json.dumps(result));self.assertNotIn('PRIVATE',json.dumps(result))

    def test_adapter_does_not_write_existing_stats_or_ratings(self):
        with patch.dict(os.environ,ENV),patch.object(store,'request',return_value={'accepted':True}) as request:
            data=reader_comments.validate(comment(),app.BOOKS)
            store.submit_comment(data)
            self.assertEqual(request.call_args.args[0],'/rest/v1/rpc/submit_reader_comment')
            self.assertNotIn('Reader@EXAMPLE.COM',str(request.call_args))
            store.book_comments('book21',20)
            self.assertEqual(request.call_args.args,('/rest/v1/rpc/list_reader_comments','POST',{'p_book_id':'book21','p_offset':20}))


class CommentHTTPTests(HTTPCase):
    def setUp(self):
        super().setUp()
        p=patch.object(app.store,'submit_comment',return_value={'accepted':True});self.submit=p.start();self.addCleanup(p.stop)
        p=patch.object(app.store,'book_comments',return_value={'comments':[],'total':0});self.listing=p.start();self.addCleanup(p.stop)

    def get_comments(self,path='/api/book-comments?book_id=book20'):
        try:response=self.opener.open(self.base+path,timeout=3)
        except HTTPError as error:response=error
        return response.status,json.loads(response.read())

    def test_valid_email_submission_is_pending_and_cannot_forge_published_or_verified(self):
        with patch.object(app.store,'submit_rating') as rate,patch.object(app.store,'claim_download') as download:
            status,data,_=self.post('/api/book-comments',comment(status='approved',verified_purchase=True,rating=5))
        self.assertEqual((status,data),(202,{'ok':True,'status':'pending'}))
        args=self.submit.call_args.args
        self.assertEqual(args[0]['book_id'],'book20');self.assertIsNone(args[1])
        self.assertNotIn('email',args[0]);self.assertNotIn('verified_purchase',args[0])
        rate.assert_not_called();download.assert_not_called()

    def test_cross_origin_invalid_and_honeypot_requests_never_write(self):
        self.assertEqual(self.post('/api/book-comments',comment(),{'Origin':'https://other.invalid','Sec-Fetch-Site':'cross-site'})[0],403)
        self.assertEqual(self.post('/api/book-comments',comment(consent=False))[0],400)
        self.assertEqual(self.post('/api/book-comments',comment(email='bad'))[0],400)
        self.assertEqual(self.post('/api/book-comments',comment(website='spam.invalid'))[0],202)
        self.submit.assert_not_called()

    def test_verified_badge_requires_existing_paid_device_session_and_same_book(self):
        oid='TABO-0000000000000001';payload=comment(order_id=oid)
        headers={'Cookie':app.download_cookie_name(oid)+'=synthetic-session'}
        with patch.object(app.Handler,'authorized_download_order',return_value=None) as auth:
            self.assertEqual(self.post('/api/book-comments',payload,headers)[0],202)
            auth.assert_called_once_with(oid)
        self.assertIsNone(self.submit.call_args.args[1])
        with patch.object(app.Handler,'authorized_download_order',return_value={'id':oid,'items':[{'id':'book1'}]}):
            self.post('/api/book-comments',payload,headers)
        self.assertIsNone(self.submit.call_args.args[1])
        with patch.object(app.Handler,'authorized_download_order',return_value={'id':oid,'items':[{'id':'book20'}]}):
            self.post('/api/book-comments',payload,headers)
        self.assertEqual(self.submit.call_args.args[1],reader_comments.fingerprint('order',oid))
        with patch.object(app.Handler,'authorized_download_order') as auth:
            self.post('/api/book-comments',payload)
            auth.assert_not_called()
        self.assertIsNone(self.submit.call_args.args[1])

    def test_public_get_is_allowlisted_and_invalid_pagination_fails_closed(self):
        self.assertEqual(self.get_comments(),(200,{'comments':[],'total':0}))
        self.listing.assert_called_once_with('book20',0)
        for query in ('book_id=book999','book_id=book20&offset=-1','book_id=book20&offset=1001','book_id=book20&offset=x'):
            self.assertEqual(self.get_comments('/api/book-comments?'+query)[0],400)

    def test_rate_limit_and_storage_failure(self):
        for _ in range(5):self.assertEqual(self.post('/api/book-comments',comment())[0],202)
        self.assertEqual(self.post('/api/book-comments',comment())[0],429)
        self.assertEqual(self.submit.call_count,5)
        with patch.object(app.store,'book_comments',side_effect=app.store.StoreUnavailable()):
            self.assertEqual(self.get_comments()[0],503)

    def test_form_present_for_both_sigma_books_and_browser_asset_available(self):
        for bid in ('book20','book21'):
            with self.opener.open(self.base+'/books/'+bid,timeout=3) as response:
                html=response.read().decode()
            self.assertIn('id="commentForm"',html)
            self.assertIn('id="commentEmail"',html)
            self.assertIn('type="email"',html)
            self.assertIn('/book-comments.js',html)
        with self.opener.open(self.base+'/book-comments.js',timeout=3) as response:
            self.assertIn(b'email',response.read())
