-- Sections catalog management and student validation against active sections

create table if not exists public.academic_sections (
  id bigserial primary key,
  grado int not null check (grado between 1 and 5),
  seccion text not null,
  activo boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (grado, seccion)
);

create or replace function public.trg_academic_sections_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

drop trigger if exists before_update_academic_sections_updated_at on public.academic_sections;
create trigger before_update_academic_sections_updated_at
before update on public.academic_sections
for each row
execute function public.trg_academic_sections_updated_at();

insert into public.academic_sections (grado, seccion, activo)
select g, s, true
from generate_series(1, 5) g
cross join (values ('A'), ('B'), ('C')) as sec(s)
on conflict (grado, seccion) do nothing;

alter table public.academic_sections enable row level security;

drop policy if exists authenticated_select_sections on public.academic_sections;
create policy authenticated_select_sections
on public.academic_sections
for select
 to authenticated
using (true);

create or replace function public.upsert_section_admin(
  p_grado int,
  p_seccion text,
  p_activo boolean default true
)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_grado int;
  v_seccion text;
begin
  v_grado := p_grado;
  v_seccion := upper(trim(coalesce(p_seccion, '')));

  if v_grado < 1 or v_grado > 5 then
    raise exception 'Grado invalido, use 1..5';
  end if;
  if v_seccion = '' then
    raise exception 'Seccion obligatoria';
  end if;

  insert into public.academic_sections (grado, seccion, activo)
  values (v_grado, v_seccion, coalesce(p_activo, true))
  on conflict (grado, seccion)
  do update set activo = excluded.activo;
end;
$$;

create or replace function public.set_section_active(
  p_grado int,
  p_seccion text,
  p_activo boolean
)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_rows int;
  v_seccion text;
begin
  v_seccion := upper(trim(coalesce(p_seccion, '')));
  update public.academic_sections
  set activo = coalesce(p_activo, true)
  where grado = p_grado and seccion = v_seccion;

  get diagnostics v_rows = row_count;
  if v_rows = 0 then
    raise exception 'Seccion no encontrada';
  end if;
end;
$$;

create or replace view public.v_sections_admin as
select id, grado, seccion, activo, created_at, updated_at
from public.academic_sections
order by grado, seccion;

grant select on public.v_sections_admin to authenticated;
grant execute on function public.upsert_section_admin(int, text, boolean) to authenticated;
grant execute on function public.set_section_active(int, text, boolean) to authenticated;

create or replace function public.upsert_student_admin(
  p_id uuid,
  p_dni text,
  p_nombres text,
  p_apellidos text,
  p_grado int,
  p_seccion text,
  p_genero text,
  p_cargo text,
  p_birth_date date,
  p_status text,
  p_status_note text
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_id uuid;
  v_dni text;
  v_nombres text;
  v_apellidos text;
  v_grado int;
  v_seccion text;
  v_genero text;
  v_cargo text;
  v_status text;
  v_note text;
  v_activo boolean;
  v_section_exists boolean;
begin
  v_dni := regexp_replace(coalesce(p_dni, ''), '[^0-9]', '', 'g');
  v_nombres := trim(coalesce(p_nombres, ''));
  v_apellidos := trim(coalesce(p_apellidos, ''));
  v_grado := p_grado;
  v_seccion := upper(trim(coalesce(p_seccion, '')));
  v_genero := upper(trim(coalesce(p_genero, '')));
  v_cargo := trim(coalesce(p_cargo, 'Alumno'));
  v_status := upper(trim(coalesce(p_status, 'ACTIVO')));
  v_note := trim(coalesce(p_status_note, ''));

  if length(v_dni) <> 8 then
    raise exception 'DNI invalido, debe tener 8 digitos';
  end if;
  if v_nombres = '' or v_apellidos = '' then
    raise exception 'Nombres y apellidos son obligatorios';
  end if;
  if v_grado < 1 or v_grado > 5 then
    raise exception 'Grado invalido, use 1..5';
  end if;
  if v_seccion = '' then
    raise exception 'Seccion obligatoria';
  end if;
  if v_genero not in ('M', 'F') then
    raise exception 'Genero invalido, use M/F';
  end if;
  if v_status not in ('ACTIVO', 'RETIRADO', 'TRASLADADO') then
    raise exception 'Estado invalido';
  end if;

  select exists (
    select 1
    from public.academic_sections s
    where s.grado = v_grado and s.seccion = v_seccion and s.activo = true
  ) into v_section_exists;

  if not v_section_exists then
    raise exception 'Seccion no activa o no registrada en catalogo';
  end if;

  v_activo := (v_status = 'ACTIVO');

  if p_id is null then
    insert into public.estudiantes(
      dni, nombres, apellidos, grado, seccion, genero, cargo,
      birth_date, status, status_note, activo
    ) values (
      v_dni, v_nombres, v_apellidos, v_grado, v_seccion, v_genero, v_cargo,
      p_birth_date, v_status, v_note, v_activo
    )
    returning id into v_id;
  else
    update public.estudiantes
    set dni = v_dni,
        nombres = v_nombres,
        apellidos = v_apellidos,
        grado = v_grado,
        seccion = v_seccion,
        genero = v_genero,
        cargo = v_cargo,
        birth_date = p_birth_date,
        status = v_status,
        status_note = v_note,
        activo = v_activo
    where id = p_id
    returning id into v_id;

    if v_id is null then
      raise exception 'Estudiante no encontrado';
    end if;
  end if;

  return v_id;
end;
$$;

grant execute on function public.upsert_student_admin(uuid, text, text, text, int, text, text, text, date, text, text) to authenticated;
