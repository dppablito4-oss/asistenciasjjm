import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { ADMIN_EMAILS, SUPABASE_PUBLISHABLE_KEY, SUPABASE_URL } from "./config.js";

const supabase = createClient(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY);

const $ = (id) => document.getElementById(id);
const statusEl = $("status");
const todayBody = $("today-body");
const overrideBody = $("override-body");

let currentSession = null;

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

async function bootstrapAuth() {
  const auth = await supabase.auth.getSession();
  currentSession = auth.data.session;
  updateUiByAuth();
  if (isLoggedIn()) {
    await loadTodayAttendance();
    if (isAdmin()) {
      await loadGlobalSchedule();
      await loadOverrides();
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

supabase.auth.onAuthStateChange((_event, session) => {
  currentSession = session;
  updateUiByAuth();
});

bootstrapAuth();
