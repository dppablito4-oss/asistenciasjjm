-- Student admin management for web panel

alter table public.estudiantes
  add column if not exists birth_date date,
  add column if not exists status text not null default 'ACTIVO',
  add column if not exists status_note text not null default '',
  add column if not exists updated_at timestamptz not null default now();

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'ck_estudiantes_status'
  ) then
    alter table public.estudiantes
      add constraint ck_estudiantes_status check (status in ('ACTIVO', 'RETIRADO', 'TRASLADADO'));
  end if;
exception
  when duplicate_object then
    null;
end $$;

update public.estudiantes
set status = case when activo then 'ACTIVO' else 'RETIRADO' end
where status is null
   or status not in ('ACTIVO', 'RETIRADO', 'TRASLADADO');

create or replace function public.trg_estudiantes_sync_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

drop trigger if exists before_update_estudiantes_updated_at on public.estudiantes;
create trigger before_update_estudiantes_updated_at
before update on public.estudiantes
for each row
execute function public.trg_estudiantes_sync_updated_at();

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

create or replace function public.regenerate_student_qr_token(p_student_id uuid)
returns text
language plpgsql
security definer
set search_path = public
as $$
declare
  v_token text;
begin
  if p_student_id is null then
    raise exception 'ID de estudiante requerido';
  end if;

  loop
    v_token := public.generate_qr_token();
    begin
      update public.estudiantes
      set qr_token = v_token
      where id = p_student_id;

      if not found then
        raise exception 'Estudiante no encontrado';
      end if;
      exit;
    exception
      when unique_violation then
        -- Retry until token is unique.
        null;
    end;
  end loop;

  return v_token;
end;
$$;

create or replace view public.v_students_admin as
select
  e.id,
  e.dni,
  e.nombres,
  e.apellidos,
  e.grado,
  e.seccion,
  e.genero,
  e.cargo,
  e.birth_date,
  e.status,
  e.status_note,
  e.activo,
  e.qr_token,
  extract(year from age(current_date, e.birth_date))::int as edad,
  e.updated_at
from public.estudiantes e
order by e.apellidos, e.nombres;

grant select on public.v_students_admin to authenticated;
grant execute on function public.upsert_student_admin(uuid, text, text, text, int, text, text, text, date, text, text) to authenticated;
grant execute on function public.regenerate_student_qr_token(uuid) to authenticated;
