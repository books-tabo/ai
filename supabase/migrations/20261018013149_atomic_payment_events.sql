-- Additive upgrade; do not reset or backfill existing orders as newly verified payments.
ALTER TABLE public.orders ADD COLUMN IF NOT EXISTS payment_mode text NOT NULL DEFAULT 'legacy';
ALTER TABLE public.orders ADD CONSTRAINT orders_payment_mode_check CHECK(payment_mode IN ('legacy','test','live'));
ALTER TABLE public.orders DROP CONSTRAINT IF EXISTS orders_status_check;
ALTER TABLE public.orders ADD CONSTRAINT orders_status_check CHECK(status IN ('PENDING','PAID','REFUNDED','VOIDED','REVERSED','REVIEW'));
ALTER TABLE public.orders DROP CONSTRAINT IF EXISTS paid_only_with_transaction;
ALTER TABLE public.orders ADD CONSTRAINT paid_only_with_transaction CHECK (
 (status='PENDING' AND transaction_id IS NULL AND paid_at IS NULL AND download_token IS NULL AND download_session_hash IS NULL)
 OR (status='PAID' AND transaction_id IS NOT NULL AND paid_at IS NOT NULL)
 OR (status IN ('REFUNDED','VOIDED','REVERSED','REVIEW') AND download_session_hash IS NULL AND checkout_session_hash IS NULL AND download_token IS NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS orders_provider_order_unique ON public.orders(payment_method,payment_mode,paymob_order_id) WHERE paymob_order_id IS NOT NULL AND payment_mode<>'legacy';
CREATE TABLE public.payment_events (
 provider text NOT NULL CHECK(provider IN ('kashier','paymob')),
 mode text NOT NULL CHECK(mode IN ('test','live')),
 transaction_id text NOT NULL CHECK(transaction_id ~ '^[A-Za-z0-9_.:-]{1,128}$'),
 kind text NOT NULL CHECK(kind IN ('paid','refund','void','reversal','partial_refund')),
 order_id text NOT NULL REFERENCES public.orders(id),
 amount numeric NOT NULL CHECK(amount>0), currency text NOT NULL,
 provider_order_id text NOT NULL, event_digest text NOT NULL CHECK(event_digest ~ '^[0-9a-f]{64}$'),
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(provider,mode,transaction_id,kind)
);
CREATE INDEX payment_events_order_idx ON public.payment_events(order_id,created_at);
ALTER TABLE public.payment_events ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.payment_events FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT ON public.payment_events TO service_role;

CREATE FUNCTION public.apply_payment_event(p_order_id text,p_provider text,p_mode text,
 p_transaction_id text,p_kind text,p_amount numeric,p_currency text,p_provider_order_id text,p_digest text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE o public.orders%ROWTYPE; target text; existing_order text;
BEGIN
 IF p_order_id IS NULL OR p_provider IS NULL OR p_mode IS NULL OR p_kind IS NULL OR p_currency IS NULL OR p_provider_order_id IS NULL OR p_provider NOT IN ('kashier','paymob') OR p_mode NOT IN ('test','live')
  OR p_kind NOT IN ('paid','refund','void','reversal','partial_refund') OR p_transaction_id IS NULL
  OR p_transaction_id !~ '^[A-Za-z0-9_.:-]{1,128}$' OR p_digest IS NULL OR p_digest !~ '^[0-9a-f]{64}$'
  OR p_amount IS NULL OR p_amount<=0 OR p_amount='NaN'::numeric THEN
  RETURN jsonb_build_object('ok',false,'reason','invalid');
 END IF;
 SELECT * INTO o FROM public.orders WHERE id=p_order_id FOR UPDATE;
 IF NOT FOUND OR lower(o.payment_method) IS DISTINCT FROM p_provider OR o.payment_mode IS DISTINCT FROM p_mode
  OR o.paymob_order_id IS DISTINCT FROM p_provider_order_id OR o.currency IS DISTINCT FROM p_currency
  OR p_amount>o.total OR (p_kind='paid' AND p_amount<>o.total) THEN
  RETURN jsonb_build_object('ok',false,'reason','mismatch');
 END IF;
 -- Serialize a provider transaction across different merchant orders as well as same-order races.
 PERFORM pg_advisory_xact_lock(hashtextextended(p_provider||':'||p_mode||':'||p_transaction_id,0));
 SELECT e.order_id INTO existing_order FROM public.payment_events e
  WHERE e.provider=p_provider AND e.mode=p_mode AND e.transaction_id=p_transaction_id LIMIT 1;
 IF existing_order IS NOT NULL AND existing_order<>p_order_id THEN
  RETURN jsonb_build_object('ok',false,'reason','transaction_conflict');
 END IF;
 IF EXISTS(SELECT 1 FROM public.payment_events e WHERE e.provider=p_provider AND e.mode=p_mode
    AND e.transaction_id=p_transaction_id AND e.kind=p_kind) THEN
  RETURN jsonb_build_object('ok',true,'duplicate',true,'status',o.status);
 END IF;
 IF p_kind='paid' AND o.transaction_id IS NOT NULL AND o.transaction_id<>p_transaction_id THEN
  RETURN jsonb_build_object('ok',false,'reason','transaction_conflict');
 END IF;
 IF p_kind='paid' AND EXISTS(SELECT 1 FROM public.orders WHERE transaction_id=p_transaction_id AND id<>p_order_id) THEN
  RETURN jsonb_build_object('ok',false,'reason','transaction_conflict');
 END IF;
 INSERT INTO public.payment_events(provider,mode,transaction_id,kind,order_id,amount,currency,provider_order_id,event_digest)
 VALUES(p_provider,p_mode,p_transaction_id,p_kind,p_order_id,p_amount,p_currency,p_provider_order_id,p_digest);
 IF p_kind='paid' THEN
  IF o.status='PENDING' THEN
   UPDATE public.orders SET status='PAID',transaction_id=p_transaction_id,paid_at=now() WHERE id=p_order_id;
   target='PAID';
  ELSE target=o.status; END IF; -- Terminal revocation wins over delayed/replayed payment.
 ELSE
  target=CASE p_kind WHEN 'void' THEN 'VOIDED' WHEN 'reversal' THEN 'REVERSED' WHEN 'partial_refund' THEN 'REVIEW' ELSE 'REFUNDED' END;
  -- A partial refund cannot be mapped to individual books. Suspend the entire entitlement for review.
  IF o.status IN ('REFUNDED','VOIDED','REVERSED','REVIEW') THEN target=o.status; END IF;
  UPDATE public.orders SET status=target,download_session_hash=NULL,download_device_hash=NULL,
    download_session_expires_at=NULL,checkout_session_hash=NULL,download_token=NULL WHERE id=p_order_id;
 END IF;
 RETURN jsonb_build_object('ok',true,'duplicate',false,'status',target);
END $$;
REVOKE ALL ON FUNCTION public.apply_payment_event(text,text,text,text,text,numeric,text,text,text) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.apply_payment_event(text,text,text,text,text,numeric,text,text,text) TO service_role;

-- No alternate aggregate-only RPC may count an unclaimed download after this migration.
DO $$ BEGIN
 IF to_regprocedure('public.increment_book_download(text,integer)') IS NOT NULL THEN
  REVOKE ALL ON FUNCTION public.increment_book_download(text,integer) FROM PUBLIC,anon,authenticated,service_role;
 END IF;
END $$;

-- Roll back the claim too if verified aggregate metadata is absent.
CREATE OR REPLACE FUNCTION public.claim_order_download(p_order_id text, p_book_id text, p_session_hash text, p_device_hash text, p_page_count integer)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
declare
  v_status text;
  v_items jsonb;
  v_session_hash text;
  v_device_hash text;
  v_expires_at timestamptz;
  v_count smallint;
begin
  if p_order_id is null or p_book_id is null or p_session_hash is null or p_device_hash is null
     or p_order_id !~ '^TABO-[A-F0-9]{16}$'
     or p_book_id !~ '^book[1-9][0-9]*$'
     or p_session_hash !~ '^[0-9a-f]{64}$'
     or p_device_hash !~ '^[0-9a-f]{64}$'
     or p_page_count is null
     or p_page_count < 1 then
    return jsonb_build_object('allowed', false, 'reason', 'unauthorized');
  end if;

  select status, items, download_session_hash, download_device_hash,
         download_session_expires_at
    into v_status, v_items, v_session_hash, v_device_hash, v_expires_at
  from public.orders
  where id = p_order_id
  for update;

  if not found
     or v_status <> 'PAID'
     or v_session_hash is distinct from p_session_hash
     or v_device_hash is distinct from p_device_hash
     or v_expires_at is null
     or v_expires_at <= now()
     or not exists (
       select 1
       from jsonb_array_elements(v_items) as item
       where item->>'id' = p_book_id
     ) then
    return jsonb_build_object('allowed', false, 'reason', 'unauthorized');
  end if;

  insert into public.order_downloads (
    order_id, book_id, download_count, first_download_at, last_download_at
  )
  values (p_order_id, p_book_id, 1, now(), now())
  on conflict (order_id, book_id) do update
    set download_count = public.order_downloads.download_count + 1,
        first_download_at = coalesce(public.order_downloads.first_download_at, now()),
        last_download_at = now()
    where public.order_downloads.download_count < 2
  returning download_count into v_count;

  if v_count is null then
    return jsonb_build_object('allowed', false, 'reason', 'limit_reached', 'remaining', 0);
  end if;

  update public.book_stats
     set download_count = download_count + 1,
         page_count = coalesce(page_count, p_page_count),
         updated_at = now()
   where book_id = p_book_id;
  if not found then raise exception 'Missing verified book metadata'; end if;

  return jsonb_build_object(
    'allowed', true,
    'reason', 'ok',
    'remaining', greatest(0, 2 - v_count),
    'used', v_count
  );
end;
$function$
;
