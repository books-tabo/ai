-- Additive owner dashboard. No existing payment/order/download object is rewritten.
CREATE TABLE IF NOT EXISTS public.owner_dashboard_logins (
 id text PRIMARY KEY CHECK(id ~ '^[a-f0-9]{64}$'),
 owner_hash text NOT NULL CHECK(owner_hash ~ '^[a-f0-9]{64}$'),
 request_hash text NOT NULL CHECK(request_hash ~ '^[a-f0-9]{64}$'),
 device_hash text NOT NULL CHECK(device_hash ~ '^[a-f0-9]{64}$'),
 code_hash text NOT NULL CHECK(code_hash ~ '^[a-f0-9]{64}$'),
 created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL DEFAULT now()+interval '10 minutes',
 attempts integer NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 5),
 verified_at timestamptz, session_hash text CHECK(session_hash ~ '^[a-f0-9]{64}$'), session_expires_at timestamptz
);
CREATE INDEX owner_dashboard_login_time ON public.owner_dashboard_logins(owner_hash,created_at);
CREATE INDEX owner_dashboard_login_request ON public.owner_dashboard_logins(request_hash,created_at);
ALTER TABLE public.owner_dashboard_logins ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.owner_dashboard_logins FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE,DELETE ON public.owner_dashboard_logins TO service_role;

CREATE TABLE IF NOT EXISTS public.owner_store_sessions (
 id text PRIMARY KEY CHECK(id ~ '^[a-f0-9]{64}$'),
 device_hash text NOT NULL CHECK(device_hash ~ '^[a-f0-9]{64}$'),
 request_hash text NOT NULL CHECK(request_hash ~ '^[a-f0-9]{64}$'),
 source text NOT NULL CHECK(source IN ('direct','google','meta','snapchat','bing','other')),
 medium text NOT NULL CHECK(medium IN ('direct','paid','organic','referral','unknown')),
 country text NOT NULL CHECK(country ~ '^[A-Z]{2}$'),
 landing_path text NOT NULL CHECK(length(landing_path)<=120 AND landing_path !~ '[?@#]'),
 created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL DEFAULT now()+interval '30 minutes',
 last_seen_at timestamptz NOT NULL DEFAULT now(), page_views integer NOT NULL DEFAULT 1 CHECK(page_views BETWEEN 1 AND 1000),
 product_at timestamptz,cart_at timestamptz,checkout_at timestamptz
);
CREATE INDEX owner_store_sessions_time ON public.owner_store_sessions(created_at);
CREATE INDEX owner_store_sessions_request ON public.owner_store_sessions(request_hash,created_at);
ALTER TABLE public.owner_store_sessions ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.owner_store_sessions FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE,DELETE ON public.owner_store_sessions TO service_role;

CREATE TABLE IF NOT EXISTS public.owner_order_sources (
 order_id text PRIMARY KEY REFERENCES public.orders(id) ON DELETE CASCADE,
 session_id text NOT NULL REFERENCES public.owner_store_sessions(id) ON DELETE CASCADE,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX owner_order_sources_session ON public.owner_order_sources(session_id);
ALTER TABLE public.owner_order_sources ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.owner_order_sources FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,DELETE ON public.owner_order_sources TO service_role;

CREATE FUNCTION public.owner_login_request(p_id text,p_owner text,p_request text,p_device text,p_code text) RETURNS boolean
LANGUAGE plpgsql SECURITY INVOKER SET search_path='' AS $$
BEGIN
 IF p_id IS NULL OR p_id !~ '^[a-f0-9]{64}$' OR p_owner IS NULL OR p_owner !~ '^[a-f0-9]{64}$'
 OR p_request IS NULL OR p_request !~ '^[a-f0-9]{64}$' OR p_device IS NULL OR p_device !~ '^[a-f0-9]{64}$'
 OR p_code IS NULL OR p_code !~ '^[a-f0-9]{64}$' THEN RETURN false; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('owner-login:'||p_owner,0));
 IF EXISTS(SELECT 1 FROM public.owner_dashboard_logins WHERE owner_hash=p_owner AND created_at>now()-interval '1 minute')
 OR (SELECT count(*) FROM public.owner_dashboard_logins WHERE owner_hash=p_owner AND created_at>now()-interval '1 hour')>=5
 THEN RETURN false; END IF;
 INSERT INTO public.owner_dashboard_logins(id,owner_hash,request_hash,device_hash,code_hash) VALUES(p_id,p_owner,p_request,p_device,p_code);
 RETURN true;
