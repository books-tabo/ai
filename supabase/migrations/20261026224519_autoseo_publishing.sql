-- Additive AutoSEO objects only. No existing store table, function, data or policy is changed.
CREATE TABLE IF NOT EXISTS public.tabo_autoseo_media (
    digest text PRIMARY KEY CHECK (digest ~ '^[0-9a-f]{64}$'),
    content_base64 text NOT NULL CHECK (length(content_base64) BETWEEN 1 AND 1333336),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.tabo_autoseo_posts (
    id bigint PRIMARY KEY CHECK (id > 0),
    slug text NOT NULL UNIQUE CHECK (length(slug) BETWEEN 1 AND 210),
    title text NOT NULL CHECK (length(title) BETWEEN 1 AND 500),
    description text NOT NULL DEFAULT '',
    language text NOT NULL,
    published_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    post jsonb NOT NULL CHECK (jsonb_typeof(post) = 'object')
);
CREATE INDEX IF NOT EXISTS tabo_autoseo_posts_published_idx
    ON public.tabo_autoseo_posts (published_at DESC, id DESC);

-- RLS/grants below apply exclusively to the two NEW AutoSEO tables.
ALTER TABLE public.tabo_autoseo_media ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tabo_autoseo_posts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.tabo_autoseo_media, public.tabo_autoseo_posts FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT, INSERT ON public.tabo_autoseo_media TO service_role;
GRANT SELECT, INSERT, UPDATE ON public.tabo_autoseo_posts TO service_role;

CREATE FUNCTION public.tabo_autoseo_publish(p_post jsonb, p_media jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = ''
AS $$
DECLARE
    asset jsonb;
    final_slug text;
    proposed_slug text := p_post->>'slug';
    attempt integer;
    article_id bigint := (p_post->>'id')::bigint;
BEGIN
    IF jsonb_typeof(p_media) <> 'array' OR jsonb_array_length(p_media) > 2 THEN
        RAISE EXCEPTION 'Invalid media';
    END IF;
    -- Media and the published post commit atomically. Lost responses can be retried.
    FOR asset IN SELECT value FROM jsonb_array_elements(p_media) LOOP
        INSERT INTO public.tabo_autoseo_media(digest, content_base64)
        VALUES (asset->>'digest', asset->>'content_base64')
        ON CONFLICT (digest) DO NOTHING;
    END LOOP;
    FOR attempt IN 0..3 LOOP
        BEGIN
            INSERT INTO public.tabo_autoseo_posts AS existing
                (id, slug, title, description, language, published_at, updated_at, post)
            VALUES (article_id, proposed_slug, p_post->>'title', p_post->>'description',
                    p_post->>'language', (p_post->>'published_at')::timestamptz,
                    (p_post->>'updated_at')::timestamptz, p_post)
            ON CONFLICT (id) DO UPDATE SET
                title = EXCLUDED.title, description = EXCLUDED.description,
                language = EXCLUDED.language, updated_at = EXCLUDED.updated_at, post = EXCLUDED.post
            WHERE EXCLUDED.updated_at >= existing.updated_at
            RETURNING slug INTO final_slug;
            EXIT;
        EXCEPTION WHEN unique_violation THEN
            IF attempt = 3 THEN RAISE; END IF;
            -- Keep both articles if upstream reuses a slug. URL stays stable after insertion.
            proposed_slug := (p_post->>'slug') || '-' || article_id::text;
            IF attempt > 0 THEN
                proposed_slug := proposed_slug || '-' || substr(md5(random()::text),1,8);
            END IF;
        END;
    END LOOP;
    IF final_slug IS NULL THEN
        SELECT slug INTO final_slug FROM public.tabo_autoseo_posts WHERE id = article_id;
    END IF;
    RETURN jsonb_build_object('slug', final_slug);
END;
$$;
REVOKE ALL ON FUNCTION public.tabo_autoseo_publish(jsonb,jsonb) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.tabo_autoseo_publish(jsonb,jsonb) TO service_role;
