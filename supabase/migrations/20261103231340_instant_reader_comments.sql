-- The existing submit RPC remains available for rollback to older app builds.
CREATE TABLE public.reader_comment_subscribers (
  email_hash text PRIMARY KEY CHECK(email_hash ~ '^[0-9a-f]{64}$'),
  email text NOT NULL CHECK(char_length(email) BETWEEN 3 AND 254),
  consented_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.reader_comment_subscribers ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.reader_comment_subscribers FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT ON public.reader_comment_subscribers TO service_role;
CREATE INDEX reader_comment_subscribers_listing
  ON public.reader_comment_subscribers(consented_at DESC, email_hash DESC);

CREATE FUNCTION public.publish_reader_comment(
  p_book_id text, p_email_hash text, p_display_name text, p_body text,
  p_verified_order_hash text, p_opt_in_email text
)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path='' AS $$
BEGIN
  INSERT INTO public.book_comments(book_id,email_hash,display_name,body,verified_order_hash,verified_purchase,status)
    VALUES(p_book_id,p_email_hash,p_display_name,p_body,p_verified_order_hash,p_verified_order_hash IS NOT NULL,'approved')
    ON CONFLICT DO NOTHING;

  -- Duplicate submissions cannot overwrite comments, republish removed comments,
  -- or change a previous subscription. Keep the receipt identical in each case.
  IF FOUND AND p_opt_in_email IS NOT NULL THEN
    INSERT INTO public.reader_comment_subscribers(email_hash,email)
      VALUES(p_email_hash,p_opt_in_email)
      ON CONFLICT DO NOTHING;
  END IF;

  RETURN jsonb_build_object('accepted',true);
END;
$$;
REVOKE ALL ON FUNCTION public.publish_reader_comment(text,text,text,text,text,text)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.publish_reader_comment(text,text,text,text,text,text)
  TO service_role;
COMMENT ON TABLE public.book_comments IS 'Reader submissions. New comments are published on submission and can be removed by the owner. Raw emails are not stored in this table.';
COMMENT ON TABLE public.reader_comment_subscribers IS 'Private mailing-list opt-ins from reader comments. Typed addresses are unverified.';
