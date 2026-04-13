import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import QRCode from "https://esm.sh/qrcode@1.5.4";
import * as XLSX from "https://esm.sh/xlsx@0.18.5";
import { jsPDF } from "https://esm.sh/jspdf@2.5.1";
import autoTable from "https://esm.sh/jspdf-autotable@3.8.2";
import { ADMIN_EMAILS, AUTH_DNI_DOMAIN, PLATFORM_LOGIN_BRANDING, SUPABASE_PUBLISHABLE_KEY, SUPABASE_URL } from "./config.js";

const supabase = createClient(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY);
const BRANDING_BUCKET = "branding-assets";
const FALLBACK_LOGO_SVG =
  "data:image/svg+xml;utf8," +
  encodeURIComponent(
    `<svg xmlns='http://www.w3.org/2000/svg' width='256' height='256' viewBox='0 0 256 256'>
      <rect width='256' height='256' rx='30' fill='#102348'/>
      <rect x='10' y='10' width='236' height='236' rx='24' fill='none' stroke='#5e81c9' stroke-width='4'/>
      <text x='50%' y='46%' dominant-baseline='middle' text-anchor='middle' fill='#dbeafe' font-size='64' font-family='Segoe UI' font-weight='700'>IE</text>
      <text x='50%' y='66%' dominant-baseline='middle' text-anchor='middle' fill='#a5b4fc' font-size='22' font-family='Segoe UI'>LOGO</text>
    </svg>`
  );

const $ = (id) => document.getElementById(id);
const statusEl = $("status");
const globalStatusEl = $("global-status");
const todayBody = $("today-body");
const overrideBody = $("override-body");
const studentsAdminBody = $("students-admin-body");
const reportBody = $("report-body");
const scanStatusEl = $("scan-status");
const sectionsBody = $("sections-body");
const dashboardRecentBody = $("dashboard-recent-body");
const loginScreen = $("login-screen");
const appShell = $("app-shell");
const navButtons = Array.from(document.querySelectorAll(".nav-btn"));
const moduleViews = Array.from(document.querySelectorAll(".module-view"));

let currentSession = null;
let studentsCache = [];
let selectedStudentId = null;
let lastReportRows = [];
let branding = {
  schoolName: PLATFORM_LOGIN_BRANDING?.title || "IE Asistencia",
  placeLabel: PLATFORM_LOGIN_BRANDING?.placeLabel || "LLATA",
  panoramicUrl: PLATFORM_LOGIN_BRANDING?.panoramicUrl || "",
  insigniaUrl: PLATFORM_LOGIN_BRANDING?.insigniaUrl || "",
};
let sectionsCache = [];
let lastImportErrors = [];
let scannerInstance = null;
let scannerRunning = false;
let lastScanAt = 0;
let lastScannedText = "";
let globalStatusTimer = null;
let qrCenterLogoDataUrlCache = null;
let calendarMonthDate = new Date();
let overrideDateSet = new Set();
let docentesCache = [];
let assignableStudentsCache = [];
let selectedDocenteUserId = "";
let currentAssignmentIds = new Set();
let currentUserProfile = {
  role: null,
  mustChangePassword: false,
  isActive: true,
  dni: null,
};

function showLoginOnly() {
  loginScreen.classList.remove("hidden");
  appShell.classList.add("hidden");
}

function showAppShell() {
  loginScreen.classList.add("hidden");
  appShell.classList.remove("hidden");
}

function setTopbarUser() {
  const emailNode = $("top-user-email");
  const initialNode = $("top-user-initial");
  const email = String(currentSession?.user?.email || "").trim();
  if (!emailNode || !initialNode) {
    return;
  }
  emailNode.textContent = email;
  initialNode.textContent = email ? email.charAt(0).toUpperCase() : "U";
}

function closeTopUserMenu() {
  const menu = $("top-user-menu");
  if (!menu) {
    return;
  }
  menu.classList.remove("is-open");
}

function applyModuleVisibilityByRole() {
  const admin = isAdmin();
  const student = isStudent();
  navButtons.forEach((btn) => {
    const isAdminOnly = btn.dataset.admin === "1";
    btn.classList.toggle("hidden", isAdminOnly && !admin);
    if (btn.dataset.view === "asistencia-view") {
      btn.textContent = student ? "Mi asistencia" : "Asistencia";
    }
  });
  moduleViews.forEach((view) => {
    const isAdminOnly = view.dataset.adminView === "1";
    if (isAdminOnly && !admin) {
      view.classList.add("hidden");
    }
  });
}

function activateView(viewId) {
  const target = viewId || "dashboard-view";
  moduleViews.forEach((view) => {
    view.classList.toggle("hidden", view.id !== target);
  });
  navButtons.forEach((btn) => {
    btn.classList.toggle("is-active", btn.dataset.view === target);
  });
}

async function refreshDashboardKpis(records = []) {
  if (!isLoggedIn()) {
    return;
  }
  const totalNode = $("stat-total");
  const attendanceNode = $("stat-attendance");
  const sectionsNode = $("stat-sections");
  const lastNode = $("stat-last");
  if (!totalNode || !attendanceNode || !sectionsNode || !lastNode) {
    return;
  }

  const [studentsRes, sectionsRes] = await Promise.all([
    supabase.from("estudiantes").select("dni", { count: "exact", head: true }).eq("activo", true),
    supabase.from("v_sections_admin").select("grado,seccion,activo").eq("activo", true),
  ]);

  const total = Number(studentsRes.count || 0);
  const presentUnique = new Set((records || []).map((r) => String(r.dni || "").trim()).filter(Boolean)).size;
  const ratio = total > 0 ? Math.round((presentUnique / total) * 100) : 0;
  const last = records?.[0]?.hora ? `Hoy ${records[0].hora}` : "-";
  const activeSections = Array.isArray(sectionsRes.data) ? sectionsRes.data.length : 0;

  totalNode.textContent = String(total);
  attendanceNode.textContent = `${ratio}%`;
  sectionsNode.textContent = String(activeSections);
  lastNode.textContent = last;
}

function isAdminEmail(email) {
  const e = String(email || "").trim().toLowerCase();
  return ADMIN_EMAILS.map((item) => String(item || "").trim().toLowerCase()).includes(e);
}

function isLoggedIn() {
  return !!currentSession?.user;
}

