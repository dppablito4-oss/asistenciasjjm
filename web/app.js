import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import QRCode from "https://esm.sh/qrcode@1.5.4";
import * as XLSX from "https://esm.sh/xlsx@0.18.5";
import { jsPDF } from "https://esm.sh/jspdf@2.5.1";
import autoTable from "https://esm.sh/jspdf-autotable@3.8.2";
import { ADMIN_EMAILS, SUPABASE_PUBLISHABLE_KEY, SUPABASE_URL } from "./config.js";

const supabase = createClient(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY);

const $ = (id) => document.getElementById(id);
const statusEl = $("status");
const todayBody = $("today-body");
const overrideBody = $("override-body");
const studentsAdminBody = $("students-admin-body");
const reportBody = $("report-body");
const scanStatusEl = $("scan-status");

let currentSession = null;
let studentsCache = [];
let selectedStudentId = null;
let lastReportRows = [];
let branding = { schoolName: "IE Asistencia", placeLabel: "Huanuco" };
let scannerInstance = null;
let scannerRunning = false;
let lastScanAt = 0;
let lastScannedText = "";

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

function setScanStatus(text) {
  scanStatusEl.textContent = text;
}

function todayIsoDate() {
  return new Date().toISOString().slice(0, 10);
}

function toIsoDate(d) {
  return d.toISOString().slice(0, 10);
}

