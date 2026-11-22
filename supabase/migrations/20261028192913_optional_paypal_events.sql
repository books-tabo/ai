-- Optional PayPal journal. No existing table, constraint or payment RPC is replaced.
CREATE TABLE public.paypal_payment_events (
  mode text NOT NULL CHECK(mode IN ('test','live')),
  transaction_id text NOT NULL CHECK(transaction_id ~ '^[A-Z0-9]{10,32}$'),
  kind text NOT NULL CHECK(kind IN ('paid','refund','review_notice')),
  order_id text NOT NULL REFERENCES public.orders(id),
  provider_order_id text NOT NULL CHECK(provider_order_id ~ '^[A-Z0-9]{10,32}$'),
  capture_total numeric NOT NULL CHECK(capture_total>0 AND capture_total<1000000),
  currency text NOT NULL CHECK(currency='USD'),
  event_digest text NOT NULL CHECK(event_digest ~ '^[0-9a-f]{64}$'),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(mode,transaction_id,kind)
);
CREATE INDEX paypal_payment_events_order ON public.paypal_payment_events(order_id,created_at);
ALTER TABLE public.paypal_payment_events ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.paypal_payment_events FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT ON public.paypal_payment_events TO service_role;

CREATE FUNCTION public.apply_paypal_event(p_order_id text,p_mode text,p_transaction_id text,p_kind text,
                                        p_amount numeric,p_provider_order_id text,p_digest text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE o public.orders%ROWTYPE; existing_order text; target text;
BEGIN
  IF p_mode IS NULL OR p_mode NOT IN ('test','live') OR p_kind IS NULL OR p_kind NOT IN ('paid','refund','review_notice')
     OR p_transaction_id IS NULL OR p_transaction_id !~ '^[A-Z0-9]{10,32}$'
     OR p_provider_order_id IS NULL OR p_provider_order_id !~ '^[A-Z0-9]{10,32}$'
     OR p_digest IS NULL OR p_digest !~ '^[0-9a-f]{64}$'
     OR p_amount IS NULL OR p_amount<=0 OR p_amount>=1000000 OR p_amount='NaN'::numeric THEN
    RETURN jsonb_build_object('ok',false,'reason','invalid');
  END IF;
  SELECT * INTO o FROM public.orders WHERE id=p_order_id FOR UPDATE;
  IF NOT FOUND OR o.payment_method<>'PayPal' OR o.payment_mode<>p_mode OR o.currency<>'USD'
     OR o.paymob_order_id IS DISTINCT FROM p_provider_order_id OR o.total<>p_amount THEN
    RETURN jsonb_build_object('ok',false,'reason','mismatch');
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended('paypal:'||p_mode||':'||p_transaction_id,0));
  SELECT order_id INTO existing_order FROM public.paypal_payment_events
    WHERE mode=p_mode AND transaction_id=p_transaction_id LIMIT 1;
  IF (existing_order IS NOT NULL AND existing_order<>p_order_id)
     OR EXISTS(SELECT 1 FROM public.orders WHERE transaction_id=p_transaction_id AND id<>p_order_id)
     OR (o.transaction_id IS NOT NULL AND o.transaction_id<>p_transaction_id) THEN
    RETURN jsonb_build_object('ok',false,'reason','transaction_conflict');
  END IF;
  IF EXISTS(SELECT 1 FROM public.paypal_payment_events
            WHERE mode=p_mode AND transaction_id=p_transaction_id AND kind=p_kind) THEN
    RETURN jsonb_build_object('ok',true,'duplicate',true,'status',o.status);
  END IF;
  INSERT INTO public.paypal_payment_events(mode,transaction_id,kind,order_id,provider_order_id,capture_total,currency,event_digest)
    VALUES(p_mode,p_transaction_id,p_kind,p_order_id,p_provider_order_id,p_amount,'USD',p_digest);
  IF p_kind='paid' THEN
    IF o.status='PENDING' THEN
      UPDATE public.orders SET status='PAID',transaction_id=p_transaction_id,paid_at=now() WHERE id=p_order_id;
      target='PAID';
    ELSE target=o.status; END IF;
  ELSE
    target=CASE WHEN p_kind='refund' THEN 'REFUNDED' ELSE 'REVIEW' END;
    IF o.status IN ('REFUNDED','VOIDED','REVERSED','REVIEW') THEN target=o.status; END IF;
    UPDATE public.orders SET status=target,download_session_hash=NULL,download_device_hash=NULL,
      download_session_expires_at=NULL,checkout_session_hash=NULL,download_token=NULL WHERE id=p_order_id;
  END IF;
  RETURN jsonb_build_object('ok',true,'duplicate',false,'status',target);
END;
$$;
-- Definer is limited to the existing trusted backend, for append-only journal
-- writes and one locked atomic order transition. Never callable by clients.
REVOKE ALL ON FUNCTION public.apply_paypal_event(text,text,text,text,numeric,text,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.apply_paypal_event(text,text,text,text,numeric,text,text) TO service_role;