function isAdmin() {
  return currentUserProfile.role === "admin_tic" || isAdminEmail(currentSession?.user?.email);
}

function isTeacher() {
  return currentUserProfile.role === "docente";
}

function isStudent() {
  return currentUserProfile.role === "alumno";
}

function canTakeAttendance() {
  return isAdmin() || isTeacher();
}

function setAuthBadge() {
  const badge = $("auth-badge");
  if (!isLoggedIn()) {
    badge.textContent = "No autenticado";
    return;
  }
  const role = isAdmin() ? "Admin TIC" : isTeacher() ? "Docente" : isStudent() ? "Alumno" : "Operador";
  badge.textContent = `Sesion: ${currentSession.user.email} (${role})`;
}

function updateUiByAuth() {
  setAuthBadge();
  setTopbarUser();
  const canMark = isLoggedIn() && canTakeAttendance() && !currentUserProfile.mustChangePassword;
  const markActions = $("attendance-actions");
  const teacherWrap = $("attendance-teacher-wrap");
  const tokenWrap = $("attendance-token-wrap");

  $("btn-mark").disabled = !isLoggedIn() || !canTakeAttendance() || currentUserProfile.mustChangePassword;
  $("btn-refresh").disabled = !isLoggedIn();
  $("btn-start-scan").disabled = !isLoggedIn() || !canTakeAttendance() || currentUserProfile.mustChangePassword;
  $("btn-stop-scan").disabled = !isLoggedIn() || !canTakeAttendance();
  $("teacher").disabled = !isLoggedIn() || !canTakeAttendance();
  $("qr-token").disabled = !isLoggedIn() || !canTakeAttendance();
  if (markActions) {
    markActions.classList.toggle("hidden", !canMark);
  }
  if (teacherWrap) {
    teacherWrap.classList.toggle("hidden", !canMark);
  }
  if (tokenWrap) {
    tokenWrap.classList.toggle("hidden", !canMark);
  }
  if (!canMark) {
    stopScanner();
    setScanStatus("Modo solo lectura de asistencia.");
  }
  $("admin-panel").classList.toggle("hidden", !isAdmin());
  applyModuleVisibilityByRole();
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

  if (globalStatusEl) {
    globalStatusEl.textContent = text;
    globalStatusEl.classList.remove("hidden", "ok", "bad", "show");
    globalStatusEl.classList.add(ok ? "ok" : "bad");

    if (globalStatusTimer) {
      clearTimeout(globalStatusTimer);
    }

    requestAnimationFrame(() => {
      globalStatusEl.classList.add("show");
    });

    globalStatusTimer = setTimeout(() => {
      globalStatusEl.classList.remove("show");
      setTimeout(() => {
        globalStatusEl.classList.add("hidden");
      }, 220);
    }, 3000);
  }
}

function setScanStatus(text) {
  scanStatusEl.textContent = text;
}

function safeImageUrl(url) {
  const value = String(url || "").trim();
  return value || "";
}

function normalizeLoginIdentifier(input) {
  const raw = String(input || "").trim();
  if (!raw) {
    return "";
  }
  const digits = raw.replace(/\D/g, "");
  if (digits.length === 8 && raw === digits) {
    return `${digits}@${AUTH_DNI_DOMAIN}`;
  }
  return raw.toLowerCase();
}

function setPasswordGateVisible(visible, message = "") {
  const gate = $("password-gate");
  const msg = $("password-gate-msg");
  if (!gate || !msg) {
    return;
  }
  gate.classList.toggle("hidden", !visible);
  msg.textContent = message;
}

async function loadMyProfile() {
  if (!isLoggedIn()) {
    currentUserProfile = { role: null, mustChangePassword: false, isActive: true, dni: null };
    return;
  }
  const { data, error } = await supabase.rpc("get_my_profile");
  if (error) {
    setStatus(`No se pudo cargar perfil: ${error.message}`, false);
    currentUserProfile = { role: null, mustChangePassword: false, isActive: true, dni: null };
    return;
  }
  const row = Array.isArray(data) ? data[0] : data;
  currentUserProfile = {
    role: row?.role || null,
    mustChangePassword: !!row?.must_change_password,
    isActive: row?.is_active !== false,
    dni: row?.dni || null,
  };
}

async function changeInitialPassword() {
  if (!requireLogin()) {
    return;
  }
  const nextPass = String($("new-password")?.value || "");
  const confirmPass = String($("confirm-password")?.value || "");
  if (nextPass.length < 8) {
    setPasswordGateVisible(true, "La nueva clave debe tener minimo 8 caracteres.");
    return;
  }
  if (nextPass !== confirmPass) {
    setPasswordGateVisible(true, "Las claves no coinciden.");
    return;
  }

  const { error } = await supabase.auth.updateUser({ password: nextPass });
  if (error) {
    setPasswordGateVisible(true, `No se pudo actualizar clave: ${error.message}`);
    return;
  }

  const { error: markError } = await supabase.rpc("complete_initial_password_change");
  if (markError) {
    setPasswordGateVisible(true, `Clave actualizada, pero no se pudo cerrar primer acceso: ${markError.message}`);
    return;
  }

  currentUserProfile.mustChangePassword = false;
  setPasswordGateVisible(false, "");
  if ($("new-password")) {
    $("new-password").value = "";
  }
  if ($("confirm-password")) {
    $("confirm-password").value = "";
  }
  updateUiByAuth();
  setStatus("Clave personal actualizada.");
}

function applyPlatformLoginBranding() {
  const title = String(PLATFORM_LOGIN_BRANDING?.title || "Plataforma de Asistencia").trim();
  const subtitle = String(PLATFORM_LOGIN_BRANDING?.subtitle || "Inicia sesion para ingresar.").trim();
  const insigniaUrl = safeImageUrl(PLATFORM_LOGIN_BRANDING?.insigniaUrl);
  const panoramicUrl = safeImageUrl(PLATFORM_LOGIN_BRANDING?.panoramicUrl);

  $("brand-title").textContent = title;
  $("hero-school-name").textContent = title;
  $("hero-place-label").textContent = subtitle;

  const hero = $("brand-hero");
  hero.style.backgroundImage = panoramicUrl
    ? `linear-gradient(118deg, rgba(18, 30, 48, 0.68), rgba(10, 16, 26, 0.5)), url('${panoramicUrl.replace(/'/g, "%27")}')`
    : "linear-gradient(128deg, #1a2a45 0%, #132033 44%, #101823 100%)";

  const insignia = $("hero-insignia");
  attachLogoFallback(insignia, "LOGIN");
  insignia.src = insigniaUrl || FALLBACK_LOGO_SVG;
  insignia.style.visibility = "visible";
}

