-- Auth profiles and role model (single institution)
-- Roles: admin_tic, docente, alumno

create table if not exists public.user_profiles (
  user_id uuid primary key references auth.users(id) on delete cascade,
  dni text unique,
  role text not null default 'alumno' check (role in ('admin_tic', 'docente', 'alumno')),
  must_change_password boolean not null default true,
  is_active boolean not null default true,
  display_name text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint ck_user_profiles_dni_format check (dni is null or dni ~ '^[0-9]{8}$')
);

create or replace function public.trg_user_profiles_updated_at()
returns trigger
language plpgsql
set search_path = pg_catalog, public
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

drop trigger if exists before_update_user_profiles_updated_at on public.user_profiles;
create trigger before_update_user_profiles_updated_at
before update on public.user_profiles
for each row
execute function public.trg_user_profiles_updated_at();

create or replace function public.bootstrap_profile_for_user(p_user_id uuid)
returns public.user_profiles
language plpgsql
security definer
set search_path = pg_catalog, public, auth
as $$
declare
  v_email text;
  v_dni text;
  v_profile public.user_profiles;
begin
  if p_user_id is null then
    raise exception 'Usuario invalido';
  end if;

  select u.email into v_email
  from auth.users u
  where u.id = p_user_id;

  if v_email is null then
    raise exception 'No se encontro el usuario auth';
  end if;

  v_dni := regexp_replace(split_part(v_email, '@', 1), '[^0-9]', '', 'g');
  if length(v_dni) <> 8 then
    v_dni := null;
  end if;

  insert into public.user_profiles(user_id, dni, role, must_change_password, is_active)
  values (
    p_user_id,
    v_dni,
    case when lower(v_email) in ('pabloclsa87@gmail.com') then 'admin_tic' else 'alumno' end,
    true,
    true
  )
  on conflict (user_id) do nothing;

  select * into v_profile
  from public.user_profiles
  where user_id = p_user_id;

  return v_profile;
end;
$$;

create or replace function public.trg_auth_user_created_profile()
returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public, auth
as $$
begin
  perform public.bootstrap_profile_for_user(new.id);
  return new;
end;
$$;

drop trigger if exists after_auth_user_created_profile on auth.users;
create trigger after_auth_user_created_profile
after insert on auth.users
for each row
execute function public.trg_auth_user_created_profile();

-- Backfill existing users
insert into public.user_profiles(user_id, dni, role, must_change_password, is_active)
select
  u.id,
  case
    when regexp_replace(split_part(u.email, '@', 1), '[^0-9]', '', 'g') ~ '^[0-9]{8}$'
      then regexp_replace(split_part(u.email, '@', 1), '[^0-9]', '', 'g')
    else null
  end as dni,
  case when lower(u.email) in ('pabloclsa87@gmail.com') then 'admin_tic' else 'alumno' end as role,
  true,
  true
from auth.users u
left join public.user_profiles p on p.user_id = u.id
where p.user_id is null;

alter table public.user_profiles enable row level security;

drop policy if exists user_profiles_select_own on public.user_profiles;
create policy user_profiles_select_own
on public.user_profiles
for select
to authenticated
using (user_id = auth.uid());

drop policy if exists user_profiles_update_own on public.user_profiles;
create policy user_profiles_update_own
on public.user_profiles
for update
to authenticated
using (user_id = auth.uid())
with check (user_id = auth.uid());

create or replace function public.get_my_profile()
returns table (
  user_id uuid,
  dni text,
  role text,
  must_change_password boolean,
  is_active boolean,
  display_name text
)
language plpgsql
security definer
set search_path = pg_catalog, public, auth
as $$
declare
  v_uid uuid;
  v_profile public.user_profiles;
begin
  v_uid := auth.uid();
  if v_uid is null then
    raise exception 'No autenticado';
  end if;

  v_profile := public.bootstrap_profile_for_user(v_uid);

  return query
  select
    v_profile.user_id,
    v_profile.dni,
    v_profile.role,
    v_profile.must_change_password,
    v_profile.is_active,
    v_profile.display_name;
end;
$$;

create or replace function public.complete_initial_password_change()
returns void
language plpgsql
security definer
set search_path = pg_catalog, public, auth
as $$
declare
  v_uid uuid;
begin
  v_uid := auth.uid();
  if v_uid is null then
    raise exception 'No autenticado';
  end if;

  update public.user_profiles
  set must_change_password = false
  where user_id = v_uid;
end;
$$;

grant execute on function public.get_my_profile() to authenticated;
grant execute on function public.complete_initial_password_change() to authenticated;
grant select, update on public.user_profiles to authenticated;
