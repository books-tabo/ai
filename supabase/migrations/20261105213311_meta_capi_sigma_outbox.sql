-- Backend-only delivery queue, scoped by dataset. No historical order backfill.
CREATE TABLE public.meta_conversion_outbox (
  event_key text PRIMARY KEY CHECK (event_key ~ '^[0-9a-f]{64}$'),
  pixel_id text NOT NULL CHECK (pixel_id ~ '^[0-9]{5,25}$'),
  event_name text NOT NULL CHECK (event_name IN ('PageView','ViewContent','AddToCart','InitiateCheckout','Purchase')),
  order_id text REFERENCES public.orders(id) ON DELETE CASCADE,
  payload jsonb NOT NULL CHECK (jsonb_typeof(payload)='object' AND octet_length(payload::text)<=8192),
  user_data jsonb NOT NULL CHECK (jsonb_typeof(user_data)='object' AND octet_length(user_data::text)<=2048
    AND user_data - ARRAY['client_ip_address','client_user_agent','fbp','fbc']::text[] = '{}'::jsonb),
  state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','sending','retry','sent','failed','skipped')),
  created_at timestamptz NOT NULL DEFAULT now(),
  next_attempt_at timestamptz NOT NULL DEFAULT now(),
  attempts integer NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 8),
  lease text CHECK (lease IS NULL OR lease ~ '^[0-9a-f]{64}$'),
  sent_at timestamptz,
  provider_trace text CHECK (length(provider_trace)<=100),
  reason text CHECK (reason IS NULL OR reason IN ('expired','ineligible','transient','provider_rejected')),
  CHECK ((event_name='Purchase') = (order_id IS NOT NULL))
);
ALTER TABLE public.meta_conversion_outbox ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.meta_conversion_outbox FROM PUBLIC, anon, authenticated;
GRANT SELECT,INSERT,UPDATE,DELETE ON public.meta_conversion_outbox TO service_role;
CREATE INDEX meta_conversion_pending_idx ON public.meta_conversion_outbox(pixel_id,next_attempt_at)
  WHERE state IN ('pending','sending','retry');
CREATE INDEX meta_conversion_order_idx ON public.meta_conversion_outbox(order_id) WHERE order_id IS NOT NULL;

CREATE FUNCTION public.claim_meta_conversion(p_pixel_id text,p_lease text) RETURNS jsonb
LANGUAGE plpgsql SECURITY INVOKER SET search_path='' AS $$
DECLARE v public.meta_conversion_outbox%ROWTYPE;
BEGIN
  IF p_pixel_id IS NULL OR p_pixel_id !~ '^[0-9]{5,25}$'
     OR p_lease IS NULL OR p_lease !~ '^[0-9a-f]{64}$' THEN RETURN NULL; END IF;
  -- Retry for less than Meta's deduplication window. Clear connection metadata
  -- immediately on completion/expiry, and retain only delivery state for 7 days.
  DELETE FROM public.meta_conversion_outbox WHERE created_at<now()-interval '7 days';
  UPDATE public.meta_conversion_outbox SET state='failed',reason='expired',lease=NULL,payload='{}',user_data='{}'
    WHERE pixel_id=p_pixel_id AND state IN ('pending','retry','sending')
      AND (created_at<=now()-interval '20 hours' OR attempts>=8) AND next_attempt_at<=now();
  SELECT q.* INTO v FROM public.meta_conversion_outbox q
    LEFT JOIN public.orders o ON o.id=q.order_id
    WHERE q.pixel_id=p_pixel_id AND q.state IN ('pending','retry','sending')
      AND q.next_attempt_at<=now() AND q.created_at>now()-interval '20 hours' AND q.attempts<8
      AND (q.order_id IS NULL OR (o.status='PAID' AND o.payment_mode='live'
        AND o.transaction_id IS NOT NULL AND o.paid_at>=q.created_at-interval '1 minute'))
    ORDER BY q.next_attempt_at,q.event_key FOR UPDATE OF q SKIP LOCKED LIMIT 1;
  IF NOT FOUND THEN RETURN NULL; END IF;
  UPDATE public.meta_conversion_outbox SET state='sending',attempts=attempts+1,
    lease=p_lease,next_attempt_at=now()+interval '2 minutes' WHERE event_key=v.event_key;
  RETURN jsonb_build_object('event_key',v.event_key,'event_name',v.event_name,'order_id',v.order_id,
    'payload',v.payload,'user_data',v.user_data,'attempts',v.attempts+1);
END;
$$;
REVOKE ALL ON FUNCTION public.claim_meta_conversion(text,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.claim_meta_conversion(text,text) TO service_role;

CREATE FUNCTION public.finish_meta_conversion(p_event_key text,p_lease text,p_state text,
  p_trace text,p_retry_seconds integer,p_reason text) RETURNS boolean
LANGUAGE plpgsql SECURITY INVOKER SET search_path='' AS $$
BEGIN
  IF p_state IS NULL OR p_state NOT IN ('sent','retry','failed','skipped')
     OR p_retry_seconds IS NULL OR p_retry_seconds NOT BETWEEN 30 AND 3600 THEN RETURN false; END IF;
  UPDATE public.meta_conversion_outbox SET state=p_state,lease=NULL,reason=p_reason,
    sent_at=CASE WHEN p_state='sent' THEN now() ELSE sent_at END,
    provider_trace=CASE WHEN p_state='sent' THEN p_trace ELSE provider_trace END,
    payload=CASE WHEN p_state='retry' THEN payload ELSE '{}'::jsonb END,
    user_data=CASE WHEN p_state='retry' THEN user_data ELSE '{}'::jsonb END,
    next_attempt_at=now()+make_interval(secs=>p_retry_seconds)
    WHERE event_key=p_event_key AND state='sending' AND lease=p_lease;
  RETURN FOUND;
END;
$$;
REVOKE ALL ON FUNCTION public.finish_meta_conversion(text,text,text,text,integer,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.finish_meta_conversion(text,text,text,text,integer,text) TO service_role;
NOTIFY pgrst,'reload schema';
