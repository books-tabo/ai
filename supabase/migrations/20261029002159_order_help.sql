-- Additive, service-only assistance. Existing order/payment/download data and RPCs stay intact.
CREATE TABLE IF NOT EXISTS public.order_help_challenges (
 id text PRIMARY KEY CHECK (id ~ '^[0-9a-f]{64}$'),
 order_id text NOT NULL REFERENCES public.orders(id) ON DELETE CASCADE,
 email_hash text NOT NULL CHECK (email_hash ~ '^[0-9a-f]{64}$'),
 request_hash text NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
 device_hash text NOT NULL CHECK (device_hash ~ '^[0-9a-f]{64}$'),
 code_hash text NOT NULL CHECK (code_hash ~ '^[0-9a-f]{64}$'),
 language text NOT NULL CHECK (language IN ('ar','en')),
 created_at timestamptz NOT NULL DEFAULT now(),
 expires_at timestamptz NOT NULL DEFAULT now()+interval '10 minutes',
 attempts integer NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 5),
 verified_at timestamptz,
 session_hash text CHECK (session_hash IS NULL OR session_hash ~ '^[0-9a-f]{64}$'),
 session_expires_at timestamptz
);
ALTER TABLE public.order_help_challenges ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.order_help_challenges FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE ON public.order_help_challenges TO service_role;
CREATE INDEX order_help_email_time_idx ON public.order_help_challenges(email_hash,created_at);
CREATE INDEX order_help_request_time_idx ON public.order_help_challenges(request_hash,created_at);
CREATE INDEX order_help_order_time_idx ON public.order_help_challenges(order_id,created_at);

CREATE TABLE IF NOT EXISTS public.order_help_messages (
 challenge_id text NOT NULL REFERENCES public.order_help_challenges(id) ON DELETE CASCADE,
 kind text NOT NULL CHECK (kind IN ('otp','receipt')),
 state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','sending','retry','sent','failed','skipped')),
 created_at timestamptz NOT NULL DEFAULT now(),
 next_attempt_at timestamptz NOT NULL DEFAULT now(),
 attempts integer NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 5),
 lease text CHECK (lease IS NULL OR lease ~ '^[0-9a-f]{64}$'),
 sent_at timestamptz,
 provider_id text CHECK (length(provider_id)<=100),
 reason text CHECK (reason IS NULL OR reason IN ('expired','ineligible','transient','provider_rejected')),
 PRIMARY KEY(challenge_id,kind)
);
ALTER TABLE public.order_help_messages ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.order_help_messages FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE ON public.order_help_messages TO service_role;
CREATE INDEX order_help_messages_pending_idx ON public.order_help_messages(next_attempt_at)
 WHERE state IN ('pending','sending','retry');

CREATE FUNCTION public.request_order_help(p_id text,p_order_id text,p_email text,p_email_hash text,p_request_hash text,
 p_device_hash text,p_code_hash text,p_language text) RETURNS boolean
LANGUAGE plpgsql SECURITY INVOKER SET search_path='' AS $$
BEGIN
 IF p_id IS NULL OR p_id !~ '^[0-9a-f]{64}$' OR p_email_hash IS NULL OR p_email_hash !~ '^[0-9a-f]{64}$'
  OR p_request_hash IS NULL OR p_request_hash !~ '^[0-9a-f]{64}$'
  OR p_device_hash IS NULL OR p_device_hash !~ '^[0-9a-f]{64}$'
  OR p_code_hash IS NULL OR p_code_hash !~ '^[0-9a-f]{64}$'
  OR p_language IS NULL OR p_language NOT IN ('ar','en') THEN RETURN false; END IF;
 -- Serialize the durable limits across processes/restarts and simultaneous requests.
 PERFORM pg_advisory_xact_lock(hashtextextended('help-client:'||p_request_hash,0));
 PERFORM pg_advisory_xact_lock(hashtextextended('help-email:'||p_email_hash,0));
 IF NOT EXISTS(SELECT 1 FROM public.orders WHERE id=p_order_id AND lower(btrim(buyer->>'email'))=p_email)
  OR (SELECT count(*) FROM public.order_help_challenges WHERE email_hash=p_email_hash AND created_at>now()-interval '1 hour')>=5
  OR (SELECT count(*) FROM public.order_help_challenges WHERE request_hash=p_request_hash AND created_at>now()-interval '1 hour')>=10
  OR EXISTS(SELECT 1 FROM public.order_help_challenges WHERE order_id=p_order_id AND created_at>now()-interval '1 minute')
 THEN RETURN false; END IF;
 INSERT INTO public.order_help_challenges(id,order_id,email_hash,request_hash,device_hash,code_hash,language)
 VALUES(p_id,p_order_id,p_email_hash,p_request_hash,p_device_hash,p_code_hash,p_language);
 INSERT INTO public.order_help_messages(challenge_id,kind) VALUES(p_id,'otp');
 RETURN true;
