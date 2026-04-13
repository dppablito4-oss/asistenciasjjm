import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { SUPABASE_PUBLISHABLE_KEY, SUPABASE_URL } from "./config.js";

const supabase = createClient(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY);

const $ = (id) => document.getElementById(id);
const statusEl = $("status");
const todayBody = $("today-body");

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
  setStatus("Login exitoso.");
}

async function signOut() {
  const { error } = await supabase.auth.signOut();
  if (error) {
    setStatus(`Error cerrando sesion: ${error.message}`, false);
    return;
  }
  setStatus("Sesion cerrada.");
}

async function markAttendance() {
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

$("btn-login").addEventListener("click", signIn);
$("btn-logout").addEventListener("click", signOut);
$("btn-mark").addEventListener("click", markAttendance);
$("btn-refresh").addEventListener("click", loadTodayAttendance);

loadTodayAttendance();
