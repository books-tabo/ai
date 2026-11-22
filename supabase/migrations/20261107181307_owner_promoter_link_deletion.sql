-- Remove links from circulation while retaining their order references.
SET LOCAL lock_timeout = '3s';

ALTER TABLE public.owner_promoter_links
    ADD COLUMN deleted_at timestamptz,
    ADD CONSTRAINT owner_promoter_links_deleted_inactive CHECK (deleted_at IS NULL OR NOT active);

CREATE UNIQUE INDEX owner_promoter_links_current_destination
    ON public.owner_promoter_links (name, destination) WHERE deleted_at IS NULL;
ALTER TABLE public.owner_promoter_links DROP CONSTRAINT owner_promoter_links_name_destination_key;

CREATE OR REPLACE FUNCTION public.owner_promoter_create(p_code text, p_name text, p_destination text)
RETURNS text LANGUAGE sql SECURITY INVOKER SET search_path = '' AS $$
    INSERT INTO public.owner_promoter_links (code, name, destination)
    VALUES (p_code, p_name, p_destination)
    ON CONFLICT (name, destination) WHERE deleted_at IS NULL DO UPDATE SET name = EXCLUDED.name
    RETURNING code;
$$;

CREATE OR REPLACE FUNCTION public.owner_promoter_open(p_code text)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $$
DECLARE target text;
BEGIN
    UPDATE public.owner_promoter_links SET opens = opens + 1
    WHERE code = p_code AND active AND deleted_at IS NULL
    RETURNING destination INTO target;
    IF FOUND THEN
        RETURN jsonb_build_object('destination', target, 'active', true);
    END IF;
    SELECT destination INTO target FROM public.owner_promoter_links
    WHERE code = p_code AND NOT active AND deleted_at IS NULL;
    IF FOUND THEN
        RETURN jsonb_build_object('destination', target, 'active', false);
    END IF;
    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION public.owner_promoter_report(p_offset integer)
RETURNS jsonb LANGUAGE sql STABLE SECURITY INVOKER SET search_path = '' AS $$
    WITH page AS MATERIALIZED (
        SELECT code, name, destination, active, opens, created_at
        FROM public.owner_promoter_links
        WHERE deleted_at IS NULL
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