END; $$;
CREATE FUNCTION public.owner_login_verify(p_id text,p_owner text,p_device text,p_code text,p_session text) RETURNS boolean
LANGUAGE plpgsql SECURITY INVOKER SET search_path='' AS $$
DECLARE v public.owner_dashboard_logins%ROWTYPE;
BEGIN
 IF p_session IS NULL OR p_session !~ '^[a-f0-9]{64}$' THEN RETURN false; END IF;
 SELECT * INTO v FROM public.owner_dashboard_logins WHERE id=p_id FOR UPDATE;
 IF NOT FOUND OR v.owner_hash IS DISTINCT FROM p_owner OR v.device_hash IS DISTINCT FROM p_device
 OR v.verified_at IS NOT NULL OR v.expires_at<=now() OR v.attempts>=5 THEN RETURN false; END IF;
 UPDATE public.owner_dashboard_logins SET attempts=attempts+1 WHERE id=p_id;
 IF v.code_hash IS DISTINCT FROM p_code THEN RETURN false; END IF;
 UPDATE public.owner_dashboard_logins SET verified_at=now(),session_hash=p_session,session_expires_at=now()+interval '8 hours' WHERE id=p_id;
 RETURN true;
END; $$;
CREATE FUNCTION public.owner_session_valid(p_id text,p_owner text,p_device text,p_session text) RETURNS boolean
LANGUAGE sql STABLE SECURITY INVOKER SET search_path='' AS $$
 SELECT EXISTS(SELECT 1 FROM public.owner_dashboard_logins WHERE id=p_id AND owner_hash=p_owner AND device_hash=p_device
 AND session_hash=p_session AND verified_at IS NOT NULL AND session_expires_at>now());
$$;
CREATE FUNCTION public.owner_session_logout(p_id text,p_owner text,p_device text,p_session text) RETURNS boolean
LANGUAGE plpgsql SECURITY INVOKER SET search_path='' AS $$
BEGIN
 UPDATE public.owner_dashboard_logins SET session_expires_at=now() WHERE id=p_id AND owner_hash=p_owner
 AND device_hash=p_device AND session_hash=p_session;
 RETURN FOUND;
END; $$;

CREATE FUNCTION public.owner_insight_start(p_old text,p_new text,p_device text,p_request text,p_source text,p_medium text,p_country text,p_path text) RETURNS jsonb
LANGUAGE plpgsql SECURITY INVOKER SET search_path='' AS $$
DECLARE v public.owner_store_sessions%ROWTYPE;
BEGIN
 SELECT * INTO v FROM public.owner_store_sessions WHERE id=p_old AND device_hash=p_device AND expires_at>now() FOR UPDATE;
 IF FOUND THEN
  UPDATE public.owner_store_sessions SET last_seen_at=now(),page_views=least(page_views+1,1000) WHERE id=v.id;
  RETURN jsonb_build_object('resumed',true,'ttl',greatest(1,extract(epoch FROM v.expires_at-now())::integer));
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('owner-insight:'||p_request,0));
 IF (SELECT count(*) FROM public.owner_store_sessions WHERE request_hash=p_request AND created_at>now()-interval '1 hour')>=100 THEN RETURN NULL; END IF;
 INSERT INTO public.owner_store_sessions(id,device_hash,request_hash,source,medium,country,landing_path)
 VALUES(p_new,p_device,p_request,p_source,p_medium,p_country,p_path);
 RETURN jsonb_build_object('resumed',false,'ttl',1800);
