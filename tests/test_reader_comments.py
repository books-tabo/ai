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

    def test_only_explicit_opt_in_sends_email_to_private_subscription_storage(self):
        with patch.dict(os.environ,ENV),patch.object(store,'request',return_value={'accepted':True}) as request:
            for consent, expected in ((True,'reader@example.com'),(False,None),('true',None)):
                store.submit_comment(reader_comments.validate(comment(updates_consent=consent),app.BOOKS))
                self.assertEqual(request.call_args.args[2]['p_opt_in_email'],expected)

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
        self.assertEqual(set(result['comments'][0]),{'id','display_name','body','verified_purchase','created_at','rating'})
        self.assertNotIn('private',json.dumps(result));self.assertNotIn('PRIVATE',json.dumps(result))

    def test_comment_rating_validation_and_legacy_requests(self):
        with patch.dict(os.environ,ENV):
            for rating in (1,2,3,4,5,None):
                value=reader_comments.validate(comment(rating=rating),app.BOOKS)
                self.assertEqual(value['rating'],rating)
            self.assertIsNone(reader_comments.validate(comment(),app.BOOKS)['rating'])
            for rating in (True,False,0,6,-1,5.0,'5',[],{}):
                with self.subTest(rating=rating),self.assertRaises(ValueError):
                    reader_comments.validate(comment(rating=rating),app.BOOKS)

    def test_rating_is_stored_with_comment_and_public_values_are_allowlisted(self):
        with patch.dict(os.environ,ENV),patch.object(store,'request',return_value={'accepted':True}) as request:
            store.submit_comment(reader_comments.validate(comment(rating=3),app.BOOKS))
            self.assertEqual(request.call_args.args[2]['p_rating'],3)
        row={'id':'abc','display_name':'Reader','body':'Opinion','verified_purchase':False,
             'created_at':'2026-11-06T00:00:00Z','email':'PRIVATE'}
        for rating in (1,3,5,None,True,6,'5'):
            result=reader_comments.public_result({'total':1,'comments':[{**row,'rating':rating}]})
            expected=rating if type(rating) is int and 1<=rating<=5 else None
            self.assertEqual(result['comments'][0]['rating'],expected)
            self.assertNotIn('PRIVATE',json.dumps(result))

    def test_adapter_does_not_write_existing_stats_or_ratings(self):
        with patch.dict(os.environ,ENV),patch.object(store,'request',return_value={'accepted':True}) as request:
            data=reader_comments.validate(comment(),app.BOOKS)
            store.submit_comment(data)
            self.assertEqual(request.call_args.args[0],'/rest/v1/rpc/publish_reader_comment')
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

    def test_valid_email_submission_is_accepted_and_cannot_forge_verified(self):
        with patch.object(app.store,'submit_rating') as rate,patch.object(app.store,'claim_download') as download:
            status,data,_=self.post('/api/book-comments',comment(status='approved',verified_purchase=True,rating=5))
        self.assertEqual((status,data),(202,{'ok':True,'status':'accepted'}))
        args=self.submit.call_args.args
        self.assertEqual(args[0]['book_id'],'book20');self.assertEqual(args[0]['rating'],5);self.assertIsNone(args[1])
        self.assertNotIn('email',args[0]);self.assertNotIn('verified_purchase',args[0])
        rate.assert_not_called();download.assert_not_called()

    def test_cross_origin_invalid_and_honeypot_requests_never_write(self):
        self.assertEqual(self.post('/api/book-comments',comment(),{'Origin':'https://other.invalid','Sec-Fetch-Site':'cross-site'})[0],403)
        self.assertEqual(self.post('/api/book-comments',comment(consent=False))[0],400)
        self.assertEqual(self.post('/api/book-comments',comment(email='bad'))[0],400)
        self.assertEqual(self.post('/api/book-comments',comment(rating=6))[0],400)
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

    def test_admin_comment_routes_require_owner_and_allow_owner_removal(self):
        url='/api/admin-dashboard/comments'
        comment_id='11111111-1111-4111-8111-111111111111'
        with patch.object(app.owner_dashboard,'enabled',return_value=True), \
             patch.object(app.owner_dashboard,'authenticated',return_value=False) as auth, \
             patch.object(app.store,'remove_reader_comment',return_value=True) as remove, \
             patch.object(app.store,'reader_comment_subscribers',return_value=[]) as subscribers:
            self.assertEqual(self.get_comments(url+'?kind=subscribers')[0],403)
            self.assertEqual(self.post(url,{'id':comment_id})[0],403)
            remove.assert_not_called()
            subscribers.assert_not_called()
            auth.return_value=True
            self.assertEqual(self.post(url,{'id':'not-a-uuid'})[0],400)
            self.assertEqual(self.post(url,{'id':comment_id})[0],200)
            remove.assert_called_once_with(comment_id)
            self.assertEqual(self.get_comments(url+'?kind=subscribers'),(200,{'rows':[],'more':False}))
            subscribers.assert_called_once_with(0)

    def test_owner_can_list_and_remove_a_published_comment(self):
        url='/api/admin-dashboard/comments'
        row={'id':'11111111-1111-4111-8111-111111111111','book_id':'book1',
             'display_name':'قارئ اختباري','body':'تعليق لاختبار لوحة التحكم',
             'status':'approved','created_at':'2026-11-04T00:00:00Z'}
        with patch.object(app.owner_dashboard,'enabled',return_value=True), \
             patch.object(app.owner_dashboard,'authenticated',return_value=True), \
             patch.object(app.store,'admin_reader_comments',side_effect=[[row.copy()],[]]), \
             patch.object(app.store,'remove_reader_comment',return_value=True) as remove:
            self.assertEqual(self.get_comments(url),(200,{'rows':[{**row,'book_name':'هرمون'}],'more':False}))
            self.assertEqual(self.post(url,{'id':row['id']})[0],200)
            remove.assert_called_once_with(row['id'])
            self.assertEqual(self.get_comments(url),(200,{'rows':[],'more':False}))

    def test_form_present_for_both_sigma_books_and_browser_asset_available(self):
        for bid in ('book20','book21'):
            with self.opener.open(self.base+'/books/'+bid,timeout=3) as response:
                html=response.read().decode()
            self.assertIn('id="commentForm"',html)
            self.assertIn('id="commentEmail"',html)
            self.assertIn('id="commentRating"',html)
            self.assertEqual(html.count('name="commentRating"'),5)
            self.assertIn('type="email"',html)
            self.assertIn('/book-comments.js',html)
        with self.opener.open(self.base+'/book-comments.js',timeout=3) as response:
            self.assertIn(b'email',response.read())
