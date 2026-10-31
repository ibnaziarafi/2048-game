-- Restrict API execution of the existing Supabase RLS event-trigger helper.
-- Preserve the helper and its event trigger; postgres remains its owner.
DO $guard$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public' AND p.proname = 'rls_auto_enable'
      AND p.pronargs = 0 AND p.prorettype = 'event_trigger'::regtype
      AND pg_get_userbyid(p.proowner) = 'postgres'
  ) THEN
    REVOKE EXECUTE ON FUNCTION public.rls_auto_enable()
      FROM PUBLIC, anon, authenticated;
  END IF;
END;
$guard$;
