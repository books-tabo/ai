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
            c.execute('TRUNCATE public.store_checkout_reminders,public.store_reminder_optouts,public.store_catalog_history,public.store_catalog_covers,public.owner_order_sources,public.owner_store_sessions,public.owner_dashboard_logins,public.order_help_messages,public.order_help_challenges,public.order_receipts,public.paypal_payment_events,public.book_comments,public.payment_events,public.order_downloads,public.book_ratings,public.orders,public.book_stats')
            c.execute("UPDATE public.store_catalog_state SET published='{}'::jsonb,draft='{}'::jsonb,revision=0,draft_revision=0,reminders_enabled=true,published_at=NULL WHERE id")
            c.execute("INSERT INTO public.book_stats(book_id,file_size_bytes) SELECT book_id,1234 FROM unnest(%s::text[]) AS book_id", (list(app.BOOKS),))
            c.execute("INSERT INTO public.orders(id,status,items,total,status_key,transaction_id,paid_at,payment_method,payment_mode,paymob_order_id,download_session_hash,download_device_hash,download_session_expires_at,buyer) VALUES('TABO-0000000000000001','PAID',%s,199,%s,'TX-SYNTHETIC',now(),'Kashier','test','synthetic-session',%s,%s,now()+interval '1 hour',%s)",
                      (json.dumps([{'id':book_id} for book_id in app.BOOKS]),'1'*64,'a'*64,'b'*64,json.dumps({'name':'Synthetic','phone':'01000000000'})))


    def recovery_order(self,number=2,consent=True,mode='live'):
        oid=self.receipt_order(number,mode=mode)
        with self.connect() as c:
            c.execute("UPDATE public.orders SET buyer=jsonb_set(buyer,'{recoveryConsent}',%s::jsonb),created_at=now()-interval '2 hours' WHERE id=%s",(json.dumps(consent),oid))
        return oid

    def enroll_recovery(self,number=2,consent=True,mode='live'):
        oid=self.recovery_order(number,consent,mode)
        accepted=self.owner_rpc('store_recovery_enroll',oid,f'{number:064x}',f'{number+1000:064x}')
        return oid,accepted

    def test_store_catalog_private_draft_publication_conflict_and_audit(self):
        auth=self.owner_auth()
        self.assertIsNone(self.owner_rpc('store_admin_catalog_read','0'*64,*auth[1:]))
        saved=self.owner_rpc('store_admin_catalog_save',*auth,0,json.dumps({'books':{'book1':{'name':'Synthetic title'}}}))
        self.assertEqual(saved['draftRevision'],1)
        with self.connect() as c:self.assertEqual(c.execute('SELECT published FROM public.store_catalog_state').fetchone()[0],{})
        self.assertTrue(self.owner_rpc('store_admin_catalog_save',*auth,0,'{}')['conflict'])
        self.assertTrue(self.owner_rpc('store_admin_catalog_publish',*auth,0)['conflict'])
        result=self.owner_rpc('store_admin_catalog_publish',*auth,1)
        self.assertEqual(result['revision'],1)
        with self.connect() as c:
            self.assertEqual(c.execute('SELECT published FROM public.store_catalog_state').fetchone()[0]['books']['book1']['name'],'Synthetic title')
            self.assertEqual(c.execute('SELECT count(*) FROM public.store_catalog_history').fetchone()[0],1)
        self.assertTrue(self.owner_rpc('owner_session_logout',*auth))
        self.assertIsNone(self.owner_rpc('store_admin_catalog_publish',*auth,1))

    def test_reminder_requires_explicit_consent_live_order_and_hour_delay(self):
        self.assertFalse(self.enroll_recovery(2,consent=False)[1])
        self.assertFalse(self.enroll_recovery(3,mode='test')[1])
        oid,accepted=self.enroll_recovery(4);self.assertTrue(accepted)
        self.assertFalse(self.owner_rpc('store_recovery_enroll',oid,'a'*64,'b'*64))
        with self.connect() as c:c.execute("UPDATE public.store_checkout_reminders SET next_attempt_at=now()+interval '1 hour'")
        self.assertIsNone(self.owner_rpc('store_recovery_claim','d'*64))
        self.assertFalse(self.enroll_recovery(5)[1])  # One enrollment per normalized email/day.

    def test_reminder_concurrent_claim_and_single_transport_completion(self):
        oid,accepted=self.enroll_recovery();self.assertTrue(accepted)
        def claim(i):return self.owner_rpc('store_recovery_claim',f'{i:064x}')
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(claim,range(10,18)))
        self.assertEqual(sum(x is not None for x in results),1)
        with self.connect() as c:lease=c.execute('SELECT lease FROM public.store_checkout_reminders').fetchone()[0]
        self.assertFalse(self.owner_rpc('store_recovery_finish',oid,'0'*64,'sent',None))
        self.assertTrue(self.owner_rpc('store_recovery_finish',oid,lease,'sent',None))
        self.assertIsNone(self.owner_rpc('store_recovery_claim','e'*64))
        value=self.owner_rpc('store_recovery_cart',f'{2:064x}')
        self.assertEqual(set(value),{'items'})
        self.assertTrue(all(set(item)=={'id'} for item in value['items']))

    def test_reminder_stops_for_paid_original_or_another_paid_order(self):
        oid,_=self.enroll_recovery();self.receipt_payment(oid)
        self.assertIsNone(self.owner_rpc('store_recovery_claim','d'*64))
        self.assertTrue(self.owner_rpc('store_recovery_cart',f'{2:064x}')['completed'])
        with self.connect() as c:c.execute('TRUNCATE public.store_checkout_reminders')
        oid=self.recovery_order(3)
        self.assertTrue(self.owner_rpc('store_recovery_enroll',oid,'a'*64,'b'*64))
        self.assertIsNone(self.owner_rpc('store_recovery_claim','d'*64))

    def test_reminder_unsubscribe_post_race_pause_and_dedup_expiry(self):
        auth=self.owner_auth();oid,_=self.enroll_recovery()
        self.assertIsNotNone(self.owner_rpc('store_recovery_claim','d'*64))
        self.assertTrue(self.owner_rpc('store_recovery_unsubscribe',f'{1002:064x}'))
        self.assertFalse(self.owner_rpc('store_recovery_sendable',oid,'d'*64))
        self.assertFalse(self.owner_rpc('store_recovery_finish',oid,'d'*64,'sent',None))
        self.assertFalse(self.enroll_recovery(3)[1])
        report=self.owner_rpc('store_admin_recovery_report',*auth)
        self.assertNotIn('reader@example.invalid',json.dumps(report))
        self.assertTrue(self.owner_rpc('store_admin_recovery_toggle',*auth,False)['ok'])
        self.assertIsNone(self.owner_rpc('store_recovery_claim','e'*64))
        with self.connect() as c:
            c.execute('TRUNCATE public.store_checkout_reminders,public.store_reminder_optouts')
            c.execute('UPDATE public.store_catalog_state SET reminders_enabled=true')
        self.assertTrue(self.owner_rpc('store_recovery_enroll',oid,'a'*64,'b'*64))
        with self.connect() as c:c.execute("UPDATE public.store_checkout_reminders SET created_at=now()-interval '21 hours'")
        self.assertIsNone(self.owner_rpc('store_recovery_claim','e'*64))
        with self.connect() as c:self.assertEqual(c.execute('SELECT state,reason FROM public.store_checkout_reminders').fetchone(),('failed','expired'))

    def test_store_admin_new_tables_and_functions_are_backend_only(self):
        tables=('store_catalog_state','store_catalog_history','store_catalog_covers','store_checkout_reminders','store_reminder_optouts')
        with self.connect() as c:
            for name in tables:
                self.assertTrue(c.execute('SELECT relrowsecurity FROM pg_class WHERE oid=%s::regclass',('public.'+name,)).fetchone()[0])
                for role in ('anon','authenticated'):
                    for permission in ('SELECT','INSERT','UPDATE','DELETE'):
                        self.assertFalse(c.execute('SELECT has_table_privilege(%s,%s,%s)',(role,'public.'+name,permission)).fetchone()[0])
            funcs=c.execute("SELECT oid,prosecdef FROM pg_proc WHERE pronamespace='public'::regnamespace AND (proname LIKE 'store_admin_%' OR proname LIKE 'store_recovery_%')").fetchall()
            self.assertEqual(len(funcs),11)
            for oid,definer in funcs:
                self.assertFalse(definer)
                for role in ('anon','authenticated'):self.assertFalse(c.execute("SELECT has_function_privilege(%s,%s,'EXECUTE')",(role,oid)).fetchone()[0])

    def owner_rpc(self,name,*args):
        with self.connect() as c:
            c.execute('SET ROLE service_role')
            return c.execute('SELECT public.'+name+'('+','.join(['%s']*len(args))+')',args).fetchone()[0]

    def owner_auth(self):
        cid='a'*64
        self.assertTrue(self.owner_rpc('owner_login_request',cid,'e'*64,'f'*64,'d'*64,'c'*64))
        self.assertTrue(self.owner_rpc('owner_login_verify',cid,'e'*64,'d'*64,'c'*64,'b'*64))
        return (cid,'e'*64,'d'*64,'b'*64)

    def owner_report(self,auth,country=None,status=None,query=None,offset=0):
        from datetime import datetime,timedelta,timezone
        current=datetime.now(timezone.utc)
        return self.owner_rpc('owner_dashboard_report',*auth,(current-timedelta(days=31)).isoformat(),(current+timedelta(days=1)).isoformat(),country,status,query,offset)

    def test_owner_auth_atomic_cooldown_guess_limit_expiry_logout_and_roles(self):
        def request(n):return self.owner_rpc('owner_login_request',f'{n:064x}','e'*64,'f'*64,'d'*64,'c'*64)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(request,range(1,9)))
        self.assertEqual(sum(results),1)
        with self.connect() as c:cid=c.execute('SELECT id FROM public.owner_dashboard_logins').fetchone()[0]
        self.assertFalse(self.owner_rpc('owner_login_verify',cid,'e'*64,'0'*64,'c'*64,'b'*64))
        for _ in range(5):self.assertFalse(self.owner_rpc('owner_login_verify',cid,'e'*64,'d'*64,'0'*64,'b'*64))
        self.assertFalse(self.owner_rpc('owner_login_verify',cid,'e'*64,'d'*64,'c'*64,'b'*64))
        with self.connect() as c:c.execute("UPDATE public.owner_dashboard_logins SET created_at=now()-interval '2 minutes'")
        self.assertTrue(request(20));cid=f'{20:064x}'
        self.assertTrue(self.owner_rpc('owner_login_verify',cid,'e'*64,'d'*64,'c'*64,'b'*64))
        self.assertFalse(self.owner_rpc('owner_login_verify',cid,'e'*64,'d'*64,'c'*64,'0'*64))
        self.assertTrue(self.owner_rpc('owner_session_valid',cid,'e'*64,'d'*64,'b'*64))
        self.assertFalse(self.owner_rpc('owner_session_valid',cid,'1'*64,'d'*64,'b'*64))
        self.assertTrue(self.owner_rpc('owner_session_logout',cid,'e'*64,'d'*64,'b'*64))
        self.assertFalse(self.owner_rpc('owner_session_valid',cid,'e'*64,'d'*64,'b'*64))
        with self.connect() as c:
            for role in ('anon','authenticated'):
                for table in ('owner_dashboard_logins','owner_store_sessions','owner_order_sources'):
                    self.assertTrue(c.execute("SELECT relrowsecurity FROM pg_class WHERE oid=%s::regclass",('public.'+table,)).fetchone()[0])
                    for privilege in ('SELECT','INSERT','UPDATE','DELETE'):
                        self.assertFalse(c.execute('SELECT has_table_privilege(%s,%s,%s)',(role,'public.'+table,privilege)).fetchone()[0])
                funcs=c.execute("SELECT p.oid FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.proname LIKE 'owner_%'").fetchall()
                self.assertEqual(len(funcs),9)
                for (oid,) in funcs:self.assertFalse(c.execute("SELECT has_function_privilege(%s,%s,'EXECUTE')",(role,oid)).fetchone()[0])
            self.assertEqual(c.execute("SELECT count(*) FROM pg_proc WHERE proname LIKE 'owner_%' AND prosecdef").fetchone()[0],0)

    def test_owner_reports_paid_live_separate_currencies_private_allowlist_and_filters(self):
        auth=self.owner_auth();usd=self.receipt_order(2);self.receipt_payment(usd,number=2)
        egp=self.receipt_order(3);self.receipt_payment(egp,number=3)
        refund=self.receipt_order(4);self.receipt_payment(refund,number=4)
        self.receipt_order(5)
        with self.connect() as c:
            c.execute("UPDATE public.orders SET country='SA' WHERE id=%s",(usd,))
            c.execute("UPDATE public.orders SET total=500,currency='EGP' WHERE id=%s",(egp,))
            c.execute("UPDATE public.orders SET status='REFUNDED',checkout_session_hash=NULL,download_session_hash=NULL,download_token=NULL WHERE id=%s",(refund,))
            c.execute("INSERT INTO public.order_downloads(order_id,book_id,download_count) VALUES(%s,'book20',1)",(egp,))
        result=self.owner_report(auth)
        self.assertEqual(result['paidOrders'],2);self.assertEqual(result['pendingOrders'],1)
        self.assertEqual({x['currency']:x['amount'] for x in result['revenue']},{'EGP':'500','USD':'20'})
        self.assertEqual({x['book_id']:x['units'] for x in result['books']},{'book20':2,'book21':2})
        self.assertEqual(result['followup']['withoutDownloads'],1)
        encoded=json.dumps(result)
        for forbidden in ('status_key','download_token','checkout_session_hash','transaction_id','reader@example.invalid','Synthetic Reader','01000000000'):
            self.assertNotIn(forbidden,encoded)
        self.assertEqual(self.owner_report(auth,country='SA')['paidOrders'],1)
        filtered=self.owner_report(auth,status='REFUNDED');self.assertEqual(filtered['orderCount'],1);self.assertEqual(filtered['orders'][0]['id'],refund)
        self.assertEqual(self.owner_report(auth,query=usd)['orderCount'],1)
        self.assertEqual(self.owner_report(auth,offset=20)['orders'],[])
        self.assertIsNone(self.owner_report(('0'*64,*auth[1:])))
        with self.connect() as c:self.assertEqual(c.execute('SELECT sum(download_count) FROM public.order_downloads').fetchone()[0],1)

    def test_owner_sessions_idempotent_steps_cannot_claim_purchase_and_source_binding(self):
        auth=self.owner_auth();sid='1'*64;device='d'*64
        self.assertEqual(self.owner_rpc('owner_insight_start',None,sid,device,'f'*64,'google','paid','SA','/books/book20')['ttl'],1800)
        resumed=self.owner_rpc('owner_insight_start',sid,'2'*64,device,'f'*64,'direct','direct','EG','/checkout');self.assertTrue(resumed['resumed'])
        for step in ('product','cart','checkout'):
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
                self.assertTrue(all(pool.map(lambda _:self.owner_rpc('owner_insight_event',sid,device,step),range(5))))
        self.assertFalse(self.owner_rpc('owner_insight_event',sid,device,'Purchase'))
        self.assertFalse(self.owner_rpc('owner_insight_event',sid,'0'*64,'cart'))
        self.assertEqual(self.owner_report(auth)['funnel'],{'sessions':1,'product':1,'cart':1,'checkout':1,'paid':0})
        oid=self.receipt_order(2);self.assertTrue(self.owner_rpc('owner_insight_order',sid,device,oid))
        self.assertFalse(self.owner_rpc('owner_insight_order',sid,device,oid))
        self.receipt_payment(oid,number=2)
        result=self.owner_report(auth);self.assertEqual(result['funnel']['paid'],1);self.assertEqual(result['sources'][0]['source'],'google');self.assertEqual(result['pageViews'],2)
        with self.connect() as c:
            c.execute("UPDATE public.orders SET status='REFUNDED',checkout_session_hash=NULL,download_session_hash=NULL,download_token=NULL WHERE id=%s",(oid,))
            c.execute("UPDATE public.owner_store_sessions SET expires_at=now()-interval '1 second'")
        self.assertEqual(self.owner_report(auth)['funnel']['paid'],0)
        self.assertFalse(self.owner_rpc('owner_insight_event',sid,device,'cart'))
        with self.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM public.owner_order_sources').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT count(*) FROM public.order_downloads').fetchone()[0],0)

    def test_owner_login_hour_limit_and_pruning_never_changes_orders(self):
        for n in range(1,6):
            self.assertTrue(self.owner_rpc('owner_login_request',f'{n:064x}','e'*64,'f'*64,'d'*64,'c'*64))
            with self.connect() as c:c.execute("UPDATE public.owner_dashboard_logins SET created_at=created_at-interval '2 minutes'")
        self.assertFalse(self.owner_rpc('owner_login_request','9'*64,'e'*64,'f'*64,'d'*64,'c'*64))
        with self.connect() as c:
            count=c.execute('SELECT count(*) FROM public.orders').fetchone()[0]
            c.execute("UPDATE public.owner_dashboard_logins SET created_at=now()-interval '3 days'")
        self.owner_rpc('owner_dashboard_prune')
        with self.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM public.owner_dashboard_logins').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT count(*) FROM public.orders').fetchone()[0],count)

    def help_request(self, oid, number=1, email='reader@example.invalid', email_hash='e'*64, client_hash='f'*64):
        with self.connect() as c:
            c.execute('SET ROLE service_role')
            return c.execute('SELECT public.request_order_help(%s,%s,%s,%s,%s,%s,%s,%s)',
                (f'{number:064x}',oid,email,email_hash,client_hash,'d'*64,'c'*64,'ar')).fetchone()[0]

    def help_rpc(self, name, args):
        with self.connect() as c:
            c.execute('SET ROLE service_role')
            return c.execute('SELECT public.'+name+'('+','.join(['%s']*len(args))+')',args).fetchone()[0]

    def test_help_unknown_email_privacy_atomic_cooldown_and_attempt_limit(self):
        oid=self.receipt_order(requested=False)
        self.assertFalse(self.help_request(oid,email='wrong@example.invalid'))
        self.assertFalse(self.help_request('TABO-FFFFFFFFFFFFFFFF'))
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(lambda n:self.help_request(oid,number=n),range(1,9)))
        self.assertEqual(sum(results),1)
        with self.connect() as c:
            cid=c.execute('SELECT id FROM public.order_help_challenges').fetchone()[0]
            self.assertEqual(c.execute('SELECT count(*) FROM public.order_help_messages').fetchone()[0],1)
        self.assertFalse(self.help_rpc('verify_order_help',(cid,'c'*64,'a'*64,'0'*64)))
        for _ in range(5):self.assertFalse(self.help_rpc('verify_order_help',(cid,'0'*64,'a'*64,'d'*64)))
        self.assertFalse(self.help_rpc('verify_order_help',(cid,'c'*64,'a'*64,'d'*64)))
        self.assertIsNone(self.help_rpc('order_help_order',(cid,'a'*64,'d'*64)))
        with self.connect() as c:
            self.assertEqual(c.execute('SELECT attempts FROM public.order_help_challenges').fetchone()[0],5)
            self.assertEqual(c.execute('SELECT count(*) FROM public.order_downloads').fetchone()[0],0)

    def test_help_verification_one_use_expiry_session_and_receipt_idempotency(self):
        oid=self.receipt_order(requested=False);self.receipt_payment(oid)
        self.assertTrue(self.help_request(oid));cid=f'{1:064x}'
        self.assertTrue(self.help_rpc('verify_order_help',(cid,'c'*64,'a'*64,'d'*64)))
        self.assertFalse(self.help_rpc('verify_order_help',(cid,'c'*64,'b'*64,'d'*64)))
        self.assertEqual(self.help_rpc('order_help_order',(cid,'a'*64,'d'*64)),oid)
        self.assertIsNone(self.help_rpc('order_help_order',(cid,'a'*64,'0'*64)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(lambda _:self.help_rpc('resend_order_help_receipt',(cid,'a'*64,'d'*64)),range(8)))
        self.assertTrue(all(r['accepted'] for r in results))
        with self.connect() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM public.order_help_messages WHERE kind='receipt'").fetchone()[0],1)
            self.assertEqual(c.execute('SELECT count(*) FROM public.order_receipts').fetchone()[0],0)
            self.assertFalse(c.execute("SELECT (buyer->>'receiptRequested')::boolean FROM public.orders WHERE id=%s",(oid,)).fetchone()[0])
            c.execute("UPDATE public.order_help_challenges SET session_expires_at=now()-interval '1 second'")
        self.assertIsNone(self.help_rpc('resend_order_help_receipt',(cid,'a'*64,'d'*64)))
        self.assertIsNone(self.help_rpc('order_help_order',(cid,'a'*64,'d'*64)))

    def test_help_durable_hourly_and_resend_limits_and_paid_only(self):
        orders=[self.receipt_order(n,requested=False) for n in range(2,14)]
        for index,oid in enumerate(orders[:5]):self.assertTrue(self.help_request(oid,number=index+1))
        self.assertFalse(self.help_request(orders[5],number=6))
        for index,oid in enumerate(orders[5:10]):self.assertTrue(self.help_request(oid,number=index+6,email_hash='a'*64))
        self.assertFalse(self.help_request(orders[10],number=11,email_hash='b'*64))
        cid=f'{1:064x}';self.assertTrue(self.help_rpc('verify_order_help',(cid,'c'*64,'a'*64,'d'*64)))
        self.assertEqual(self.help_rpc('resend_order_help_receipt',(cid,'a'*64,'d'*64))['reason'],'not_paid')
        with self.connect() as c:
            c.execute("UPDATE public.orders SET status='PAID',transaction_id='SYNTHETIC',paid_at=now() WHERE id=%s",(orders[0],))
        self.assertTrue(self.help_rpc('resend_order_help_receipt',(cid,'a'*64,'d'*64))['accepted'])
        for attempt in range(2,5):
            with self.connect() as c:
                c.execute("UPDATE public.order_help_challenges SET created_at=now()-interval '2 hours'")
                c.execute("UPDATE public.order_help_messages SET created_at=now()-interval '11 minutes'")
            self.assertTrue(self.help_request(orders[0],number=20+attempt,email_hash='e'*64))
            current=f'{20+attempt:064x}';self.assertTrue(self.help_rpc('verify_order_help',(current,'c'*64,'a'*64,'d'*64)))
            result=self.help_rpc('resend_order_help_receipt',(current,'a'*64,'d'*64))
            self.assertEqual(result.get('reason'),'rate_limited' if attempt==4 else None)
        # A separate fresh challenge proves the 10-minute cooldown independently of the daily cap.
        oid=orders[1]
        with self.connect() as c:
            c.execute("UPDATE public.orders SET status='PAID',transaction_id='SYNTHETIC2',paid_at=now() WHERE id=%s",(oid,))
        self.assertTrue(self.help_request(oid,number=50));cid=f'{50:064x}'
        self.assertTrue(self.help_rpc('verify_order_help',(cid,'c'*64,'a'*64,'d'*64)))
        self.assertTrue(self.help_rpc('resend_order_help_receipt',(cid,'a'*64,'d'*64))['accepted'])
        with self.connect() as c:c.execute("UPDATE public.order_help_challenges SET created_at=now()-interval '2 hours'")
        self.assertTrue(self.help_request(oid,number=51));cid=f'{51:064x}'
        self.assertTrue(self.help_rpc('verify_order_help',(cid,'c'*64,'a'*64,'d'*64)))
        self.assertEqual(self.help_rpc('resend_order_help_receipt',(cid,'a'*64,'d'*64))['reason'],'rate_limited')

    def test_help_concurrent_queue_leases_expiry_and_private_permissions(self):
        oid=self.receipt_order();self.assertTrue(self.help_request(oid));cid=f'{1:064x}'
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(lambda n:(n,self.help_rpc('claim_order_help_message',(f'{n:064x}',))),range(1,9)))
        claims=[(n,r) for n,r in results if r];self.assertEqual(len(claims),1);lease,claim=claims[0]
        self.assertEqual(claim['kind'],'otp');self.assertNotIn('email',claim)
        self.assertFalse(self.help_rpc('finish_order_help_message',(cid,'otp','0'*64,'sent','synthetic',30,None)))
        self.assertTrue(self.help_rpc('finish_order_help_message',(cid,'otp',f'{lease:064x}','retry',None,30,'transient')))
        self.assertIsNone(self.help_rpc('claim_order_help_message',('a'*64,)))
        with self.connect() as c:c.execute("UPDATE public.order_help_messages SET next_attempt_at=now()-interval '1 second'")
        self.assertEqual(self.help_rpc('claim_order_help_message',('a'*64,))['attempts'],2)
        self.assertFalse(self.help_rpc('finish_order_help_message',(cid,'otp',f'{lease:064x}','sent','synthetic',30,None)))
        with self.connect() as c:
            c.execute("UPDATE public.order_help_challenges SET expires_at=now()-interval '1 second'")
            c.execute("UPDATE public.order_help_messages SET next_attempt_at=now()-interval '1 second'")
        self.assertFalse(self.help_rpc('verify_order_help',(cid,'c'*64,'a'*64,'d'*64)))
        self.assertIsNone(self.help_rpc('claim_order_help_message',('b'*64,)))
        with self.connect() as c:
            self.assertEqual(c.execute('SELECT state,reason FROM public.order_help_messages').fetchone(),('skipped','expired'))
            for role in ('anon','authenticated'):
                for table in ('order_help_challenges','order_help_messages'):
                    self.assertTrue(c.execute("SELECT relrowsecurity FROM pg_class WHERE oid=%s::regclass",('public.'+table,)).fetchone()[0])
                    for privilege in ('SELECT','INSERT','UPDATE','DELETE'):
                        self.assertFalse(c.execute('SELECT has_table_privilege(%s,%s,%s)',(role,'public.'+table,privilege)).fetchone()[0])
                for signature in ('request_order_help(text,text,text,text,text,text,text,text)','verify_order_help(text,text,text,text)','order_help_order(text,text,text)','resend_order_help_receipt(text,text,text)','claim_order_help_message(text)','finish_order_help_message(text,text,text,text,text,integer,text)'):
                    self.assertFalse(c.execute("SELECT has_function_privilege(%s,%s,'EXECUTE')",(role,'public.'+signature)).fetchone()[0])
            self.assertEqual(c.execute("SELECT count(*) FROM pg_proc WHERE proname LIKE '%order_help%' AND prosecdef").fetchone()[0],0)

    def paypal_order(self, number=2):
        oid=f'TABO-{number:016X}'
        with self.connect() as c:
            c.execute("INSERT INTO public.orders(id,status,items,total,currency,payment_method,status_key,payment_mode,paymob_order_id,buyer,checkout_session_hash) VALUES(%s,'PENDING',%s,20,'USD','PayPal',%s,'test',%s,%s,%s)",
                (oid,json.dumps([{'id':'book20'},{'id':'book21'}]),f'{number:064x}',f'PAYPALORDER{number:06d}',json.dumps({'name':'Synthetic','phone':'01000000000'}),f'{number:064x}'))
        return oid

    def paypal_event(self, oid, kind='paid', transaction='CAPTURE12345678901', number=2, barrier=None):
        with self.connect() as c:
            c.execute('SET ROLE service_role')
            if barrier:barrier.wait(timeout=15)
            return c.execute('SELECT public.apply_paypal_event(%s,%s,%s,%s,%s,%s,%s)',
                             (oid,'test',transaction,kind,20,f'PAYPALORDER{number:06d}','f'*64)).fetchone()[0]

    def receipt_order(self, number=2, mode='live', requested=True):
        oid=self.paypal_order(number)
        with self.connect() as c:
            c.execute("UPDATE public.orders SET payment_mode=%s,buyer=%s WHERE id=%s",
                      (mode,json.dumps({'name':'Synthetic Reader','phone':'01000000000','email':'reader@example.invalid','language':'ar','receiptRequested':requested}),oid))
        return oid

    def receipt_payment(self, oid, number=2, mode='live', barrier=None):
        with self.connect() as c:
            c.execute('SET ROLE service_role')
            if barrier:barrier.wait(timeout=15)
            return c.execute('SELECT public.apply_paypal_event(%s,%s,%s,%s,%s,%s,%s)',
                             (oid,mode,f'RECEIPTCAP{number:08d}','paid',20,f'PAYPALORDER{number:06d}','f'*64)).fetchone()[0]

    def test_receipt_enqueue_is_atomic_future_paid_only_and_duplicate_safe(self):
        oid=self.receipt_order();barrier=threading.Barrier(8)
        with self.connect() as c:self.assertEqual(c.execute('SELECT count(*) FROM public.order_receipts').fetchone()[0],0)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(lambda _:self.receipt_payment(oid,barrier=barrier),range(8)))
        self.assertTrue(all(r['status']=='PAID' for r in results))
        with self.connect() as c:
            self.assertEqual(c.execute('SELECT order_id,state,attempts FROM public.order_receipts').fetchall(),[(oid,'pending',0)])
            c.execute("UPDATE public.orders SET buyer=%s WHERE id='TABO-0000000000000001'",(json.dumps({'email':'old@example.invalid','receiptRequested':True}),))
            self.assertEqual(c.execute('SELECT count(*) FROM public.order_receipts').fetchone()[0],1)
        test=self.receipt_order(3,mode='test');self.receipt_payment(test,number=3,mode='test')
        opted_out=self.receipt_order(4,requested=False);self.receipt_payment(opted_out,number=4)
        with self.connect() as c:self.assertEqual(c.execute('SELECT count(*) FROM public.order_receipts').fetchone()[0],1)

    def test_receipt_queue_leases_retries_and_completion_are_concurrent_safe(self):
        oid=self.receipt_order();self.receipt_payment(oid)
        def claim(index):
            with self.connect() as c:
                c.execute('SET ROLE service_role')
                return index,c.execute('SELECT public.claim_order_receipt(%s)',(f'{index:064x}',)).fetchone()[0]
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(claim,range(1,9)))
        claimed=[(i,r) for i,r in results if r]
        self.assertEqual(len(claimed),1);index,value=claimed[0]
        self.assertEqual(value['order_id'],oid);self.assertEqual(value['attempts'],1)
        with self.connect() as c:
            c.execute('SET ROLE service_role')
            args=(oid,f'{index:064x}','retry',None,30,'transient')
            self.assertFalse(c.execute('SELECT public.finish_order_receipt(%s,%s,%s,%s,%s,%s)',(oid,'0'*64,'sent','synthetic',30,None)).fetchone()[0])
            self.assertTrue(c.execute('SELECT public.finish_order_receipt(%s,%s,%s,%s,%s,%s)',args).fetchone()[0])
            self.assertIsNone(c.execute('SELECT public.claim_order_receipt(%s)',('a'*64,)).fetchone()[0])
        with self.connect() as c:c.execute("UPDATE public.order_receipts SET next_attempt_at=now()-interval '1 second'")
        with self.connect() as c:
            c.execute('SET ROLE service_role')
            self.assertEqual(c.execute('SELECT public.claim_order_receipt(%s)',('a'*64,)).fetchone()[0]['attempts'],2)
            self.assertFalse(c.execute('SELECT public.finish_order_receipt(%s,%s,%s,%s,%s,%s)',(oid,f'{index:064x}','sent','synthetic',30,None)).fetchone()[0])
            self.assertTrue(c.execute('SELECT public.finish_order_receipt(%s,%s,%s,%s,%s,%s)',(oid,'a'*64,'sent','synthetic',30,None)).fetchone()[0])
            self.assertIsNone(c.execute('SELECT public.claim_order_receipt(%s)',('b'*64,)).fetchone()[0])

    def test_receipt_queue_stops_before_provider_deduplication_expiry(self):
        oid=self.receipt_order();self.receipt_payment(oid)
        with self.connect() as c:c.execute("UPDATE public.order_receipts SET created_at=now()-interval '21 hours'")
        with self.connect() as c:
            c.execute('SET ROLE service_role')
            self.assertIsNone(c.execute('SELECT public.claim_order_receipt(%s)',('c'*64,)).fetchone()[0])
            self.assertEqual(c.execute('SELECT state,reason FROM public.order_receipts').fetchone(),('failed','expired'))

    def test_receipt_data_and_functions_are_server_only(self):
        with self.connect() as c:
            self.assertTrue(c.execute("SELECT relrowsecurity FROM pg_class WHERE oid='public.order_receipts'::regclass").fetchone()[0])
            for role in ('anon','authenticated'):
                for privilege in ('SELECT','INSERT','UPDATE','DELETE'):
                    self.assertFalse(c.execute("SELECT has_table_privilege(%s,'public.order_receipts',%s)",(role,privilege)).fetchone()[0])
                for signature in ('public.claim_order_receipt(text)','public.finish_order_receipt(text,text,text,text,integer,text)','public.enqueue_order_receipt()'):
                    self.assertFalse(c.execute("SELECT has_function_privilege(%s,%s,'EXECUTE')",(role,signature)).fetchone()[0])
            self.assertEqual(c.execute("SELECT count(*) FROM pg_proc WHERE proname IN ('enqueue_order_receipt','claim_order_receipt','finish_order_receipt') AND prosecdef").fetchone()[0],0)

    def test_isolated_sandbox_transport_uses_real_payment_and_download_rpcs(self):
        from testing.paypal_sandbox import NativeStore
        from unittest.mock import patch
        native=NativeStore(DSN)
        oid='TABO-AABBCCDDEEFF0011'
        order={'id':oid,'status':'PENDING','items':[{'id':'book20','name':'Synthetic title'}],
               'total':20,'currency':'USD','paymentMethod':'PayPal','paymentMode':'test',
               'statusKey':'f'*64,'country':'SA','checkoutSessionHash':'e'*64,
               'buyer':{'name':'Must not persist visitor data','phone':'1234567890'}}
        with patch.object(app.store,'request',native.request):
            app.store.insert(order)
            found=app.store.find('status_key','f'*64)
            self.assertEqual(found['buyer'],{'name':'Synthetic Sandbox Buyer','phone':'01000000000'})
            self.assertEqual(found['status'],'PENDING')
            order=app.store.patch(oid,{'paymob_order_id':'PAYPALORDER000033'},{'status':'eq.PENDING'})
            event={'transaction':'SBOXCAPTURE1234567','kind':'paid','amount':'20.00'}
            self.assertEqual(app.store.apply_paypal_event(order,event,'d'*64)['status'],'PAID')
            paid=app.store.find('id',oid)
            self.assertIsNotNone(paid['paidAt'])
            bound=app.store.patch(oid,{'download_session_hash':'c'*64,'download_device_hash':'d'*64,
                                     'download_session_expires_at':'2099-01-01T00:00:00Z'},
                                 {'status':'eq.PAID','download_session_hash':'is.null','checkout_session_hash':'eq.'+'e'*64})
            self.assertEqual(bound['downloadSessionHash'],'c'*64)
            for allowed in (True,True,False):
                self.assertEqual(app.store.claim_download(oid,'book20','c'*64,'d'*64,1)['allowed'],allowed)
            self.assertEqual(app.store.download_usage(oid),{'book20':2})
            # Recording remains durable for the owner, while public metadata
            # no longer exposes how often a title has been downloaded.
            with self.connect() as c:
                self.assertEqual(c.execute("SELECT download_count FROM public.book_stats WHERE book_id='book20'").fetchone()[0],2)
            self.assertNotIn('downloads',app.store.book_stats()['book20'])
            self.assertEqual(app.store.book_comments('book20'),{'comments':[],'total':0})
            event['kind']='refund'
            self.assertEqual(app.store.apply_paypal_event(order,event,'d'*64)['status'],'REFUNDED')
            self.assertFalse(app.store.claim_download(oid,'book20','c'*64,'d'*64,1)['allowed'])
            with self.assertRaises(ValueError):app.store.patch(oid,{'status':'PAID'})
            with self.assertRaises(ValueError):native.request('/rest/v1/orders',params={'id; SELECT 1':'eq.bad'})
            with self.assertRaises(ValueError):native.request('/rest/v1/rpc/apply_payment_event','POST',{})

    def test_paypal_concurrent_callbacks_mark_one_order_once_without_changing_kashier(self):
        oid=self.paypal_order();barrier=threading.Barrier(12)
        with self.connect() as c:
            before=c.execute("SELECT row_to_json(o) FROM public.orders o WHERE id='TABO-0000000000000001'").fetchone()[0]
            stats=c.execute('SELECT row_to_json(s) FROM public.book_stats s ORDER BY book_id').fetchall()
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            results=list(pool.map(lambda _:self.paypal_event(oid,barrier=barrier),range(12)))
        self.assertTrue(all(result['ok'] and result['status']=='PAID' for result in results))
        with self.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM public.paypal_payment_events').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT count(*) FROM public.payment_events').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT row_to_json(s) FROM public.book_stats s ORDER BY book_id').fetchall(),stats)
            self.assertEqual(c.execute("SELECT row_to_json(o) FROM public.orders o WHERE id='TABO-0000000000000001'").fetchone()[0],before)

    def test_paypal_capture_is_unique_across_orders_and_refund_remains_terminal(self):
        first=self.paypal_order();second=self.paypal_order(3)
        self.assertTrue(self.paypal_event(first)['ok'])
        conflict=self.paypal_event(second,number=3)
        self.assertFalse(conflict['ok']);self.assertEqual(conflict['reason'],'transaction_conflict')
        self.assertEqual(self.paypal_event(first,'refund')['status'],'REFUNDED')
        self.assertEqual(self.paypal_event(first)['status'],'REFUNDED')
        with self.connect() as c:
            self.assertEqual(c.execute('SELECT status,checkout_session_hash,download_session_hash FROM public.orders WHERE id=%s',(first,)).fetchone(),('REFUNDED',None,None))

    def test_paypal_refund_before_paid_never_reactivates_and_unauthorized_roles_cannot_write(self):
        oid=self.paypal_order()
        self.assertEqual(self.paypal_event(oid,'review_notice')['status'],'REVIEW')
        self.assertEqual(self.paypal_event(oid)['status'],'REVIEW')
        with self.connect() as c:
            for role in ('anon','authenticated'):
                self.assertFalse(c.execute("SELECT has_table_privilege(%s,'public.paypal_payment_events','SELECT')",(role,)).fetchone()[0])
                self.assertFalse(c.execute("SELECT has_function_privilege(%s,'public.apply_paypal_event(text,text,text,text,numeric,text,text)','EXECUTE')",(role,)).fetchone()[0])
            self.assertFalse(c.execute("SELECT has_table_privilege('service_role','public.paypal_payment_events','INSERT')").fetchone()[0])
            self.assertTrue(c.execute("SELECT relrowsecurity FROM pg_class WHERE oid='public.paypal_payment_events'::regclass").fetchone()[0])

    def comment(self, email='c'*64, order_hash=None, body='Synthetic honest opinion', barrier=None):
        with self.connect() as c:
            c.execute('SET ROLE service_role')
            if barrier:barrier.wait(timeout=15)
            return c.execute('SELECT public.submit_reader_comment(%s,%s,%s,%s,%s)',
                             ('book20',email,'Synthetic reader',body,order_hash)).fetchone()[0]

    def test_reader_comments_are_pending_private_and_do_not_modify_real_counters(self):
        with self.connect() as c:
            before=c.execute('SELECT row_to_json(s) FROM public.book_stats s ORDER BY book_id').fetchall()
        self.assertEqual(self.comment(),{'accepted':True})
        self.assertEqual(self.comment(body='Duplicate must not replace original'),{'accepted':True})
        with self.connect() as c:
            self.assertEqual(c.execute('SELECT status,body,verified_purchase FROM public.book_comments').fetchone(),
                             ('pending','Synthetic honest opinion',False))
            c.execute('SET ROLE service_role')
            self.assertEqual(c.execute("SELECT public.list_reader_comments('book20',0)").fetchone()[0],{'comments':[],'total':0})
            c.execute("UPDATE public.book_comments SET status='approved',moderated_at=now()")
            result=c.execute("SELECT public.list_reader_comments('book20',0)").fetchone()[0]
            self.assertEqual(result['total'],1)
            self.assertEqual(set(result['comments'][0]),{'id','display_name','body','verified_purchase','created_at'})
            self.assertEqual(c.execute('SELECT row_to_json(s) FROM public.book_stats s ORDER BY book_id').fetchall(),before)

    def test_reader_comment_unique_email_and_verified_order_survive_races(self):
        barrier=threading.Barrier(12)
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            results=list(pool.map(lambda _:self.comment(order_hash='d'*64,barrier=barrier),range(12)))
        self.assertTrue(all(result=={'accepted':True} for result in results))
        self.comment(email='e'*64,order_hash='d'*64)
        with self.connect() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM public.book_comments').fetchone()[0],1)
            self.assertTrue(c.execute('SELECT verified_purchase FROM public.book_comments').fetchone()[0])

    def test_reader_comment_database_access_and_constraints(self):
        import psycopg
        with self.connect() as c:
            self.assertTrue(c.execute("SELECT relrowsecurity FROM pg_class WHERE oid='public.book_comments'::regclass").fetchone()[0])
            self.assertFalse(c.execute("SELECT prosecdef FROM pg_proc WHERE oid='public.submit_reader_comment(text,text,text,text,text)'::regprocedure").fetchone()[0])
            for role in ('anon','authenticated'):
                for privilege in ('SELECT','INSERT','UPDATE','DELETE'):
                    self.assertFalse(c.execute("SELECT has_table_privilege(%s,'public.book_comments',%s)",(role,privilege)).fetchone()[0])
                for signature in ('public.submit_reader_comment(text,text,text,text,text)','public.list_reader_comments(text,integer)'):
                    self.assertFalse(c.execute("SELECT has_function_privilege(%s,%s,'EXECUTE')",(role,signature)).fetchone()[0])
        for values in (('bad-hash','Honest opinion'),('c'*64,'tiny')):
            with self.assertRaises(psycopg.errors.CheckViolation):
                self.comment(email=values[0],body=values[1])

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
