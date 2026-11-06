-- Captured from read-only catalog inspection on 2026-10-18.
-- Run as postgres. Existing tables and rows are preserved. Do not apply to live as part of PR review.

CREATE TABLE IF NOT EXISTS public.orders (
  id text NOT NULL,
  status text NOT NULL DEFAULT 'PENDING'::text,
  items jsonb NOT NULL,
  total numeric NOT NULL,
  currency text NOT NULL DEFAULT 'EGP'::text,
  payment_method text NOT NULL DEFAULT 'Payment Link'::text,
  status_key text NOT NULL,
  download_token text,
  buyer jsonb,
  transaction_id text,
  paymob_order_id text,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  paid_at timestamp with time zone,
  country text NOT NULL DEFAULT 'EG'::text,
  paymob_method_id integer,
  checkout_session_hash text,
  download_session_hash text,
  download_device_hash text,
  download_session_expires_at timestamp with time zone,
  download_token_expires_at timestamp with time zone,
  download_token_used_at timestamp with time zone,
  CONSTRAINT orders_checkout_session_hash_check CHECK (((checkout_session_hash IS NULL) OR (checkout_session_hash ~ '^[0-9a-f]{64}$'::text))),
  CONSTRAINT orders_country_check CHECK ((country = ANY (ARRAY['EG'::text, 'SA'::text, 'AE'::text, 'KW'::text, 'QA'::text, 'BH'::text, 'OM'::text, 'JO'::text, 'PS'::text, 'IQ'::text, 'LB'::text, 'SY'::text, 'YE'::text, 'SD'::text, 'SO'::text, 'DJ'::text, 'KM'::text, 'LY'::text, 'TN'::text, 'DZ'::text, 'MA'::text, 'MR'::text]))),
  CONSTRAINT orders_currency_check CHECK ((currency = ANY (ARRAY['EGP'::text, 'USD'::text, 'SAR'::text, 'AED'::text, 'KWD'::text, 'QAR'::text, 'BHD'::text, 'OMR'::text, 'JOD'::text, 'ILS'::text, 'IQD'::text, 'LBP'::text, 'SYP'::text, 'YER'::text, 'SDG'::text, 'SOS'::text, 'DJF'::text, 'KMF'::text, 'LYD'::text, 'TND'::text, 'DZD'::text, 'MAD'::text, 'MRU'::text]))),
  CONSTRAINT orders_download_device_hash_check CHECK (((download_device_hash IS NULL) OR (download_device_hash ~ '^[0-9a-f]{64}$'::text))),
  CONSTRAINT orders_download_session_hash_check CHECK (((download_session_hash IS NULL) OR (download_session_hash ~ '^[0-9a-f]{64}$'::text))),
  CONSTRAINT orders_download_token_check CHECK (((download_token IS NULL) OR (length(download_token) = 64))),
  CONSTRAINT orders_download_token_key UNIQUE (download_token),
  CONSTRAINT orders_id_check CHECK ((id ~ '^TABO-[A-F0-9]{16}$'::text)),
  CONSTRAINT orders_items_check CHECK ((jsonb_typeof(items) = 'array'::text)),
  CONSTRAINT orders_pkey PRIMARY KEY (id),
  CONSTRAINT orders_status_check CHECK ((status = ANY (ARRAY['PENDING'::text, 'PAID'::text]))),
  CONSTRAINT orders_status_key_check CHECK ((length(status_key) = 64)),
  CONSTRAINT orders_status_key_key UNIQUE (status_key),
  CONSTRAINT orders_total_check CHECK ((total > (0)::numeric)),
  CONSTRAINT orders_transaction_id_key UNIQUE (transaction_id),
  CONSTRAINT paid_only_with_transaction CHECK ((((status = 'PENDING'::text) AND (transaction_id IS NULL) AND (paid_at IS NULL) AND (download_token IS NULL) AND (download_session_hash IS NULL)) OR ((status = 'PAID'::text) AND (transaction_id IS NOT NULL) AND (paid_at IS NOT NULL))))
);
ALTER TABLE public.orders ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.orders FROM PUBLIC,anon,authenticated;

CREATE TABLE IF NOT EXISTS public.book_stats (
  book_id text NOT NULL,
  rating_sum numeric NOT NULL DEFAULT 0,
  review_count integer NOT NULL DEFAULT 0,
  download_count bigint NOT NULL DEFAULT 0,
  page_count integer,
  file_size_bytes bigint NOT NULL,
  updated_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT book_stats_book_id_check CHECK ((book_id ~ '^book[1-9][0-9]*$'::text)),
  CONSTRAINT book_stats_download_count_check CHECK ((download_count >= 0)),
  CONSTRAINT book_stats_file_size_bytes_check CHECK ((file_size_bytes > 0)),
  CONSTRAINT book_stats_page_count_check CHECK (((page_count IS NULL) OR (page_count > 0))),
  CONSTRAINT book_stats_pkey PRIMARY KEY (book_id),
  CONSTRAINT book_stats_rating_sum_check CHECK ((rating_sum >= (0)::numeric)),
  CONSTRAINT book_stats_review_count_check CHECK ((review_count >= 0))
);
ALTER TABLE public.book_stats ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.book_stats FROM PUBLIC,anon,authenticated;

