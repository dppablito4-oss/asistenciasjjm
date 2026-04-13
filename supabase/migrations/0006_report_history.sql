-- Report history compatible with desktop logic

create table if not exists public.report_history (
  id bigserial primary key,
  created_at timestamptz not null default now(),
  period text not null,
  condition text not null,
  ref_date date,
  start_date date,
  end_date date,
  grado text,
  seccion text,
  genero text,
  cargo text,
  row_count int not null default 0
);

create index if not exists idx_report_history_created_at on public.report_history(created_at desc);

alter table public.report_history enable row level security;

drop policy if exists authenticated_select_report_history on public.report_history;
create policy authenticated_select_report_history
on public.report_history
for select
to authenticated
using (true);

create or replace function public.save_report_history_web(
  p_period text,
  p_condition text,
  p_ref_date date,
  p_start_date date,
  p_end_date date,
  p_grado text,
  p_seccion text,
  p_genero text,
  p_cargo text,
  p_row_count int
)
returns bigint
language sql
security definer
set search_path = public
as $$
  insert into public.report_history(
    period, condition, ref_date, start_date, end_date,
    grado, seccion, genero, cargo, row_count
  ) values (
    coalesce(trim(p_period), ''),
    coalesce(trim(p_condition), ''),
    p_ref_date,
    p_start_date,
    p_end_date,
    nullif(trim(coalesce(p_grado, '')), ''),
    nullif(trim(coalesce(p_seccion, '')), ''),
    nullif(trim(coalesce(p_genero, '')), ''),
    nullif(trim(coalesce(p_cargo, '')), ''),
    greatest(0, coalesce(p_row_count, 0))
  )
  returning id;
$$;

create or replace function public.list_report_history_web(p_limit int default 100)
returns table (
  id bigint,
  created_at timestamptz,
  period text,
  condition text,
  ref_date date,
  start_date date,
  end_date date,
  grado text,
  seccion text,
  genero text,
  cargo text,
  row_count int
)
language sql
security definer
set search_path = public
as $$
  select
    rh.id,
    rh.created_at,
    rh.period,
    rh.condition,
    rh.ref_date,
    rh.start_date,
    rh.end_date,
    rh.grado,
    rh.seccion,
    rh.genero,
    rh.cargo,
    rh.row_count
  from public.report_history rh
  order by rh.id desc
  limit greatest(1, coalesce(p_limit, 100));
$$;

grant execute on function public.save_report_history_web(text, text, date, date, date, text, text, text, text, int) to authenticated;
grant execute on function public.list_report_history_web(int) to authenticated;
