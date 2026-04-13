-- QR automation and attendance reporting filters

create extension if not exists pgcrypto;

create or replace function public.generate_qr_token()
returns text
language sql
volatile
as $$
  select rtrim(translate(encode(gen_random_bytes(18), 'base64'), '+/', '-_'), '=');
$$;

create or replace function public.trg_set_estudiante_qr_token()
returns trigger
language plpgsql
as $$
begin
  if coalesce(trim(new.qr_token), '') = '' then
    new.qr_token := public.generate_qr_token();
  end if;
  return new;
end;
$$;

drop trigger if exists before_insert_estudiantes_qr_token on public.estudiantes;
create trigger before_insert_estudiantes_qr_token
before insert on public.estudiantes
for each row
execute function public.trg_set_estudiante_qr_token();

create or replace function public.set_attendance_schedule(
  p_entry_time text,
  p_tolerance_min int
)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_entry text;
  v_h int;
  v_m int;
  v_tol int;
begin
  v_entry := trim(coalesce(p_entry_time, '08:00'));
  v_h := split_part(v_entry, ':', 1)::int;
  v_m := split_part(v_entry, ':', 2)::int;

  if v_h < 0 or v_h > 23 or v_m < 0 or v_m > 59 then
    raise exception 'Hora invalida. Use formato HH:MM';
  end if;

  v_tol := greatest(0, coalesce(p_tolerance_min, 10));

  insert into public.app_settings(key, value)
  values ('entry_time', lpad(v_h::text, 2, '0') || ':' || lpad(v_m::text, 2, '0'))
  on conflict (key) do update set value = excluded.value;

  insert into public.app_settings(key, value)
  values ('tolerance_min', v_tol::text)
  on conflict (key) do update set value = excluded.value;
end;
$$;

create or replace function public.report_attendance_filtered(
  p_start_date date,
  p_end_date date,
  p_grado int default null,
  p_seccion text default null,
  p_genero text default null,
  p_cargo text default null,
  p_condition text default 'all'
)
returns table (
  fecha date,
  hora time,
  dni text,
  nombres text,
  apellidos text,
  grado int,
  seccion text,
  genero text,
  cargo text,
  profesor_encargado text,
  estado text
)
language sql
security definer
set search_path = public
as $$
with students as (
  select e.id, e.dni, e.nombres, e.apellidos, e.grado, e.seccion, e.genero, e.cargo
  from public.estudiantes e
  where e.activo = true
    and (p_grado is null or e.grado = p_grado)
    and (p_seccion is null or upper(e.seccion) = upper(trim(p_seccion)))
    and (p_genero is null or upper(e.genero) = upper(trim(p_genero)))
    and (p_cargo is null or e.cargo = p_cargo)
),
present as (
  select
    a.fecha,
    a.hora,
    s.dni,
    s.nombres,
    s.apellidos,
    s.grado,
    s.seccion,
    s.genero,
    s.cargo,
    a.profesor_encargado,
    a.estado
  from public.asistencia a
  join students s on s.id = a.estudiante_id
  where a.fecha between p_start_date and p_end_date
),
days as (
  select d::date as fecha
  from generate_series(p_start_date, p_end_date, interval '1 day') d
),
expected as (
  select
    d.fecha,
    null::time as hora,
    s.dni,
    s.nombres,
    s.apellidos,
    s.grado,
    s.seccion,
    s.genero,
    s.cargo,
    ''::text as profesor_encargado,
    'Falto'::text as estado
  from students s
  cross join days d
),
absent as (
  select e.*
  from expected e
  left join present p on p.dni = e.dni and p.fecha = e.fecha
  where p.dni is null
),
all_rows as (
  select * from present
  union all
  select * from absent
)
select
  r.fecha,
  coalesce(r.hora, time '00:00:00') as hora,
  r.dni,
  r.nombres,
  r.apellidos,
  r.grado,
  r.seccion,
  r.genero,
  r.cargo,
  r.profesor_encargado,
  r.estado
from all_rows r
where case
  when lower(coalesce(p_condition, 'all')) in ('present', 'asistieron') then r.estado in ('Asistio', 'Tardanza')
  when lower(coalesce(p_condition, 'all')) in ('absent', 'faltaron') then r.estado = 'Falto'
  else true
end
order by r.fecha, r.grado, r.seccion, r.apellidos, r.nombres, r.hora;
$$;

grant execute on function public.set_attendance_schedule(text, int) to authenticated;
grant execute on function public.report_attendance_filtered(date, date, int, text, text, text, text) to authenticated;