CREATE TABLE IF NOT EXISTS public.book_ratings (
  book_id text NOT NULL,
  voter_hash text NOT NULL,
  rating smallint NOT NULL,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  updated_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT book_ratings_book_id_check CHECK ((book_id ~ '^book[1-9][0-9]*$'::text)),
  CONSTRAINT book_ratings_pkey PRIMARY KEY (book_id, voter_hash),
  CONSTRAINT book_ratings_rating_check CHECK (((rating >= 1) AND (rating <= 5))),
  CONSTRAINT book_ratings_voter_hash_check CHECK ((voter_hash ~ '^[0-9a-f]{64}$'::text))
);
ALTER TABLE public.book_ratings ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.book_ratings FROM PUBLIC,anon,authenticated;

CREATE TABLE IF NOT EXISTS public.order_downloads (
  order_id text NOT NULL,
  book_id text NOT NULL,
  download_count smallint NOT NULL DEFAULT 0,
  first_download_at timestamp with time zone,
  last_download_at timestamp with time zone,
  CONSTRAINT order_downloads_book_id_check CHECK ((book_id ~ '^book[1-9][0-9]*$'::text)),
  CONSTRAINT order_downloads_download_count_check CHECK (((download_count >= 0) AND (download_count <= 2))),
  CONSTRAINT order_downloads_order_id_fkey FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
  CONSTRAINT order_downloads_pkey PRIMARY KEY (order_id, book_id)
);
ALTER TABLE public.order_downloads ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.order_downloads FROM PUBLIC,anon,authenticated;
CREATE UNIQUE INDEX IF NOT EXISTS orders_checkout_session_hash_key ON public.orders USING btree (checkout_session_hash) WHERE (checkout_session_hash IS NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS orders_download_session_hash_key ON public.orders USING btree (download_session_hash) WHERE (download_session_hash IS NOT NULL);

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
  if p_order_id !~ '^TABO-[A-F0-9]{16}$'
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

  return jsonb_build_object(
    'allowed', true,
    'reason', 'ok',
    'remaining', greatest(0, 2 - v_count),
    'used', v_count
  );
end;
$function$
;

CREATE OR REPLACE FUNCTION public.submit_book_rating(p_book_id text, p_voter_hash text, p_rating integer)
 RETURNS jsonb
 LANGUAGE plpgsql
 SET search_path TO ''
AS $function$
declare
  v_sum numeric;
  v_count integer;
begin
  if p_book_id !~ '^book[1-9][0-9]*$'
     or not exists (select 1 from public.book_stats where book_id=p_book_id)
     or p_voter_hash !~ '^[0-9a-f]{64}$'
     or p_rating not between 1 and 5 then
    raise exception 'invalid rating';
  end if;

  insert into public.book_ratings (book_id,voter_hash,rating)
  values (p_book_id,p_voter_hash,p_rating::smallint)
  on conflict (book_id,voter_hash)
  do update set rating=excluded.rating,updated_at=now();

  select coalesce(sum(rating),0),count(*)::integer
    into v_sum,v_count
    from public.book_ratings
   where book_id=p_book_id;

  update public.book_stats
     set rating_sum=v_sum,review_count=v_count,updated_at=now()
   where book_id=p_book_id;

  return jsonb_build_object(
    'rating',round(v_sum/nullif(v_count,0),1),
    'reviews',v_count
  );
end;
$function$
;

GRANT SELECT,INSERT,UPDATE ON public.orders,public.book_stats,public.book_ratings TO service_role;
REVOKE ALL ON public.order_downloads FROM service_role;
GRANT SELECT ON public.order_downloads TO service_role;
REVOKE ALL ON FUNCTION public.claim_order_download(text,text,text,text,integer),public.submit_book_rating(text,text,integer) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.claim_order_download(text,text,text,text,integer),public.submit_book_rating(text,text,integer) TO service_role;
-- Scope is the current migration creator, not every database role.
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM PUBLIC,anon,authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM PUBLIC,anon,authenticated;
ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC,anon,authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC,anon,authenticated;
-- supabase_admin has separate defaults; audit them, never claim these ALTERs changed that role.
DO $$ BEGIN
 IF to_regclass('storage.buckets') IS NOT NULL THEN
  INSERT INTO storage.buckets(id,name,public) VALUES('tabo-originals','tabo-originals',false) ON CONFLICT(id) DO NOTHING;
  IF EXISTS(SELECT 1 FROM storage.buckets WHERE id='tabo-originals' AND public) THEN
    RAISE EXCEPTION 'Existing originals bucket must be private; review its configuration';
  END IF;
 END IF;
END $$;

