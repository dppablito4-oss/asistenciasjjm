import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import QRCode from "https://esm.sh/qrcode@1.5.4";
import { ADMIN_EMAILS, SUPABASE_PUBLISHABLE_KEY, SUPABASE_URL } from "./config.js";

const supabase = createClient(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY);

const $ = (id) => document.getElementById(id);
const statusEl = $("status");
const todayBody = $("today-body");
const overrideBody = $("override-body");
const studentsAdminBody = $("students-admin-body");

let currentSession = null;
let studentsCache = [];
let selectedStudentId = null;

function isAdminEmail(email) {
  const e = String(email || "").trim().toLowerCase();
  return ADMIN_EMAILS.map((item) => String(item || "").trim().toLowerCase()).includes(e);
}

function isLoggedIn() {
  return !!currentSession?.user;
}

function isAdmin() {
  return isAdminEmail(currentSession?.user?.email);
}

function setAuthBadge() {
  const badge = $("auth-badge");
  if (!isLoggedIn()) {
    badge.textContent = "No autenticado";
    return;
  }
  const role = isAdmin() ? "Admin" : "Operador";
  badge.textContent = `Sesion: ${currentSession.user.email} (${role})`;
}

function updateUiByAuth() {
  setAuthBadge();
  $("btn-mark").disabled = !isLoggedIn();
  $("btn-refresh").disabled = !isLoggedIn();
  $("admin-panel").classList.toggle("hidden", !isAdmin());
}

function requireLogin() {
  if (!isLoggedIn()) {
    setStatus("Debes iniciar sesion para usar el sistema.", false);
    return false;
  }
  return true;
}

function requireAdmin() {
  if (!isAdmin()) {
    setStatus("Solo un admin puede editar configuraciones.", false);
    return false;
  }
  return true;
}

function setStatus(text, ok = true) {
  statusEl.textContent = text;
  statusEl.classList.remove("ok", "bad");
  statusEl.classList.add(ok ? "ok" : "bad");
}

function todayIsoDate() {
  return new Date().toISOString().slice(0, 10);
}

async function signIn() {
  const email = $("email").value.trim();
  const password = $("password").value;
  if (!email || !password) {
    setStatus("Completa correo y clave.", false);
    return;
  }

  const { error } = await supabase.auth.signInWithPassword({ email, password });
  if (error) {
    setStatus(`Login fallido: ${error.message}`, false);
    return;
  }
  const auth = await supabase.auth.getSession();
  currentSession = auth.data.session;
  updateUiByAuth();
  setStatus("Login exitoso.");
  await loadTodayAttendance();
  if (isAdmin()) {
    await loadGlobalSchedule();
    await loadOverrides();
    await loadStudentsAdmin();
  }
}

async function signOut() {
  const { error } = await supabase.auth.signOut();
  if (error) {
    setStatus(`Error cerrando sesion: ${error.message}`, false);
    return;
  }
  currentSession = null;
  updateUiByAuth();
  todayBody.innerHTML = "";
  overrideBody.innerHTML = "";
  studentsAdminBody.innerHTML = "";
  setStatus("Sesion cerrada.");
}

async function markAttendance() {
  if (!requireLogin()) {
    return;
  }
  const token = $("qr-token").value.trim();
  const teacher = $("teacher").value.trim();

  if (!token || !teacher) {
    setStatus("Ingresa token/DNI y profesor.", false);
    return;
  }

  const { data, error } = await supabase.rpc("mark_attendance", {
    p_identifier: token,
    p_teacher: teacher,
  });

  if (error) {
    setStatus(`Error marcando: ${error.message}`, false);
    return;
  }

  const result = Array.isArray(data) ? data[0] : data;
  if (!result?.ok) {
    setStatus(result?.message || "No se pudo marcar asistencia.", false);
    return;
  }

  setStatus(result.message || "Asistencia registrada.");
  $("qr-token").value = "";
  await loadTodayAttendance();
}

