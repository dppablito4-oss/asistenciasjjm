-- Storage bucket for institution branding assets

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'branding-assets',
  'branding-assets',
  true,
  10485760,
  array['image/png', 'image/jpeg', 'image/webp']::text[]
)
on conflict (id) do nothing;

-- Public read for branding assets
drop policy if exists branding_assets_public_read on storage.objects;
create policy branding_assets_public_read
on storage.objects
for select
to public
using (bucket_id = 'branding-assets');

-- Authenticated users can upload/update/delete branding files
drop policy if exists branding_assets_auth_insert on storage.objects;
create policy branding_assets_auth_insert
on storage.objects
for insert
to authenticated
with check (bucket_id = 'branding-assets');

drop policy if exists branding_assets_auth_update on storage.objects;
create policy branding_assets_auth_update
on storage.objects
for update
to authenticated
using (bucket_id = 'branding-assets')
with check (bucket_id = 'branding-assets');

drop policy if exists branding_assets_auth_delete on storage.objects;
create policy branding_assets_auth_delete
on storage.objects
for delete
to authenticated
using (bucket_id = 'branding-assets');
