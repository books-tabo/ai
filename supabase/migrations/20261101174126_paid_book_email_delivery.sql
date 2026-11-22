-- Future purchases only. Originals and stamped copies are never made public.
CREATE TABLE public.order_book_emails (
 order_id text NOT NULL REFERENCES public.orders(id) ON DELETE CASCADE,
 book_id text NOT NULL CHECK (book_id ~ '^book[1-9][0-9]{0,3}$'),
 state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','sending','retry','sent','failed','skipped')),
 created_at timestamptz NOT NULL DEFAULT now(),
 next_attempt_at timestamptz NOT NULL DEFAULT now(),
 attempts integer NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 8),
 lease text CHECK (lease IS NULL OR lease ~ '^[0-9a-f]{64}$'),
 payload_sha256 text CHECK (payload_sha256 IS NULL OR payload_sha256 ~ '^[0-9a-f]{64}$'),
 sent_at timestamptz,
 provider_id text CHECK (length(provider_id)<=100),
 reason text CHECK (reason IS NULL OR reason IN ('expired','ineligible','transient','provider_rejected','payload_changed')),
 PRIMARY KEY(order_id,book_id)
);
ALTER TABLE public.order_book_emails ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.order_book_emails FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE ON public.order_book_emails TO service_role;
CREATE INDEX order_book_emails_pending_idx ON public.order_book_emails(next_attempt_at)
 WHERE state IN ('pending','sending','retry');

CREATE FUNCTION public.enqueue_order_book_emails() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path='' AS $$
BEGIN
 IF OLD.status='PENDING' AND NEW.status='PAID' AND NEW.payment_mode='live'
 AND NEW.buyer->'receiptRequested'='true'::jsonb AND NEW.buyer->'bookEmailRequested'='true'::jsonb
 AND length(NEW.buyer->>'email') BETWEEN 3 AND 254
 AND NEW.buyer->>'email' ~ '^[^[:space:]@<>]+@[^[:space:]@<>]+\.[^[:space:]@<>]+$' THEN
  INSERT INTO public.order_book_emails(order_id,book_id)
  SELECT DISTINCT NEW.id,item->>'id' FROM jsonb_array_elements(NEW.items)item
  WHERE item->>'id' ~ '^book[1-9][0-9]{0,3}$' ON CONFLICT DO NOTHING;
 END IF;
 RETURN NEW;
END; $$;
REVOKE ALL ON FUNCTION public.enqueue_order_book_emails() FROM PUBLIC,anon,authenticated;
CREATE TRIGGER enqueue_order_book_emails AFTER UPDATE OF status ON public.orders
 FOR EACH ROW WHEN (OLD.status IS DISTINCT FROM NEW.status)
 EXECUTE FUNCTION public.enqueue_order_book_emails();

-- New attachment deliveries replace the separate plain confirmation for these
-- future orders. Legacy purchases/receipts retain their existing flow.
CREATE OR REPLACE FUNCTION public.enqueue_order_receipt() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path='' AS $$
BEGIN
 IF OLD.status='PENDING' AND NEW.status='PAID' AND NEW.payment_mode='live'
 AND NEW.buyer->'receiptRequested'='true'::jsonb
 AND NEW.buyer->'bookEmailRequested' IS DISTINCT FROM 'true'::jsonb
 AND length(NEW.buyer->>'email') BETWEEN 3 AND 254
 AND NEW.buyer->>'email' ~ '^[^[:space:]@<>]+@[^[:space:]@<>]+\.[^[:space:]@<>]+$' THEN
  INSERT INTO public.order_receipts(order_id) VALUES(NEW.id) ON CONFLICT DO NOTHING;
 END IF;
 RETURN NEW;
END; $$;

