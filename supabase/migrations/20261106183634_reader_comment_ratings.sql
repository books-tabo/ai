ALTER TABLE public.book_comments
  ADD COLUMN rating smallint CHECK (rating BETWEEN 1 AND 5);

COMMENT ON COLUMN public.book_comments.rating IS
  'Optional rating supplied with the comment. Existing comments have no inferred rating.';

-- A default keeps requests from older app builds valid without RPC overloads.
DROP FUNCTION public.publish_reader_comment(text,text,text,text,text,text);
CREATE FUNCTION public.publish_reader_comment(
  p_book_id text, p_email_hash text, p_display_name text, p_body text,
  p_verified_order_hash text, p_opt_in_email text, p_rating integer DEFAULT NULL
)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path='' AS $$
BEGIN
  INSERT INTO public.book_comments(
    book_id,email_hash,display_name,body,verified_order_hash,verified_purchase,status,rating
  )
    VALUES(
      p_book_id,p_email_hash,p_display_name,p_body,p_verified_order_hash,
      p_verified_order_hash IS NOT NULL,'approved',p_rating
    )
    ON CONFLICT DO NOTHING;

  IF FOUND AND p_opt_in_email IS NOT NULL THEN
    INSERT INTO public.reader_comment_subscribers(email_hash,email)
      VALUES(p_email_hash,p_opt_in_email)
      ON CONFLICT DO NOTHING;
  END IF;

  RETURN jsonb_build_object('accepted',true);
END;
$$;
REVOKE ALL ON FUNCTION public.publish_reader_comment(text,text,text,text,text,text,integer)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.publish_reader_comment(text,text,text,text,text,text,integer)
  TO service_role;

CREATE OR REPLACE FUNCTION public.list_reader_comments(p_book_id text,p_offset integer DEFAULT 0)
RETURNS jsonb LANGUAGE sql STABLE SECURITY INVOKER SET search_path='' AS $$
  SELECT jsonb_build_object('total',(SELECT count(*) FROM public.book_comments WHERE book_id=p_book_id AND status='approved'),
    'comments',coalesce((SELECT jsonb_agg(row_to_json(c) ORDER BY c.created_at DESC,c.id DESC)
    FROM (SELECT id,display_name,body,verified_purchase,created_at,rating FROM public.book_comments
          WHERE book_id=p_book_id AND status='approved'
          ORDER BY created_at DESC,id DESC LIMIT 20 OFFSET greatest(0,least(p_offset,1000))) c),'[]'::jsonb));
$$;

NOTIFY pgrst, 'reload schema';
