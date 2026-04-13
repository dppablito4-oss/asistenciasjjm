-- Branding visual assets for web UI, cards and reports

insert into public.app_settings(key, value)
values ('panoramic_url', '')
on conflict (key) do nothing;

insert into public.app_settings(key, value)
values ('insignia_url', '')
on conflict (key) do nothing;

insert into public.app_settings(key, value)
values ('minedu_logo_url', '')
on conflict (key) do nothing;

create or replace function public.set_branding_assets(
  p_panoramic_url text,
  p_insignia_url text,
  p_minedu_logo_url text
)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.app_settings(key, value)
  values ('panoramic_url', trim(coalesce(p_panoramic_url, '')))
  on conflict (key) do update set value = excluded.value;

  insert into public.app_settings(key, value)
  values ('insignia_url', trim(coalesce(p_insignia_url, '')))
  on conflict (key) do update set value = excluded.value;

  insert into public.app_settings(key, value)
  values ('minedu_logo_url', trim(coalesce(p_minedu_logo_url, '')))
  on conflict (key) do update set value = excluded.value;
end;
$$;

grant execute on function public.set_branding_assets(text, text, text) to authenticated;