CREATE FUNCTION public.claim_order_book_email(p_lease text) RETURNS jsonb
LANGUAGE plpgsql SECURITY INVOKER SET search_path='' AS $$
DECLARE v public.order_book_emails%ROWTYPE;
BEGIN
 IF p_lease IS NULL OR p_lease !~ '^[0-9a-f]{64}$' THEN RETURN NULL; END IF;
 UPDATE public.order_book_emails b SET state='skipped',reason='ineligible',lease=NULL
 FROM public.orders o WHERE o.id=b.order_id AND b.state IN('pending','retry','sending')
 AND b.next_attempt_at<=now() AND (o.status<>'PAID' OR o.payment_mode<>'live'
 OR o.buyer->'bookEmailRequested' IS DISTINCT FROM 'true'::jsonb);
 UPDATE public.order_book_emails SET state='failed',reason='expired',lease=NULL
 WHERE state IN('pending','retry','sending') AND next_attempt_at<=now()
 AND (created_at<=now()-interval '20 hours' OR attempts>=8);
 SELECT * INTO v FROM public.order_book_emails
 WHERE state IN('pending','retry','sending') AND next_attempt_at<=now()
 AND created_at>now()-interval '20 hours' AND attempts<8
 ORDER BY next_attempt_at,order_id,book_id FOR UPDATE SKIP LOCKED LIMIT 1;
 IF NOT FOUND THEN RETURN NULL; END IF;
 UPDATE public.order_book_emails SET state='sending',attempts=attempts+1,
 lease=p_lease,next_attempt_at=now()+interval '10 minutes'
 WHERE order_id=v.order_id AND book_id=v.book_id;
 RETURN jsonb_build_object('order_id',v.order_id,'book_id',v.book_id,'created_at',v.created_at,'attempts',v.attempts+1);
END; $$;
REVOKE ALL ON FUNCTION public.claim_order_book_email(text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.claim_order_book_email(text) TO service_role;

CREATE FUNCTION public.prepare_order_book_email(p_order_id text,p_book_id text,p_lease text,p_payload_sha256 text)
RETURNS text LANGUAGE plpgsql SECURITY INVOKER SET search_path='' AS $$
DECLARE v public.order_book_emails%ROWTYPE;
BEGIN
 IF p_payload_sha256 IS NULL OR p_payload_sha256 !~ '^[0-9a-f]{64}$' THEN RETURN NULL; END IF;
 SELECT * INTO v FROM public.order_book_emails WHERE order_id=p_order_id AND book_id=p_book_id
 AND state='sending' AND lease=p_lease AND next_attempt_at>now() FOR UPDATE;
 IF NOT FOUND THEN RETURN NULL; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.orders o WHERE o.id=p_order_id AND o.status='PAID'
 AND o.payment_mode='live' AND o.buyer->'bookEmailRequested'='true'::jsonb
 AND o.buyer->'receiptRequested'='true'::jsonb
 AND EXISTS(SELECT 1 FROM jsonb_array_elements(o.items)i WHERE i->>'id'=p_book_id)) THEN RETURN 'ineligible'; END IF;
 IF v.payload_sha256 IS NOT NULL AND v.payload_sha256<>p_payload_sha256 THEN RETURN 'changed'; END IF;
 UPDATE public.order_book_emails SET payload_sha256=p_payload_sha256
 WHERE order_id=p_order_id AND book_id=p_book_id;
 RETURN 'ready';