function makePlaceholderDataUrl(text = "LOGO") {
  const t = String(text || "LOGO").slice(0, 10).toUpperCase();
  return (
    "data:image/svg+xml;utf8," +
    encodeURIComponent(
      `<svg xmlns='http://www.w3.org/2000/svg' width='256' height='256' viewBox='0 0 256 256'>
        <rect width='256' height='256' rx='24' fill='#0f1f44'/>
        <rect x='10' y='10' width='236' height='236' rx='20' fill='none' stroke='#5b7ec9' stroke-width='3'/>
        <text x='50%' y='54%' dominant-baseline='middle' text-anchor='middle' fill='#e2e8f0' font-size='30' font-family='Segoe UI'>${t}</text>
      </svg>`
    )
  );
}

function attachLogoFallback(imgEl, fallbackText) {
  if (!imgEl) {
    return;
  }
  const fb = makePlaceholderDataUrl(fallbackText);
  imgEl.onerror = () => {
    imgEl.onerror = null;
    imgEl.src = fb;
    imgEl.style.visibility = "visible";
  };
}

async function loadImageElementFromFile(file) {
  return await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => reject(new Error("Imagen invalida"));
      img.src = String(reader.result || "");
    };
    reader.onerror = () => reject(new Error("No se pudo leer el archivo"));
    reader.readAsDataURL(file);
  });
}

async function normalizeBrandingImage(file, kind) {
  const cfg =
    kind === "panoramic"
      ? { maxW: 1920, maxH: 720, mime: "image/jpeg", quality: 0.9, ext: "jpg", fill: "#0f1f44" }
      : { maxW: 512, maxH: 512, mime: "image/png", quality: 0.92, ext: "png", fill: null };

  const img = await loadImageElementFromFile(file);
  const scale = Math.min(1, cfg.maxW / img.width, cfg.maxH / img.height);
  const targetW = Math.max(1, Math.round(img.width * scale));
  const targetH = Math.max(1, Math.round(img.height * scale));

  const canvas = document.createElement("canvas");
  canvas.width = targetW;
  canvas.height = targetH;
  const ctx = canvas.getContext("2d");
  if (!ctx) {
    throw new Error("No se pudo procesar imagen en navegador");
  }

  if (cfg.fill) {
    ctx.fillStyle = cfg.fill;
    ctx.fillRect(0, 0, targetW, targetH);
  }
  ctx.drawImage(img, 0, 0, targetW, targetH);

  const blob = await new Promise((resolve) => canvas.toBlob(resolve, cfg.mime, cfg.quality));
  if (!blob) {
    throw new Error("No se pudo normalizar imagen");
  }
  return { blob, ext: cfg.ext, width: targetW, height: targetH };
}

function applyBrandingToUi() {
  qrCenterLogoDataUrlCache = null;
  const schoolName = branding.schoolName || "IE Asistencia";
  const place = branding.placeLabel || "Huanuco";

  $("brand-title").textContent = schoolName;
  $("hero-school-name").textContent = schoolName;
  $("hero-place-label").textContent = `Sede: ${place}`;

  const hero = $("brand-hero");
  const pano = safeImageUrl(branding.panoramicUrl);
  hero.style.backgroundImage = pano
    ? `linear-gradient(118deg, rgba(18, 30, 48, 0.68), rgba(10, 16, 26, 0.5)), url('${pano.replace(/'/g, "%27")}')`
    : "linear-gradient(128deg, #1a2a45 0%, #132033 44%, #101823 100%)";

  const insignia = $("hero-insignia");
  const insUrl = safeImageUrl(branding.insigniaUrl);

  attachLogoFallback(insignia, "INSIGNIA");

  insignia.src = insUrl || FALLBACK_LOGO_SVG;
  insignia.style.visibility = "visible";

  const dashboardInsignia = $("dashboard-insignia");
  const dashboardTitle = $("dashboard-title");
  const dashboardSubtitle = $("dashboard-subtitle");
  if (dashboardInsignia) {
    attachLogoFallback(dashboardInsignia, "INSIGNIA");
    dashboardInsignia.src = insUrl || FALLBACK_LOGO_SVG;
  }
  if (dashboardTitle) {
    dashboardTitle.textContent = schoolName;
  }
  if (dashboardSubtitle) {
    dashboardSubtitle.textContent = `Sede: ${place}`;
  }
}

async function urlToDataUrl(url) {
  const clean = safeImageUrl(url);
  if (!clean) {
    return null;
  }
  try {
    const res = await fetch(clean, { mode: "cors" });
    if (!res.ok) {
      return null;
    }
    const blob = await res.blob();
    return await new Promise((resolve) => {
      const reader = new FileReader();
      reader.onloadend = () => resolve(reader.result);
      reader.readAsDataURL(blob);
    });
  } catch {
    return null;
  }
}

async function uploadBrandingAsset(file, kind) {
  if (!file) {
    return null;
  }
  const normalized = await normalizeBrandingImage(file, kind);
  const safeExt = normalized.ext;
  const uid = currentSession?.user?.id || "public";
  const filePath = `${uid}/${kind}_${Date.now()}.${safeExt}`;
  const { error } = await supabase.storage
    .from(BRANDING_BUCKET)
    .upload(filePath, normalized.blob, {
      upsert: true,
      contentType: normalized.blob.type || undefined,
      cacheControl: "3600",
    });
  if (error) {
    throw new Error(error.message);
  }
  const { data } = supabase.storage.from(BRANDING_BUCKET).getPublicUrl(filePath);
  return data.publicUrl;
}

function todayIsoDate() {
  return new Date().toISOString().slice(0, 10);
}

function formatMonthLabel(d) {
  return d.toLocaleDateString("es-PE", { month: "long", year: "numeric" });
}

