-- Branding settings for reports and ID cards

insert into public.app_settings(key, value)
values ('school_name', 'IE Asistencia')
on conflict (key) do nothing;

insert into public.app_settings(key, value)
values ('place_label', 'Huanuco')
on conflict (key) do nothing;

create or replace function public.set_branding_settings(
  p_school_name text,
  p_place_label text
)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_school text;
  v_place text;
begin
  v_school := trim(coalesce(p_school_name, ''));
  v_place := trim(coalesce(p_place_label, ''));

  if v_school = '' then
    v_school := 'IE Asistencia';
  end if;

  if v_place = '' then
    v_place := 'Huanuco';
  end if;

  insert into public.app_settings(key, value)
  values ('school_name', v_school)
  on conflict (key) do update set value = excluded.value;

  insert into public.app_settings(key, value)
  values ('place_label', v_place)
  on conflict (key) do update set value = excluded.value;
end;
$$;

grant execute on function public.set_branding_settings(text, text) to authenticated;
