-- Additive only; existing orders keep their amount/currency and a NULL quote.
ALTER TABLE public.orders ADD COLUMN pricing_quote jsonb;
ALTER TABLE public.orders ADD CONSTRAINT orders_pricing_quote_matches_charge CHECK (
    pricing_quote IS NULL OR ((
        jsonb_typeof(pricing_quote) = 'object'
        AND pricing_quote ?& ARRAY['displayCurrency','displayTotal','chargeCurrency','chargeTotal',
                                   'rate','rateDate','quotedAt','expiresAt']
        AND pricing_quote->>'displayCurrency' = 'USD'
        AND pricing_quote->>'chargeCurrency' = 'EGP'
        AND currency = 'EGP'
        AND pricing_quote->>'chargeTotal' IS NOT NULL
        AND pricing_quote->>'displayTotal' IS NOT NULL
        AND pricing_quote->>'rate' IS NOT NULL
        AND (pricing_quote->>'chargeTotal')::numeric = total
        AND (pricing_quote->>'displayTotal')::numeric > 0
        AND (pricing_quote->>'rate')::numeric > 0
    ) IS TRUE)
);
-- Existing server-only orders grants/RLS cover this column; no new privileges.
NOTIFY pgrst, 'reload schema';
