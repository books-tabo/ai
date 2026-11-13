-- Supabase-only scheduler. Stock PostgreSQL CI lacks these hosted extensions.
-- The scheduled request is a public health GET with no customer data or secrets.
-- It wakes an idle web service only when consented reminder work is due; the
-- existing durable worker performs all payment, consent and opt-out checks.
DO $scheduler$
BEGIN
 IF EXISTS(SELECT 1 FROM pg_available_extensions WHERE name='pg_cron')
 AND EXISTS(SELECT 1 FROM pg_available_extensions WHERE name='pg_net') THEN
  EXECUTE 'CREATE EXTENSION IF NOT EXISTS pg_cron';
  EXECUTE 'CREATE EXTENSION IF NOT EXISTS pg_net WITH SCHEMA extensions';
  EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA net FROM PUBLIC,anon,authenticated';
  EXECUTE 'REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA net FROM PUBLIC,anon,authenticated';
  PERFORM cron.schedule('tabo-checkout-reminder-wake','*/5 * * * *',$job$
   SELECT net.http_get(
    url:='https://tabobooks.com/health',
    headers:='{"User-Agent":"TABO-Books-Reminder-Scheduler/1"}'::jsonb,
    timeout_milliseconds:=60000)
   WHERE (SELECT reminders_enabled FROM public.store_catalog_state WHERE id)
   AND (
    EXISTS(SELECT 1 FROM public.store_checkout_reminders
     WHERE state IN('pending','retry','sending') AND next_attempt_at<=now())
    OR EXISTS(SELECT 1 FROM public.orders o
     WHERE o.status='PENDING' AND o.payment_mode='live'
     AND o.buyer->'recoveryConsent'='true'::jsonb
     AND o.created_at BETWEEN now()-interval '20 hours' AND now()-interval '1 hour'
     AND NOT EXISTS(SELECT 1 FROM public.store_checkout_reminders r WHERE r.order_id=o.id
      OR (r.email_hash=encode(sha256(convert_to(lower(btrim(o.buyer->>'email')),'UTF8')),'hex') AND r.created_at>now()-interval '1 day'))
     AND NOT EXISTS(SELECT 1 FROM public.store_reminder_optouts x
      WHERE x.email_hash=encode(sha256(convert_to(lower(btrim(o.buyer->>'email')),'UTF8')),'hex')))
   );
  $job$);
 END IF;
END;
$scheduler$;
