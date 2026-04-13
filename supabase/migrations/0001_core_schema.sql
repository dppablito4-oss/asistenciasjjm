-- Core schema for web attendance flow (Supabase / PostgreSQL)

create extension if not exists pgcrypto;

create table if not exists public.app_settings (
  key text primary key,
  value text not null
);

insert into public.app_settings(key, value)
values ('entry_time', '08:00')
on conflict (key) do nothing;

insert into public.app_settings(key, value)
values ('tolerance_min', '10')
on conflict (key) do nothing;

create table if not exists public.estudiantes (
  id uuid primary key default gen_random_uuid(),
  dni text not null unique,
  nombres text not null,
  apellidos text not null,
  grado integer not null check (grado between 1 and 5),
  seccion text not null,
  genero text not null check (genero in ('M', 'F')),
  cargo text not null default 'Alumno',
  qr_token text not null unique,
  activo boolean not null default true,
  created_at timestamptz not null default now()
);

create table if not exists public.asistencia (
  id bigserial primary key,
  estudiante_id uuid not null references public.estudiantes(id) on delete cascade,
  fecha date not null,
  hora time not null,
  profesor_encargado text not null,
  estado text not null check (estado in ('Asistio', 'Tardanza')),
  created_at timestamptz not null default now(),
  constraint uq_asistencia_estudiante_fecha unique (estudiante_id, fecha)
);

create index if not exists idx_asistencia_fecha on public.asistencia(fecha);
create index if not exists idx_asistencia_estudiante_fecha on public.asistencia(estudiante_id, fecha);

create or replace function public.get_attendance_cutoff()
returns time
language plpgsql
stable
as $$
declare
  v_entry text;
  v_tol text;
  v_h int;
  v_m int;
  v_total int;
begin
  select value into v_entry from public.app_settings where key = 'entry_time';
  select value into v_tol from public.app_settings where key = 'tolerance_min';

  if v_entry is null then
    v_entry := '08:00';
  end if;
  if v_tol is null then
    v_tol := '10';
  end if;

  v_h := split_part(v_entry, ':', 1)::int;
  v_m := split_part(v_entry, ':', 2)::int;
  v_total := (v_h * 60) + v_m + greatest(0, v_tol::int);

  return make_time((v_total / 60) % 24, v_total % 60, 0);
exception
  when others then
    return time '08:10';
end;
$$;

create or replace function public.mark_attendance(
  p_identifier text,
  p_teacher text
)
returns table (
  ok boolean,
  message text,
  estado text,
  dni text,
  nombres text,
  apellidos text,
  fecha date,
  hora time
)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_identifier text;
  v_teacher text;
  v_student public.estudiantes%rowtype;
  v_fecha date := current_date;
  v_hora time := localtime(0);
  v_estado text;
begin
  v_identifier := trim(coalesce(p_identifier, ''));
  v_teacher := trim(coalesce(p_teacher, ''));

  if v_identifier = '' then
    return query select false, 'Codigo no autorizado.', null::text, null::text, null::text, null::text, null::date, null::time;
    return;
  end if;

  if v_teacher = '' then
    return query select false, 'Ingrese profesor encargado.', null::text, null::text, null::text, null::text, null::date, null::time;
    return;
  end if;

  select *
  into v_student
  from public.estudiantes e
  where e.activo = true
    and (e.qr_token = v_identifier or e.dni = regexp_replace(v_identifier, '[^0-9]', '', 'g'))
  limit 1;

  if not found then
    return query select false, 'Codigo no autorizado.', null::text, null::text, null::text, null::text, null::date, null::time;
    return;
  end if;

  v_estado := case when v_hora > public.get_attendance_cutoff() then 'Tardanza' else 'Asistio' end;

  insert into public.asistencia(estudiante_id, fecha, hora, profesor_encargado, estado)
  values (v_student.id, v_fecha, v_hora, v_teacher, v_estado)
  on conflict do nothing;

  if not found then
    return query
      select
        false,
        'Asistencia duplicada: el estudiante ya marco hoy.',
        null::text,
        v_student.dni,
        v_student.nombres,
        v_student.apellidos,
        v_fecha,
        v_hora;
    return;
  end if;

  return query
    select
      true,
      format('Registro exitoso: %s %s (%s).', v_student.nombres, v_student.apellidos, v_estado),
      v_estado,
      v_student.dni,
      v_student.nombres,
      v_student.apellidos,
      v_fecha,
      v_hora;
end;
$$;

create or replace view public.v_today_attendance as
select
  a.fecha,
  a.hora,
  e.dni,
  e.nombres,
  e.apellidos,
  e.grado,
  e.seccion,
  a.estado,
  a.profesor_encargado
from public.asistencia a
join public.estudiantes e on e.id = a.estudiante_id
where a.fecha = current_date
order by a.hora desc;

alter table public.app_settings enable row level security;
alter table public.estudiantes enable row level security;
alter table public.asistencia enable row level security;

drop policy if exists authenticated_select_settings on public.app_settings;
create policy authenticated_select_settings
on public.app_settings
for select
to authenticated
using (true);

drop policy if exists authenticated_select_estudiantes on public.estudiantes;
create policy authenticated_select_estudiantes
on public.estudiantes
for select
to authenticated
using (true);

drop policy if exists authenticated_select_asistencia on public.asistencia;
create policy authenticated_select_asistencia
on public.asistencia
for select
to authenticated
using (true);

drop policy if exists authenticated_insert_asistencia on public.asistencia;
create policy authenticated_insert_asistencia
on public.asistencia
for insert
to authenticated
with check (true);

grant usage on schema public to anon, authenticated;
grant select on public.v_today_attendance to anon, authenticated;
grant execute on function public.mark_attendance(text, text) to anon, authenticated;