END; $$;
REVOKE ALL ON FUNCTION public.prepare_order_book_email(text,text,text,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.prepare_order_book_email(text,text,text,text) TO service_role;

CREATE FUNCTION public.finish_order_book_email(p_order_id text,p_book_id text,p_lease text,p_state text,
 p_provider_id text,p_retry_seconds integer,p_reason text) RETURNS boolean
LANGUAGE plpgsql SECURITY INVOKER SET search_path='' AS $$
BEGIN
 IF p_state IS NULL OR p_state NOT IN('sent','retry','failed','skipped')
 OR p_retry_seconds IS NULL OR p_retry_seconds NOT BETWEEN 30 AND 3600 THEN RETURN false; END IF;
 UPDATE public.order_book_emails SET state=p_state,lease=NULL,
 sent_at=CASE WHEN p_state='sent' THEN now() ELSE sent_at END,
 provider_id=CASE WHEN p_state='sent' THEN p_provider_id ELSE provider_id END,
 next_attempt_at=now()+make_interval(secs=>p_retry_seconds),reason=p_reason
 WHERE order_id=p_order_id AND book_id=p_book_id AND state='sending' AND lease=p_lease;
 RETURN FOUND;
END; $$;
REVOKE ALL ON FUNCTION public.finish_order_book_email(text,text,text,text,text,integer,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.finish_order_book_email(text,text,text,text,text,integer,text) TO service_role;

CREATE OR REPLACE FUNCTION public.owner_dashboard_report(p_login text,p_owner text,p_device text,p_session text,
 p_from timestamptz,p_to timestamptz,p_country text,p_status text,p_query text,p_offset integer) RETURNS jsonb
LANGUAGE plpgsql SECURITY INVOKER SET search_path='' AS $$
DECLARE result jsonb;
BEGIN
 IF NOT public.owner_session_valid(p_login,p_owner,p_device,p_session) THEN RETURN NULL; END IF;
 IF p_from IS NULL OR p_to IS NULL OR p_to<=p_from OR p_to-p_from>interval '94 days' OR p_offset NOT BETWEEN 0 AND 10000
 OR length(coalesce(p_country,''))>2 OR length(coalesce(p_status,''))>20 OR length(coalesce(p_query,''))>21 THEN
 RAISE EXCEPTION 'Invalid report range'; END IF;
 WITH paid AS MATERIALIZED (
 SELECT * FROM public.orders WHERE status='PAID' AND payment_mode='live' AND transaction_id IS NOT NULL
 AND paid_at>=p_from AND paid_at<p_to AND (p_country IS NULL OR country=p_country)),
 created AS MATERIALIZED (
 SELECT * FROM public.orders WHERE payment_mode='live' AND created_at>=p_from AND created_at<p_to
 AND (p_country IS NULL OR country=p_country)),
 sessions AS MATERIALIZED (
 SELECT * FROM public.owner_store_sessions WHERE created_at>=p_from AND created_at<p_to AND (p_country IS NULL OR country=p_country)),
 matched AS MATERIALIZED (SELECT * FROM created WHERE (p_status IS NULL OR status=p_status) AND (p_query IS NULL OR id=p_query)),
 recent AS (SELECT * FROM matched ORDER BY created_at DESC,id DESC LIMIT 20 OFFSET p_offset),
 paid_sessions AS (SELECT DISTINCT s.id FROM sessions s JOIN public.owner_order_sources os ON os.session_id=s.id
 JOIN public.orders o ON o.id=os.order_id WHERE o.status='PAID' AND o.payment_mode='live' AND o.transaction_id IS NOT NULL)
 SELECT jsonb_build_object(
 'paidOrders',(SELECT count(*) FROM paid),'createdOrders',(SELECT count(*) FROM created),
 'pendingOrders',(SELECT count(*) FROM created WHERE status='PENDING'),
 'revenue',coalesce((SELECT jsonb_agg(x) FROM (SELECT currency,sum(total)::text AS amount,count(*) AS orders FROM paid GROUP BY currency ORDER BY currency)x),'[]'::jsonb),
 'statuses',coalesce((SELECT jsonb_agg(x) FROM (SELECT status,count(*) AS orders FROM created GROUP BY status ORDER BY status)x),'[]'::jsonb),
 'daily',coalesce((SELECT jsonb_agg(x ORDER BY x.day,x.currency) FROM (
 SELECT to_char(paid_at AT TIME ZONE 'Asia/Kuwait','YYYY-MM-DD') AS day,currency,sum(total)::text AS amount,count(*) AS orders
 FROM paid GROUP BY 1,2)x),'[]'::jsonb),
 'books',coalesce((SELECT jsonb_agg(x ORDER BY x.units DESC,x.book_id) FROM (
 SELECT item->>'id' AS book_id,count(*) AS units FROM paid CROSS JOIN LATERAL jsonb_array_elements(items) item
 GROUP BY 1 ORDER BY units DESC,book_id LIMIT 30)x),'[]'::jsonb),
 'countries',coalesce((SELECT jsonb_agg(x ORDER BY x.orders DESC,x.country) FROM (SELECT country,count(*) AS orders FROM paid GROUP BY country)x),'[]'::jsonb),
 'payments',coalesce((SELECT jsonb_agg(x) FROM (SELECT payment_method AS method,currency,count(*) AS orders,sum(total)::text AS amount
 FROM paid GROUP BY payment_method,currency ORDER BY payment_method,currency)x),'[]'::jsonb),
 'sessions',(SELECT count(*) FROM sessions),'activeSessions',(SELECT count(*) FROM public.owner_store_sessions
 WHERE last_seen_at>now()-interval '5 minutes' AND (p_country IS NULL OR country=p_country)),
 'pageViews',(SELECT coalesce(sum(page_views),0) FROM sessions),
 'funnel',jsonb_build_object('sessions',(SELECT count(*) FROM sessions),'product',(SELECT count(*) FROM sessions WHERE product_at IS NOT NULL),
 'cart',(SELECT count(*) FROM sessions WHERE cart_at IS NOT NULL),'checkout',(SELECT count(*) FROM sessions WHERE checkout_at IS NOT NULL),'paid',(SELECT count(*) FROM paid_sessions)),
 'sources',coalesce((SELECT jsonb_agg(x ORDER BY x.sessions DESC) FROM (SELECT source,medium,count(*) AS sessions,
 count(*) FILTER(WHERE cart_at IS NOT NULL) AS carts,count(*) FILTER(WHERE checkout_at IS NOT NULL) AS checkouts,
 count(*) FILTER(WHERE id IN(SELECT id FROM paid_sessions)) AS paid FROM sessions GROUP BY source,medium)x),'[]'::jsonb),
 'attributedPaidOrders',(SELECT count(*) FROM paid p JOIN public.owner_order_sources os ON os.order_id=p.id),
 'followup',jsonb_build_object('withoutDownloads',(SELECT count(*) FROM paid p WHERE NOT EXISTS(SELECT 1 FROM public.order_downloads d WHERE d.order_id=p.id AND d.download_count>0)),
 'emailRetry',(SELECT count(*) FROM paid p WHERE EXISTS(SELECT 1 FROM public.order_receipts r WHERE r.order_id=p.id AND r.state IN('pending','retry','sending')) OR EXISTS(SELECT 1 FROM public.order_book_emails b WHERE b.order_id=p.id AND b.state IN('pending','retry','sending'))),
 'emailFailed',(SELECT count(*) FROM paid p WHERE EXISTS(SELECT 1 FROM public.order_receipts r WHERE r.order_id=p.id AND r.state='failed') OR EXISTS(SELECT 1 FROM public.order_book_emails b WHERE b.order_id=p.id AND b.state='failed'))),
 'orderCount',(SELECT count(*) FROM matched),
 'orders',coalesce((SELECT jsonb_agg(x ORDER BY x.created_at DESC,x.id DESC) FROM (SELECT r.id,r.status,r.created_at,r.paid_at,
 r.total::text AS total,r.currency,r.payment_method,r.country,(SELECT jsonb_agg(jsonb_build_object('id',i->>'id','name',i->>'name')) FROM jsonb_array_elements(r.items)i) AS items,jsonb_array_length(r.items) AS book_count,
 coalesce((SELECT sum(d.download_count) FROM public.order_downloads d WHERE d.order_id=r.id),0) AS downloads,
 (SELECT er.state FROM public.order_receipts er WHERE er.order_id=r.id) AS email_state,
 coalesce((SELECT jsonb_agg(jsonb_build_object('book_id',b.book_id,'state',b.state,'reason',b.reason,'sent_at',b.sent_at) ORDER BY b.book_id) FROM public.order_book_emails b WHERE b.order_id=r.id),'[]'::jsonb) AS book_emails FROM recent r)x),'[]'::jsonb)
 ) INTO result;
 RETURN result;
END; $$;

NOTIFY pgrst, 'reload schema';
