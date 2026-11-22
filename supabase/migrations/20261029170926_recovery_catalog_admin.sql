-- Additive administration and opt-in recovery only. Existing objects stay intact.
CREATE TABLE IF NOT EXISTS public.store_catalog_state (
 id boolean PRIMARY KEY DEFAULT true CHECK(id),
 published jsonb NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(published)='object'),
 draft jsonb NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(draft)='object'),
 revision integer NOT NULL DEFAULT 0, draft_revision integer NOT NULL DEFAULT 0,
 reminders_enabled boolean NOT NULL DEFAULT true,
 updated_at timestamptz NOT NULL DEFAULT now(), published_at timestamptz
);
INSERT INTO public.store_catalog_state(id) VALUES(true) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS public.store_catalog_history (
 revision integer PRIMARY KEY, document jsonb NOT NULL,
 owner_hash text NOT NULL, published_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.store_catalog_covers (
 digest text PRIMARY KEY CHECK(digest ~ '^[a-f0-9]{64}$'),
 data_base64 text NOT NULL CHECK(length(data_base64) BETWEEN 1 AND 1333336),
 width integer NOT NULL CHECK(width BETWEEN 1 AND 1800),height integer NOT NULL CHECK(height BETWEEN 1 AND 1800),
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.store_checkout_reminders (
 order_id text PRIMARY KEY REFERENCES public.orders(id) ON DELETE CASCADE,
 email_hash text NOT NULL CHECK(email_hash ~ '^[a-f0-9]{64}$'),
 link_hash text NOT NULL UNIQUE CHECK(link_hash ~ '^[a-f0-9]{64}$'),
 unsubscribe_hash text NOT NULL UNIQUE CHECK(unsubscribe_hash ~ '^[a-f0-9]{64}$'),
 consent_version text NOT NULL DEFAULT 'checkout-checkbox-v1',
 created_at timestamptz NOT NULL DEFAULT now(),expires_at timestamptz NOT NULL DEFAULT now()+interval '3 days',
 next_attempt_at timestamptz NOT NULL DEFAULT now()+interval '1 hour',
 state text NOT NULL DEFAULT 'pending' CHECK(state IN('pending','sending','retry','sent','failed','skipped','unsubscribed')),
 attempts integer NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 4),
 lease text, sent_at timestamptz,reason text
);
CREATE INDEX store_checkout_reminders_due ON public.store_checkout_reminders(next_attempt_at) WHERE state IN('pending','retry','sending');
CREATE INDEX store_checkout_reminders_email ON public.store_checkout_reminders(email_hash,created_at);
CREATE TABLE IF NOT EXISTS public.store_reminder_optouts (
 email_hash text PRIMARY KEY CHECK(email_hash ~ '^[a-f0-9]{64}$'), created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.store_catalog_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.store_catalog_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.store_catalog_covers ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.store_checkout_reminders ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.store_reminder_optouts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.store_catalog_state,public.store_catalog_history,public.store_catalog_covers,public.store_checkout_reminders,public.store_reminder_optouts FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE ON public.store_catalog_state TO service_role;
GRANT SELECT,INSERT ON public.store_catalog_history,public.store_catalog_covers,public.store_reminder_optouts TO service_role;
GRANT SELECT,INSERT,UPDATE ON public.store_checkout_reminders TO service_role;

CREATE FUNCTION public.store_admin_catalog_read(p_login text,p_owner text,p_device text,p_session text) RETURNS jsonb
LANGUAGE sql STABLE SECURITY INVOKER SET search_path='' AS $$
 SELECT CASE WHEN public.owner_session_valid(p_login,p_owner,p_device,p_session) THEN
 (SELECT jsonb_build_object('published',published,'draft',draft,'revision',revision,'draftRevision',draft_revision,'publishedAt',published_at,'updatedAt',updated_at) FROM public.store_catalog_state WHERE id) ELSE NULL END;
$$;
CREATE FUNCTION public.store_admin_catalog_save(p_login text,p_owner text,p_device text,p_session text,p_expected integer,p_document jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY INVOKER SET search_path='' AS $$
DECLARE v public.store_catalog_state%ROWTYPE;
BEGIN
 IF NOT public.owner_session_valid(p_login,p_owner,p_device,p_session) THEN RETURN NULL; END IF;
 IF p_document IS NULL OR jsonb_typeof(p_document)<>'object' OR octet_length(p_document::text)>110000 THEN RAISE EXCEPTION 'Invalid document'; END IF;
 SELECT * INTO v FROM public.store_catalog_state WHERE id FOR UPDATE;
 IF v.draft_revision<>p_expected THEN RETURN jsonb_build_object('conflict',true); END IF;
 UPDATE public.store_catalog_state SET draft=p_document,draft_revision=draft_revision+1,updated_at=now() WHERE id;
 RETURN jsonb_build_object('ok',true,'draftRevision',v.draft_revision+1);
END; $$;
CREATE FUNCTION public.store_admin_catalog_publish(p_login text,p_owner text,p_device text,p_session text,p_expected integer) RETURNS jsonb
LANGUAGE plpgsql SECURITY INVOKER SET search_path='' AS $$
DECLARE v public.store_catalog_state%ROWTYPE;
BEGIN
 IF NOT public.owner_session_valid(p_login,p_owner,p_device,p_session) THEN RETURN NULL; END IF;
 SELECT * INTO v FROM public.store_catalog_state WHERE id FOR UPDATE;
 IF v.draft_revision<>p_expected THEN RETURN jsonb_build_object('conflict',true); END IF;
 INSERT INTO public.store_catalog_history(revision,document,owner_hash) VALUES(v.revision+1,v.draft,p_owner);
 UPDATE public.store_catalog_state SET published=draft,revision=revision+1,published_at=now(),updated_at=now() WHERE id;
 RETURN jsonb_build_object('ok',true,'revision',v.revision+1,'draftRevision',v.draft_revision);
END; $$;
CREATE FUNCTION public.store_admin_recovery_toggle(p_login text,p_owner text,p_device text,p_session text,p_enabled boolean) RETURNS jsonb
LANGUAGE plpgsql SECURITY INVOKER SET search_path='' AS $$
BEGIN
 IF NOT public.owner_session_valid(p_login,p_owner,p_device,p_session) THEN RETURN NULL; END IF;
 IF p_enabled IS NULL THEN RAISE EXCEPTION 'Invalid state'; END IF;
 UPDATE public.store_catalog_state SET reminders_enabled=p_enabled WHERE id;
 RETURN jsonb_build_object('ok',true,'enabled',p_enabled);
END; $$;
CREATE FUNCTION public.store_admin_recovery_report(p_login text,p_owner text,p_device text,p_session text) RETURNS jsonb
LANGUAGE sql STABLE SECURITY INVOKER SET search_path='' AS $$
 SELECT CASE WHEN public.owner_session_valid(p_login,p_owner,p_device,p_session) THEN jsonb_build_object(
 'enabled',(SELECT reminders_enabled FROM public.store_catalog_state WHERE id),
 'counts',coalesce((SELECT jsonb_agg(s) FROM (SELECT state,count(*) AS count FROM public.store_checkout_reminders GROUP BY state)s),'[]'::jsonb),
 'recent',coalesce((SELECT jsonb_agg(r ORDER BY r.created_at DESC) FROM
 (SELECT order_id,state,created_at,sent_at,attempts FROM public.store_checkout_reminders ORDER BY created_at DESC LIMIT 20)r),'[]'::jsonb)) ELSE NULL END;
$$;
CREATE FUNCTION public.store_recovery_enroll(p_order text,p_link text,p_unsubscribe text) RETURNS boolean
LANGUAGE plpgsql SECURITY INVOKER SET search_path='' AS $$
DECLARE v public.orders%ROWTYPE; e text;
BEGIN
 IF p_link IS NULL OR p_link !~ '^[a-f0-9]{64}$' OR p_unsubscribe IS NULL OR p_unsubscribe !~ '^[a-f0-9]{64}$' THEN RETURN false; END IF;
 SELECT * INTO v FROM public.orders WHERE id=p_order;
 IF NOT FOUND OR v.status<>'PENDING' OR v.payment_mode<>'live' OR v.created_at<now()-interval '3 days'
 OR v.buyer->'recoveryConsent' IS DISTINCT FROM 'true'::jsonb OR v.buyer->>'email' IS NULL
 OR NOT(SELECT reminders_enabled FROM public.store_catalog_state WHERE id) THEN RETURN false; END IF;
 e=encode(sha256(convert_to(lower(btrim(v.buyer->>'email')),'UTF8')),'hex');
 PERFORM pg_advisory_xact_lock(hashtextextended('recovery-email:'||e,0));
 IF EXISTS(SELECT 1 FROM public.store_reminder_optouts WHERE email_hash=e)
 OR EXISTS(SELECT 1 FROM public.store_checkout_reminders WHERE email_hash=e AND created_at>now()-interval '1 day') THEN RETURN false; END IF;
 INSERT INTO public.store_checkout_reminders(order_id,email_hash,link_hash,unsubscribe_hash,created_at,expires_at,next_attempt_at)
 VALUES(p_order,e,p_link,p_unsubscribe,v.created_at,v.created_at+interval '3 days',v.created_at+interval '1 hour') ON CONFLICT DO NOTHING;
 RETURN FOUND;
END; $$;
CREATE FUNCTION public.store_recovery_sendable(p_order text,p_lease text) RETURNS boolean
LANGUAGE sql STABLE SECURITY INVOKER SET search_path='' AS $$
 SELECT EXISTS(SELECT 1 FROM public.store_checkout_reminders r JOIN public.orders o ON o.id=r.order_id
 WHERE r.order_id=p_order AND r.lease=p_lease AND r.state='sending' AND r.expires_at>now()
 AND o.status='PENDING' AND o.payment_mode='live' AND o.buyer->'recoveryConsent'='true'::jsonb
 AND (SELECT reminders_enabled FROM public.store_catalog_state WHERE id)
 AND NOT EXISTS(SELECT 1 FROM public.store_reminder_optouts x WHERE x.email_hash=r.email_hash)
 AND NOT EXISTS(SELECT 1 FROM public.orders paid WHERE paid.status='PAID' AND paid.payment_mode='live'
 AND lower(btrim(paid.buyer->>'email'))=lower(btrim(o.buyer->>'email')) AND paid.paid_at>=r.created_at-interval '10 minutes'));
$$;
CREATE FUNCTION public.store_recovery_claim(p_lease text) RETURNS jsonb
LANGUAGE plpgsql SECURITY INVOKER SET search_path='' AS $$
DECLARE v public.store_checkout_reminders%ROWTYPE; o public.orders%ROWTYPE;
BEGIN
 IF p_lease IS NULL OR p_lease !~ '^[a-f0-9]{64}$' OR NOT(SELECT reminders_enabled FROM public.store_catalog_state WHERE id) THEN RETURN NULL; END IF;
 SELECT * INTO v FROM public.store_checkout_reminders WHERE
 ((state IN('pending','retry') AND next_attempt_at<=now()) OR (state='sending' AND next_attempt_at<now()))
 AND attempts<4 ORDER BY next_attempt_at FOR UPDATE SKIP LOCKED LIMIT 1;
 IF NOT FOUND THEN RETURN NULL; END IF;
 IF v.created_at<now()-interval '20 hours' THEN
  UPDATE public.store_checkout_reminders SET state='failed',lease=NULL,reason='expired' WHERE order_id=v.order_id;
  RETURN NULL;
 END IF;
 UPDATE public.store_checkout_reminders SET state='sending',lease=p_lease,attempts=attempts+1,next_attempt_at=now()+interval '5 minutes' WHERE order_id=v.order_id;
 IF NOT public.store_recovery_sendable(v.order_id,p_lease) THEN
  UPDATE public.store_checkout_reminders SET state='skipped',lease=NULL,reason='ineligible' WHERE order_id=v.order_id;
  RETURN NULL;
 END IF;
 SELECT * INTO o FROM public.orders WHERE id=v.order_id;
 RETURN jsonb_build_object('order_id',v.order_id,'email',o.buyer->>'email','language',coalesce(o.buyer->>'language','ar'),'attempts',v.attempts+1);
END; $$;
CREATE FUNCTION public.store_recovery_finish(p_order text,p_lease text,p_state text,p_reason text) RETURNS boolean
LANGUAGE plpgsql SECURITY INVOKER SET search_path='' AS $$
BEGIN
 IF p_state IS NULL OR p_state NOT IN('sent','retry','failed','skipped') OR (p_reason IS NOT NULL AND p_reason NOT IN('transport','api_key','sender_unverified','test_recipient','quota','rate_limit','provider_rejected','ineligible')) THEN RETURN false; END IF;
 UPDATE public.store_checkout_reminders SET state=p_state,lease=NULL,reason=p_reason,
 sent_at=CASE WHEN p_state='sent' THEN now() ELSE sent_at END,next_attempt_at=now()+interval '10 minutes'
 WHERE order_id=p_order AND lease=p_lease AND state='sending';
 RETURN FOUND;
END; $$;
CREATE FUNCTION public.store_recovery_cart(p_link text) RETURNS jsonb
LANGUAGE sql STABLE SECURITY INVOKER SET search_path='' AS $$
 SELECT CASE WHEN o.status='PAID' THEN jsonb_build_object('completed',true) ELSE
 jsonb_build_object('items',(SELECT jsonb_agg(jsonb_build_object('id',it->>'id')) FROM jsonb_array_elements(o.items)it)) END
 FROM public.store_checkout_reminders r JOIN public.orders o ON o.id=r.order_id
 WHERE r.link_hash=p_link AND r.expires_at>now() AND r.state<>'unsubscribed' AND o.status IN('PENDING','PAID');
$$;
CREATE FUNCTION public.store_recovery_unsubscribe(p_unsubscribe text) RETURNS boolean
LANGUAGE plpgsql SECURITY INVOKER SET search_path='' AS $$
DECLARE e text;
BEGIN
 SELECT email_hash INTO e FROM public.store_checkout_reminders WHERE unsubscribe_hash=p_unsubscribe;
 IF NOT FOUND THEN RETURN false; END IF;
 INSERT INTO public.store_reminder_optouts(email_hash) VALUES(e) ON CONFLICT DO NOTHING;
 UPDATE public.store_checkout_reminders SET state='unsubscribed',lease=NULL WHERE email_hash=e AND state IN('pending','retry','sending');
 RETURN true;
END; $$;
REVOKE ALL ON FUNCTION public.store_admin_catalog_read(text,text,text,text),public.store_admin_catalog_save(text,text,text,text,integer,jsonb),
 public.store_admin_catalog_publish(text,text,text,text,integer),public.store_admin_recovery_toggle(text,text,text,text,boolean),public.store_admin_recovery_report(text,text,text,text),
 public.store_recovery_enroll(text,text,text),public.store_recovery_sendable(text,text),public.store_recovery_claim(text),public.store_recovery_finish(text,text,text,text),public.store_recovery_cart(text),public.store_recovery_unsubscribe(text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.store_admin_catalog_read(text,text,text,text),public.store_admin_catalog_save(text,text,text,text,integer,jsonb),
 public.store_admin_catalog_publish(text,text,text,text,integer),public.store_admin_recovery_toggle(text,text,text,text,boolean),public.store_admin_recovery_report(text,text,text,text),
 public.store_recovery_enroll(text,text,text),public.store_recovery_sendable(text,text),public.store_recovery_claim(text),public.store_recovery_finish(text,text,text,text),public.store_recovery_cart(text),public.store_recovery_unsubscribe(text) TO service_role;
NOTIFY pgrst,'reload schema';