function renderOverrideCalendar() {
  const grid = $("override-calendar");
  const title = $("calendar-title");
  if (!grid || !title) {
    return;
  }

  const current = new Date(calendarMonthDate.getFullYear(), calendarMonthDate.getMonth(), 1);
  title.textContent = formatMonthLabel(current).toUpperCase();

  const selected = $("override-date").value;
  const today = todayIsoDate();
  const weekdayHeads = ["L", "M", "X", "J", "V", "S", "D"];

  grid.innerHTML = "";
  for (const name of weekdayHeads) {
    const h = document.createElement("div");
    h.className = "cal-cell head";
    h.textContent = name;
    grid.appendChild(h);
  }

  const firstWeekday = (current.getDay() + 6) % 7;
  const daysInMonth = new Date(current.getFullYear(), current.getMonth() + 1, 0).getDate();
  const prevMonthDays = new Date(current.getFullYear(), current.getMonth(), 0).getDate();

  for (let i = 0; i < 42; i++) {
    const cell = document.createElement("div");
    cell.className = "cal-cell day";

    let dayNum = 0;
    let cellDate = null;
    if (i < firstWeekday) {
      dayNum = prevMonthDays - firstWeekday + i + 1;
      cell.classList.add("dim");
      cellDate = new Date(current.getFullYear(), current.getMonth() - 1, dayNum);
    } else if (i >= firstWeekday + daysInMonth) {
      dayNum = i - (firstWeekday + daysInMonth) + 1;
      cell.classList.add("dim");
      cellDate = new Date(current.getFullYear(), current.getMonth() + 1, dayNum);
    } else {
      dayNum = i - firstWeekday + 1;
      cellDate = new Date(current.getFullYear(), current.getMonth(), dayNum);
    }

    const iso = toIsoDate(cellDate);
    cell.textContent = String(dayNum);
    if (iso === today) {
      cell.classList.add("today");
    }
    if (iso === selected) {
      cell.classList.add("active");
    }
    if (overrideDateSet.has(iso)) {
      cell.classList.add("has-override");
      cell.title = "Tiene horario especial";
    }

    cell.addEventListener("click", () => {
      $("override-date").value = iso;
      renderOverrideCalendar();
    });
    grid.appendChild(cell);
  }
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
  const email = normalizeLoginIdentifier($("email").value);
  const password = $("password").value;
  if (!email || !password) {
    setStatus("Completa usuario y clave.", false);
    return;
  }

  const { error } = await supabase.auth.signInWithPassword({ email, password });
  if (error) {
    setStatus(`Login fallido: ${error.message}`, false);
    return;
  }
  const auth = await supabase.auth.getSession();
  currentSession = auth.data.session;
  await loadMyProfile();
  showAppShell();
  updateUiByAuth();
  if (!currentUserProfile.isActive) {
    await signOut();
    setStatus("Tu cuenta esta inactiva. Contacta al admin TIC.", false);
    return;
  }
  setPasswordGateVisible(currentUserProfile.mustChangePassword, currentUserProfile.mustChangePassword ? "Debes cambiar tu clave para continuar." : "");
  setStatus("Login exitoso.");
  applyBrandingToUi();
  await loadTodayAttendance();
  if (isAdmin()) {
    await loadSectionsAdmin();
    await loadAssignmentAdminData();
    await loadGlobalSchedule();
    await loadOverrides();
    await loadStudentsAdmin();
    const t = todayIsoDate();
    $("rp-ref-date").value = t;
    $("rp-start-date").value = t;
    $("rp-end-date").value = t;
  }
}

async function signOut() {
  const { error } = await supabase.auth.signOut();
  if (error) {
    setStatus(`Error cerrando sesion: ${error.message}`, false);
    return;
  }
  currentSession = null;
  currentUserProfile = { role: null, mustChangePassword: false, isActive: true, dni: null };
  showLoginOnly();
  applyPlatformLoginBranding();
  setPasswordGateVisible(false, "");
  updateUiByAuth();
  todayBody.innerHTML = "";
  overrideBody.innerHTML = "";
  studentsAdminBody.innerHTML = "";
  if (dashboardRecentBody) {
    dashboardRecentBody.innerHTML = "";
  }
  await stopScanner();
  closeTopUserMenu();
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
  if (dashboardRecentBody) {
    dashboardRecentBody.innerHTML = "";
  }
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
    if (dashboardRecentBody) {
      dashboardRecentBody.appendChild(tr.cloneNode(true));
    }
  }

  await refreshDashboardKpis(data || []);
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

function parseBirthDateCell(value) {
  if (value === null || value === undefined || value === "") {
    return null;
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    const parsed = XLSX.SSF.parse_date_code(value);
    if (parsed && parsed.y && parsed.m && parsed.d) {
      const mm = String(parsed.m).padStart(2, "0");
      const dd = String(parsed.d).padStart(2, "0");
      return `${parsed.y}-${mm}-${dd}`;
    }
    return null;
  }
  const text = String(value).trim();
  if (!text) {
    return null;
  }
  const date = new Date(text);
  if (!Number.isNaN(date.getTime())) {
    return toIsoDate(date);
  }
  return text;
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
  overrideDateSet = new Set((data || []).map((r) => String(r.day)));
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
  renderOverrideCalendar();
}

async function loadSectionsAdmin() {
  if (!requireAdmin()) {
    return;
  }
  const { data, error } = await supabase
    .from("v_sections_admin")
    .select("grado,seccion,activo")
    .order("grado", { ascending: true })
    .order("seccion", { ascending: true });

  if (error) {
    setStatus(`Error cargando secciones: ${error.message}`, false);
    return;
  }
  sectionsCache = data || [];
  renderSectionsTable();
}

function renderSectionsTable() {
  sectionsBody.innerHTML = "";
  for (const row of sectionsCache) {
    const tr = document.createElement("tr");
    const btnId = `sec-toggle-${row.grado}-${row.seccion}`;
    tr.innerHTML = `
      <td>${row.grado}</td>
      <td>${row.seccion}</td>
      <td>${row.activo ? "ACTIVA" : "INACTIVA"}</td>
      <td><button id="${btnId}" class="ghost" type="button">${row.activo ? "Desactivar" : "Activar"}</button></td>
    `;
    sectionsBody.appendChild(tr);
    tr.querySelector(`#${btnId}`)?.addEventListener("click", async () => {
      await toggleSection(row.grado, row.seccion, !row.activo);
    });
  }
}