END; $$;
CREATE FUNCTION public.owner_insight_event(p_id text,p_device text,p_step text) RETURNS boolean
LANGUAGE plpgsql SECURITY INVOKER SET search_path='' AS $$
BEGIN
 IF p_step IS NULL OR p_step NOT IN ('product','cart','checkout') THEN RETURN false; END IF;
 UPDATE public.owner_store_sessions SET last_seen_at=now(),
 product_at=CASE WHEN p_step='product' THEN coalesce(product_at,now()) ELSE product_at END,
 cart_at=CASE WHEN p_step='cart' THEN coalesce(cart_at,now()) ELSE cart_at END,
 checkout_at=CASE WHEN p_step='checkout' THEN coalesce(checkout_at,now()) ELSE checkout_at END
 WHERE id=p_id AND device_hash=p_device AND expires_at>now();
 RETURN FOUND;
END; $$;
CREATE FUNCTION public.owner_insight_order(p_id text,p_device text,p_order text) RETURNS boolean
LANGUAGE plpgsql SECURITY INVOKER SET search_path='' AS $$
BEGIN
 INSERT INTO public.owner_order_sources(order_id,session_id)
 SELECT p_order,id FROM public.owner_store_sessions WHERE id=p_id AND device_hash=p_device AND expires_at>now()
 AND EXISTS(SELECT 1 FROM public.orders WHERE id=p_order)
 ON CONFLICT(order_id) DO NOTHING;
 RETURN FOUND;
END; $$;

CREATE FUNCTION public.owner_dashboard_report(p_login text,p_owner text,p_device text,p_session text,
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
 'emailRetry',(SELECT count(*) FROM paid p JOIN public.order_receipts r ON r.order_id=p.id WHERE r.state IN('pending','retry','sending')),
 'emailFailed',(SELECT count(*) FROM paid p JOIN public.order_receipts r ON r.order_id=p.id WHERE r.state='failed')),
 'orderCount',(SELECT count(*) FROM matched),
 'orders',coalesce((SELECT jsonb_agg(x ORDER BY x.created_at DESC,x.id DESC) FROM (SELECT r.id,r.status,r.created_at,r.paid_at,
 r.total::text AS total,r.currency,r.payment_method,r.country,(SELECT jsonb_agg(jsonb_build_object('id',i->>'id','name',i->>'name')) FROM jsonb_array_elements(r.items)i) AS items,jsonb_array_length(r.items) AS book_count,
 coalesce((SELECT sum(d.download_count) FROM public.order_downloads d WHERE d.order_id=r.id),0) AS downloads,
 (SELECT er.state FROM public.order_receipts er WHERE er.order_id=r.id) AS email_state FROM recent r)x),'[]'::jsonb)
 ) INTO result;
 RETURN result;
END; $$;

-- Short-lived auth history and a 180-day first-party analytics window only.
CREATE FUNCTION public.owner_dashboard_prune() RETURNS integer
LANGUAGE plpgsql SECURITY INVOKER SET search_path='' AS $$
DECLARE n integer;
BEGIN
 DELETE FROM public.owner_dashboard_logins WHERE id IN(SELECT id FROM public.owner_dashboard_logins WHERE created_at<now()-interval '2 days' LIMIT 200);
 DELETE FROM public.owner_store_sessions WHERE id IN(SELECT id FROM public.owner_store_sessions WHERE created_at<now()-interval '180 days' LIMIT 200);
 GET DIAGNOSTICS n=ROW_COUNT; RETURN n;
END; $$;
REVOKE ALL ON FUNCTION public.owner_login_request(text,text,text,text,text),public.owner_login_verify(text,text,text,text,text),
 public.owner_session_valid(text,text,text,text),public.owner_session_logout(text,text,text,text),
 public.owner_insight_start(text,text,text,text,text,text,text,text),public.owner_insight_event(text,text,text),
 public.owner_insight_order(text,text,text),public.owner_dashboard_report(text,text,text,text,timestamptz,timestamptz,text,text,text,integer),
 public.owner_dashboard_prune() FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.owner_login_request(text,text,text,text,text),public.owner_login_verify(text,text,text,text,text),
 public.owner_session_valid(text,text,text,text),public.owner_session_logout(text,text,text,text),
 public.owner_insight_start(text,text,text,text,text,text,text,text),public.owner_insight_event(text,text,text),
 public.owner_insight_order(text,text,text),public.owner_dashboard_report(text,text,text,text,timestamptz,timestamptz,text,text,text,integer),
 public.owner_dashboard_prune() TO service_role;
NOTIFY pgrst,'reload schema';