async function loadTodayAttendance() {
  if (!requireLogin()) {
    return;
  }
  const today = todayIsoDate();
  $("today-date").textContent = `Fecha: ${today}`;

  const { data, error } = await supabase
    .from("v_today_attendance")
    .select("hora,dni,nombres,apellidos,grado,seccion,estado,profesor_encargado")
    .order("hora", { ascending: false });

  if (error) {
    setStatus(`Error cargando tabla: ${error.message}`, false);
    return;
  }

  todayBody.innerHTML = "";
  for (const row of data || []) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${row.hora ?? ""}</td>
      <td>${row.dni ?? ""}</td>
      <td>${row.apellidos ?? ""}, ${row.nombres ?? ""}</td>
      <td>${row.grado ?? ""}${row.seccion ?? ""}</td>
      <td>${row.estado ?? ""}</td>
      <td>${row.profesor_encargado ?? ""}</td>
    `;
    todayBody.appendChild(tr);
  }
}

async function loadGlobalSchedule() {
  if (!requireAdmin()) {
    return;
  }
  const { data, error } = await supabase
    .from("app_settings")
    .select("key,value")
    .in("key", ["entry_time", "tolerance_min"]);

  if (error) {
    setStatus(`Error cargando horario: ${error.message}`, false);
    return;
  }

  const map = Object.fromEntries((data || []).map((r) => [r.key, r.value]));
  $("global-entry-time").value = map.entry_time || "08:00";
  $("global-tolerance").value = Number(map.tolerance_min || 10);
}

async function saveGlobalSchedule() {
  if (!requireAdmin()) {
    return;
  }

  const entry = $("global-entry-time").value;
  const tolerance = Number($("global-tolerance").value);
  if (!entry || Number.isNaN(tolerance) || tolerance < 0) {
    setStatus("Horario general invalido.", false);
    return;
  }

  const { error } = await supabase.rpc("set_attendance_schedule", {
    p_entry_time: entry,
    p_tolerance_min: tolerance,
  });

  if (error) {
    setStatus(`Error guardando horario: ${error.message}`, false);
    return;
  }
  setStatus("Horario general actualizado.");
}

async function loadOverrides() {
  if (!requireAdmin()) {
    return;
  }

  const { data, error } = await supabase
    .from("attendance_schedule_overrides")
    .select("day,entry_time,tolerance_min,reason")
    .order("day", { ascending: false });

  if (error) {
    setStatus(`Error cargando excepciones: ${error.message}`, false);
    return;
  }

  overrideBody.innerHTML = "";
  for (const row of data || []) {
    const tr = document.createElement("tr");
    const btnId = `del-${String(row.day).replaceAll("-", "")}`;
    tr.innerHTML = `
      <td>${row.day}</td>
      <td>${String(row.entry_time || "").slice(0, 5)}</td>
      <td>${row.tolerance_min ?? 0}</td>
      <td>${row.reason ?? ""}</td>
      <td><button id="${btnId}" class="danger" type="button">Eliminar</button></td>
    `;
    overrideBody.appendChild(tr);
    tr.querySelector(`#${btnId}`)?.addEventListener("click", async () => {
      await deleteOverride(row.day);
    });
  }
}

async function saveOverride() {
  if (!requireAdmin()) {
    return;
  }

  const day = $("override-date").value;
  const entry = $("override-entry-time").value;
  const tolerance = Number($("override-tolerance").value);
  const reason = $("override-reason").value.trim();

  if (!day || !entry || Number.isNaN(tolerance) || tolerance < 0) {
    setStatus("Completa fecha, hora y tolerancia validas.", false);
    return;
  }

  const { error } = await supabase.rpc("upsert_attendance_schedule_override", {
    p_day: day,
    p_entry_time: entry,
    p_tolerance_min: tolerance,
    p_reason: reason,
  });

  if (error) {
    setStatus(`Error guardando excepcion: ${error.message}`, false);
    return;
  }

  setStatus("Excepcion guardada correctamente.");
  await loadOverrides();
}

async function deleteOverride(day) {
  if (!requireAdmin()) {
    return;
  }
  const { error } = await supabase.rpc("delete_attendance_schedule_override", {
    p_day: day,
  });
  if (error) {
    setStatus(`Error eliminando excepcion: ${error.message}`, false);
    return;
  }
  setStatus("Excepcion eliminada.");
  await loadOverrides();
}

function getStudentFormPayload() {
  const studentId = $("st-id").value.trim();
  return {
    p_id: studentId || null,
    p_dni: $("st-dni").value.trim(),
    p_nombres: $("st-nombres").value.trim(),
    p_apellidos: $("st-apellidos").value.trim(),
    p_grado: Number($("st-grado").value),
    p_seccion: $("st-seccion").value.trim().toUpperCase(),
    p_genero: $("st-genero").value,
    p_cargo: $("st-cargo").value.trim() || "Alumno",
    p_birth_date: $("st-birth-date").value || null,
    p_status: $("st-status").value,
    p_status_note: $("st-status-note").value.trim(),
  };
}

