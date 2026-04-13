-- Hotfix: generate_qr_token fails when search_path excludes extensions schema.
-- Error seen: function gen_random_bytes(integer) does not exist

create or replace function public.generate_qr_token()
returns text
language sql
volatile
set search_path = pg_catalog, public, extensions
as $$
  select rtrim(translate(encode(extensions.gen_random_bytes(18), 'base64'), '+/', '-_'), '=');
$$;

grant execute on function public.generate_qr_token() to authenticated;