function periodBounds(period, refDate, startDate, endDate) {
  const p = String(period || "day").toLowerCase();
  const ref = refDate ? new Date(refDate) : new Date();
  if (Number.isNaN(ref.getTime())) {
    throw new Error("Fecha referencia invalida");
  }

  if (p === "day") {
    const day = toIsoDate(ref);
    return { start: day, end: day };
  }

  if (p === "week") {
    const monday = new Date(ref);
    const dayOfWeek = (monday.getDay() + 6) % 7;
    monday.setDate(monday.getDate() - dayOfWeek);
    const sunday = new Date(monday);
    sunday.setDate(monday.getDate() + 6);
    return { start: toIsoDate(monday), end: toIsoDate(sunday) };
  }

  if (p === "month") {
    const first = new Date(ref.getFullYear(), ref.getMonth(), 1);
    const last = new Date(ref.getFullYear(), ref.getMonth() + 1, 0);
    return { start: toIsoDate(first), end: toIsoDate(last) };
  }

  if (p === "range") {
    if (!startDate || !endDate) {
      throw new Error("Para rango debes indicar fecha inicio y fin");
    }
    if (startDate > endDate) {
      throw new Error("Fecha inicio no puede ser mayor a fecha fin");
    }
    return { start: startDate, end: endDate };
  }

  throw new Error("Periodo invalido");
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
  await stopScanner();
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

async function loadBrandingSettings() {
  if (!requireAdmin()) {
    return;
  }

  const { data, error } = await supabase
    .from("app_settings")
    .select("key,value")
    .in("key", ["school_name", "place_label"]);

  if (error) {
    setStatus(`Error cargando identidad: ${error.message}`, false);
    return;
  }

  const map = Object.fromEntries((data || []).map((r) => [r.key, r.value]));
  branding.schoolName = map.school_name || "IE Asistencia";
  branding.placeLabel = map.place_label || "Huanuco";
  $("brand-school-name").value = branding.schoolName;
  $("brand-place-label").value = branding.placeLabel;
}

async function saveBrandingSettings() {
  if (!requireAdmin()) {
    return;
  }
  const schoolName = $("brand-school-name").value.trim();
  const placeLabel = $("brand-place-label").value.trim();
  const { error } = await supabase.rpc("set_branding_settings", {
    p_school_name: schoolName,
    p_place_label: placeLabel,
  });
  if (error) {
    setStatus(`Error guardando identidad: ${error.message}`, false);
    return;
  }
  branding.schoolName = schoolName || "IE Asistencia";
  branding.placeLabel = placeLabel || "Huanuco";
  setStatus("Identidad institucional actualizada.");
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

function renderReportTable() {
  reportBody.innerHTML = "";
  for (const r of lastReportRows) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${r.fecha ?? ""}</td>
      <td>${r.hora ?? ""}</td>
      <td>${r.dni ?? ""}</td>
      <td>${r.nombres ?? ""}</td>
      <td>${r.apellidos ?? ""}</td>
      <td>${r.grado ?? ""}</td>
      <td>${r.seccion ?? ""}</td>
      <td>${r.genero ?? ""}</td>
      <td>${r.cargo ?? ""}</td>
      <td>${r.profesor_encargado ?? ""}</td>
      <td>${r.estado ?? ""}</td>
    `;
    reportBody.appendChild(tr);
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

async function importStudentsFile() {
  if (!requireAdmin()) {
    return;
  }
  const file = $("students-file").files?.[0];
  if (!file) {
    setStatus("Selecciona un archivo CSV o XLSX.", false);
    return;
  }

  const buffer = await file.arrayBuffer();
  const wb = XLSX.read(buffer, { type: "array" });
  const sheet = wb.Sheets[wb.SheetNames[0]];
  const rows = XLSX.utils.sheet_to_json(sheet, { defval: "" });
  if (!rows.length) {
    setStatus("El archivo no tiene filas de alumnos.", false);
    return;
  }

  const indexByDni = new Map(studentsCache.map((s) => [String(s.dni), s]));
  let ok = 0;
  let fail = 0;

  for (const row of rows) {
    const normalized = {};
    for (const [k, v] of Object.entries(row)) {
      normalized[String(k).trim().toLowerCase()] = v;
    }
    const dni = String(normalized.dni || "").replace(/\D/g, "");
    if (!dni) {
      fail += 1;
      continue;
    }
    const existing = indexByDni.get(dni);
    const payload = {
      p_id: existing?.id || null,
      p_dni: dni,
      p_nombres: String(normalized.nombres || "").trim(),
      p_apellidos: String(normalized.apellidos || "").trim(),
      p_grado: Number(normalized.grado || 1),
      p_seccion: String(normalized.seccion || "A").trim().toUpperCase(),
      p_genero: String(normalized.genero || "F").trim().toUpperCase(),
      p_cargo: String(normalized.cargo || "Alumno").trim(),
      p_birth_date: normalized.birth_date ? String(normalized.birth_date) : null,
      p_status: String(normalized.status || "ACTIVO").trim().toUpperCase(),
      p_status_note: String(normalized.status_note || "").trim(),
    };

    const { error } = await supabase.rpc("upsert_student_admin", payload);
    if (error) {
      fail += 1;
    } else {
      ok += 1;
    }
  }

  await loadStudentsAdmin();
  setStatus(`Importacion finalizada. OK: ${ok}, Fallidos: ${fail}.`, fail === 0);
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

async function startScanner() {
  if (!requireLogin()) {
    return;
  }
  if (scannerRunning) {
    return;
  }

  if (!window.Html5Qrcode) {
    setStatus("No se pudo cargar libreria de escaner QR.", false);
    return;
  }

  $("qr-reader").classList.remove("hidden");
  scannerInstance = new window.Html5Qrcode("qr-reader");
  try {
    await scannerInstance.start(
      { facingMode: "environment" },
      { fps: 10, qrbox: 240 },
      async (decodedText) => {
        const now = Date.now();
        if (decodedText === lastScannedText && now - lastScanAt < 1800) {
          return;
        }
        lastScanAt = now;
        lastScannedText = decodedText;

        let identifier = decodedText;
        try {
          const parsed = JSON.parse(decodedText);
          identifier = parsed.t || parsed.qr_token || parsed.token || decodedText;
        } catch {
          // Keep raw text for DNI fallback.
        }

        $("qr-token").value = String(identifier).trim();
        setScanStatus(`Escaneado: ${String(identifier).slice(0, 48)}`);
        await markAttendance();
      },
      () => {}
    );
    scannerRunning = true;
    setScanStatus("Escaner activo. Apunta a un QR.");
  } catch (err) {
    $("qr-reader").classList.add("hidden");
    scannerInstance = null;
    scannerRunning = false;
    setStatus(`No se pudo iniciar camara: ${String(err?.message || err)}`, false);
  }
}

async function stopScanner() {
  if (!scannerInstance || !scannerRunning) {
    setScanStatus("Escaner detenido.");
    $("qr-reader").classList.add("hidden");
    return;
  }
  try {
    await scannerInstance.stop();
    await scannerInstance.clear();
  } catch {
    // Ignore cleanup errors.
  }
  scannerInstance = null;
  scannerRunning = false;
  $("qr-reader").classList.add("hidden");
  setScanStatus("Escaner detenido.");
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
          <div class="head">${branding.schoolName}</div>
          <div class="grid">
            <div>
              <div class="line"><strong>DNI:</strong> ${student.dni}</div>
              <div class="line"><strong>Estudiante:</strong> ${student.nombres} ${student.apellidos}</div>
              <div class="line"><strong>Grado/Seccion:</strong> ${student.grado}${student.seccion}</div>
              <div class="line"><strong>Estado:</strong> ${student.status}</div>
              <div class="line muted">${student.status_note || ""}</div>
              <div class="line muted">${branding.placeLabel} - ${new Date().getFullYear()}</div>
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

async function printBulkCardsPdf() {
  if (!requireAdmin()) {
    return;
  }
  const rows = getFilteredStudents();
  if (!rows.length) {
    setStatus("No hay alumnos filtrados para carnets.", false);
    return;
  }

  setStatus("Generando PDF de carnets, espera un momento...");
  const doc = new jsPDF({ orientation: "portrait", unit: "pt", format: "a4" });
  const pageW = doc.internal.pageSize.getWidth();
  const pageH = doc.internal.pageSize.getHeight();
  const cardW = 245;
  const cardH = 140;
  const cols = 2;
  const rowsPerPage = 4;
  const gapX = 18;
  const gapY = 14;
  const marginX = (pageW - (cols * cardW + gapX)) / 2;
  const marginY = 22;

  for (let i = 0; i < rows.length; i++) {
    const pagePos = i % (cols * rowsPerPage);
    if (i > 0 && pagePos === 0) {
      doc.addPage();
    }

    const col = pagePos % cols;
    const row = Math.floor(pagePos / cols);
    const x = marginX + col * (cardW + gapX);
    const y = marginY + row * (cardH + gapY);
    const s = rows[i];

    doc.setDrawColor(18, 26, 49);
    doc.setFillColor(255, 255, 255);
    doc.roundedRect(x, y, cardW, cardH, 8, 8, "FD");

    doc.setFillColor(18, 35, 92);
    doc.roundedRect(x + 4, y + 4, cardW - 8, 24, 6, 6, "F");
    doc.setTextColor(255, 255, 255);
    doc.setFontSize(10);
    doc.text(branding.schoolName, x + cardW / 2, y + 20, { align: "center" });

    doc.setTextColor(20, 20, 20);
    doc.setFontSize(9);
    doc.text(`DNI: ${s.dni}`, x + 10, y + 46);
    doc.text(`Alumno: ${s.nombres} ${s.apellidos}`.slice(0, 50), x + 10, y + 62);
    doc.text(`Grado/Seccion: ${s.grado}${s.seccion}`, x + 10, y + 78);
    doc.text(`Estado: ${s.status}`, x + 10, y + 94);
    doc.setTextColor(90, 90, 90);
    doc.text(`${branding.placeLabel} - ${new Date().getFullYear()}`, x + 10, y + 112);

    const qr = await studentQrDataUrl(s);
    doc.addImage(qr, "PNG", x + cardW - 88, y + 38, 72, 72);
  }

  doc.save(`carnets_filtrados_${todayIsoDate()}.pdf`);
  setStatus("Carnets PDF descargados.");
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

async function runAttendanceReport() {
  if (!requireAdmin()) {
    return;
  }
  try {
    const period = $("rp-period").value;
    const refDate = $("rp-ref-date").value || todayIsoDate();
    const startDate = $("rp-start-date").value;
    const endDate = $("rp-end-date").value;
    const bounds = periodBounds(period, refDate, startDate, endDate);

    const { data, error } = await supabase.rpc("report_attendance_filtered", {
      p_start_date: bounds.start,
      p_end_date: bounds.end,
      p_grado: $("rp-grado").value ? Number($("rp-grado").value) : null,
      p_seccion: $("rp-seccion").value.trim() || null,
      p_genero: $("rp-genero").value || null,
      p_cargo: $("rp-cargo").value.trim() || null,
      p_condition: $("rp-condition").value,
    });

    if (error) {
      setStatus(`Error generando reporte: ${error.message}`, false);
      return;
    }
    lastReportRows = data || [];
    renderReportTable();

    await supabase.rpc("save_report_history_web", {
      p_period: period,
      p_condition: $("rp-condition").value,
      p_ref_date: refDate || null,
      p_start_date: bounds.start,
      p_end_date: bounds.end,
      p_grado: $("rp-grado").value || null,
      p_seccion: $("rp-seccion").value.trim() || null,
      p_genero: $("rp-genero").value || null,
      p_cargo: $("rp-cargo").value.trim() || null,
      p_row_count: lastReportRows.length,
    });

    setStatus(`Reporte generado con ${lastReportRows.length} filas.`);
  } catch (err) {
    setStatus(String(err.message || err), false);
  }
}

function exportReportCsv() {
  if (!lastReportRows.length) {
    setStatus("Primero genera el reporte.", false);
    return;
  }
  const headers = ["fecha", "hora", "dni", "nombres", "apellidos", "grado", "seccion", "genero", "cargo", "profesor_encargado", "estado"];
  const lines = [headers.join(",")];
  for (const r of lastReportRows) {
    lines.push(headers.map((h) => csvEscape(r[h])).join(","));
  }
  const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  downloadDataUrl(url, `reporte_asistencia_${todayIsoDate()}.csv`);
  URL.revokeObjectURL(url);
  setStatus("Reporte CSV descargado.");
}

function exportReportXlsx() {
  if (!lastReportRows.length) {
    setStatus("Primero genera el reporte.", false);
    return;
  }
  const reportDate = todayIsoDate();
  const meta = [
    [branding.schoolName],
    [`Generado: ${reportDate}`],
    [""],
  ];
  const ws = XLSX.utils.aoa_to_sheet(meta);
  XLSX.utils.sheet_add_json(ws, lastReportRows, { origin: "A4", skipHeader: false });
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, "Reporte");
  XLSX.writeFile(wb, `reporte_asistencia_${todayIsoDate()}.xlsx`);
  setStatus("Reporte XLSX descargado.");
}

function exportReportPdf() {
  if (!lastReportRows.length) {
    setStatus("Primero genera el reporte.", false);
    return;
  }
  const doc = new jsPDF({ orientation: "landscape", unit: "pt", format: "a4" });
  doc.setFontSize(12);
  doc.text(`${branding.schoolName} - Reporte Asistencia`, 36, 24);
  doc.setFontSize(10);
  doc.text(`Generado: ${todayIsoDate()} | Sede: ${branding.placeLabel}`, 36, 40);
  const head = [["Fecha", "Hora", "DNI", "Nombres", "Apellidos", "Grado", "Sec", "Genero", "Cargo", "Profesor", "Estado"]];
  const body = lastReportRows.map((r) => [
    r.fecha || "",
    r.hora || "",
    r.dni || "",
    r.nombres || "",
    r.apellidos || "",
    r.grado || "",
    r.seccion || "",
    r.genero || "",
    r.cargo || "",
    r.profesor_encargado || "",
    r.estado || "",
  ]);
  autoTable(doc, {
    head,
    body,
    startY: 52,
    styles: { fontSize: 8 },
    headStyles: { fillColor: [24, 58, 126] },
  });
  doc.save(`reporte_asistencia_${todayIsoDate()}.pdf`);
  setStatus("Reporte PDF descargado.");
}

async function bootstrapAuth() {
  const auth = await supabase.auth.getSession();
  currentSession = auth.data.session;
  updateUiByAuth();
  if (isLoggedIn()) {
    await loadTodayAttendance();
    if (isAdmin()) {
      await loadBrandingSettings();
      await loadGlobalSchedule();
      await loadOverrides();
      await loadStudentsAdmin();
      const t = todayIsoDate();
      $("rp-ref-date").value = t;
      $("rp-start-date").value = t;
      $("rp-end-date").value = t;
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
$("btn-save-branding").addEventListener("click", saveBrandingSettings);
$("btn-refresh-students").addEventListener("click", loadStudentsAdmin);
$("btn-save-student").addEventListener("click", saveStudent);
$("btn-clear-student").addEventListener("click", clearStudentForm);
$("btn-download-selected-qr").addEventListener("click", downloadSelectedQr);
$("btn-print-card").addEventListener("click", printSelectedCard);
$("btn-print-cards-bulk").addEventListener("click", printBulkCardsPdf);
$("btn-export-students-csv").addEventListener("click", exportStudentsCsv);
$("student-search").addEventListener("input", renderStudentsTable);
$("btn-import-students").addEventListener("click", importStudentsFile);
$("btn-run-report").addEventListener("click", runAttendanceReport);
$("btn-export-report-csv").addEventListener("click", exportReportCsv);
$("btn-export-report-xlsx").addEventListener("click", exportReportXlsx);
$("btn-export-report-pdf").addEventListener("click", exportReportPdf);
$("btn-start-scan").addEventListener("click", startScanner);
$("btn-stop-scan").addEventListener("click", stopScanner);

supabase.auth.onAuthStateChange((_event, session) => {
  currentSession = session;
  updateUiByAuth();
});

bootstrapAuth();
