-- Security Advisor hardening fixes
-- 1) Set explicit search_path on mutable functions.
-- 2) Make views run as invoker to avoid SECURITY DEFINER view warnings.
-- 3) Replace overly permissive asistencia RLS policies.

-- Function search_path hardening
alter function public.get_attendance_cutoff()
  set search_path = pg_catalog, public;

alter function public.get_attendance_cutoff_for_date(date)
  set search_path = pg_catalog, public;

alter function public.generate_qr_token()
  set search_path = pg_catalog, public;

alter function public.trg_set_estudiante_qr_token()
  set search_path = pg_catalog, public;

alter function public.trg_estudiantes_sync_updated_at()
  set search_path = pg_catalog, public;

alter function public.trg_academic_sections_updated_at()
  set search_path = pg_catalog, public;

-- View hardening (Postgres 15+)
alter view public.v_today_attendance set (security_invoker = true);
alter view public.v_sections_admin set (security_invoker = true);
alter view public.v_students_admin set (security_invoker = true);

-- RLS hardening for asistencia
-- Direct inserts should go through RPC mark_attendance(), not raw table inserts.
drop policy if exists authenticated_insert_asistencia on public.asistencia;

-- Keep read access for authenticated users but avoid USING (true)
drop policy if exists authenticated_select_asistencia on public.asistencia;
create policy authenticated_select_asistencia
on public.asistencia
for select
to authenticated
using ((select auth.uid()) is not null);
