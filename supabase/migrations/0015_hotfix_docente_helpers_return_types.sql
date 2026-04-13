-- Hotfix: align helper RPC return columns exactly with declared return types

create or replace function public.list_docentes_admin()
returns table (
  user_id uuid,
  dni text,
  display_name text,
  email text,
  is_active boolean
)
language plpgsql
security definer
set search_path = pg_catalog, public, auth
as $$
begin
  if public.current_user_role() <> 'admin_tic' then
    raise exception 'Solo admin TIC';
  end if;

  return query
  select
    p.user_id::uuid,
    p.dni::text,
    coalesce(nullif(trim(p.display_name), ''), 'DOCENTE ' || coalesce(p.dni, 'SIN DNI'))::text,
    coalesce(u.email, '')::text,
    p.is_active::boolean
  from public.user_profiles p
  join auth.users u on u.id = p.user_id
  where p.role = 'docente'
  order by p.is_active desc, p.display_name nulls last, p.dni nulls last;
end;
$$;

create or replace function public.list_assignable_students_admin()
returns table (
  estudiante_id uuid,
  dni text,
  nombres text,
  apellidos text,
  grado int,
  seccion text,
  status text,
  activo boolean
)
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
begin
  if public.current_user_role() <> 'admin_tic' then
    raise exception 'Solo admin TIC';
  end if;

  return query
  select
    e.id::uuid,
    e.dni::text,
    e.nombres::text,
    e.apellidos::text,
    e.grado::int,
    e.seccion::text,
    e.status::text,
    e.activo::boolean
  from public.estudiantes e
  order by e.activo desc, e.grado, e.seccion, e.apellidos, e.nombres;
end;
$$;

create or replace function public.list_docente_assignment_ids_admin(p_docente_user_id uuid)
returns table (estudiante_id uuid)
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
begin
  if public.current_user_role() <> 'admin_tic' then
    raise exception 'Solo admin TIC';
  end if;

  return query
  select a.estudiante_id::uuid
  from public.docente_estudiante_asignaciones a
  where a.docente_user_id = p_docente_user_id
    and a.activo = true;
end;
$$;

grant execute on function public.list_docentes_admin() to authenticated;
grant execute on function public.list_assignable_students_admin() to authenticated;
grant execute on function public.list_docente_assignment_ids_admin(uuid) to authenticated;