async function saveSection() {
  if (!requireAdmin()) {
    return;
  }
  const grado = Number($("sec-grado").value);
  const seccion = $("sec-seccion").value.trim().toUpperCase();
  const activo = $("sec-activo").value === "1";
  if (!grado || !seccion) {
    setStatus("Completa grado y seccion.", false);
    return;
  }
  const { error } = await supabase.rpc("upsert_section_admin", {
    p_grado: grado,
    p_seccion: seccion,
    p_activo: activo,
  });
  if (error) {
    setStatus(`Error guardando seccion: ${error.message}`, false);
    return;
  }
  setStatus("Seccion guardada.");
  await loadSectionsAdmin();
}

async function toggleSection(grado, seccion, activo) {
  if (!requireAdmin()) {
    return;
  }
  const { error } = await supabase.rpc("set_section_active", {
    p_grado: grado,
    p_seccion: seccion,
    p_activo: activo,
  });
  if (error) {
    setStatus(`Error actualizando seccion: ${error.message}`, false);
    return;
  }
  setStatus("Estado de seccion actualizado.");
  await loadSectionsAdmin();
}

function renderDocentesSelect() {
  const select = $("assign-teacher");
  if (!select) {
    return;
  }
  select.innerHTML = "";

  const empty = document.createElement("option");
  empty.value = "";
  empty.textContent = docentesCache.length ? "Selecciona docente" : "Sin docentes";
  select.appendChild(empty);

  for (const d of docentesCache) {
    const opt = document.createElement("option");
    opt.value = d.user_id;
    const label = `${d.display_name || d.email || d.dni || "Docente"} ${d.dni ? `(${d.dni})` : ""}`.trim();
    opt.textContent = d.is_active ? label : `${label} [INACTIVO]`;
    select.appendChild(opt);
  }

  if (selectedDocenteUserId) {
    select.value = selectedDocenteUserId;
  }
}

function renderAssignStudentsTable() {
  const body = $("assign-students-body");
  if (!body) {
    return;
  }
  body.innerHTML = "";
  for (const s of assignableStudentsCache) {
    const tr = document.createElement("tr");
    const checked = currentAssignmentIds.has(s.estudiante_id) ? "checked" : "";
    const checkboxId = `assign-st-${s.estudiante_id}`;
    tr.innerHTML = `
      <td><input id="${checkboxId}" type="checkbox" ${checked} /></td>
      <td>${s.dni || ""}</td>
      <td>${s.apellidos || ""}, ${s.nombres || ""}</td>
      <td>${s.grado || ""}${s.seccion || ""}</td>
      <td>${s.status || ""}</td>
    `;
    body.appendChild(tr);
    tr.querySelector(`#${checkboxId}`)?.addEventListener("change", (ev) => {
      if (ev.target.checked) {
        currentAssignmentIds.add(s.estudiante_id);
      } else {
        currentAssignmentIds.delete(s.estudiante_id);
      }
    });
  }
}

async function loadSelectedDocenteAssignments() {
  const summary = $("assign-summary");
  const select = $("assign-teacher");
  if (!summary || !select) {
    return;
  }
  selectedDocenteUserId = select.value || "";
  currentAssignmentIds = new Set();

  if (!selectedDocenteUserId) {
    summary.textContent = "Selecciona un docente para gestionar sus alumnos.";
    renderAssignStudentsTable();
    return;
  }

  const { data, error } = await supabase.rpc("list_docente_assignment_ids_admin", {
    p_docente_user_id: selectedDocenteUserId,
  });
  if (error) {
    setStatus(`Error cargando asignaciones: ${error.message}`, false);
    return;
  }

  currentAssignmentIds = new Set((data || []).map((r) => r.estudiante_id));
  const docente = docentesCache.find((d) => d.user_id === selectedDocenteUserId);
  summary.textContent = `Docente: ${docente?.display_name || docente?.email || "-"} | Asignados: ${currentAssignmentIds.size}`;
  renderAssignStudentsTable();
}

async function loadAssignmentAdminData() {
  if (!requireAdmin()) {
    return;
  }

  const [{ data: docentesData, error: docentesErr }, { data: studentsData, error: studentsErr }] = await Promise.all([
    supabase.rpc("list_docentes_admin"),
    supabase.rpc("list_assignable_students_admin"),
  ]);

  if (docentesErr) {
    setStatus(`Error cargando docentes: ${docentesErr.message}`, false);
    return;
  }
  if (studentsErr) {
    setStatus(`Error cargando alumnos asignables: ${studentsErr.message}`, false);
    return;
  }

  docentesCache = docentesData || [];
  assignableStudentsCache = studentsData || [];

  if (!selectedDocenteUserId && docentesCache.length) {
    selectedDocenteUserId = docentesCache[0].user_id;
  }

  renderDocentesSelect();
  await loadSelectedDocenteAssignments();
}

async function saveDocenteAssignments() {
  if (!requireAdmin()) {
    return;
  }

  const docenteId = $("assign-teacher")?.value || "";
  if (!docenteId) {
    setStatus("Selecciona un docente para guardar asignaciones.", false);
    return;
  }

  const studentIds = Array.from(currentAssignmentIds);
  const { error } = await supabase.rpc("assign_docente_estudiantes", {
    p_docente_user_id: docenteId,
    p_estudiante_ids: studentIds,
  });
  if (error) {
    setStatus(`Error guardando asignaciones: ${error.message}`, false);
    return;
  }

  setStatus("Asignaciones guardadas correctamente.");
  await loadSelectedDocenteAssignments();
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
    const msg = String(error.message || "");
    if (msg.toLowerCase().includes("gen_random_bytes")) {
      setStatus(
        "Falta aplicar el hotfix SQL de QR token (gen_random_bytes). Ejecuta la migracion 0016_hotfix_generate_qr_token_search_path.sql y vuelve a intentar.",
        false
      );
      return;
    }
    if (msg.toLowerCase().includes("seccion no activa") || msg.toLowerCase().includes("no registrada en catalogo")) {
      setStatus(
        "No se pudo guardar: la seccion no esta activa en el catalogo. Ve a Configuracion > Catalogo de secciones y activa/crea esa combinacion (grado-seccion).",
        false
      );
      return;
    }
    setStatus(`Error guardando alumno: ${error.message}`, false);
    return;
  }

  selectedStudentId = data || selectedStudentId;
  setStatus("Alumno guardado correctamente.");
  await loadStudentsAdmin();
}

