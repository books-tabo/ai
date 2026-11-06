-- Future paid purchases only. No historical backfill or changes to download RPCs.
CREATE TABLE public.order_receipts (
  order_id text PRIMARY KEY REFERENCES public.orders(id) ON DELETE CASCADE,
  state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','sending','retry','sent','failed','skipped')),
  created_at timestamptz NOT NULL DEFAULT now(),
  next_attempt_at timestamptz NOT NULL DEFAULT now(),
  attempts integer NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 8),
  lease text CHECK (lease IS NULL OR lease ~ '^[0-9a-f]{64}$'),
  sent_at timestamptz,
  provider_id text CHECK (length(provider_id) <= 100),
  reason text CHECK (reason IS NULL OR reason IN ('expired','ineligible','transient','provider_rejected'))
);
ALTER TABLE public.order_receipts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.order_receipts FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON public.order_receipts TO service_role;
CREATE INDEX order_receipts_pending_idx ON public.order_receipts(next_attempt_at)
  WHERE state IN ('pending','sending','retry');

CREATE FUNCTION public.enqueue_order_receipt() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $$
BEGIN
  IF OLD.status = 'PENDING' AND NEW.status = 'PAID' AND NEW.payment_mode = 'live'
     AND NEW.buyer->'receiptRequested' = 'true'::jsonb
     AND length(NEW.buyer->>'email') BETWEEN 3 AND 254
     AND NEW.buyer->>'email' ~ '^[^[:space:]@<>]+@[^[:space:]@<>]+\.[^[:space:]@<>]+$' THEN
    INSERT INTO public.order_receipts(order_id) VALUES (NEW.id) ON CONFLICT DO NOTHING;
  END IF;
  RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION public.enqueue_order_receipt() FROM PUBLIC, anon, authenticated;
CREATE TRIGGER enqueue_order_receipt AFTER UPDATE OF status ON public.orders
FOR EACH ROW WHEN (OLD.status IS DISTINCT FROM NEW.status)
EXECUTE FUNCTION public.enqueue_order_receipt();

CREATE FUNCTION public.claim_order_receipt(p_lease text) RETURNS jsonb
LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $$
DECLARE v public.order_receipts%ROWTYPE;
BEGIN
  IF p_lease IS NULL OR p_lease !~ '^[0-9a-f]{64}$' THEN RETURN NULL; END IF;
  -- Stop before the email provider's 24-hour idempotency window can expire.
  UPDATE public.order_receipts SET state='failed', reason='expired', lease=NULL
  WHERE state IN ('pending','retry','sending') AND next_attempt_at<=now()
    AND (created_at<=now()-interval '20 hours' OR attempts>=8);
  SELECT * INTO v FROM public.order_receipts
  WHERE state IN ('pending','retry','sending') AND next_attempt_at<=now()
    AND created_at>now()-interval '20 hours' AND attempts<8
  ORDER BY next_attempt_at,order_id FOR UPDATE SKIP LOCKED LIMIT 1;
  IF NOT FOUND THEN RETURN NULL; END IF;
  UPDATE public.order_receipts SET state='sending', attempts=attempts+1,
    lease=p_lease, next_attempt_at=now()+interval '2 minutes' WHERE order_id=v.order_id;
  RETURN jsonb_build_object('order_id',v.order_id,'created_at',v.created_at,'attempts',v.attempts+1);
END;
$$;
REVOKE ALL ON FUNCTION public.claim_order_receipt(text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_order_receipt(text) TO service_role;

CREATE FUNCTION public.finish_order_receipt(p_order_id text,p_lease text,p_state text,
  p_provider_id text,p_retry_seconds integer,p_reason text) RETURNS boolean
LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $$
BEGIN
  IF p_state IS NULL OR p_state NOT IN ('sent','retry','failed','skipped')
     OR p_retry_seconds IS NULL OR p_retry_seconds NOT BETWEEN 30 AND 3600 THEN RETURN false; END IF;
  UPDATE public.order_receipts SET state=p_state, lease=NULL,
    sent_at=CASE WHEN p_state='sent' THEN now() ELSE sent_at END,
    provider_id=CASE WHEN p_state='sent' THEN p_provider_id ELSE provider_id END,
    next_attempt_at=now()+make_interval(secs=>p_retry_seconds), reason=p_reason
  WHERE order_id=p_order_id AND state='sending' AND lease=p_lease;
  RETURN FOUND;
END;
$$;
REVOKE ALL ON FUNCTION public.finish_order_receipt(text,text,text,text,integer,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.finish_order_receipt(text,text,text,text,integer,text) TO service_role;

-- Refresh the backend-only RPC signatures in the Data API schema cache.
NOTIFY pgrst, 'reload schema';
