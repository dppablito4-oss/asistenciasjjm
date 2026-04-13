# asistenciasjjm

Sistema de asistencia escolar. Este repositorio ahora incluye:

- App desktop original en Python (`main.py`)
- Frontend web estatico para GitHub Pages (`web/`)
- Migracion SQL para backend en Supabase (`supabase/migrations/0001_core_schema.sql`)

## Conectar Supabase (paso rapido)

1. En Supabase abre `SQL Editor`.
2. Ejecuta el archivo `supabase/migrations/0001_core_schema.sql` completo.
3. Crea al menos un usuario en `Authentication > Users` (email/password).
4. Inserta estudiantes de prueba en tabla `estudiantes` con `dni` y `qr_token`.
5. Publica la carpeta `web/` en GitHub Pages.

## Credenciales frontend

La app web usa `web/config.js` con:

- `SUPABASE_URL`
- `SUPABASE_PUBLISHABLE_KEY`

Importante:

- Esta key publishable es publica por diseno.
- Nunca uses `service_role` en frontend.

## Ejecutar local (opcional)

Puedes probar el frontend estatico con cualquier servidor simple, por ejemplo VS Code Live Server, o:

```powershell
cd web
python -m http.server 5500
```

Luego abrir `http://localhost:5500`.

## Flujo de asistencia web

1. Login con usuario de Supabase Auth.
2. Ingresa profesor y token QR (o DNI).
3. El frontend llama RPC `mark_attendance`.
4. La logica de validacion y duplicados vive en SQL.
5. La tabla muestra registros del dia desde `v_today_attendance`.

## Notas de migracion desde Python

- `mark_attendance()` de Python fue migrado a la funcion SQL `public.mark_attendance`.
- Se mantiene regla de tardanza usando `entry_time + tolerance_min`.
- Se evita duplicado con `UNIQUE (estudiante_id, fecha)` en `asistencia`.