async function importStudentsFile() {
    if (!sectionsCache.length) {
      await loadSectionsAdmin();
    }

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
  const activeSections = new Set(
    sectionsCache
      .filter((s) => s.activo)
      .map((s) => `${Number(s.grado)}-${String(s.seccion).toUpperCase()}`)
  );
  const seenInFile = new Set();
  let ok = 0;
  let fail = 0;
  lastImportErrors = [];

  for (let i = 0; i < rows.length; i++) {
    const row = rows[i];
    const rowNum = i + 2;
    const normalized = {};
    for (const [k, v] of Object.entries(row)) {
      normalized[String(k).trim().toLowerCase()] = v;
    }
    const dni = String(normalized.dni || "").replace(/\D/g, "");
    const nombres = String(normalized.nombres || "").trim();
    const apellidos = String(normalized.apellidos || "").trim();
    const grado = Number(normalized.grado || 0);
    const seccion = String(normalized.seccion || "").trim().toUpperCase();
    const genero = String(normalized.genero || "").trim().toUpperCase();
    const status = String(normalized.status || "ACTIVO").trim().toUpperCase();

    const validationErrors = [];
    if (!dni) {
      validationErrors.push("DNI vacio");
    } else if (dni.length !== 8) {
      validationErrors.push("DNI debe tener 8 digitos");
    }
    if (!nombres) {
      validationErrors.push("Nombres vacio");
    }
    if (!apellidos) {
      validationErrors.push("Apellidos vacio");
    }
    if (grado < 1 || grado > 5) {
      validationErrors.push("Grado invalido (1..5)");
    }
    if (!seccion) {
      validationErrors.push("Seccion vacia");
    } else if (!activeSections.has(`${grado}-${seccion}`)) {
      validationErrors.push("Seccion no activa/no registrada en catalogo");
    }
    if (!["M", "F"].includes(genero)) {
      validationErrors.push("Genero invalido (M/F)");
    }
    if (!["ACTIVO", "RETIRADO", "TRASLADADO"].includes(status)) {
      validationErrors.push("Status invalido");
    }
    if (dni && seenInFile.has(dni)) {
      validationErrors.push("DNI duplicado en archivo");
    }
    seenInFile.add(dni);

    if (validationErrors.length) {
      fail += 1;
      lastImportErrors.push({
        row: rowNum,
        dni,
        error: validationErrors.join("; "),
      });
      continue;
    }

    const existing = indexByDni.get(dni);
    const payload = {
      p_id: existing?.id || null,
      p_dni: dni,
      p_nombres: nombres,
      p_apellidos: apellidos,
      p_grado: grado,
      p_seccion: seccion,
      p_genero: genero,
      p_cargo: String(normalized.cargo || "Alumno").trim(),
      p_birth_date: parseBirthDateCell(normalized.birth_date),
      p_status: status,
      p_status_note: String(normalized.status_note || "").trim(),
    };

    const { error } = await supabase.rpc("upsert_student_admin", payload);
    if (error) {
      fail += 1;
      lastImportErrors.push({
        row: rowNum,
        dni,
        error: error.message,
      });
    } else {
      ok += 1;
    }
  }

  await loadStudentsAdmin();
  $("import-summary").textContent = `Resultado importacion: OK ${ok} | Fallidos ${fail}`;
  setStatus(`Importacion finalizada. OK: ${ok}, Fallidos: ${fail}.`, fail === 0);
}

function exportImportErrorsCsv() {
  if (!lastImportErrors.length) {
    setStatus("No hay errores de importacion para exportar.", false);
    return;
  }
  const headers = ["row", "dni", "error"];
  const lines = [headers.join(",")];
  for (const r of lastImportErrors) {
    lines.push([csvEscape(r.row), csvEscape(r.dni), csvEscape(r.error)].join(","));
  }
  const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  downloadDataUrl(url, `errores_importacion_${todayIsoDate()}.csv`);
  URL.revokeObjectURL(url);
  setStatus("CSV de errores descargado.");
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
  await loadStudentsAdmin();

  const updated = studentsCache.find((s) => s.id === studentId);
  if (!updated) {
    setStatus("QR regenerado correctamente.");
    return;
  }

  const dataUrl = await studentQrDataUrl(updated);
  await saveQrForStudent(updated, dataUrl, { fromRegenerate: true });
}

function getSelectedStudent() {
  if (!selectedStudentId) {
    return null;
  }
  return studentsCache.find((s) => s.id === selectedStudentId) || null;
}

async function studentQrDataUrl(student) {
  const payload = JSON.stringify({ t: student.qr_token || "" });
  const qrBaseDataUrl = await QRCode.toDataURL(payload, {
    width: 420,
    margin: 1,
    errorCorrectionLevel: "H",
  });

  const centerLogo = await getQrCenterLogoDataUrl();
  if (!centerLogo) {
    return qrBaseDataUrl;
  }

  try {
    return await mergeQrWithCenterLogo(qrBaseDataUrl, centerLogo);
  } catch {
    return qrBaseDataUrl;
  }
}

async function getQrCenterLogoDataUrl() {
  if (qrCenterLogoDataUrlCache) {
    return qrCenterLogoDataUrlCache;
  }

  const insigniaUrl = safeImageUrl(branding.insigniaUrl);
  if (!insigniaUrl) {
    return null;
  }

  const dataUrl = await urlToDataUrl(insigniaUrl);
  if (!dataUrl) {
    return null;
  }

  qrCenterLogoDataUrlCache = dataUrl;
  return dataUrl;
}

function loadImage(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("No se pudo cargar imagen."));
    img.src = src;
  });
}

