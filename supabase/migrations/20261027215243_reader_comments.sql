-- Only new objects. Never rewrite ratings, downloads, orders or existing policies.
CREATE TABLE IF NOT EXISTS public.book_comments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  book_id text NOT NULL REFERENCES public.book_stats(book_id),
  email_hash text NOT NULL CHECK(email_hash ~ '^[0-9a-f]{64}$'),
  display_name text NOT NULL CHECK(char_length(display_name) BETWEEN 1 AND 80),
  body text NOT NULL CHECK(char_length(body) BETWEEN 5 AND 2000),
  verified_order_hash text CHECK(verified_order_hash IS NULL OR verified_order_hash ~ '^[0-9a-f]{64}$'),
  verified_purchase boolean NOT NULL DEFAULT false,
  status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')),
  created_at timestamptz NOT NULL DEFAULT now(),
  moderated_at timestamptz,
  moderation_note text CHECK(char_length(moderation_note)<=1000),
  CHECK(verified_purchase = (verified_order_hash IS NOT NULL)),
  UNIQUE(book_id,email_hash)
);
CREATE UNIQUE INDEX IF NOT EXISTS book_comments_one_purchase
  ON public.book_comments(book_id,verified_order_hash) WHERE verified_order_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS book_comments_public_listing
  ON public.book_comments(book_id,created_at DESC,id DESC) WHERE status='approved';
ALTER TABLE public.book_comments ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.book_comments FROM PUBLIC,anon,authenticated;
GRANT SELECT,INSERT,UPDATE ON public.book_comments TO service_role;

CREATE FUNCTION public.submit_reader_comment(p_book_id text,p_email_hash text,p_display_name text,p_body text,
                                           p_verified_order_hash text DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path='' AS $$
BEGIN
  INSERT INTO public.book_comments(book_id,email_hash,display_name,body,verified_order_hash,verified_purchase)
    VALUES(p_book_id,p_email_hash,p_display_name,p_body,p_verified_order_hash,p_verified_order_hash IS NOT NULL)
    ON CONFLICT DO NOTHING;
  -- Identical response for new submissions and duplicates; no email enumeration.
  -- Duplicates never overwrite a previously reviewed comment.
  RETURN jsonb_build_object('accepted',true);
END;
$$;
CREATE FUNCTION public.list_reader_comments(p_book_id text,p_offset integer DEFAULT 0)
RETURNS jsonb LANGUAGE sql STABLE SECURITY INVOKER SET search_path='' AS $$
  SELECT jsonb_build_object('total',(SELECT count(*) FROM public.book_comments WHERE book_id=p_book_id AND status='approved'),
    'comments',coalesce((SELECT jsonb_agg(row_to_json(c) ORDER BY c.created_at DESC,c.id DESC)
    FROM (SELECT id,display_name,body,verified_purchase,created_at FROM public.book_comments
          WHERE book_id=p_book_id AND status='approved'
          ORDER BY created_at DESC,id DESC LIMIT 20 OFFSET greatest(0,least(p_offset,1000))) c),'[]'::jsonb));
$$;
REVOKE ALL ON FUNCTION public.submit_reader_comment(text,text,text,text,text),public.list_reader_comments(text,integer)
  FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.submit_reader_comment(text,text,text,text,text),public.list_reader_comments(text,integer)
  TO service_role;
COMMENT ON TABLE public.book_comments IS 'Genuine reader submissions. Publish after content-neutral spam/abuse moderation. No raw email stored, no aggregate counters changed.';
