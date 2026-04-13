-- Daily schedule overrides for special institution events

create table if not exists public.attendance_schedule_overrides (
  day date primary key,
  entry_time time not null,
  tolerance_min int not null check (tolerance_min >= 0),
  reason text not null default '',
  created_at timestamptz not null default now()
);

alter table public.attendance_schedule_overrides enable row level security;

drop policy if exists authenticated_select_schedule_overrides on public.attendance_schedule_overrides;
create policy authenticated_select_schedule_overrides
on public.attendance_schedule_overrides
for select
to authenticated
using (true);

create or replace function public.get_attendance_cutoff_for_date(p_day date)
returns time
language plpgsql
stable
as $$
declare
  v_day date;
  v_entry time;
  v_tol int;
  v_default_entry text;
  v_default_tol text;
  v_h int;
  v_m int;
  v_total int;
begin
  v_day := coalesce(p_day, current_date);

  select o.entry_time, o.tolerance_min
  into v_entry, v_tol
  from public.attendance_schedule_overrides o
  where o.day = v_day;

  if found then
    v_total := (extract(hour from v_entry)::int * 60)
      + extract(minute from v_entry)::int
      + greatest(0, v_tol);
    return make_time((v_total / 60) % 24, v_total % 60, 0);
  end if;

  select value into v_default_entry from public.app_settings where key = 'entry_time';
  select value into v_default_tol from public.app_settings where key = 'tolerance_min';

  if v_default_entry is null then
    v_default_entry := '08:00';
  end if;
  if v_default_tol is null then
    v_default_tol := '10';
  end if;

  v_h := split_part(v_default_entry, ':', 1)::int;
  v_m := split_part(v_default_entry, ':', 2)::int;
  v_total := (v_h * 60) + v_m + greatest(0, v_default_tol::int);

  return make_time((v_total / 60) % 24, v_total % 60, 0);
exception
  when others then
    return time '08:10';
end;
$$;

create or replace function public.upsert_attendance_schedule_override(
  p_day date,
  p_entry_time text,
  p_tolerance_min int,
  p_reason text default ''
)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_day date;
  v_entry text;
  v_h int;
  v_m int;
  v_tol int;
  v_reason text;
begin
  v_day := coalesce(p_day, current_date);
  v_entry := trim(coalesce(p_entry_time, '08:00'));
  v_h := split_part(v_entry, ':', 1)::int;
  v_m := split_part(v_entry, ':', 2)::int;

  if v_h < 0 or v_h > 23 or v_m < 0 or v_m > 59 then
    raise exception 'Hora invalida. Use formato HH:MM';
  end if;

  v_tol := greatest(0, coalesce(p_tolerance_min, 0));
  v_reason := trim(coalesce(p_reason, ''));

  insert into public.attendance_schedule_overrides(day, entry_time, tolerance_min, reason)
  values (v_day, make_time(v_h, v_m, 0), v_tol, v_reason)
  on conflict (day)
  do update
     set entry_time = excluded.entry_time,
         tolerance_min = excluded.tolerance_min,
         reason = excluded.reason;
end;
$$;

create or replace function public.delete_attendance_schedule_override(p_day date)
returns void
language sql
security definer
set search_path = public
as $$
  delete from public.attendance_schedule_overrides
  where day = p_day;
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
  v_cutoff time;
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

  v_cutoff := public.get_attendance_cutoff_for_date(v_fecha);
  v_estado := case when v_hora > v_cutoff then 'Tardanza' else 'Asistio' end;

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

grant execute on function public.upsert_attendance_schedule_override(date, text, int, text) to authenticated;
grant execute on function public.delete_attendance_schedule_override(date) to authenticated;