function fillStudentForm(student) {
  $("st-id").value = student?.id || "";
  $("st-dni").value = student?.dni || "";
  $("st-nombres").value = student?.nombres || "";
  $("st-apellidos").value = student?.apellidos || "";
  $("st-grado").value = student?.grado ?? 1;
  $("st-seccion").value = student?.seccion || "A";
  $("st-genero").value = student?.genero || "F";
  $("st-cargo").value = student?.cargo || "Alumno";
  $("st-birth-date").value = student?.birth_date || "";
  $("st-status").value = student?.status || "ACTIVO";
  $("st-status-note").value = student?.status_note || "";
}

function clearStudentForm() {
  selectedStudentId = null;
  fillStudentForm(null);
  renderStudentsTable();
}

async function loadStudentsAdmin() {
  if (!requireAdmin()) {
    return;
  }

  const { data, error } = await supabase
    .from("v_students_admin")
    .select("id,dni,nombres,apellidos,grado,seccion,genero,cargo,birth_date,status,status_note,activo,qr_token,edad,updated_at")
    .order("apellidos", { ascending: true })
    .order("nombres", { ascending: true });

  if (error) {
    setStatus(`Error cargando alumnos: ${error.message}`, false);
    return;
  }
  studentsCache = data || [];
  renderStudentsTable();
}

function getFilteredStudents() {
  const q = String($("student-search").value || "").trim().toLowerCase();
  if (!q) {
    return studentsCache;
  }
  return studentsCache.filter((s) => {
    const full = `${s.dni} ${s.nombres} ${s.apellidos} ${s.status} ${s.status_note || ""}`.toLowerCase();
    return full.includes(q);
  });
}

function renderStudentsTable() {
  studentsAdminBody.innerHTML = "";
  const rows = getFilteredStudents();

  for (const student of rows) {
    const tr = document.createElement("tr");
    if (selectedStudentId && selectedStudentId === student.id) {
      tr.classList.add("is-selected");
    }
    const btnEditId = `edit-${student.id}`;
    const btnQrId = `qr-${student.id}`;
    tr.innerHTML = `
      <td>${student.dni}</td>
      <td>${student.apellidos}, ${student.nombres}</td>
      <td>${student.grado}${student.seccion}</td>
      <td>${student.genero}</td>
      <td>${student.status}</td>
      <td>${student.edad ?? "-"}</td>
      <td>
        <button id="${btnEditId}" class="ghost" type="button">Editar</button>
        <button id="${btnQrId}" class="ghost" type="button">Nuevo QR</button>
      </td>
    `;
    studentsAdminBody.appendChild(tr);

    tr.querySelector(`#${btnEditId}`)?.addEventListener("click", () => {
      selectedStudentId = student.id;
      fillStudentForm(student);
      renderStudentsTable();
    });

    tr.querySelector(`#${btnQrId}`)?.addEventListener("click", async () => {
      await regenerateStudentQr(student.id);
    });
  }
}

async function saveStudent() {
  if (!requireAdmin()) {
    return;
  }

  const payload = getStudentFormPayload();
  if (!payload.p_dni || !payload.p_nombres || !payload.p_apellidos) {
    setStatus("DNI, nombres y apellidos son obligatorios.", false);
    return;
  }

  const { data, error } = await supabase.rpc("upsert_student_admin", payload);
  if (error) {
    setStatus(`Error guardando alumno: ${error.message}`, false);
    return;
  }

  selectedStudentId = data || selectedStudentId;
  setStatus("Alumno guardado correctamente.");
  await loadStudentsAdmin();
}

async function regenerateStudentQr(studentId) {
  if (!requireAdmin()) {
    return;
  }
  const { error } = await supabase.rpc("regenerate_student_qr_token", {
    p_student_id: studentId,
  });
  if (error) {
    setStatus(`Error regenerando QR: ${error.message}`, false);
    return;
  }
  setStatus("QR regenerado correctamente.");
  await loadStudentsAdmin();
}

function getSelectedStudent() {
  if (!selectedStudentId) {
    return null;
  }
  return studentsCache.find((s) => s.id === selectedStudentId) || null;
}

async function studentQrDataUrl(student) {
  const payload = JSON.stringify({ t: student.qr_token || "" });
  return QRCode.toDataURL(payload, {
    width: 420,
    margin: 1,
    errorCorrectionLevel: "H",
  });
}