async function mergeQrWithCenterLogo(qrDataUrl, logoDataUrl) {
  const [qrImg, logoImg] = await Promise.all([loadImage(qrDataUrl), loadImage(logoDataUrl)]);
  const size = Math.max(qrImg.naturalWidth || qrImg.width || 420, qrImg.naturalHeight || qrImg.height || 420);

  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  if (!ctx) {
    throw new Error("No se pudo crear contexto de canvas.");
  }

  ctx.drawImage(qrImg, 0, 0, size, size);

  const logoSize = Math.round(size * 0.22);
  const pad = Math.round(size * 0.02);
  const boxSize = logoSize + pad * 2;
  const boxX = Math.round((size - boxSize) / 2);
  const boxY = Math.round((size - boxSize) / 2);
  const radius = Math.round(boxSize * 0.16);

  ctx.fillStyle = "#ffffff";
  ctx.beginPath();
  ctx.moveTo(boxX + radius, boxY);
  ctx.lineTo(boxX + boxSize - radius, boxY);
  ctx.quadraticCurveTo(boxX + boxSize, boxY, boxX + boxSize, boxY + radius);
  ctx.lineTo(boxX + boxSize, boxY + boxSize - radius);
  ctx.quadraticCurveTo(boxX + boxSize, boxY + boxSize, boxX + boxSize - radius, boxY + boxSize);
  ctx.lineTo(boxX + radius, boxY + boxSize);
  ctx.quadraticCurveTo(boxX, boxY + boxSize, boxX, boxY + boxSize - radius);
  ctx.lineTo(boxX, boxY + radius);
  ctx.quadraticCurveTo(boxX, boxY, boxX + radius, boxY);
  ctx.closePath();
  ctx.fill();

  const logoX = Math.round((size - logoSize) / 2);
  const logoY = Math.round((size - logoSize) / 2);
  ctx.drawImage(logoImg, logoX, logoY, logoSize, logoSize);

  return canvas.toDataURL("image/png");
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
      {
        fps: 12,
        aspectRatio: 1.333334,
        // No qrbox: scan the full visible frame so the code does not need to be centered.
        disableFlip: false,
      },
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

async function saveDataUrlWithPicker(dataUrl, filename) {
  if (typeof window.showSaveFilePicker !== "function") {
    return { method: "download" };
  }

  try {
    const handle = await window.showSaveFilePicker({
      suggestedName: filename,
      types: [
        {
          description: "Imagen PNG",
          accept: { "image/png": [".png"] },
        },
      ],
    });

    const writable = await handle.createWritable();
    const blob = await (await fetch(dataUrl)).blob();
    await writable.write(blob);
    await writable.close();
    return { method: "picker", filename: handle.name || filename };
  } catch (err) {
    if (err?.name === "AbortError") {
      return { method: "cancelled" };
    }
    return { method: "download" };
  }
}

async function saveQrForStudent(student, dataUrl, options = {}) {
  const fileName = `qr_${student.dni}_${todayIsoDate()}.png`;
  const result = await saveDataUrlWithPicker(dataUrl, fileName);

  if (result.method === "picker") {
    setStatus(`QR guardado como ${result.filename}.`);
    return true;
  }

  if (result.method === "cancelled") {
    if (options.fromRegenerate) {
      setStatus("QR regenerado, pero cancelaste la ventana de guardado.", false);
    } else {
      setStatus("Guardado cancelado por el usuario.", false);
    }
    return false;
  }

  downloadDataUrl(dataUrl, fileName);
  setStatus("QR descargado en la carpeta Descargas de tu navegador.");
  return true;
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
  await saveQrForStudent(student, dataUrl);
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

  const insignia = safeImageUrl(branding.insigniaUrl);
  const insigniaSrc = insignia || makePlaceholderDataUrl("INSIGNIA");
  const insigniaHtml = `<img class="logo" src="${insigniaSrc}" alt="insignia" />`;
  w.document.write(`
    <html>
      <head>
        <title>Carnet ${student.dni}</title>
        <style>
          body { font-family: Arial, sans-serif; background: #f3f4f6; padding: 20px; }
          .card { width: 540px; border: 1px solid #111827; border-radius: 14px; background: white; padding: 16px; }
          .head { font-weight: 700; font-size: 24px; margin-bottom: 10px; }
          .logos { display: flex; justify-content: space-between; margin-bottom: 8px; }
          .logo { width: 52px; height: 52px; object-fit: contain; border: 1px solid #cbd5e1; border-radius: 8px; padding: 4px; }
          .muted { color: #4b5563; }
          .grid { display: grid; grid-template-columns: 1fr 170px; gap: 14px; align-items: center; }
          .line { margin: 6px 0; }
          img { width: 170px; height: 170px; }
        </style>
      </head>
      <body>
        <div class="card">
          <div class="logos">
            ${insigniaHtml}
          </div>
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
  const insigniaData = await urlToDataUrl(branding.insigniaUrl);

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

    if (insigniaData) {
      doc.addImage(insigniaData, "PNG", x + 10, y + 36, 24, 24);
    }

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

async function exportReportPdf() {
  if (!lastReportRows.length) {
    setStatus("Primero genera el reporte.", false);
    return;
  }

  const doc = new jsPDF({ orientation: "landscape", unit: "pt", format: "a4" });
  const generatedAt = new Date().toLocaleString("es-PE");
  const insigniaData = await urlToDataUrl(branding.insigniaUrl);

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
    startY: 68,
    theme: "grid",
    styles: { fontSize: 8, cellPadding: 4, lineColor: [215, 222, 240], lineWidth: 0.5 },
    headStyles: { fillColor: [20, 52, 116], textColor: 255, fontStyle: "bold" },
    alternateRowStyles: { fillColor: [247, 250, 255] },
    columnStyles: {
      0: { cellWidth: 62 },
      1: { cellWidth: 52 },
      2: { cellWidth: 58 },
      3: { cellWidth: 76 },
      4: { cellWidth: 86 },
      5: { cellWidth: 44 },
      6: { cellWidth: 34 },
      7: { cellWidth: 42 },
      8: { cellWidth: 72 },
      9: { cellWidth: 86 },
      10: { cellWidth: 60 },
    },
    margin: { left: 24, right: 24, top: 68, bottom: 28 },
    didDrawPage: (data) => {
      const pageW = doc.internal.pageSize.getWidth();
      const pageH = doc.internal.pageSize.getHeight();

      doc.setFillColor(20, 52, 116);
      doc.rect(0, 0, pageW, 52, "F");

      if (insigniaData) {
        doc.addImage(insigniaData, "PNG", 24, 9, 34, 34);
      }

      doc.setTextColor(255, 255, 255);
      doc.setFontSize(12);
      doc.text(`${branding.schoolName} - REPORTE DE ASISTENCIA`, pageW / 2, 22, { align: "center" });
      doc.setFontSize(9);
      doc.text(`Sede: ${branding.placeLabel} | Generado: ${generatedAt}`, pageW / 2, 38, { align: "center" });

      doc.setTextColor(80, 92, 120);
      doc.setFontSize(8);
      doc.text(`Pagina ${data.pageNumber}`, pageW - 24, pageH - 12, { align: "right" });
      doc.text("Sistema IE Asistencia", 24, pageH - 12);
    },
  });

  doc.save(`reporte_asistencia_${todayIsoDate()}.pdf`);
  if (!insigniaData) {
    setStatus("Reporte PDF descargado (sin insignia: revisa CORS de imagen o usa subida interna).", true);
    return;
  }
  setStatus("Reporte PDF descargado.");
}

async function bootstrapAuth() {
  const auth = await supabase.auth.getSession();
  currentSession = auth.data.session;
  if (isLoggedIn()) {
    showAppShell();
    await loadMyProfile();
  } else {
    showLoginOnly();
    applyPlatformLoginBranding();
  }
  updateUiByAuth();
  if (isLoggedIn()) {
    if (!currentUserProfile.isActive) {
      await signOut();
      setStatus("Tu cuenta esta inactiva. Contacta al admin TIC.", false);
      return;
    }
    setPasswordGateVisible(currentUserProfile.mustChangePassword, currentUserProfile.mustChangePassword ? "Debes cambiar tu clave para continuar." : "");
    applyBrandingToUi();
    await loadTodayAttendance();
    if (isAdmin()) {
      await loadGlobalSchedule();
      await loadOverrides();
      await loadSectionsAdmin();
      await loadAssignmentAdminData();
      await loadStudentsAdmin();
      const t = todayIsoDate();
      $("rp-ref-date").value = t;
      $("rp-start-date").value = t;
      $("rp-end-date").value = t;
    }
    activateView("dashboard-view");
  } else {
    setStatus("Inicia sesion para usar asistencia y admin.", false);
  }
  renderOverrideCalendar();
  if (isLoggedIn()) {
    applyBrandingToUi();
  } else {
    applyPlatformLoginBranding();
  }
}

for (const btn of navButtons) {
  btn.addEventListener("click", () => {
    const target = btn.dataset.view;
    const adminOnly = btn.dataset.admin === "1";
    if (adminOnly && !isAdmin()) {
      setStatus("No tienes permisos para ese modulo.", false);
      return;
    }
    activateView(target);
  });
}

$("btn-login").addEventListener("click", signIn);
$("btn-logout").addEventListener("click", signOut);
$("btn-mark").addEventListener("click", markAttendance);
$("btn-refresh").addEventListener("click", loadTodayAttendance);
$("btn-save-global").addEventListener("click", saveGlobalSchedule);
$("btn-save-override").addEventListener("click", saveOverride);
$("btn-refresh-overrides").addEventListener("click", loadOverrides);
$("btn-cal-prev").addEventListener("click", () => {
  calendarMonthDate = new Date(calendarMonthDate.getFullYear(), calendarMonthDate.getMonth() - 1, 1);
  renderOverrideCalendar();
});
$("btn-cal-next").addEventListener("click", () => {
  calendarMonthDate = new Date(calendarMonthDate.getFullYear(), calendarMonthDate.getMonth() + 1, 1);
  renderOverrideCalendar();
});
$("override-date").addEventListener("change", renderOverrideCalendar);
$("btn-save-section").addEventListener("click", saveSection);
$("btn-refresh-sections").addEventListener("click", loadSectionsAdmin);
$("btn-refresh-assign").addEventListener("click", loadAssignmentAdminData);
$("btn-save-assign").addEventListener("click", saveDocenteAssignments);
$("assign-teacher").addEventListener("change", loadSelectedDocenteAssignments);
$("btn-refresh-students").addEventListener("click", loadStudentsAdmin);
$("btn-save-student").addEventListener("click", saveStudent);
$("btn-clear-student").addEventListener("click", clearStudentForm);
$("btn-download-selected-qr").addEventListener("click", downloadSelectedQr);
$("btn-print-card").addEventListener("click", printSelectedCard);
$("btn-print-cards-bulk").addEventListener("click", printBulkCardsPdf);
$("btn-export-students-csv").addEventListener("click", exportStudentsCsv);
$("student-search").addEventListener("input", renderStudentsTable);
$("btn-import-students").addEventListener("click", importStudentsFile);
$("btn-export-import-errors").addEventListener("click", exportImportErrorsCsv);
$("btn-run-report").addEventListener("click", runAttendanceReport);
$("btn-export-report-csv").addEventListener("click", exportReportCsv);
$("btn-export-report-xlsx").addEventListener("click", exportReportXlsx);
$("btn-export-report-pdf").addEventListener("click", exportReportPdf);
$("btn-start-scan").addEventListener("click", startScanner);
$("btn-stop-scan").addEventListener("click", stopScanner);
$("btn-change-password").addEventListener("click", changeInitialPassword);

const topUserTrigger = $("top-user-trigger");
if (topUserTrigger) {
  topUserTrigger.addEventListener("click", (event) => {
    event.preventDefault();
    const menu = $("top-user-menu");
    if (!menu) {
      return;
    }
    menu.classList.toggle("is-open");
  });
}

document.addEventListener("click", (event) => {
  const menu = $("top-user-menu");
  if (!menu) {
    return;
  }
  if (!menu.contains(event.target)) {
    menu.classList.remove("is-open");
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeTopUserMenu();
  }
});

supabase.auth.onAuthStateChange((_event, session) => {
  currentSession = session;
  if (session?.user) {
    showAppShell();
    loadMyProfile().then(() => {
      updateUiByAuth();
      setPasswordGateVisible(currentUserProfile.mustChangePassword, currentUserProfile.mustChangePassword ? "Debes cambiar tu clave para continuar." : "");
    });
  } else {
    showLoginOnly();
    applyPlatformLoginBranding();
    currentUserProfile = { role: null, mustChangePassword: false, isActive: true, dni: null };
    setPasswordGateVisible(false, "");
    updateUiByAuth();
  }
  if (session?.user) {
    applyBrandingToUi();
    activateView("dashboard-view");
  }
});

bootstrapAuth();