END;
$$;
REVOKE ALL ON FUNCTION public.request_order_help(text,text,text,text,text,text,text,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.request_order_help(text,text,text,text,text,text,text,text) TO service_role;

CREATE FUNCTION public.verify_order_help(p_id text,p_code_hash text,p_session_hash text,p_device_hash text) RETURNS boolean
LANGUAGE plpgsql SECURITY INVOKER SET search_path='' AS $$
DECLARE v public.order_help_challenges%ROWTYPE;
BEGIN
 IF p_session_hash IS NULL OR p_session_hash !~ '^[0-9a-f]{64}$' THEN RETURN false; END IF;
 SELECT * INTO v FROM public.order_help_challenges WHERE id=p_id FOR UPDATE;
 IF NOT FOUND OR v.verified_at IS NOT NULL OR v.expires_at<=now() OR v.attempts>=5
  OR p_device_hash IS DISTINCT FROM v.device_hash THEN RETURN false; END IF;
 UPDATE public.order_help_challenges SET attempts=attempts+1 WHERE id=p_id;
 IF p_code_hash IS DISTINCT FROM v.code_hash THEN RETURN false; END IF;
 UPDATE public.order_help_challenges SET verified_at=now(),session_hash=p_session_hash,
  session_expires_at=now()+interval '15 minutes' WHERE id=p_id;
 RETURN true;
END;
$$;
REVOKE ALL ON FUNCTION public.verify_order_help(text,text,text,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.verify_order_help(text,text,text,text) TO service_role;

CREATE FUNCTION public.order_help_order(p_id text,p_session_hash text,p_device_hash text) RETURNS text
LANGUAGE sql STABLE SECURITY INVOKER SET search_path='' AS $$
 SELECT order_id FROM public.order_help_challenges WHERE id=p_id AND verified_at IS NOT NULL
  AND session_expires_at>now() AND session_hash=p_session_hash AND device_hash=p_device_hash;
$$;
REVOKE ALL ON FUNCTION public.order_help_order(text,text,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.order_help_order(text,text,text) TO service_role;

CREATE FUNCTION public.resend_order_help_receipt(p_id text,p_session_hash text,p_device_hash text) RETURNS jsonb
LANGUAGE plpgsql SECURITY INVOKER SET search_path='' AS $$
DECLARE oid text; v_state text;
BEGIN
 oid=public.order_help_order(p_id,p_session_hash,p_device_hash);
 IF oid IS NULL THEN RETURN NULL; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('help-receipt:'||oid,0));
 SELECT state INTO v_state FROM public.order_help_messages WHERE challenge_id=p_id AND kind='receipt';
 IF FOUND THEN RETURN jsonb_build_object('accepted',true,'state',v_state); END IF;
 IF NOT EXISTS(SELECT 1 FROM public.orders WHERE id=oid AND status='PAID' AND payment_mode='live' AND transaction_id IS NOT NULL)
 THEN RETURN jsonb_build_object('accepted',false,'reason','not_paid'); END IF;
 IF (SELECT count(*) FROM public.order_help_messages m JOIN public.order_help_challenges c ON c.id=m.challenge_id
     WHERE c.order_id=oid AND m.kind='receipt' AND m.created_at>now()-interval '1 day')>=3
  OR EXISTS(SELECT 1 FROM public.order_help_messages m JOIN public.order_help_challenges c ON c.id=m.challenge_id
     WHERE c.order_id=oid AND m.kind='receipt' AND m.created_at>now()-interval '10 minutes')
 THEN RETURN jsonb_build_object('accepted',false,'reason','rate_limited'); END IF;
 INSERT INTO public.order_help_messages(challenge_id,kind) VALUES(p_id,'receipt');
 RETURN jsonb_build_object('accepted',true,'state','pending');
END;
$$;
REVOKE ALL ON FUNCTION public.resend_order_help_receipt(text,text,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.resend_order_help_receipt(text,text,text) TO service_role;

CREATE FUNCTION public.claim_order_help_message(p_lease text) RETURNS jsonb
LANGUAGE plpgsql SECURITY INVOKER SET search_path='' AS $$
DECLARE v public.order_help_messages%ROWTYPE; c public.order_help_challenges%ROWTYPE;
BEGIN
 IF p_lease IS NULL OR p_lease !~ '^[0-9a-f]{64}$' THEN RETURN NULL; END IF;
 UPDATE public.order_help_messages m SET state='skipped',reason='expired',lease=NULL
 FROM public.order_help_challenges c WHERE c.id=m.challenge_id AND m.state IN ('pending','sending','retry')
  AND m.next_attempt_at<=now() AND (m.attempts>=5 OR m.created_at<=now()-interval '20 hours'
   OR (m.kind='otp' AND (c.expires_at<=now() OR c.verified_at IS NOT NULL)));
 SELECT m.* INTO v FROM public.order_help_messages m JOIN public.order_help_challenges c ON c.id=m.challenge_id
 WHERE m.state IN ('pending','sending','retry') AND m.next_attempt_at<=now() AND m.attempts<5
  AND m.created_at>now()-interval '20 hours'
  AND (m.kind='receipt' OR (c.expires_at>now() AND c.verified_at IS NULL))
 ORDER BY CASE WHEN m.kind='otp' THEN 0 ELSE 1 END,m.next_attempt_at,m.challenge_id
 FOR UPDATE OF m SKIP LOCKED LIMIT 1;
 IF NOT FOUND THEN RETURN NULL; END IF;
 SELECT * INTO c FROM public.order_help_challenges WHERE id=v.challenge_id;
 UPDATE public.order_help_messages SET state='sending',attempts=attempts+1,lease=p_lease,
  next_attempt_at=now()+interval '2 minutes' WHERE challenge_id=v.challenge_id AND kind=v.kind;
 RETURN jsonb_build_object('challenge_id',v.challenge_id,'kind',v.kind,'order_id',c.order_id,
  'email_hash',c.email_hash,'language',c.language,'created_at',v.created_at,'attempts',v.attempts+1);
END;
$$;
REVOKE ALL ON FUNCTION public.claim_order_help_message(text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.claim_order_help_message(text) TO service_role;

CREATE FUNCTION public.finish_order_help_message(p_id text,p_kind text,p_lease text,p_state text,
 p_provider_id text,p_retry_seconds integer,p_reason text) RETURNS boolean
LANGUAGE plpgsql SECURITY INVOKER SET search_path='' AS $$
BEGIN
 IF p_state IS NULL OR p_state NOT IN ('sent','retry','failed','skipped')
  OR p_retry_seconds IS NULL OR p_retry_seconds NOT BETWEEN 30 AND 3600 THEN RETURN false; END IF;
 UPDATE public.order_help_messages SET state=p_state,lease=NULL,
  sent_at=CASE WHEN p_state='sent' THEN now() ELSE sent_at END,
  provider_id=CASE WHEN p_state='sent' THEN p_provider_id ELSE provider_id END,
  next_attempt_at=now()+make_interval(secs=>p_retry_seconds),reason=p_reason
 WHERE challenge_id=p_id AND kind=p_kind AND state='sending' AND lease=p_lease;
 RETURN FOUND;
END;
$$;
REVOKE ALL ON FUNCTION public.finish_order_help_message(text,text,text,text,text,integer,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.finish_order_help_message(text,text,text,text,text,integer,text) TO service_role;
NOTIFY pgrst,'reload schema';
