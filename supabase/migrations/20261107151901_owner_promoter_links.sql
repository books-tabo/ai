-- Owner-managed destinations and aggregate attribution for confirmed orders.
SET LOCAL lock_timeout = '3s';

CREATE TABLE public.owner_promoter_links (
    code text PRIMARY KEY CHECK (code ~ '^[a-f0-9]{16}$'),
    name text NOT NULL CHECK (char_length(btrim(name)) BETWEEN 1 AND 80),
    destination text NOT NULL CHECK (destination ~ '^/(books/book[1-9][0-9]*|offers(/[a-z0-9-]{1,80})?)?$'),
    active boolean NOT NULL DEFAULT true,
    opens bigint NOT NULL DEFAULT 0 CHECK (opens >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (name, destination)
);
CREATE INDEX owner_promoter_links_recent ON public.owner_promoter_links (created_at DESC, code DESC);
ALTER TABLE public.owner_promoter_links ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.owner_promoter_links FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON public.owner_promoter_links TO service_role;

ALTER TABLE public.orders ADD COLUMN promoter_code text REFERENCES public.owner_promoter_links (code);
CREATE INDEX orders_promoter_code ON public.orders (promoter_code) WHERE promoter_code IS NOT NULL;

CREATE FUNCTION public.owner_promoter_create(p_code text, p_name text, p_destination text)
RETURNS text LANGUAGE sql SECURITY INVOKER SET search_path = '' AS $$
    INSERT INTO public.owner_promoter_links (code, name, destination)
    VALUES (p_code, p_name, p_destination)
    ON CONFLICT (name, destination) DO UPDATE SET name = EXCLUDED.name
    RETURNING code;
$$;

CREATE FUNCTION public.owner_promoter_open(p_code text)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $$
DECLARE target text;
BEGIN
    UPDATE public.owner_promoter_links SET opens = opens + 1
    WHERE code = p_code AND active
    RETURNING destination INTO target;
    IF FOUND THEN
        RETURN jsonb_build_object('destination', target, 'active', true);
    END IF;
    SELECT destination INTO target FROM public.owner_promoter_links WHERE code = p_code AND NOT active;
    IF FOUND THEN
        RETURN jsonb_build_object('destination', target, 'active', false);
    END IF;
    RETURN NULL;
END;
$$;

CREATE FUNCTION public.owner_promoter_report(p_offset integer)
RETURNS jsonb LANGUAGE sql STABLE SECURITY INVOKER SET search_path = '' AS $$
    WITH page AS MATERIALIZED (
        SELECT code, name, destination, active, opens, created_at
        FROM public.owner_promoter_links
        ORDER BY created_at DESC, code DESC
        LIMIT 21 OFFSET greatest(0, least(p_offset, 10000))
    ), currency_totals AS (
        SELECT o.promoter_code, o.currency, count(*) AS orders, sum(o.total) AS amount
        FROM page p JOIN public.orders o ON o.promoter_code = p.code
        WHERE o.status = 'PAID' AND o.payment_mode = 'live'
            AND o.transaction_id IS NOT NULL AND o.paid_at IS NOT NULL
        GROUP BY o.promoter_code, o.currency
    ), totals AS (
        SELECT promoter_code, sum(orders) AS paid_orders,
            jsonb_agg(jsonb_build_object('currency', currency, 'amount', amount) ORDER BY currency) AS sales
        FROM currency_totals GROUP BY promoter_code
    )
    SELECT coalesce(jsonb_agg(to_jsonb(p) || jsonb_build_object(
        'paidOrders', coalesce(t.paid_orders, 0), 'sales', coalesce(t.sales, '[]'::jsonb)
    ) ORDER BY p.created_at DESC, p.code DESC), '[]'::jsonb)
    FROM page p LEFT JOIN totals t ON t.promoter_code = p.code;
$$;

REVOKE ALL ON FUNCTION public.owner_promoter_create(text, text, text),
    public.owner_promoter_open(text), public.owner_promoter_report(integer) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.owner_promoter_create(text, text, text),
    public.owner_promoter_open(text), public.owner_promoter_report(integer) TO service_role;
