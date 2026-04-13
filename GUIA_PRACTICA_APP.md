# Guia practica - Registro de Asistencia Escolar

## 1. Que hace esta app
Esta app permite gestionar asistencia escolar con:
- Registro por scanner QR o ingreso manual.
- Panel de administracion para estudiantes, secciones y configuracion.
- Reportes por dia, semana, mes o rango.
- Exportacion de reportes en Excel o PDF.
- Generacion de QR y fotochecks.
- Historial de reportes con vista temporal.

## 2. Instalacion y primer inicio
### Instalacion
1. Ejecuta el instalador:
   - `installer/output/RegistroAsistenciaEscolar_Installer.exe`
2. Sigue el asistente tipico: Siguiente -> Siguiente -> Instalar.
3. Abre la app desde el acceso directo.

### Donde se guardan los datos
La base de datos NO se guarda dentro de la carpeta del programa instalado.
Se guarda en AppData de Windows:
- `%APPDATA%/RegistroAsistenciaEscolar/colegio.db`

Esto permite que al actualizar la app no pierdas la informacion.

## 3. Acceso general y panel admin
### Pantalla inicial
Desde la pantalla principal puedes:
- Entrar al sistema.
- Abrir panel Admin.
- Recuperar admin por codigo.

### Admin (seguridad)
- Si es primera vez o password temporal, cambia la clave.
- Puedes generar codigos de recuperacion de un solo uso.
- Al generar un nuevo lote, invalida lotes anteriores.

## 4. Modulo Registro (asistencia diaria)
### A) Registro por QR (camara)
1. Ir a pestana `Registro`.
2. Iniciar camara.
3. Mostrar QR del estudiante al lector.
4. La app registra asistencia si no fue marcada antes ese dia.

### B) Registro manual
1. En la misma pestana, usar el campo DNI/busqueda.
2. Escribir DNI o seleccionar alumno de la lista.
3. Presionar `Registrar Asistencia`.

### Reglas de asistencia
- Evita duplicados el mismo dia.
- Marca `Asistio` o `Tardanza` segun horario y tolerancia configurados.

## 5. Modulo Administracion
## 5.1 CRUD de estudiantes
Campos:
- DNI
- Nombres
- Apellidos
- Grado
- Seccion
- Genero
- Cargo

Acciones:
- Agregar
- Actualizar
- Desactivar
- Reactivar
- Limpiar

## 5.2 Filtros en tabla de estudiantes
Puedes filtrar por:
- Texto de busqueda (DNI, nombre, apellido, etc.)
- Grado
- Seccion

## 5.3 Gestion de secciones
Puedes crear/reactivar secciones por grado desde Admin.
Ejemplo:
- Grado: 4
- Seccion: D

La app actualiza combos y filtros automaticamente.

## 5.4 Importacion Excel/CSV
Boton: `Importar Excel/CSV`

Columnas requeridas:
- `dni`
- `nombres`
- `apellidos`
- `genero`

Columnas opcionales:
- `grado`
- `seccion`
- `cargo`

Si el archivo no trae `grado` y/o `seccion`, puedes definir defaults desde:
- `Importacion sin columnas grado/seccion`

Resultado de importacion:
- Nuevos
- Actualizados
- Omitidos

## 5.5 Backup
Boton: `Backup DB`
- Crea una copia de la base para respaldo.

## 5.6 Horario y operador
Configurable en Admin:
- Hora de entrada (HH:MM)
- Minutos de tolerancia
- Operador responsable

## 5.7 Branding
Puedes configurar:
- Nombre del colegio
- Insignia
- Logo MINEDU
- Foto panoramica

## 6. Modulo Reportes
## 6.1 Filtros disponibles
- Periodo: Dia / Semana / Mes / Rango
- Condicion: Todos / Asistieron / Faltaron
- Grado
- Seccion
- Genero
- Cargo
- Fechas

## 6.2 Generar reporte
1. Configurar filtros.
2. Clic en `Generar Reporte`.
3. Ver resultados en tabla previa.

## 6.3 Exportar reporte
Formato:
- Excel (.xlsx)
- PDF (.pdf)

Al exportar, SIEMPRE el usuario elige donde guardar.
No se guarda exporte final en AppData automaticamente.

## 6.4 Historial de reportes
Cada reporte generado se registra en historial con sus filtros.
Desde `Ver Historial (PDF Temporal)`:
1. Seleccionas un item del historial.
2. La app vuelve a generar el reporte.
3. Abre un PDF temporal.
4. Ese PDF temporal se elimina automaticamente (aprox. 10 min).

Objetivo:
- No llenar disco con archivos temporales.
- Mantener historial funcional de consultas.

## 7. Modulo Generador QR
## 7.1 QR individual
- Buscar estudiante.
- Vista previa.
- Guardar QR PNG.

## 7.2 QR por lote
- Definir alcance: seleccionado / filtrado / todos activos.
- Aplicar filtros (grado, seccion, genero, cargo).
- Exportar ZIP de QRs.

## 7.3 Fotocheck PDF
- Generar carnets tipo fotocheck.
- Layouts: 8, 6 o 1 por hoja A4.
- Exporta PDF en la ruta elegida por el usuario.

## 8. Recomendaciones de uso diario
- Al iniciar jornada:
  1. Verificar camara y scanner.
  2. Revisar horario y tolerancia.
  3. Confirmar operador.
- Durante el dia:
  1. Registrar por QR primero.
  2. Usar manual para casos especiales.
- Al finalizar:
  1. Generar reporte diario.
  2. Exportar PDF/Excel si hace falta.
  3. Ejecutar backup periodico.

## 9. Solucion de problemas (rapido)
## 9.1 Error de DLL pyzbar/libiconv al abrir instalada
- Usa el instalador mas reciente generado despues del fix.
- Si ya habia una version instalada, desinstala y reinstala.

## 9.2 La camara no detecta QR
- Revisar iluminacion.
- Acercar QR y evitar desenfoque.
- Confirmar que el alumno este activo en base.

## 9.3 No puedo entrar a Admin
- Verificar clave.
- Usar recuperacion por codigo.
- Al recuperar, cambiar password inmediatamente.

## 9.4 El reporte sale vacio
- Revisar filtros (fechas, grado, seccion, condicion).
- Probar con `Todos` y luego reducir filtro.

## 10. Estructura funcional del proyecto
Archivos principales:
- `main.py` -> interfaz y flujos
- `database.py` -> base de datos, seguridad, historial
- `reports.py` -> logica y exportes de reportes
- `scanner.py` -> lectura QR
- `qr_generator.py` -> generacion de imagen QR
- `id_cards.py` -> fotocheck PDF
- `build_installer.ps1` -> build de exe e instalador
- `installer/setup.iss` -> script Inno Setup

## 11. Checklist rapido para personal
- [ ] Scanner funcionando
- [ ] Hora/tolerancia correctas
- [ ] Operador correcto
- [ ] Alumnos importados y activos
- [ ] Secciones actualizadas
- [ ] Reporte diario generado
- [ ] Backup realizado

---
Si quieres, se puede crear tambien una version corta para imprimir en 1 pagina (formato "manual operativo diario").
