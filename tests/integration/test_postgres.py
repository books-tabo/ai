"""Real PostgreSQL, independent connections. Refuses non-local/non-test databases."""
import concurrent.futures,json,os,subprocess,sys,threading,unittest
import app
from pathlib import Path

DSN=os.environ.get('TABO_TEST_DATABASE_URL')
@unittest.skipUnless(DSN,'Set TABO_TEST_DATABASE_URL for isolated PostgreSQL integration tests')
class PostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg.conninfo import conninfo_to_dict
        info=conninfo_to_dict(DSN)
        if info.get('host') not in ('localhost','127.0.0.1','::1') or not info.get('dbname','').startswith('tabo_test') or os.environ.get('TABO_ALLOW_TEST_DATABASE_RESET')!='1':
            raise RuntimeError('Refusing to initialize a database outside the isolated test boundary')
        cls.connect=staticmethod(lambda:psycopg.connect(DSN))
        with cls.connect() as c:
            c.execute('DROP SCHEMA public CASCADE; CREATE SCHEMA public; GRANT USAGE ON SCHEMA public TO PUBLIC')
            for role in ('anon','authenticated','service_role'):
                if not c.execute('SELECT 1 FROM pg_roles WHERE rolname=%s',(role,)).fetchone():
                    c.execute('CREATE ROLE '+role+' NOLOGIN'+(' BYPASSRLS' if role=='service_role' else ''))
            for path in sorted((Path(__file__).resolve().parents[2]/'supabase/migrations').glob('*.sql')):
                c.execute(path.read_text())

    def setUp(self):
        with self.connect() as c:
            c.execute('TRUNCATE public.payment_events,public.order_downloads,public.book_ratings,public.orders,public.book_stats')
            c.execute("INSERT INTO public.book_stats(book_id,file_size_bytes) SELECT book_id,1234 FROM unnest(%s::text[]) AS book_id", (list(app.BOOKS),))
            c.execute("INSERT INTO public.orders(id,status,items,total,status_key,transaction_id,paid_at,payment_method,payment_mode,paymob_order_id,download_session_hash,download_device_hash,download_session_expires_at,buyer) VALUES('TABO-0000000000000001','PAID',%s,199,%s,'TX-SYNTHETIC',now(),'Kashier','test','synthetic-session',%s,%s,now()+interval '1 hour',%s)",
                      (json.dumps([{'id':book_id} for book_id in app.BOOKS]),'1'*64,'a'*64,'b'*64,json.dumps({'name':'Synthetic','phone':'01000000000'})))

    def autoseo_publish(self, identifier, slug, title='Synthetic article', updated='2026-10-26T12:00:00Z', media=None, barrier=None):
        post={'id':identifier,'slug':slug,'title':title,'description':'Synthetic description',
              'language':'en','published_at':'2026-10-26T12:00:00Z','updated_at':updated,'content_html':'<p>Synthetic</p>'}
        with self.connect() as c:
            c.execute('SET ROLE service_role')
            if barrier:barrier.wait(timeout=15)
            return c.execute('SELECT public.tabo_autoseo_publish(%s::jsonb,%s::jsonb)',
                             (json.dumps(post),json.dumps(media or []))).fetchone()[0]

    def test_autoseo_concurrent_duplicates_are_one_row_and_keep_existing_orders(self):
        barrier=threading.Barrier(12)
        with self.connect() as c:
            before=c.execute('SELECT row_to_json(o) FROM public.orders o ORDER BY id').fetchall()
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            result=list(pool.map(lambda _:self.autoseo_publish(80001,'concurrent-post',barrier=barrier),range(12)))
        self.assertTrue(all(item=={'slug':'concurrent-post'} for item in result))
        with self.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM public.tabo_autoseo_posts WHERE id=80001').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT row_to_json(o) FROM public.orders o ORDER BY id').fetchall(),before)

    def test_autoseo_stale_updates_preserve_newer_content_and_original_url(self):
        self.autoseo_publish(80002,'original-slug')
        self.assertEqual(self.autoseo_publish(80002,'changed-slug','Newer title','2026-10-27T12:00:00Z'),{'slug':'original-slug'})
        self.assertEqual(self.autoseo_publish(80002,'old-slug','Stale title'),{'slug':'original-slug'})
        with self.connect() as c:
            self.assertEqual(c.execute('SELECT slug,title FROM public.tabo_autoseo_posts WHERE id=80002').fetchone(),('original-slug','Newer title'))

    def test_autoseo_slug_collisions_and_media_are_atomic(self):
        self.autoseo_publish(80003,'shared-slug')
        self.autoseo_publish(80005,'shared-slug-80004')
        result=self.autoseo_publish(80004,'shared-slug',media=[{'digest':'a'*64,'content_base64':'U3ludGhldGlj'}])
        self.assertTrue(result['slug'].startswith('shared-slug-80004-'))
        with self.connect() as c:
            self.assertEqual(c.execute("SELECT content_base64 FROM public.tabo_autoseo_media WHERE digest=%s",('a'*64,)).fetchone()[0],'U3ludGhldGlj')
        import psycopg
        with self.assertRaises(psycopg.errors.NotNullViolation):
            self.autoseo_publish(80006,'failed-atomic',title=None,media=[{'digest':'b'*64,'content_base64':'U3ludGhldGlj'}])
        with self.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM public.tabo_autoseo_media WHERE digest=%s',('b'*64,)).fetchone()[0],0)
            self.assertEqual(c.execute('SELECT count(*) FROM public.tabo_autoseo_posts WHERE id=80006').fetchone()[0],0)

    def test_autoseo_objects_are_server_only_and_do_not_change_store_privileges(self):
        for role in ('anon','authenticated'):
            with self.connect() as c:
                for table in ('tabo_autoseo_media','tabo_autoseo_posts'):
                    self.assertFalse(c.execute('SELECT has_table_privilege(%s,%s,\'SELECT\')',(role,'public.'+table)).fetchone()[0])
                self.assertFalse(c.execute("SELECT has_function_privilege(%s,'public.tabo_autoseo_publish(jsonb,jsonb)','EXECUTE')",(role,)).fetchone()[0])
        with self.connect() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM pg_class WHERE relname IN ('tabo_autoseo_media','tabo_autoseo_posts') AND relrowsecurity").fetchone()[0],2)
            self.assertFalse(c.execute("SELECT prosecdef FROM pg_proc WHERE oid='public.tabo_autoseo_publish(jsonb,jsonb)'::regprocedure").fetchone()[0])

    def claim(self,book='book1',session='a'*64,device='b'*64,barrier=None):
        with self.connect() as c:
            c.execute('SET ROLE service_role')
            if barrier:barrier.wait(timeout=15)
            return c.execute('SELECT public.claim_order_download(%s,%s,%s,%s,%s)',('TABO-0000000000000001',book,session,device,20)).fetchone()[0]

    def event(self,kind='paid',transaction='TX-SYNTHETIC',barrier=None,oid='TABO-0000000000000001'):
        with self.connect() as c:
            c.execute('SET ROLE service_role')
            if barrier:barrier.wait(timeout=15)
            return c.execute('SELECT public.apply_payment_event(%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (oid,'kashier','test',transaction,kind,199,'EGP','synthetic-session','d'*64)).fetchone()[0]

    def test_twenty_independent_connections_allow_exactly_two(self):
        barrier=threading.Barrier(20)
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
            results=list(pool.map(lambda _:self.claim(barrier=barrier),range(20)))
        self.assertEqual(sum(r['allowed'] for r in results),2)
        with self.connect() as c:
            self.assertEqual(c.execute('SELECT download_count FROM public.order_downloads').fetchone()[0],2)
            self.assertEqual(c.execute("SELECT download_count FROM public.book_stats WHERE book_id='book1'").fetchone()[0],2)
        self.assertFalse(self.claim()['allowed'])
        # A separate database client also observes the committed counter.
        script="import os,psycopg; c=psycopg.connect(os.environ['TABO_TEST_DATABASE_URL']); print(c.execute(\"SELECT download_count FROM public.order_downloads WHERE book_id='book1'\").fetchone()[0]);c.close()"
        self.assertEqual(subprocess.check_output([sys.executable,'-c',script],text=True).strip(),'2')

    def test_all_catalog_books_have_independent_durable_two_download_limits(self):
        for attempt in (1,2,3):
            for book_id in app.BOOKS:self.assertEqual(self.claim(book_id)['allowed'],attempt<=2)
        with self.connect() as c:
            self.assertEqual(c.execute('SELECT sum(download_count) FROM public.order_downloads').fetchone()[0],2*len(app.BOOKS))
            self.assertEqual(c.execute('SELECT sum(download_count) FROM public.book_stats').fetchone()[0],2*len(app.BOOKS))

    def test_pending_wrong_book_wrong_or_expired_session(self):
        for book,session,device in [('book99','a'*64,'b'*64),('book1','c'*64,'b'*64),('book1','a'*64,'c'*64)]:
            self.assertFalse(self.claim(book,session,device)['allowed'])
        with self.connect() as c:c.execute("UPDATE public.orders SET download_session_expires_at=now()-interval '1 second'")
        self.assertFalse(self.claim()['allowed'])
        with self.connect() as c:c.execute("UPDATE public.orders SET status='PENDING',transaction_id=NULL,paid_at=NULL,download_session_hash=NULL")
        self.assertFalse(self.claim()['allowed'])
        with self.connect() as c:self.assertEqual(c.execute('SELECT count(*) FROM public.order_downloads').fetchone()[0],0)

    def test_real_legacy_compare_and_set_has_one_winner(self):
        with self.connect() as c:c.execute("UPDATE public.orders SET download_session_hash=NULL,download_token=%s,download_token_expires_at=now()+interval '1 hour'",('c'*64,))
        barrier=threading.Barrier(2)
        def activate(n):
            with self.connect() as c:
                barrier.wait(timeout=10)
                return c.execute("UPDATE public.orders SET download_token=NULL,download_token_used_at=now(),download_session_hash=%s WHERE id='TABO-0000000000000001' AND status='PAID' AND download_token=%s AND download_token_used_at IS NULL AND download_token_expires_at>now() AND download_session_hash IS NULL RETURNING id",(str(n)*64,'c'*64)).fetchone()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(activate,(1,2)))
        self.assertEqual(sum(bool(x) for x in results),1)

    def test_atomic_payment_retries_twenty_calls_and_terminal_revocation(self):
        with self.connect() as c:c.execute("UPDATE public.orders SET status='PENDING',paid_at=NULL,transaction_id=NULL,download_session_hash=NULL")
        barrier=threading.Barrier(20)
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:results=list(pool.map(lambda _:self.event(barrier=barrier),range(20)))
        self.assertEqual(sum(not r['duplicate'] for r in results),1)
        with self.connect() as c:self.assertEqual(c.execute('SELECT count(*) FROM public.payment_events').fetchone()[0],1)
        self.assertEqual(self.event('refund','REFUND-SYNTHETIC')['status'],'REFUNDED')
        self.assertEqual(self.event()['status'],'REFUNDED')
        self.assertFalse(self.claim()['allowed'])

    def test_revocation_before_payment_and_partial_refund_suspension(self):
        for kind,status in [('void','VOIDED'),('reversal','REVERSED'),('partial_refund','REVIEW'),('review_notice','REVIEW')]:
            self.setUp()
            with self.connect() as c:c.execute("UPDATE public.orders SET status='PENDING',paid_at=NULL,transaction_id=NULL,download_session_hash=NULL")
            self.assertEqual(self.event(kind)['status'],status)
            self.assertEqual(self.event()['status'],status)
            self.assertFalse(self.claim()['allowed'])

    def test_http_download_counter_survives_application_process_restarts(self):
        import hashlib,hmac
        from urllib.request import Request,build_opener,ProxyHandler
        from urllib.error import HTTPError
        binding='synthetic-restart-binding-key-32-bytes'
        agent='SyntheticRestart/1';raw='synthetic-restart-session'
        with self.connect() as c:
            c.execute('UPDATE public.orders SET download_session_hash=%s,download_device_hash=%s',
                (hashlib.sha256(raw.encode()).hexdigest(),hmac.new(binding.encode(),agent.encode(),hashlib.sha256).hexdigest()))
        env={**os.environ,'TABO_CHECKOUT_ENABLED':'0','TABO_SESSION_BINDING_KEY':binding,'TABO_TRUSTED_PROXY_CIDRS':''}
        opener=build_opener(ProxyHandler({}))
        fixture=Path(__file__).with_name('restart_server.py')
        for expected in (200,200,403):
            process=subprocess.Popen([sys.executable,'-u',str(fixture)],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                port=int(process.stdout.readline().strip())
                request=Request(f'http://127.0.0.1:{port}/api/download/book1',
                    data=json.dumps({'order_id':'TABO-0000000000000001'}).encode(),
                    headers={'Content-Type':'application/json','User-Agent':agent,'Cookie':'__Host-tabo_download_0000000000000001='+raw},method='POST')
                try:response=opener.open(request,timeout=15)
                except HTTPError as error:response=error
                with response:
                    self.assertEqual(response.status,expected)
                    if expected==200:self.assertTrue(response.read().startswith(b'%PDF-'))
            finally:
                process.terminate()
                process.communicate(timeout=10)
        with self.connect() as c:
            self.assertEqual(c.execute('SELECT download_count FROM public.order_downloads').fetchone()[0],2)
            self.assertEqual(c.execute("SELECT download_count FROM public.book_stats WHERE book_id='book1'").fetchone()[0],2)

    def test_browser_roles_cannot_execute_or_read_sensitive_objects(self):
        for role in ('anon','authenticated'):
            with self.connect() as c:
                self.assertFalse(c.execute("SELECT has_function_privilege(%s,'public.claim_order_download(text,text,text,text,integer)','EXECUTE')",(role,)).fetchone()[0])
                self.assertFalse(c.execute("SELECT has_function_privilege(%s,'public.apply_payment_event(text,text,text,text,text,numeric,text,text,text)','EXECUTE')",(role,)).fetchone()[0])
                for table in ('orders','order_downloads','book_stats','book_ratings','payment_events'):
                    self.assertFalse(c.execute('SELECT has_table_privilege(%s,%s,\'SELECT\')',(role,'public.'+table)).fetchone()[0])
        with self.connect() as c:
            rows=c.execute("SELECT prosecdef,proconfig FROM pg_proc WHERE oid IN ('public.claim_order_download(text,text,text,text,integer)'::regprocedure,'public.apply_payment_event(text,text,text,text,text,numeric,text,text,text)'::regprocedure)").fetchall()
            self.assertEqual(len(rows),2)
            self.assertTrue(all(row[0] and 'search_path=""' in row[1] for row in rows))

    def test_pricing_quote_persists_and_must_match_actual_charge(self):
        quote={'displayCurrency':'USD','displayTotal':'9.99','chargeCurrency':'EGP',
               'chargeTotal':'541.16','rate':'54.16975051','rateDate':'2026-10-20',
               'quotedAt':1792497600,'expiresAt':1792497900}
        with self.connect() as c:
            self.assertIsNone(c.execute('SELECT pricing_quote FROM public.orders').fetchone()[0])
            c.execute('SET ROLE service_role')
            c.execute('UPDATE public.orders SET total=541.16,pricing_quote=%s',(json.dumps(quote),))
        # A new independent connection observes the exact persisted quote.
        with self.connect() as c:
            amount,currency,saved=c.execute('SELECT total,currency,pricing_quote FROM public.orders').fetchone()
            self.assertEqual(str(amount),'541.16')
            self.assertEqual(currency,'EGP')
            self.assertEqual(saved,quote)
        import psycopg
        for bad in ({**quote,'chargeTotal':'1.00'},{**quote,'chargeCurrency':'USD'},{'displayCurrency':'USD'}):
            with self.assertRaises(psycopg.errors.CheckViolation):
                with self.connect() as c:
                    c.execute('UPDATE public.orders SET pricing_quote=%s',(json.dumps(bad),))