function downloadDataUrl(dataUrl, filename) {
  const a = document.createElement("a");
  a.href = dataUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

async function downloadSelectedQr() {
  if (!requireAdmin()) {
    return;
  }
  const student = getSelectedStudent();
  if (!student) {
    setStatus("Selecciona un alumno primero.", false);
    return;
  }
  const dataUrl = await studentQrDataUrl(student);
  downloadDataUrl(dataUrl, `qr_${student.dni}.png`);
  setStatus("QR descargado.");
}

async function printSelectedCard() {
  if (!requireAdmin()) {
    return;
  }
  const student = getSelectedStudent();
  if (!student) {
    setStatus("Selecciona un alumno primero.", false);
    return;
  }

  const qr = await studentQrDataUrl(student);
  const w = window.open("", "_blank", "width=900,height=600");
  if (!w) {
    setStatus("El navegador bloqueo la ventana de impresion.", false);
    return;
  }

  w.document.write(`
    <html>
      <head>
        <title>Carnet ${student.dni}</title>
        <style>
          body { font-family: Arial, sans-serif; background: #f3f4f6; padding: 20px; }
          .card { width: 540px; border: 1px solid #111827; border-radius: 14px; background: white; padding: 16px; }
          .head { font-weight: 700; font-size: 24px; margin-bottom: 10px; }
          .muted { color: #4b5563; }
          .grid { display: grid; grid-template-columns: 1fr 170px; gap: 14px; align-items: center; }
          .line { margin: 6px 0; }
          img { width: 170px; height: 170px; }
        </style>
      </head>
      <body>
        <div class="card">
          <div class="head">IE Asistencia</div>
          <div class="grid">
            <div>
              <div class="line"><strong>DNI:</strong> ${student.dni}</div>
              <div class="line"><strong>Estudiante:</strong> ${student.nombres} ${student.apellidos}</div>
              <div class="line"><strong>Grado/Seccion:</strong> ${student.grado}${student.seccion}</div>
              <div class="line"><strong>Estado:</strong> ${student.status}</div>
              <div class="line muted">${student.status_note || ""}</div>
            </div>
            <div><img src="${qr}" alt="QR" /></div>
          </div>
        </div>
        <script>window.print();</script>
      </body>
    </html>
  `);
  w.document.close();
  setStatus("Abierto carnet para imprimir.");
}

function csvEscape(v) {
  const s = String(v ?? "");
  return `"${s.replaceAll('"', '""')}"`;
}

function exportStudentsCsv() {
  if (!requireAdmin()) {
    return;
  }
  const rows = getFilteredStudents();
  if (!rows.length) {
    setStatus("No hay alumnos para exportar.", false);
    return;
  }
  const headers = ["dni", "nombres", "apellidos", "grado", "seccion", "genero", "cargo", "birth_date", "edad", "status", "status_note", "qr_token"];
  const lines = [headers.join(",")];
  for (const s of rows) {
    lines.push([
      s.dni,
      s.nombres,
      s.apellidos,
      s.grado,
      s.seccion,
      s.genero,
      s.cargo,
      s.birth_date || "",
      s.edad ?? "",
      s.status,
      s.status_note || "",
      s.qr_token || "",
    ].map(csvEscape).join(","));
  }
  const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  downloadDataUrl(url, `alumnos_${todayIsoDate()}.csv`);
  URL.revokeObjectURL(url);
  setStatus("CSV de alumnos descargado.");
}

async function bootstrapAuth() {
  const auth = await supabase.auth.getSession();
  currentSession = auth.data.session;
  updateUiByAuth();
  if (isLoggedIn()) {
    await loadTodayAttendance();
    if (isAdmin()) {
      await loadGlobalSchedule();
      await loadOverrides();
      await loadStudentsAdmin();
    }
  } else {
    setStatus("Inicia sesion para usar asistencia y admin.", false);
  }
}

$("btn-login").addEventListener("click", signIn);
$("btn-logout").addEventListener("click", signOut);
$("btn-mark").addEventListener("click", markAttendance);
$("btn-refresh").addEventListener("click", loadTodayAttendance);
$("btn-save-global").addEventListener("click", saveGlobalSchedule);
$("btn-save-override").addEventListener("click", saveOverride);
$("btn-refresh-overrides").addEventListener("click", loadOverrides);
$("btn-refresh-students").addEventListener("click", loadStudentsAdmin);
$("btn-save-student").addEventListener("click", saveStudent);
$("btn-clear-student").addEventListener("click", clearStudentForm);
$("btn-download-selected-qr").addEventListener("click", downloadSelectedQr);
$("btn-print-card").addEventListener("click", printSelectedCard);
$("btn-export-students-csv").addEventListener("click", exportStudentsCsv);
$("student-search").addEventListener("input", renderStudentsTable);

supabase.auth.onAuthStateChange((_event, session) => {
  currentSession = session;
  updateUiByAuth();
});

bootstrapAuth();
