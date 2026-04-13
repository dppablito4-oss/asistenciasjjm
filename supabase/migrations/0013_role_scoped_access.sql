-- Role-scoped access: teacher ownership + student self-visibility + strong RLS

create table if not exists public.docente_estudiante_asignaciones (
  docente_user_id uuid not null references auth.users(id) on delete cascade,
  estudiante_id uuid not null references public.estudiantes(id) on delete cascade,
  activo boolean not null default true,
  created_at timestamptz not null default now(),
  primary key (docente_user_id, estudiante_id)
);

alter table public.docente_estudiante_asignaciones enable row level security;

-- Only admin_tic can list/manage assignments from SQL/API.
drop policy if exists dea_select_admin_only on public.docente_estudiante_asignaciones;
create policy dea_select_admin_only
on public.docente_estudiante_asignaciones
for select
to authenticated
using (
  exists (
    select 1
    from public.user_profiles p
    where p.user_id = auth.uid() and p.role = 'admin_tic' and p.is_active = true
  )
);

drop policy if exists dea_mutation_admin_only on public.docente_estudiante_asignaciones;
create policy dea_mutation_admin_only
on public.docente_estudiante_asignaciones
for all
to authenticated
using (
  exists (
    select 1
    from public.user_profiles p
    where p.user_id = auth.uid() and p.role = 'admin_tic' and p.is_active = true
  )
)
with check (
  exists (
    select 1
    from public.user_profiles p
    where p.user_id = auth.uid() and p.role = 'admin_tic' and p.is_active = true
  )
);

create or replace function public.current_user_role()
returns text
language sql
stable
security definer
set search_path = pg_catalog, public, auth
as $$
  select p.role
  from public.user_profiles p
  where p.user_id = auth.uid() and p.is_active = true
  limit 1;
$$;

create or replace function public.current_user_estudiante_id()
returns uuid
language sql
stable
security definer
set search_path = pg_catalog, public, auth
as $$
  select e.id
  from public.user_profiles p
  join public.estudiantes e on e.dni = p.dni
  where p.user_id = auth.uid()
    and p.role = 'alumno'
    and p.is_active = true
    and e.activo = true
  limit 1;
$$;

create or replace function public.is_docente_of_estudiante(p_estudiante_id uuid)
returns boolean
language sql
stable
security definer
set search_path = pg_catalog, public, auth
as $$
  select exists (
    select 1
    from public.docente_estudiante_asignaciones a
    join public.user_profiles p on p.user_id = a.docente_user_id
    where a.docente_user_id = auth.uid()
      and a.estudiante_id = p_estudiante_id
      and a.activo = true
      and p.role = 'docente'
      and p.is_active = true
  );
$$;

create or replace function public.can_access_estudiante(p_estudiante_id uuid)
returns boolean
language plpgsql
stable
security definer
set search_path = pg_catalog, public, auth
as $$
declare
  v_role text;
  v_self_estudiante uuid;
begin
  v_role := public.current_user_role();

  if v_role = 'admin_tic' then
    return true;
  end if;

  if v_role = 'docente' then
    return public.is_docente_of_estudiante(p_estudiante_id);
  end if;

  if v_role = 'alumno' then
    v_self_estudiante := public.current_user_estudiante_id();
    return v_self_estudiante is not null and v_self_estudiante = p_estudiante_id;
  end if;

  return false;
end;
$$;

-- Harden estudiantes / asistencia table reads by role.
drop policy if exists authenticated_select_estudiantes on public.estudiantes;
create policy role_select_estudiantes
on public.estudiantes
for select
to authenticated
using (public.can_access_estudiante(id));

drop policy if exists authenticated_select_asistencia on public.asistencia;
create policy role_select_asistencia
on public.asistencia
for select
to authenticated
using (public.can_access_estudiante(estudiante_id));

-- No direct insert into asistencia from clients.
drop policy if exists authenticated_insert_asistencia on public.asistencia;

-- Mark attendance with role-aware access checks.
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
set search_path = public, auth
as $$
declare
  v_identifier text;
  v_teacher text;
  v_student public.estudiantes%rowtype;
  v_fecha date := current_date;
  v_hora time := localtime(0);
  v_estado text;
  v_cutoff time;
  v_role text;
begin
  v_role := public.current_user_role();
  if v_role not in ('admin_tic', 'docente') then
    return query select false, 'No tienes permisos para registrar asistencia.', null::text, null::text, null::text, null::text, null::date, null::time;
    return;
  end if;

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

  if v_role = 'docente' and not public.is_docente_of_estudiante(v_student.id) then
    return query
      select false, 'No tienes asignado a este estudiante.', null::text, v_student.dni, v_student.nombres, v_student.apellidos, v_fecha, v_hora;
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

-- Restrict report rows by role inside RPC.
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
set search_path = public, auth
as $$
with role_ctx as (
  select public.current_user_role() as role, public.current_user_estudiante_id() as self_estudiante_id
),
students as (
  select e.id, e.dni, e.nombres, e.apellidos, e.grado, e.seccion, e.genero, e.cargo
  from public.estudiantes e
  cross join role_ctx r
  where e.activo = true
    and (p_grado is null or e.grado = p_grado)
    and (p_seccion is null or upper(e.seccion) = upper(trim(p_seccion)))
    and (p_genero is null or upper(e.genero) = upper(trim(p_genero)))
    and (p_cargo is null or e.cargo = p_cargo)
    and (
      r.role = 'admin_tic'
      or (r.role = 'docente' and exists (
        select 1 from public.docente_estudiante_asignaciones a
        where a.docente_user_id = auth.uid() and a.estudiante_id = e.id and a.activo = true
      ))
      or (r.role = 'alumno' and r.self_estudiante_id = e.id)
    )
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

-- Admin-only helpers for assignment management.
create or replace function public.assign_docente_estudiantes(
  p_docente_user_id uuid,
  p_estudiante_ids uuid[]
)
returns void
language plpgsql
security definer
set search_path = public, auth
as $$
declare
  v_role text;
begin
  v_role := public.current_user_role();
  if v_role <> 'admin_tic' then
    raise exception 'Solo admin TIC puede asignar docentes';
  end if;

  if p_docente_user_id is null then
    raise exception 'Docente invalido';
  end if;

  update public.docente_estudiante_asignaciones
  set activo = false
  where docente_user_id = p_docente_user_id;

  if p_estudiante_ids is null or array_length(p_estudiante_ids, 1) is null then
    return;
  end if;

  insert into public.docente_estudiante_asignaciones(docente_user_id, estudiante_id, activo)
  select p_docente_user_id, x, true
  from unnest(p_estudiante_ids) x
  on conflict (docente_user_id, estudiante_id)
  do update set activo = true;
end;
$$;

grant execute on function public.current_user_role() to authenticated;
grant execute on function public.current_user_estudiante_id() to authenticated;
grant execute on function public.can_access_estudiante(uuid) to authenticated;
grant execute on function public.is_docente_of_estudiante(uuid) to authenticated;
grant execute on function public.assign_docente_estudiantes(uuid, uuid[]) to authenticated;
