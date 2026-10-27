"""Temporary importer security checks use synthetic bytes and no live storage."""
import hashlib
import io
import json
import os
import threading
import time
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request

import app
import supabase_store as store
from support import HTTPCase


class Book21ImportTests(HTTPCase):
    def setUp(self):
        super().setUp()
        self.payload=b'%PDF-synthetic-owner-file'
        values={'BOOK21_UPLOAD_BYTES':len(self.payload),
                'BOOK21_UPLOAD_SHA':hashlib.sha256(self.payload).hexdigest(),
                'BOOK21_UPLOAD_TOKEN_HASH':app.hash_secret('synthetic-upload-token'),
                'BOOK21_UPLOAD_EXPIRES':time.time()+3600,
                'BOOK21_UPLOAD_DONE':threading.Event(),'BOOK21_UPLOAD_LOCK':threading.Lock()}
        for name,value in values.items():
            p=patch.object(app,name,value);p.start();self.addCleanup(p.stop)
        p=patch.object(store,'upload_book21');self.upload=p.start();self.addCleanup(p.stop)
        p=patch.object(store,'original',return_value=self.payload);self.original=p.start();self.addCleanup(p.stop)

    def send(self,payload=None,headers=None):
        headers={'Content-Type':'application/pdf','X-Upload-Token':'synthetic-upload-token',**(headers or {})}
        request=Request(self.base+'/api/private/upload-book21',data=self.payload if payload is None else payload,headers=headers,method='POST')
        try: response=self.opener.open(request,timeout=5)
        except HTTPError as error: response=error
        return response.status,json.loads(response.read())

    def test_unauthorized_expired_wrong_type_size_or_content_never_reaches_storage(self):
        for headers,expected in [({'X-Upload-Token':''},403),({'X-Upload-Token':'wrong'},403),
                                 ({'Content-Type':'application/json'},415),({'Origin':'https://other.invalid'},403)]:
            self.assertEqual(self.send(headers=headers)[0],expected)
        self.assertEqual(self.send(b'x')[0],413)
        self.assertEqual(self.send(b'x'*len(self.payload))[0],400)
        with patch.object(app,'BOOK21_UPLOAD_EXPIRES',time.time()-1):
            self.assertEqual(self.send()[0],404)
        self.upload.assert_not_called();self.original.assert_not_called()

    def test_exact_file_is_verified_and_endpoint_is_consumed_without_disclosing_bytes(self):
        status,data=self.send();self.assertEqual(status,201)
        self.upload.assert_called_once_with(self.payload)
        self.original.assert_called_once_with('book21.pdf')
        self.assertEqual(data,{'ok':True,'size':len(self.payload),'sha256':hashlib.sha256(self.payload).hexdigest()})
        self.assertEqual(self.send()[0],404)
        self.assertEqual(self.upload.call_count,1)

    def test_review_concurrency_and_failed_verification_do_not_claim_success(self):
        with patch.dict(os.environ,{'TABO_SITE_ENV':'review'}):
            self.assertEqual(self.send()[0],503)
        app.BOOK21_UPLOAD_LOCK.acquire()
        try:self.assertEqual(self.send()[0],409)
        finally:app.BOOK21_UPLOAD_LOCK.release()
        self.upload.assert_not_called()
        self.original.return_value=b'%PDF-wrong'
        self.assertEqual(self.send()[0],500)
        self.assertFalse(app.BOOK21_UPLOAD_DONE.is_set())

    def test_storage_is_private_fixed_destination_create_only_and_uses_server_auth(self):
        actual=store.upload_book21
        # The HTTP tests patch this method; unwrap the production function here.
        import importlib.util
        spec=importlib.util.spec_from_file_location('book21_store_adapter',store.__file__)
        adapter=importlib.util.module_from_spec(spec);spec.loader.exec_module(adapter)
        with patch.object(adapter,'request',return_value={'public':True}),patch.object(adapter,'urlopen') as send:
            with self.assertRaises(adapter.StoreError):adapter.upload_book21(self.payload)
            send.assert_not_called()
        with patch.object(adapter,'request',return_value={'public':False}),patch.object(adapter,'urlopen',return_value=io.BytesIO(b'{}')) as send:
            adapter.upload_book21(self.payload)
            request=send.call_args.args[0]
            self.assertTrue(request.full_url.endswith('/storage/v1/object/tabo-originals/book21.pdf'))
            self.assertEqual(request.get_method(),'POST')
            self.assertEqual(request.get_header('X-upsert'),'false')
            self.assertEqual(request.get_header('Apikey'),'sb_secret_synthetic_not_a_real_key')
            self.assertIsNone(request.get_header('Authorization'))
            self.assertEqual(request.data,self.payload)
