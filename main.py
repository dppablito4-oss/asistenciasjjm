from __future__ import annotations

import threading
import os
import tempfile
import zipfile
from datetime import date, datetime
from pathlib import Path
from tkinter import END, StringVar, filedialog, messagebox
import tkinter as tk
import ctypes

import customtkinter as ctk
import cv2
from PIL import Image, ImageTk

from database import (
    DEFAULT_ADMIN_PASSWORD,
    DB_PATH,
    add_student,
    add_section_catalog,
    bootstrap_database,
    create_database_backup,
    change_admin_password,
    generate_admin_recovery_codes,
    get_config,
    get_admin_quick_stats,
    get_report_history,
    get_attendance_cutoff,
    get_connection,
    is_default_admin_password,
    has_admin_password,
    get_setting,
    set_setting,
    get_student_by_dni,
    import_students_from_file,
    list_report_history,
    list_students_by_section,
    list_sections,
    list_students,
    mark_attendance,
    search_students,
    set_admin_password,
    set_admin_password_generic,
    set_attendance_schedule,
    set_student_active,
    reset_admin_password_with_recovery_code,
    save_report_history,
    update_student,
    verify_admin_password,
)
from id_cards import generate_id_cards_pdf
from qr_generator import generate_student_qr_image
from reports import export_report_to_excel, export_report_to_pdf, generate_report
from scanner import QRScanner

try:
    from tkcalendar import DateEntry
except Exception:
    DateEntry = None


COLOR_BG = "#000000"
COLOR_PANEL = "#0a0f1f"
COLOR_PANEL_2 = "#121a2b"
COLOR_ACCENT = "#2d63ff"
COLOR_ACCENT_2 = "#3b82f6"
COLOR_TEXT = "#dbeafe"
COLOR_MUTED = "#94a3b8"


def _build_ttk_dark_style() -> None:
    style = tk.ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure("Dark.Treeview", background="#0f172a", foreground="#dbeafe", fieldbackground="#0f172a", rowheight=28)
    style.map("Dark.Treeview", background=[("selected", "#1d4ed8")], foreground=[("selected", "#eff6ff")])
    style.configure("Dark.Treeview.Heading", background="#1e293b", foreground="#bfdbfe")
    style.configure("Dark.Vertical.TScrollbar", troughcolor="#0b1220", background="#334155")
    style.configure("Dark.Horizontal.TScrollbar", troughcolor="#0b1220", background="#334155")


class AttendanceApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Registro de Asistencia Escolar")
        self.geometry("1360x860")
        self.minsize(1180, 720)
        self.configure(fg_color=COLOR_BG)

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        _build_ttk_dark_style()

        icon_path = Path(__file__).resolve().parent / "logo.ico"
        if icon_path.exists():
            try:
                self.iconbitmap(str(icon_path))
            except Exception:
                pass

        bootstrap_database(DB_PATH)
        self._ensure_admin_password_setup()

        self._frame_lock = threading.Lock()
        self._latest_frame = None
        self._camera_photo = None
        self._last_report_df = None
        self._admin_unlocked = False
        self._manual_autosubmit_pending = False
        self._qr_preview_photo = None
        self._registro_search_cache = []
        self._qr_search_cache = []
        self._wheel_containers = []
        self._wheel_scroll_lines = self._get_system_wheel_lines()
        self._wheel_speed_multiplier = 4
        self._wheel_delta_buffer = 0.0
        self._sections_cache = []
        self._report_history_cache = []
        self._temp_cleanup_timers = []
        self._temp_report_ttl_sec = 600

        self.school_name_var = StringVar(value=get_setting("school_name", "Asistencia Escolar", DB_PATH))
        self.logo_path_var = StringVar(value=get_setting("logo_path", "", DB_PATH))
        self.insignia_path_var = StringVar(value=get_setting("logo_path", "", DB_PATH))
        self.panoramic_path_var = StringVar(value=get_setting("panoramic_path", "", DB_PATH))
        self.minedu_logo_path_var = StringVar(value=get_setting("minedu_logo_path", "", DB_PATH))
        self._brand_photo_banner = None
        self._brand_photo_left = None
        self._brand_photo_right = None

        self.scanner = QRScanner(
            on_detect=self._on_scanner_detect,
            on_frame=self._on_scanner_frame,
            on_error=self._on_scanner_error,
        )

        self._build_ui()
        self._build_report_table([])
        self._refresh_dashboard_stats()

        self.after(33, self._refresh_camera_panel)
        self.bind_all("<MouseWheel>", self._on_global_mousewheel)
        self.bind_all("<Button-4>", self._on_global_mousewheel)
        self.bind_all("<Button-5>", self._on_global_mousewheel)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _ensure_admin_password_setup(self):
        if not has_admin_password(DB_PATH):
            set_admin_password_generic(DB_PATH)
            messagebox.showinfo(
                "Admin",
                "Se configuro la clave temporal 1111. Al ingresar como admin se solicitara cambiarla.",
            )

    def _password_dialog(self, title: str, prompt: str) -> str | None:
        win = ctk.CTkToplevel(self)
        win.title(title)
        win.geometry("420x170")
        win.resizable(False, False)
        win.transient(self)
        win.grab_set()

        value = {"text": None}
        var = StringVar(value="")

        ctk.CTkLabel(win, text=prompt, text_color=COLOR_TEXT, wraplength=390).pack(anchor="w", padx=14, pady=(14, 6))
        row = ctk.CTkFrame(win, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(0, 8))
        row.grid_columnconfigure(0, weight=1)

        entry = ctk.CTkEntry(row, textvariable=var, show="*", fg_color="#111827", border_color="#334155", text_color=COLOR_TEXT)
        entry.grid(row=0, column=0, sticky="ew")
        eye = ctk.CTkButton(row, text="ojo", width=56, fg_color="#1f2937", hover_color="#334155")
        eye.grid(row=0, column=1, padx=(6, 0))
        eye.bind("<ButtonPress-1>", lambda _e: entry.configure(show=""))
        eye.bind("<ButtonRelease-1>", lambda _e: entry.configure(show="*"))
        eye.bind("<Leave>", lambda _e: entry.configure(show="*"))

        actions = ctk.CTkFrame(win, fg_color="transparent")
        actions.pack(fill="x", padx=14, pady=(4, 10))

        def _ok():
            value["text"] = var.get()
            win.destroy()

        def _cancel():
            value["text"] = None
            win.destroy()

        ctk.CTkButton(actions, text="Cancelar", fg_color="#374151", command=_cancel).pack(side="right", padx=4)
        ctk.CTkButton(actions, text="Aceptar", fg_color=COLOR_ACCENT, command=_ok).pack(side="right", padx=4)

        entry.focus_set()
        win.bind("<Return>", lambda _e: _ok())
        win.bind("<Escape>", lambda _e: _cancel())
        self.wait_window(win)
        return value["text"]

    def _force_change_if_default_password(self) -> bool:
        if not is_default_admin_password(DB_PATH):
            return True

        messagebox.showwarning(
            "Seguridad",
            f"Estas usando la clave temporal {DEFAULT_ADMIN_PASSWORD}. Debes cambiarla ahora.",
        )

        while True:
            new_pw = self._password_dialog("Nueva contrasena", "Crea nueva contrasena admin")
            if new_pw is None:
                return False
            confirm = self._password_dialog("Confirmar", "Repite la nueva contrasena")
            if confirm is None:
                return False
            if new_pw != confirm:
                messagebox.showerror("Contrasena", "Las contrasenas no coinciden")
                continue
            try:
                set_admin_password(new_pw, DB_PATH)
                messagebox.showinfo("Admin", "Contrasena actualizada correctamente")
                return True
            except Exception as exc:
                messagebox.showerror("Contrasena", str(exc))

    def _on_global_mousewheel(self, event):
        x_root = getattr(event, "x_root", 0)
        y_root = getattr(event, "y_root", 0)

        for container in reversed(self._wheel_containers):
            try:
                if not container.winfo_exists():
                    continue
                x0 = container.winfo_rootx()
                y0 = container.winfo_rooty()
                x1 = x0 + container.winfo_width()
                y1 = y0 + container.winfo_height()
                if x0 <= x_root <= x1 and y0 <= y_root <= y1:
                    target = container._parent_canvas if hasattr(container, "_parent_canvas") else container
                    delta = self._wheel_steps_from_event(event)
                    if delta == 0:
                        return "break"
                    target.yview_scroll(delta, "units")
                    return "break"
            except Exception:
                continue

        target = self.winfo_containing(event.x_root, event.y_root)
        while target is not None:
            if hasattr(target, "yview_scroll"):
                break
            if hasattr(target, "_parent_canvas") and hasattr(target._parent_canvas, "yview_scroll"):
                target = target._parent_canvas
                break
            target = target.master
        if target is None:
            return

        delta = self._wheel_steps_from_event(event)
        if delta == 0:
            return
        try:
            target.yview_scroll(delta, "units")
        except Exception:
            pass

    def _get_system_wheel_lines(self) -> int:
        # Windows system setting: lines scrolled per wheel notch.
        try:
            SPI_GETWHEELSCROLLLINES = 0x0068
            lines = ctypes.c_uint()
            ok = ctypes.windll.user32.SystemParametersInfoW(SPI_GETWHEELSCROLLLINES, 0, ctypes.byref(lines), 0)
            if ok and int(lines.value) > 0:
                return int(lines.value)
        except Exception:
            pass
        return 3

    def _wheel_steps_from_event(self, event) -> int:
        lines = max(1, int(getattr(self, "_wheel_scroll_lines", 3)))
        speed = max(1, int(getattr(self, "_wheel_speed_multiplier", 4)))
        num = getattr(event, "num", None)
        if num == 4:
            return -(lines * speed)
        if num == 5:
            return lines * speed

        delta = int(getattr(event, "delta", 0))
        if delta == 0:
            return 0

        # Tk on Windows usually reports 120 per notch, but touchpads can send
        # smaller deltas; accumulate sub-steps for smooth and fast scrolling.
        lines_float = (-delta / 120.0) * (lines * speed)
        self._wheel_delta_buffer += lines_float
        steps = int(self._wheel_delta_buffer)
        if steps != 0:
            self._wheel_delta_buffer -= steps
            return steps

        # Guarantee perceptible movement for full wheel notches.
        if abs(delta) >= 120:
            return -(lines * speed) if delta > 0 else (lines * speed)
        return 0

    def _register_wheel_container(self, container):
        if container is None:
            return
        self._wheel_containers = [c for c in self._wheel_containers if c is not container]
        self._wheel_containers.append(container)

    def _scroll_target_by(self, target, steps: int):
        if target is None:
            return
        real_target = target._parent_canvas if hasattr(target, "_parent_canvas") else target
        try:
            real_target.yview_scroll(int(steps), "units")
        except Exception:
            pass

    def _add_scroll_controls(self, parent, target):
        nav = ctk.CTkFrame(parent, fg_color="transparent")
        nav.pack(fill="x", padx=14, pady=(2, 8))
        ctk.CTkButton(
            nav,
            text="▲ Subir",
            width=92,
            fg_color="#1f2937",
            hover_color="#334155",
            command=lambda: self._scroll_target_by(target, -8),
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            nav,
            text="▼ Bajar",
            width=92,
            fg_color="#1f2937",
            hover_color="#334155",
            command=lambda: self._scroll_target_by(target, 8),
        ).pack(side="left")

    def _build_ui(self):
        self.gateway_frame = ctk.CTkFrame(self, fg_color=COLOR_BG)
        self.gateway_frame.pack(fill="both", expand=True)

        self.app_frame = ctk.CTkFrame(self, fg_color=COLOR_BG)

        self._build_gateway_screen()
        self._build_app_shell()

    def _build_gateway_screen(self):
        header = ctk.CTkFrame(self.gateway_frame, fg_color=COLOR_BG)
        header.pack(fill="x", padx=20, pady=(20, 8))

        self.gateway_title_label = ctk.CTkLabel(
            header,
            text=self.school_name_var.get() or "Asistencia Escolar",
            font=ctk.CTkFont(size=30, weight="bold"),
            text_color=COLOR_TEXT,
        )
        self.gateway_title_label.pack(anchor="w")

        ctk.CTkLabel(
            header,
            text="Panel de Inicio - Branding institucional",
            font=ctk.CTkFont(size=13),
            text_color=COLOR_MUTED,
        ).pack(anchor="w", pady=(4, 0))

        brand_row = ctk.CTkFrame(self.gateway_frame, fg_color=COLOR_BG)
        brand_row.pack(fill="x", padx=20, pady=(6, 8))
        brand_row.grid_columnconfigure(0, weight=1)
        brand_row.grid_columnconfigure(1, weight=5)
        brand_row.grid_columnconfigure(2, weight=1)

        self.brand_logo_left = tk.Label(brand_row, bg=COLOR_BG)
        self.brand_logo_left.grid(row=0, column=0, sticky="w")

        self.brand_banner = tk.Label(brand_row, bg="#0b1020", fg="#93c5fd", text="Sin foto panorámica")
        self.brand_banner.grid(row=0, column=1, sticky="ew", padx=10)

        self.brand_logo_right = tk.Label(brand_row, bg=COLOR_BG)
        self.brand_logo_right.grid(row=0, column=2, sticky="e")

        body = ctk.CTkFrame(self.gateway_frame, fg_color=COLOR_BG)
        body.pack(fill="both", expand=True, padx=20, pady=12)
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(body, fg_color=COLOR_PANEL, corner_radius=14)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10), pady=0)
        left.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(left, text="Resumen de Hoy", font=ctk.CTkFont(size=20, weight="bold"), text_color=COLOR_TEXT).pack(
            anchor="w", padx=16, pady=(16, 6)
        )

        cards = ctk.CTkFrame(left, fg_color="transparent")
        cards.pack(fill="x", padx=16, pady=(4, 6))
        cards.grid_columnconfigure((0, 1, 2), weight=1)

        self.card_total = self._create_stat_card(cards, "Total Alumnos", "0", 0)
        self.card_present = self._create_stat_card(cards, "Asistieron", "0", 1)
        self.card_absent = self._create_stat_card(cards, "Faltaron", "0", 2)

        chart_frame = ctk.CTkFrame(left, fg_color=COLOR_PANEL_2, corner_radius=12)
        chart_frame.pack(fill="both", expand=True, padx=16, pady=(8, 16))
        ctk.CTkLabel(chart_frame, text="Mini Grafico del Dia", text_color=COLOR_TEXT, font=ctk.CTkFont(size=14, weight="bold")).pack(
            anchor="w", padx=12, pady=(10, 6)
        )
        self.chart_canvas = tk.Canvas(chart_frame, bg="#0b1020", highlightthickness=0, height=260)
        self.chart_canvas.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        right = ctk.CTkFrame(body, fg_color=COLOR_PANEL, corner_radius=14)
        right.grid(row=0, column=1, sticky="nsew", padx=(10, 0), pady=0)

        ctk.CTkLabel(right, text="Acceso", font=ctk.CTkFont(size=20, weight="bold"), text_color=COLOR_TEXT).pack(
            anchor="w", padx=16, pady=(16, 8)
        )

        ctk.CTkLabel(
            right,
            text="Entra al sistema normal o desbloquea Admin con clave para editar estudiantes.",
            text_color=COLOR_MUTED,
            justify="left",
            wraplength=360,
        ).pack(anchor="w", padx=16, pady=(0, 10))

        ctk.CTkButton(
            right,
            text="INICIAR ESCÁNER",
            fg_color=COLOR_ACCENT,
            hover_color="#1e40af",
            height=44,
            command=self._enter_and_start_scanner,
        ).pack(fill="x", padx=16, pady=(6, 8))

        ctk.CTkButton(
            right,
            text="Abrir Panel Admin (Contrasena)",
            fg_color="#1f2937",
            hover_color="#334155",
            border_width=1,
            border_color=COLOR_ACCENT_2,
            height=42,
            command=self._request_admin_access,
        ).pack(fill="x", padx=16, pady=(0, 12))

        ctk.CTkButton(
            right,
            text="Recuperar Admin (Codigo)",
            fg_color="#172554",
            hover_color="#1e3a8a",
            height=38,
            command=self._recover_admin_password_with_code,
        ).pack(fill="x", padx=16, pady=(0, 12))

        self.gateway_status = ctk.CTkLabel(
            right,
            text="Listo para ingresar.",
            text_color=COLOR_TEXT,
            fg_color="#111827",
            corner_radius=10,
            wraplength=360,
            justify="left",
            padx=12,
            pady=10,
        )
        self.gateway_status.pack(fill="x", padx=16, pady=(0, 12))

        ctk.CTkButton(
            right,
            text="Actualizar Estadisticas",
            fg_color="#0f172a",
            hover_color="#1e293b",
            command=self._refresh_gateway_view,
        ).pack(fill="x", padx=16, pady=(0, 16))

        self._refresh_gateway_branding()

    def _build_app_shell(self):
        topbar = ctk.CTkFrame(self.app_frame, fg_color=COLOR_BG)
        topbar.pack(fill="x", padx=14, pady=(14, 6))

        ctk.CTkLabel(topbar, text="Asistencia Escolar", font=ctk.CTkFont(size=22, weight="bold"), text_color=COLOR_TEXT).pack(
            side="left"
        )

        ctk.CTkButton(
            topbar,
            text="Inicio",
            width=90,
            fg_color="#111827",
            hover_color="#1f2937",
            command=self._back_to_gateway,
        ).pack(side="right", padx=5)

        ctk.CTkButton(
            topbar,
            text="Admin",
            width=90,
            fg_color="#0f172a",
            hover_color="#1e293b",
            command=self._request_admin_access,
        ).pack(side="right", padx=5)

        self.tabs = ctk.CTkTabview(self.app_frame, corner_radius=12, fg_color=COLOR_PANEL)
        self.tabs.pack(fill="both", expand=True, padx=14, pady=(0, 10))
        self.tabs.add("Registro")
        self.tabs.add("Reportes")
        self.tabs.add("Generador QR")

        self._build_tab_registro()
        self._build_tab_reportes()
        self._build_tab_qr()
        self._refresh_dynamic_academic_options()

    def _create_stat_card(self, parent, title: str, value: str, column: int):
        card = ctk.CTkFrame(parent, fg_color=COLOR_PANEL_2, corner_radius=12)
        card.grid(row=0, column=column, sticky="nsew", padx=6, pady=6)
        ctk.CTkLabel(card, text=title, text_color=COLOR_MUTED).pack(anchor="w", padx=12, pady=(10, 2))
        value_label = ctk.CTkLabel(card, text=value, text_color=COLOR_TEXT, font=ctk.CTkFont(size=24, weight="bold"))
        value_label.pack(anchor="w", padx=12, pady=(0, 10))
        return value_label

    def _enter_app(self):
        self.gateway_frame.pack_forget()
        self.app_frame.pack(fill="both", expand=True)

    def _enter_and_start_scanner(self):
        self._enter_app()
        self._start_camera()

    def _back_to_gateway(self):
        self._stop_camera()
        self.app_frame.pack_forget()
        self._refresh_gateway_view()
        self.gateway_frame.pack(fill="both", expand=True)

    def _refresh_gateway_view(self):
        self._refresh_dashboard_stats()
        self._refresh_gateway_branding()

    def _load_brand_image(self, path: str, size: tuple[int, int]):
        if not path:
            return None
        p = Path(path)
        if not p.exists():
            return None
        img = Image.open(p).convert("RGB")
        img.thumbnail(size, Image.Resampling.LANCZOS)
        return ImageTk.PhotoImage(img)

    def _refresh_gateway_branding(self):
        cfg = get_config(DB_PATH)
        school_name = str(cfg.get("nombre_colegio", "Asistencia Escolar"))
        self.gateway_title_label.configure(text=school_name)

        self._brand_photo_left = self._load_brand_image(str(cfg.get("ruta_insignia", "")), (120, 120))
        self._brand_photo_right = self._load_brand_image(str(cfg.get("ruta_logo_minedu", "")), (120, 120))
        self._brand_photo_banner = self._load_brand_image(str(cfg.get("ruta_foto_panoramica", "")), (900, 220))

        if self._brand_photo_left:
            self.brand_logo_left.configure(image=self._brand_photo_left, text="")
        else:
            self.brand_logo_left.configure(image="", text="")

        if self._brand_photo_right:
            self.brand_logo_right.configure(image=self._brand_photo_right, text="")
        else:
            self.brand_logo_right.configure(image="", text="")

        if self._brand_photo_banner:
            self.brand_banner.configure(image=self._brand_photo_banner, text="")
        else:
            self.brand_banner.configure(image="", text="Sin foto panorámica")

    def _request_admin_access(self):
        password = self._password_dialog("Acceso Admin", "Ingrese contrasena de admin")
        if password is None:
            return

        if not verify_admin_password(password, DB_PATH):
            self._set_gateway_status("Contrasena incorrecta.", ok=False)
            return

        if not self._force_change_if_default_password():
            self._set_gateway_status("Debe actualizar la contrasena temporal para continuar.", ok=False)
            return

        self._set_gateway_status("Admin desbloqueado.", ok=True)
        self._unlock_admin_tab()
        self._enter_app()
        self.tabs.set("Administracion")

    def _recover_admin_password_with_code(self):
        d1 = ctk.CTkInputDialog(title="Recuperar Admin", text="Ingrese codigo de recuperacion")
        code = d1.get_input()
        if code is None:
            return

        try:
            reset_admin_password_with_recovery_code(code, db_path=DB_PATH)
            self._set_gateway_status("Contrasena restablecida a 1111. Debe cambiarla al ingresar.", ok=True)
            messagebox.showinfo("Recuperacion", "Contrasena restablecida a 1111")
        except Exception as exc:
            self._set_gateway_status(f"Recuperacion fallida: {exc}", ok=False)
            messagebox.showerror("Recuperacion", str(exc))

    def _unlock_admin_tab(self):
        if self._admin_unlocked:
            return
        self.tabs.add("Administracion")
        self._build_tab_administracion()
        self._load_students_table()
        self._load_branding_settings()
        self._admin_unlocked = True

    def _set_gateway_status(self, message: str, ok: bool = True):
        bg = "#1e3a2b" if ok else "#4c1d1d"
        self.gateway_status.configure(text=message, fg_color=bg)

    def _get_today_stats(self):
        today = date.today().isoformat()
        with get_connection(DB_PATH) as conn:
            total_students = conn.execute("SELECT COUNT(*) FROM estudiantes").fetchone()[0]
            present = conn.execute(
                "SELECT COUNT(DISTINCT estudiante_dni) FROM asistencia WHERE fecha = ?",
                (today,),
            ).fetchone()[0]
            tardy = conn.execute(
                "SELECT COUNT(*) FROM asistencia WHERE fecha = ? AND estado = 'Tardanza'",
                (today,),
            ).fetchone()[0]

        absent = max(total_students - present, 0)
        return {
            "total": int(total_students),
            "present": int(present),
            "absent": int(absent),
            "tardy": int(tardy),
        }

    def _refresh_dashboard_stats(self):
        stats = self._get_today_stats()
        self.card_total.configure(text=str(stats["total"]))
        self.card_present.configure(text=str(stats["present"]))
        self.card_absent.configure(text=str(stats["absent"]))
        self._draw_mini_chart(stats)

    def _draw_mini_chart(self, stats):
        canvas = self.chart_canvas
        canvas.delete("all")
        canvas.update_idletasks()
        w = max(canvas.winfo_width(), 420)
        h = max(canvas.winfo_height(), 220)

        labels = ["Asistieron", "Tardanza", "Faltaron"]
        values = [stats["present"], stats["tardy"], stats["absent"]]
        colors = ["#2563eb", "#3b82f6", "#475569"]
        max_val = max(max(values), 1)

        base_y = h - 40
        chart_h = h - 70
        bar_w = 70
        gap = 50
        start_x = (w - (3 * bar_w + 2 * gap)) // 2

        canvas.create_line(30, base_y, w - 30, base_y, fill="#334155")

        for idx, (label, val, color) in enumerate(zip(labels, values, colors)):
            x0 = start_x + idx * (bar_w + gap)
            x1 = x0 + bar_w
            bar_h = int((val / max_val) * chart_h)
            y0 = base_y - bar_h
            y1 = base_y
            canvas.create_rectangle(x0, y0, x1, y1, fill=color, outline="")
            canvas.create_text((x0 + x1) // 2, y0 - 12, text=str(val), fill="#bfdbfe", font=("Segoe UI", 10, "bold"))
            canvas.create_text((x0 + x1) // 2, base_y + 16, text=label, fill="#94a3b8", font=("Segoe UI", 9))

    def _build_tab_registro(self):
        tab = self.tabs.tab("Registro")
        tab.grid_columnconfigure(0, weight=3)
        tab.grid_columnconfigure(1, weight=2)
        tab.grid_rowconfigure(0, weight=1)

        left = ctk.CTkScrollableFrame(tab, corner_radius=12, fg_color=COLOR_PANEL)
        left.grid(row=0, column=0, sticky="nsew", padx=(8, 6), pady=8)
        left.grid_rowconfigure(1, weight=1)
        left.grid_rowconfigure(4, weight=1)
        left.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            left,
            text="Feed de Camara",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=COLOR_TEXT,
        ).grid(row=0, column=0, pady=(12, 8))

        self.camera_panel = tk.Label(left, text="Camara detenida", anchor="center", bg="#0b1020", fg="#93c5fd")
        self.camera_panel.grid(row=1, column=0, padx=12, pady=8, sticky="nsew")

        camera_buttons = ctk.CTkFrame(left, fg_color="transparent")
        camera_buttons.grid(row=2, column=0, pady=(8, 12))

        ctk.CTkButton(camera_buttons, text="Iniciar Camara", command=self._start_camera, width=150, fg_color=COLOR_ACCENT).pack(side="left", padx=6)
        ctk.CTkButton(camera_buttons, text="Detener Camara", command=self._stop_camera, width=150, fg_color="#1f2937").pack(side="left", padx=6)

        ctk.CTkLabel(left, text="Asistencias Registradas (Hoy)", text_color=COLOR_TEXT, font=ctk.CTkFont(size=15, weight="bold")).grid(
            row=3, column=0, sticky="w", padx=12, pady=(4, 6)
        )
        today_frame = ctk.CTkFrame(left, corner_radius=10, fg_color=COLOR_PANEL_2)
        today_frame.grid(row=4, column=0, sticky="nsew", padx=12, pady=(0, 12))
        today_frame.grid_columnconfigure(0, weight=1)
        today_frame.grid_rowconfigure(0, weight=1)

        cols = ("hora", "dni", "alumno", "grado", "estado")
        self.today_attendance_tree = tk.ttk.Treeview(today_frame, columns=cols, show="headings", height=10, style="Dark.Treeview")
        self.today_attendance_tree.heading("hora", text="HORA")
        self.today_attendance_tree.heading("dni", text="DNI")
        self.today_attendance_tree.heading("alumno", text="ALUMNO")
        self.today_attendance_tree.heading("grado", text="GRADO")
        self.today_attendance_tree.heading("estado", text="ESTADO")
        self.today_attendance_tree.column("hora", width=90, anchor="center")
        self.today_attendance_tree.column("dni", width=100, anchor="center")
        self.today_attendance_tree.column("alumno", width=230, anchor="w")
        self.today_attendance_tree.column("grado", width=80, anchor="center")
        self.today_attendance_tree.column("estado", width=100, anchor="center")

        today_scroll = tk.ttk.Scrollbar(today_frame, orient="vertical", command=self.today_attendance_tree.yview, style="Dark.Vertical.TScrollbar")
        self.today_attendance_tree.configure(yscrollcommand=today_scroll.set)
        self.today_attendance_tree.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
        today_scroll.grid(row=0, column=1, sticky="ns", padx=(0, 8), pady=8)

        right = ctk.CTkFrame(tab, corner_radius=12, fg_color=COLOR_PANEL)
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 8), pady=8)
        right.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(right, text="Registro Manual", text_color=COLOR_TEXT, font=ctk.CTkFont(size=18, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=14, pady=(14, 8)
        )

        self.dni_manual_var = StringVar(value="")

        ctk.CTkLabel(right, text="DNI o Nombre del estudiante", text_color="#bfdbfe", font=ctk.CTkFont(size=15, weight="bold")).grid(
            row=1, column=0, sticky="w", padx=14, pady=(4, 2)
        )
        self.dni_entry = ctk.CTkEntry(
            right,
            textvariable=self.dni_manual_var,
            fg_color="#111827",
            border_color="#3b82f6",
            border_width=2,
            text_color="#eff6ff",
            font=ctk.CTkFont(size=20, weight="bold"),
            height=42,
        )
        self.dni_entry.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 8))

        self.dni_entry.bind("<Return>", lambda _e: self._register_manual())
        self.dni_manual_var.trace_add("write", self._on_dni_manual_change)

        self.registro_search_list = tk.Listbox(
            right,
            height=7,
            bg="#0b1020",
            fg="#dbeafe",
            selectbackground="#1d4ed8",
            selectforeground="#eff6ff",
            relief="flat",
            highlightthickness=1,
            highlightbackground="#334155",
        )
        self.registro_search_list.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 8))
        self.registro_search_list.bind("<<ListboxSelect>>", self._on_registro_search_select)
        self.registro_search_list.bind("<Double-Button-1>", lambda _e: self._register_manual())
        self.registro_search_list.bind("<Return>", lambda _e: self._register_manual())

        ctk.CTkButton(
            right,
            text="Registrar Asistencia",
            command=self._register_manual,
            fg_color=COLOR_ACCENT,
            font=ctk.CTkFont(size=16, weight="bold"),
            height=44,
        ).grid(row=4, column=0, sticky="ew", padx=14, pady=(6, 10))

        self.status_label = ctk.CTkLabel(
            right,
            text="Esperando registro...",
            justify="left",
            wraplength=400,
            text_color=COLOR_TEXT,
            fg_color="#111827",
            corner_radius=10,
            padx=12,
            pady=12,
        )
        self.status_label.grid(row=5, column=0, sticky="ew", padx=14, pady=8)

        last_card = ctk.CTkFrame(right, corner_radius=10, fg_color=COLOR_PANEL_2)
        last_card.grid(row=6, column=0, sticky="nsew", padx=14, pady=(8, 14))
        last_card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(last_card, text="Ultimo Alumno Registrado", text_color=COLOR_TEXT, font=ctk.CTkFont(size=16, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=10, pady=(10, 4)
        )

        self.last_student_label = ctk.CTkLabel(
            last_card,
            text="Sin registros aun.",
            justify="left",
            anchor="w",
            wraplength=380,
            text_color=COLOR_TEXT,
            padx=10,
            pady=8,
        )
        self.last_student_label.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 8))
        self._refresh_today_attendance_table()

    def _build_tab_administracion(self):
        tab = self.tabs.tab("Administracion")
        tab.grid_columnconfigure(0, weight=2)
        tab.grid_columnconfigure(1, weight=3)
        tab.grid_rowconfigure(0, weight=1)

        form = ctk.CTkScrollableFrame(tab, corner_radius=12, fg_color=COLOR_PANEL)
        form.grid(row=0, column=0, sticky="nsew", padx=(8, 6), pady=8)
        form.grid_columnconfigure(0, weight=1)
        self._register_wheel_container(form)
        self._add_scroll_controls(form, form)

        ctk.CTkLabel(form, text="CRUD Estudiantes", text_color=COLOR_TEXT, font=ctk.CTkFont(size=18, weight="bold")).pack(
            anchor="w", padx=14, pady=(14, 10)
        )

        self.admin_stats_label = ctk.CTkLabel(
            form,
            text="",
            text_color=COLOR_TEXT,
            fg_color="#111827",
            corner_radius=8,
            justify="left",
            anchor="w",
            padx=10,
            pady=8,
        )
        self.admin_stats_label.pack(fill="x", padx=14, pady=(0, 8))

        self.admin_vars = {
            "dni": StringVar(),
            "nombres": StringVar(),
            "apellidos": StringVar(),
            "grado": StringVar(value="1"),
            "seccion": StringVar(value="A"),
            "genero": StringVar(value="M"),
            "cargo": StringVar(value="Alumno"),
        }
        self.admin_vars["dni"].trace_add("write", self._on_admin_dni_change)

        self._labeled_entry(form, "DNI", self.admin_vars["dni"], pack=True)
        self._labeled_entry(form, "Nombres", self.admin_vars["nombres"], pack=True)
        self._labeled_entry(form, "Apellidos", self.admin_vars["apellidos"], pack=True)

        self.admin_grado_combo = self._labeled_combo(form, "Grado", self.admin_vars["grado"], ["1", "2", "3", "4", "5"])
        self.admin_seccion_combo = self._labeled_combo(form, "Seccion", self.admin_vars["seccion"], ["A", "B", "C"])
        self.admin_vars["grado"].trace_add("write", self._on_admin_grade_change)
        self._labeled_combo(form, "Genero", self.admin_vars["genero"], ["M", "F"])
        self._labeled_combo(
            form,
            "Cargo",
            self.admin_vars["cargo"],
            ["Alumno", "Brigadier", "Policia Escolar"],
        )

        actions = ctk.CTkFrame(form, fg_color="transparent")
        actions.pack(fill="x", padx=14, pady=(8, 14))

        ctk.CTkButton(actions, text="Agregar", command=self._add_student).pack(side="left", padx=4)
        ctk.CTkButton(actions, text="Actualizar", command=self._update_student).pack(side="left", padx=4)
        ctk.CTkButton(actions, text="Desactivar", command=self._deactivate_student).pack(side="left", padx=4)
        ctk.CTkButton(actions, text="Reactivar", command=self._reactivate_student).pack(side="left", padx=4)
        ctk.CTkButton(actions, text="Limpiar", command=self._clear_admin_form).pack(side="left", padx=4)

        tools = ctk.CTkFrame(form, fg_color="transparent")
        tools.pack(fill="x", padx=14, pady=(0, 10))
        ctk.CTkButton(tools, text="Importar Excel/CSV", command=self._import_students_file, fg_color="#1f2937").pack(side="left", padx=4)
        ctk.CTkButton(tools, text="Backup DB", command=self._backup_database, fg_color="#1f2937").pack(side="left", padx=4)
        ctk.CTkButton(tools, text="Refrescar Estadisticas", command=self._refresh_admin_quick_stats, fg_color="#1f2937").pack(side="left", padx=4)

        import_defaults = ctk.CTkFrame(form, fg_color=COLOR_PANEL_2, corner_radius=10)
        import_defaults.pack(fill="x", padx=14, pady=(0, 12))
        ctk.CTkLabel(import_defaults, text="Importacion sin columnas grado/seccion", text_color=COLOR_TEXT, font=ctk.CTkFont(size=14, weight="bold")).pack(
            anchor="w", padx=10, pady=(10, 6)
        )
        self.import_default_grado_var = StringVar(value="archivo")
        self.import_default_seccion_var = StringVar(value="archivo")
        row = ctk.CTkFrame(import_defaults, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=(0, 8))
        row.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkLabel(row, text="Grado por defecto", text_color=COLOR_TEXT).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(row, text="Seccion por defecto", text_color=COLOR_TEXT).grid(row=0, column=1, sticky="w")
        self.import_default_grado_combo = ctk.CTkComboBox(
            row,
            variable=self.import_default_grado_var,
            values=["archivo", "1", "2", "3", "4", "5"],
            state="readonly",
            fg_color="#111827",
            border_color="#334155",
            button_color="#1f2937",
            button_hover_color="#334155",
            text_color=COLOR_TEXT,
        )
        self.import_default_grado_combo.grid(row=1, column=0, sticky="ew", padx=(0, 6), pady=(2, 0))
        self.import_default_seccion_combo = ctk.CTkComboBox(
            row,
            variable=self.import_default_seccion_var,
            values=["archivo", "A", "B", "C"],
            state="readonly",
            fg_color="#111827",
            border_color="#334155",
            button_color="#1f2937",
            button_hover_color="#334155",
            text_color=COLOR_TEXT,
        )
        self.import_default_seccion_combo.grid(row=1, column=1, sticky="ew", padx=(6, 0), pady=(2, 0))
        self.import_default_grado_var.trace_add("write", self._on_import_default_grade_change)

        section_manager = ctk.CTkFrame(form, fg_color=COLOR_PANEL_2, corner_radius=10)
        section_manager.pack(fill="x", padx=14, pady=(0, 12))
        ctk.CTkLabel(section_manager, text="Gestion de Secciones", text_color=COLOR_TEXT, font=ctk.CTkFont(size=14, weight="bold")).pack(
            anchor="w", padx=10, pady=(10, 6)
        )
        self.new_section_grade_var = StringVar(value="1")
        self.new_section_name_var = StringVar(value="")
        sm_row = ctk.CTkFrame(section_manager, fg_color="transparent")
        sm_row.pack(fill="x", padx=10, pady=(0, 8))
        sm_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(sm_row, text="Grado", text_color=COLOR_TEXT).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(sm_row, text="Seccion (ej. F)", text_color=COLOR_TEXT).grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.new_section_grade_combo = ctk.CTkComboBox(
            sm_row,
            variable=self.new_section_grade_var,
            values=["1", "2", "3", "4", "5"],
            state="readonly",
            fg_color="#111827",
            border_color="#334155",
            button_color="#1f2937",
            button_hover_color="#334155",
            text_color=COLOR_TEXT,
            width=120,
        )
        self.new_section_grade_combo.grid(row=1, column=0, sticky="w", pady=(2, 0))
        ctk.CTkEntry(
            sm_row,
            textvariable=self.new_section_name_var,
            fg_color="#111827",
            border_color="#334155",
            text_color=COLOR_TEXT,
        ).grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(2, 0))
        ctk.CTkButton(section_manager, text="Agregar o Reactivar Seccion", command=self._add_section_from_admin, fg_color=COLOR_ACCENT).pack(
            fill="x", padx=10, pady=(0, 8)
        )
        self.sections_summary_label = ctk.CTkLabel(section_manager, text="", text_color=COLOR_MUTED, justify="left", anchor="w")
        self.sections_summary_label.pack(fill="x", padx=10, pady=(0, 10))

        schedule = ctk.CTkFrame(form, fg_color=COLOR_PANEL_2, corner_radius=10)
        schedule.pack(fill="x", padx=14, pady=(0, 12))
        ctk.CTkLabel(schedule, text="Horario de Asistencia", text_color=COLOR_TEXT, font=ctk.CTkFont(size=14, weight="bold")).pack(
            anchor="w", padx=10, pady=(10, 6)
        )
        self.entry_time_var = StringVar(value=get_setting("entry_time", "08:00", DB_PATH))
        self.tolerance_var = StringVar(value=get_setting("tolerance_min", "10", DB_PATH))
        self.operator_name_var = StringVar(value=get_setting("operator_name", "Soporte TIC", DB_PATH))
        ctk.CTkLabel(schedule, text="Hora de entrada (HH:MM)", text_color=COLOR_TEXT).pack(anchor="w", padx=10)
        ctk.CTkEntry(schedule, textvariable=self.entry_time_var, fg_color="#111827", border_color="#334155", text_color=COLOR_TEXT).pack(
            fill="x", padx=10, pady=(2, 8)
        )
        ctk.CTkLabel(schedule, text="Tolerancia (minutos)", text_color=COLOR_TEXT).pack(anchor="w", padx=10)
        ctk.CTkEntry(schedule, textvariable=self.tolerance_var, fg_color="#111827", border_color="#334155", text_color=COLOR_TEXT).pack(
            fill="x", padx=10, pady=(2, 8)
        )
        ctk.CTkLabel(schedule, text="Operador responsable (uso de app)", text_color=COLOR_TEXT).pack(anchor="w", padx=10)
        ctk.CTkEntry(schedule, textvariable=self.operator_name_var, fg_color="#111827", border_color="#334155", text_color=COLOR_TEXT).pack(
            fill="x", padx=10, pady=(2, 8)
        )
        ctk.CTkButton(schedule, text="Guardar Horario", command=self._save_schedule_settings, fg_color=COLOR_ACCENT).pack(
            fill="x", padx=10, pady=(0, 10)
        )

        brand = ctk.CTkFrame(form, fg_color=COLOR_PANEL_2, corner_radius=10)
        brand.pack(fill="x", padx=14, pady=(4, 12))
        ctk.CTkLabel(brand, text="Branding del Colegio", text_color=COLOR_TEXT, font=ctk.CTkFont(size=14, weight="bold")).pack(
            anchor="w", padx=10, pady=(10, 6)
        )

        ctk.CTkLabel(brand, text="Nombre del Colegio", text_color=COLOR_TEXT).pack(anchor="w", padx=10)
        ctk.CTkEntry(
            brand,
            textvariable=self.school_name_var,
            fg_color="#111827",
            border_color="#334155",
            text_color=COLOR_TEXT,
        ).pack(fill="x", padx=10, pady=(2, 8))

        ctk.CTkLabel(brand, text="Insignia del Colegio (PNG/JPG)", text_color=COLOR_TEXT).pack(anchor="w", padx=10)
        ctk.CTkEntry(
            brand,
            textvariable=self.insignia_path_var,
            fg_color="#111827",
            border_color="#334155",
            text_color=COLOR_TEXT,
        ).pack(fill="x", padx=10, pady=(2, 8))

        ctk.CTkLabel(brand, text="Logo MINEDU (PNG/JPG)", text_color=COLOR_TEXT).pack(anchor="w", padx=10)
        ctk.CTkEntry(
            brand,
            textvariable=self.minedu_logo_path_var,
            fg_color="#111827",
            border_color="#334155",
            text_color=COLOR_TEXT,
        ).pack(fill="x", padx=10, pady=(2, 8))

        ctk.CTkLabel(brand, text="Foto Panoramica (PNG/JPG)", text_color=COLOR_TEXT).pack(anchor="w", padx=10)
        ctk.CTkEntry(
            brand,
            textvariable=self.panoramic_path_var,
            fg_color="#111827",
            border_color="#334155",
            text_color=COLOR_TEXT,
        ).pack(fill="x", padx=10, pady=(2, 8))

        brand_actions_1 = ctk.CTkFrame(brand, fg_color="transparent")
        brand_actions_1.pack(fill="x", padx=10, pady=(0, 6))
        ctk.CTkButton(brand_actions_1, text="Subir Insignia", command=self._choose_insignia, fg_color=COLOR_ACCENT).pack(side="left", padx=4)
        ctk.CTkButton(brand_actions_1, text="Quitar Insignia", command=lambda: self.insignia_path_var.set(""), fg_color="#374151").pack(side="left", padx=4)
        ctk.CTkButton(brand_actions_1, text="Subir Logo MINEDU", command=self._choose_minedu_logo, fg_color="#1f2937").pack(side="left", padx=4)
        ctk.CTkButton(brand_actions_1, text="Quitar Logo MINEDU", command=lambda: self.minedu_logo_path_var.set(""), fg_color="#374151").pack(side="left", padx=4)

        brand_actions_2 = ctk.CTkFrame(brand, fg_color="transparent")
        brand_actions_2.pack(fill="x", padx=10, pady=(0, 10))
        ctk.CTkButton(brand_actions_2, text="Subir Panoramica", command=self._choose_panoramic, fg_color="#1f2937").pack(side="left", padx=4)
        ctk.CTkButton(brand_actions_2, text="Quitar Panoramica", command=lambda: self.panoramic_path_var.set(""), fg_color="#374151").pack(side="left", padx=4)

        ctk.CTkButton(
            brand,
            text="Guardar Cambios de Branding",
            command=self._save_branding,
            fg_color=COLOR_ACCENT,
            hover_color="#1e40af",
            height=38,
        ).pack(fill="x", padx=10, pady=(0, 10))

        security = ctk.CTkFrame(form, fg_color=COLOR_PANEL_2, corner_radius=10)
        security.pack(fill="x", padx=14, pady=(4, 12))
        ctk.CTkLabel(security, text="Seguridad Admin", text_color=COLOR_TEXT, font=ctk.CTkFont(size=14, weight="bold")).pack(
            anchor="w", padx=10, pady=(10, 6)
        )

        self.admin_current_pw_var = StringVar(value="")
        self.admin_new_pw_var = StringVar(value="")
        self.admin_confirm_pw_var = StringVar(value="")
        self.recovery_codes_count_var = StringVar(value="10")

        self._password_entry_with_eye(security, "Contrasena actual", self.admin_current_pw_var)
        self._password_entry_with_eye(security, "Nueva contrasena", self.admin_new_pw_var)
        self._password_entry_with_eye(security, "Confirmar nueva contrasena", self.admin_confirm_pw_var)
        ctk.CTkButton(security, text="Cambiar Contrasena Admin", command=self._change_admin_password, fg_color="#1f2937").pack(
            fill="x", padx=10, pady=(0, 10)
        )

        ctk.CTkLabel(security, text="Codigos de recuperacion (cantidad)", text_color=COLOR_TEXT).pack(anchor="w", padx=10)
        ctk.CTkEntry(security, textvariable=self.recovery_codes_count_var, fg_color="#111827", border_color="#334155", text_color=COLOR_TEXT).pack(
            fill="x", padx=10, pady=(2, 8)
        )
        ctk.CTkButton(security, text="Generar Codigos .TXT", command=self._generate_recovery_codes_txt, fg_color=COLOR_ACCENT).pack(
            fill="x", padx=10, pady=(0, 10)
        )

        table_frame = ctk.CTkFrame(tab, corner_radius=12, fg_color=COLOR_PANEL)
        table_frame.grid(row=0, column=1, sticky="nsew", padx=(6, 8), pady=8)
        table_frame.grid_columnconfigure(0, weight=1)
        table_frame.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(table_frame, text="Listado de Estudiantes", text_color=COLOR_TEXT, font=ctk.CTkFont(size=18, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=12, pady=(12, 8)
        )

        self.admin_table_search_var = StringVar(value="")
        self.admin_table_search_var.trace_add("write", self._on_admin_table_search_change)
        search_bar = ctk.CTkEntry(
            table_frame,
            textvariable=self.admin_table_search_var,
            placeholder_text="Buscar en tabla (DNI, nombre, apellido, grado, seccion, genero, cargo)",
            fg_color="#111827",
            border_color="#334155",
            text_color=COLOR_TEXT,
        )
        search_bar.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))

        self.admin_table_grado_var = StringVar(value="todos")
        self.admin_table_seccion_var = StringVar(value="todos")
        filters_row = ctk.CTkFrame(table_frame, fg_color="transparent")
        filters_row.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 8))
        filters_row.grid_columnconfigure((0, 1), weight=1)
        self.admin_table_grado_combo = ctk.CTkComboBox(
            filters_row,
            variable=self.admin_table_grado_var,
            values=["todos", "1", "2", "3", "4", "5"],
            state="readonly",
            fg_color="#111827",
            border_color="#334155",
            button_color="#1f2937",
            button_hover_color="#334155",
            text_color=COLOR_TEXT,
        )
        self.admin_table_grado_combo.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.admin_table_seccion_combo = ctk.CTkComboBox(
            filters_row,
            variable=self.admin_table_seccion_var,
            values=["todos", "A", "B", "C"],
            state="readonly",
            fg_color="#111827",
            border_color="#334155",
            button_color="#1f2937",
            button_hover_color="#334155",
            text_color=COLOR_TEXT,
        )
        self.admin_table_seccion_combo.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        self.admin_table_grado_var.trace_add("write", self._on_admin_table_grade_change)
        self.admin_table_seccion_var.trace_add("write", self._on_admin_table_search_change)

        cols = ("dni", "nombres", "apellidos", "grado", "seccion", "genero", "cargo", "estado")
        self.students_tree = tk.ttk.Treeview(table_frame, columns=cols, show="headings", height=18, style="Dark.Treeview")
        for col in cols:
            self.students_tree.heading(col, text=col.upper())
            self.students_tree.column(col, width=90 if col == "dni" else 120, anchor="center")
        self.students_tree.column("cargo", width=160, anchor="center")
        self.students_tree.column("estado", width=90, anchor="center")

        y_scroll = tk.ttk.Scrollbar(table_frame, orient="vertical", command=self.students_tree.yview, style="Dark.Vertical.TScrollbar")
        self.students_tree.configure(yscrollcommand=y_scroll.set)

        self.students_tree.grid(row=3, column=0, sticky="nsew", padx=(12, 0), pady=(0, 12))
        y_scroll.grid(row=3, column=1, sticky="ns", padx=(0, 12), pady=(0, 12))
        self.students_tree.bind("<<TreeviewSelect>>", self._on_student_selected)
        self._refresh_dynamic_academic_options()

    def _build_tab_reportes(self):
        tab = self.tabs.tab("Reportes")
        tab.grid_columnconfigure(0, weight=2)
        tab.grid_columnconfigure(1, weight=5)
        tab.grid_rowconfigure(0, weight=1)

        filters = ctk.CTkScrollableFrame(tab, corner_radius=12, fg_color=COLOR_PANEL)
        filters.grid(row=0, column=0, sticky="nsew", padx=(8, 6), pady=8)
        self._register_wheel_container(filters)
        self._add_scroll_controls(filters, filters)

        ctk.CTkLabel(filters, text="Filtros Inteligentes", text_color=COLOR_TEXT, font=ctk.CTkFont(size=18, weight="bold")).pack(
            anchor="w", padx=14, pady=(14, 10)
        )

        self.filter_vars = {
            "period": StringVar(value="Dia"),
            "condition": StringVar(value="Todos"),
            "grado": StringVar(value="todos"),
            "seccion": StringVar(value="todos"),
            "genero": StringVar(value="todos"),
            "cargo": StringVar(value="todos"),
        }

        self._labeled_combo(filters, "Periodo", self.filter_vars["period"], ["Dia", "Semana", "Mes", "Rango"])
        self._labeled_combo(filters, "Condicion", self.filter_vars["condition"], ["Todos", "Asistieron", "Faltaron"])
        self.report_grado_combo = self._labeled_combo(filters, "Grado", self.filter_vars["grado"], ["todos", "1", "2", "3", "4", "5"])
        self.report_seccion_combo = self._labeled_combo(filters, "Seccion", self.filter_vars["seccion"], ["todos", "A", "B", "C"])
        self.filter_vars["grado"].trace_add("write", self._on_report_grade_change)
        self._labeled_combo(filters, "Genero", self.filter_vars["genero"], ["todos", "M", "F"])
        self._labeled_combo(filters, "Cargo", self.filter_vars["cargo"], ["todos", "Alumno", "Brigadier", "Policia Escolar"])

        ctk.CTkLabel(filters, text="Fecha base", text_color=COLOR_TEXT).pack(anchor="w", padx=14)
        if DateEntry:
            self.ref_date_picker = DateEntry(filters, date_pattern="yyyy-mm-dd")
            self.ref_date_picker.pack(fill="x", padx=14, pady=(2, 8))
            ctk.CTkLabel(filters, text="Inicio (rango)", text_color=COLOR_TEXT).pack(anchor="w", padx=14)
            self.start_date_picker = DateEntry(filters, date_pattern="yyyy-mm-dd")
            self.start_date_picker.pack(fill="x", padx=14, pady=(2, 8))
            ctk.CTkLabel(filters, text="Fin (rango)", text_color=COLOR_TEXT).pack(anchor="w", padx=14)
            self.end_date_picker = DateEntry(filters, date_pattern="yyyy-mm-dd")
            self.end_date_picker.pack(fill="x", padx=14, pady=(2, 8))
        else:
            self.ref_date_var = StringVar(value=date.today().isoformat())
            self.start_date_var = StringVar(value=date.today().isoformat())
            self.end_date_var = StringVar(value=date.today().isoformat())
            self._labeled_entry(filters, "Fecha base (YYYY-MM-DD)", self.ref_date_var, pack=True)
            self._labeled_entry(filters, "Inicio rango (YYYY-MM-DD)", self.start_date_var, pack=True)
            self._labeled_entry(filters, "Fin rango (YYYY-MM-DD)", self.end_date_var, pack=True)

        actions = ctk.CTkFrame(filters, fg_color="transparent")
        actions.pack(fill="x", padx=14, pady=(8, 14))
        ctk.CTkButton(actions, text="Generar Reporte", command=self._generate_report, fg_color=COLOR_ACCENT).pack(fill="x", pady=(0, 8))

        ctk.CTkLabel(actions, text="Formato de exporte", text_color=COLOR_MUTED).pack(anchor="w")
        self.report_export_format_var = StringVar(value="Excel (.xlsx)")
        ctk.CTkComboBox(
            actions,
            variable=self.report_export_format_var,
            values=["Excel (.xlsx)", "PDF (.pdf)"],
            state="readonly",
            fg_color="#111827",
            border_color="#334155",
            button_color="#1f2937",
            button_hover_color="#334155",
            text_color=COLOR_TEXT,
        ).pack(fill="x", pady=(2, 8))
        ctk.CTkButton(actions, text="Exportar Reporte", command=self._export_report, fg_color="#1f2937").pack(fill="x")

        ctk.CTkLabel(actions, text="Historial de reportes", text_color=COLOR_MUTED).pack(anchor="w", pady=(10, 2))
        history_frame = ctk.CTkFrame(actions, fg_color=COLOR_PANEL_2, corner_radius=8)
        history_frame.pack(fill="x", pady=(0, 8))
        history_frame.grid_columnconfigure(0, weight=1)
        history_frame.grid_rowconfigure(0, weight=1)
        self.report_history_list = tk.Listbox(
            history_frame,
            height=6,
            bg="#0f172a",
            fg="#dbeafe",
            selectbackground="#1d4ed8",
            selectforeground="#eff6ff",
            activestyle="none",
            relief="flat",
            highlightthickness=0,
        )
        self.report_history_list.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        history_scroll = tk.ttk.Scrollbar(history_frame, orient="vertical", command=self.report_history_list.yview, style="Dark.Vertical.TScrollbar")
        history_scroll.grid(row=0, column=1, sticky="ns", padx=(0, 6), pady=6)
        self.report_history_list.configure(yscrollcommand=history_scroll.set)
        self.report_history_list.bind("<Double-Button-1>", lambda _e: self._open_selected_history_temp_report())

        ctk.CTkButton(actions, text="Refrescar Historial", command=self._refresh_report_history_list, fg_color="#1f2937").pack(fill="x", pady=(0, 6))
        ctk.CTkButton(actions, text="Ver Historial (PDF Temporal)", command=self._open_selected_history_temp_report, fg_color="#0f4c81").pack(fill="x")

        preview = ctk.CTkFrame(tab, corner_radius=12, fg_color=COLOR_PANEL)
        preview.grid(row=0, column=1, sticky="nsew", padx=(6, 8), pady=8)
        preview.grid_rowconfigure(1, weight=1)
        preview.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(preview, text="Vista Previa", text_color=COLOR_TEXT, font=ctk.CTkFont(size=18, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=12, pady=(12, 8)
        )

        report_cols = (
            "fecha",
            "hora",
            "dni",
            "nombres",
            "apellidos",
            "grado",
            "seccion",
            "genero",
            "cargo",
            "profesor_encargado",
            "estado",
        )
        self.report_tree = tk.ttk.Treeview(preview, columns=report_cols, show="headings", height=20, style="Dark.Treeview")
        for col in report_cols:
            self.report_tree.heading(col, text=col)
            self.report_tree.column(col, width=95, anchor="center")
        self.report_tree.column("apellidos", width=140, anchor="w")
        self.report_tree.column("nombres", width=140, anchor="w")
        self.report_tree.column("cargo", width=130, anchor="center")
        self.report_tree.column("profesor_encargado", width=160, anchor="w")

        y_scroll = tk.ttk.Scrollbar(preview, orient="vertical", command=self.report_tree.yview, style="Dark.Vertical.TScrollbar")
        x_scroll = tk.ttk.Scrollbar(preview, orient="horizontal", command=self.report_tree.xview, style="Dark.Horizontal.TScrollbar")
        self.report_tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)

        self.report_tree.grid(row=1, column=0, sticky="nsew", padx=(12, 0), pady=(0, 0))
        y_scroll.grid(row=1, column=1, sticky="ns", padx=(0, 12), pady=(0, 0))
        x_scroll.grid(row=2, column=0, sticky="ew", padx=(12, 0), pady=(0, 12))
        self._refresh_report_history_list()

    def _build_tab_qr(self):
        tab = self.tabs.tab("Generador QR")
        tab.grid_columnconfigure(0, weight=3, minsize=760)
        tab.grid_columnconfigure(1, weight=1, minsize=380)
        tab.grid_rowconfigure(0, weight=1)

        left = ctk.CTkScrollableFrame(tab, corner_radius=12, fg_color=COLOR_PANEL)
        left.grid(row=0, column=0, sticky="nsew", padx=(8, 6), pady=8)
        self._register_wheel_container(left)
        self._add_scroll_controls(left, left)

        ctk.CTkLabel(left, text="Emision de QR y Fotocheck", text_color=COLOR_TEXT, font=ctk.CTkFont(size=18, weight="bold")).pack(
            anchor="w", padx=14, pady=(14, 10)
        )

        self.qr_student_var = StringVar(value="")
        self.qr_search_var = StringVar(value="")

        self._labeled_entry(left, "Buscar (DNI, nombre, grado, seccion, genero, cargo)", self.qr_search_var, pack=True)
        self.qr_search_var.trace_add("write", self._on_qr_search_change)

        ctk.CTkLabel(left, text="Resultados filtrados", text_color=COLOR_TEXT).pack(anchor="w", padx=14)
        qr_results_frame = ctk.CTkFrame(left, fg_color=COLOR_PANEL_2, corner_radius=10)
        qr_results_frame.pack(fill="x", padx=14, pady=(2, 10))
        qr_results_frame.grid_columnconfigure(0, weight=1)
        qr_results_frame.grid_rowconfigure(0, weight=1)

        self.qr_results_list = tk.Listbox(
            qr_results_frame,
            height=6,
            bg="#0f172a",
            fg="#dbeafe",
            selectbackground="#1d4ed8",
            selectforeground="#eff6ff",
            activestyle="none",
            relief="flat",
            highlightthickness=0,
        )
        self.qr_results_list.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        qr_results_scroll = tk.ttk.Scrollbar(qr_results_frame, orient="vertical", command=self.qr_results_list.yview, style="Dark.Vertical.TScrollbar")
        qr_results_scroll.grid(row=0, column=1, sticky="ns", padx=(0, 6), pady=6)
        self.qr_results_list.configure(yscrollcommand=qr_results_scroll.set)
        self.qr_results_list.bind("<<ListboxSelect>>", self._on_qr_result_selected)
        self.qr_results_list.bind("<Double-Button-1>", lambda _e: self._generate_qr_preview_only())

        ctk.CTkButton(left, text="Actualizar Lista", command=self._refresh_qr_student_options, fg_color="#1f2937").pack(
            fill="x", padx=14, pady=(0, 8)
        )
        ctk.CTkButton(left, text="Generar Vista Previa", command=self._generate_qr_preview_only, fg_color=COLOR_ACCENT).pack(
            fill="x", padx=14, pady=(0, 8)
        )

        self.qr_scope_var = StringVar(value="Alumno seleccionado")
        self.qr_grade_var = StringVar(value="Seleccionar grado")
        self.qr_section_var = StringVar(value="Seleccionar seccion")
        self.qr_gender_var = StringVar(value="Seleccionar genero")
        self.qr_cargo_var = StringVar(value="Seleccionar cargo")
        self.qr_cards_layout_var = StringVar(value="8 por hoja (A4)")
        self.qr_cards_layout_var.trace_add("write", lambda *_: self._draw_layout_avatar_mock())
        self.qr_scope_var.trace_add("write", lambda *_: self._draw_layout_avatar_mock())
        self.qr_grade_var.trace_add("write", lambda *_: self._draw_layout_avatar_mock())
        self.qr_section_var.trace_add("write", lambda *_: self._draw_layout_avatar_mock())
        self.qr_gender_var.trace_add("write", lambda *_: self._draw_layout_avatar_mock())
        self.qr_cargo_var.trace_add("write", lambda *_: self._draw_layout_avatar_mock())

        ctk.CTkLabel(left, text="Alcance de emision", text_color=COLOR_TEXT, font=ctk.CTkFont(size=13, weight="bold")).pack(
            anchor="w", padx=14, pady=(6, 2)
        )
        ctk.CTkComboBox(
            left,
            variable=self.qr_scope_var,
            values=["Alumno seleccionado", "Filtrado (grado/genero/seccion/cargo)", "Todos los alumnos activos"],
            state="readonly",
            fg_color="#111827",
            border_color="#334155",
            button_color="#1f2937",
            button_hover_color="#334155",
            text_color=COLOR_TEXT,
        ).pack(fill="x", padx=14, pady=(0, 6))

        ctk.CTkLabel(left, text="Filtros rapidos", text_color=COLOR_TEXT).pack(anchor="w", padx=14)
        filters = ctk.CTkFrame(left, fg_color=COLOR_PANEL_2)
        filters.pack(fill="x", padx=14, pady=(2, 8))
        filters.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkLabel(filters, text="Grado", text_color=COLOR_MUTED).grid(row=0, column=0, sticky="w", padx=6, pady=(6, 0))
        ctk.CTkLabel(filters, text="Seccion", text_color=COLOR_MUTED).grid(row=0, column=1, sticky="w", padx=6, pady=(6, 0))

        self.qr_grade_combo = ctk.CTkComboBox(
            filters,
            variable=self.qr_grade_var,
            values=["Seleccionar grado", "1", "2", "3", "4", "5"],
            state="readonly",
            fg_color="#0b1220",
            border_color="#334155",
            button_color="#1f2937",
            button_hover_color="#334155",
            text_color=COLOR_TEXT,
        )
        self.qr_grade_combo.grid(row=1, column=0, sticky="ew", padx=6, pady=6)
        self.qr_section_combo = ctk.CTkComboBox(
            filters,
            variable=self.qr_section_var,
            values=["Seleccionar seccion", "A", "B", "C"],
            state="readonly",
            fg_color="#0b1220",
            border_color="#334155",
            button_color="#1f2937",
            button_hover_color="#334155",
            text_color=COLOR_TEXT,
        )
        self.qr_section_combo.grid(row=1, column=1, sticky="ew", padx=6, pady=6)
        self.qr_grade_var.trace_add("write", self._on_qr_grade_change)

        ctk.CTkLabel(filters, text="Genero", text_color=COLOR_MUTED).grid(row=2, column=0, sticky="w", padx=6, pady=(2, 0))
        ctk.CTkLabel(filters, text="Cargo", text_color=COLOR_MUTED).grid(row=2, column=1, sticky="w", padx=6, pady=(2, 0))
        ctk.CTkComboBox(
            filters,
            variable=self.qr_gender_var,
            values=["Seleccionar genero", "M", "F"],
            state="readonly",
            fg_color="#0b1220",
            border_color="#334155",
            button_color="#1f2937",
            button_hover_color="#334155",
            text_color=COLOR_TEXT,
        ).grid(row=3, column=0, sticky="ew", padx=6, pady=6)
        self.qr_cargo_combo = ctk.CTkComboBox(
            filters,
            variable=self.qr_cargo_var,
            values=["Seleccionar cargo"],
            state="readonly",
            fg_color="#0b1220",
            border_color="#334155",
            button_color="#1f2937",
            button_hover_color="#334155",
            text_color=COLOR_TEXT,
        )
        self.qr_cargo_combo.grid(row=3, column=1, sticky="ew", padx=6, pady=6)

        ctk.CTkButton(left, text="Actualizar conteo", command=self._refresh_qr_filter_summary, fg_color="#1f2937").pack(
            fill="x", padx=14, pady=(0, 6)
        )
        self.qr_filter_count_label = ctk.CTkLabel(left, text="Seleccionados: 0", text_color=COLOR_MUTED)
        self.qr_filter_count_label.pack(anchor="w", padx=14, pady=(0, 8))

        ctk.CTkLabel(left, text="Opcion 1: QR Simple", text_color=COLOR_TEXT, font=ctk.CTkFont(size=14, weight="bold")).pack(
            anchor="w", padx=14, pady=(4, 4)
        )
        ctk.CTkButton(left, text="Guardar QR PNG", command=self._save_qr_png, fg_color="#2563eb").pack(
            fill="x", padx=14, pady=(0, 8)
        )
        ctk.CTkButton(left, text="Generar QRs por Filtro (ZIP)", command=self._save_qr_batch_zip, fg_color="#1d4ed8").pack(
            fill="x", padx=14, pady=(0, 8)
        )

        ctk.CTkLabel(left, text="Opcion 2: Fotocheck Profesional (PDF)", text_color=COLOR_TEXT, font=ctk.CTkFont(size=14, weight="bold")).pack(
            anchor="w", padx=14, pady=(8, 4)
        )

        ctk.CTkLabel(left, text="Distribucion A4", text_color=COLOR_MUTED).pack(anchor="w", padx=14)
        ctk.CTkComboBox(
            left,
            variable=self.qr_cards_layout_var,
            values=["8 por hoja (A4)", "6 por hoja (A4)", "1 por hoja (A4)"],
            state="readonly",
            fg_color="#111827",
            border_color="#334155",
            button_color="#1f2937",
            button_hover_color="#334155",
            text_color=COLOR_TEXT,
        ).pack(fill="x", padx=14, pady=(0, 8))

        ctk.CTkButton(left, text="Generar Fotocheck PDF (segun alcance)", command=self._generate_id_cards_pdf, fg_color="#1f2937").pack(
            fill="x", padx=14, pady=(0, 8)
        )

        info_text = (
            "Opcion 1: QR simple con token seguro (sin DNI ni datos personales).\n"
            "Opcion 2: Fotocheck horizontal profesional con logos, nombre y QR tokenizado."
        )
        ctk.CTkLabel(left, text=info_text, text_color=COLOR_MUTED, justify="left", wraplength=430).pack(
            anchor="w", padx=14, pady=(8, 12)
        )

        right = ctk.CTkFrame(tab, corner_radius=12, fg_color=COLOR_PANEL)
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 8), pady=8)
        right.grid_rowconfigure(4, weight=1)
        right.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(right, text="Vista Previa Compacta", text_color=COLOR_TEXT, font=ctk.CTkFont(size=16, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=12, pady=(12, 8)
        )

        self.qr_preview_label = tk.Label(right, text="QR", bg="#0b1020", fg="#93c5fd", width=180, height=180)
        self.qr_preview_label.grid(row=1, column=0, sticky="n", padx=12, pady=(0, 8))

        self.qr_avatar_hint = ctk.CTkLabel(
            right,
            text="Avatar de referencia",
            text_color=COLOR_MUTED,
            fg_color="#0b1020",
            corner_radius=8,
            padx=8,
            pady=6,
        )
        self.qr_avatar_hint.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 8))

        ctk.CTkLabel(right, text="Mock de hoja (sin render PDF)", text_color=COLOR_TEXT).grid(
            row=3, column=0, sticky="w", padx=12, pady=(2, 6)
        )
        self.qr_layout_mock_canvas = tk.Canvas(right, bg="#0b1020", highlightthickness=0, width=300, height=240)
        self.qr_layout_mock_canvas.grid(row=4, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self._refresh_qr_student_options(query="")
        self._refresh_qr_filter_options()
        self._refresh_qr_filter_summary()
        self._draw_layout_avatar_mock()
        self.after(40, self._stabilize_qr_layout)

    def _stabilize_qr_layout(self):
        # Ensure initial distribution matches intended 3:1 layout on first paint.
        try:
            tab = self.tabs.tab("Generador QR")
            tab.update_idletasks()
            tab.grid_columnconfigure(0, weight=3, minsize=760)
            tab.grid_columnconfigure(1, weight=1, minsize=380)
        except Exception:
            pass

    def _load_branding_settings(self):
        self.school_name_var.set(get_setting("school_name", "Asistencia Escolar", DB_PATH))
        insignia = get_setting("logo_path", "", DB_PATH)
        self.insignia_path_var.set(insignia)
        self.logo_path_var.set(insignia)
        self.minedu_logo_path_var.set(get_setting("minedu_logo_path", "", DB_PATH))
        self.panoramic_path_var.set(get_setting("panoramic_path", "", DB_PATH))

    def _choose_logo(self):
        self._choose_insignia()

    def _choose_insignia(self):
        path = filedialog.askopenfilename(
            title="Seleccionar insignia del colegio",
            filetypes=[("Images", "*.png;*.jpg;*.jpeg;*.webp"), ("All files", "*.*")],
        )
        if not path:
            return
        self.insignia_path_var.set(path)
        self.logo_path_var.set(path)

    def _choose_minedu_logo(self):
        path = filedialog.askopenfilename(
            title="Seleccionar logo MINEDU",
            filetypes=[("Images", "*.png;*.jpg;*.jpeg;*.webp"), ("All files", "*.*")],
        )
        if not path:
            return
        self.minedu_logo_path_var.set(path)

    def _choose_panoramic(self):
        path = filedialog.askopenfilename(
            title="Seleccionar foto panoramica",
            filetypes=[("Images", "*.png;*.jpg;*.jpeg;*.webp"), ("All files", "*.*")],
        )
        if not path:
            return
        self.panoramic_path_var.set(path)

    def _save_branding(self):
        insignia = self.insignia_path_var.get().strip()
        set_setting("school_name", self.school_name_var.get().strip() or "Asistencia Escolar", DB_PATH)
        set_setting("logo_path", insignia, DB_PATH)
        set_setting("minedu_logo_path", self.minedu_logo_path_var.get().strip(), DB_PATH)
        set_setting("panoramic_path", self.panoramic_path_var.get().strip(), DB_PATH)
        self.logo_path_var.set(insignia)
        self._refresh_gateway_branding()
        self._set_status("Branding guardado (insignia, logo MINEDU y panoramica).", ok=True)

    def _change_admin_password(self):
        current_pw = self.admin_current_pw_var.get()
        new_pw = self.admin_new_pw_var.get()
        confirm_pw = self.admin_confirm_pw_var.get()
        if not current_pw or not new_pw or not confirm_pw:
            self._set_status("Complete los campos de contrasena.", ok=False)
            return
        if new_pw != confirm_pw:
            self._set_status("La confirmacion no coincide.", ok=False)
            return
        try:
            change_admin_password(current_pw, new_pw, db_path=DB_PATH)
            self.admin_current_pw_var.set("")
            self.admin_new_pw_var.set("")
            self.admin_confirm_pw_var.set("")
            self._set_status("Contrasena admin actualizada correctamente.", ok=True)
        except Exception as exc:
            self._set_status(f"Error al cambiar contrasena: {exc}", ok=False)

    def _generate_recovery_codes_txt(self):
        try:
            qty = int((self.recovery_codes_count_var.get() or "10").strip())
        except Exception:
            self._set_status("Cantidad invalida para codigos de recuperacion.", ok=False)
            return

        out = filedialog.asksaveasfilename(
            title="Guardar codigos de recuperacion",
            defaultextension=".txt",
            initialfile="codigos_recuperacion_admin.txt",
            filetypes=[("Text", "*.txt")],
        )
        if not out:
            return

        try:
            result = generate_admin_recovery_codes(out, count=qty, db_path=DB_PATH)
            self._set_status(
                f"Codigos generados: {result['count']} (lote {result['batch_id']}). Los anteriores fueron invalidados.",
                ok=True,
            )
            messagebox.showinfo(
                "Codigos de Recuperacion",
                "Se genero un nuevo lote de codigos.\nGuarde el archivo en lugar seguro.\nLos lotes anteriores quedaron invalidados.",
            )
        except Exception as exc:
            self._set_status(f"Error generando codigos: {exc}", ok=False)

    def _refresh_qr_student_options(self, query: str = ""):
        students = search_students(query, limit=200, db_path=DB_PATH) if query else list_students(DB_PATH)
        self._qr_search_cache = students
        items = [f"{s['dni']} - {s['apellidos']}, {s['nombres']} ({s['grado']}{s['seccion']})" for s in students]
        self.qr_results_list.delete(0, END)
        for item in items:
            self.qr_results_list.insert(END, item)

        if items:
            self.qr_results_list.selection_clear(0, END)
            self.qr_results_list.selection_set(0)
            self.qr_results_list.activate(0)
            self.qr_student_var.set(items[0])
        else:
            self.qr_student_var.set("")
        self._refresh_qr_filter_summary()

    def _on_qr_result_selected(self, _event=None):
        if not hasattr(self, "qr_results_list"):
            return
        selected = self.qr_results_list.curselection()
        if not selected:
            return
        value = self.qr_results_list.get(selected[0])
        self.qr_student_var.set(value)
        self._draw_layout_avatar_mock()

    def _refresh_qr_filter_options(self):
        sections = list_sections(db_path=DB_PATH)
        grados = sorted({str(int(s["grado"])) for s in sections}) if sections else ["1", "2", "3", "4", "5"]
        if hasattr(self, "qr_grade_combo"):
            grade_values = ["Seleccionar grado", *grados]
            self.qr_grade_combo.configure(values=grade_values)
            if self.qr_grade_var.get() not in grade_values:
                self.qr_grade_var.set("Seleccionar grado")
        if hasattr(self, "qr_section_combo"):
            section_values = self._section_values_for_grade(self.qr_grade_var.get(), include_select=True)
            self.qr_section_combo.configure(values=section_values)
            if self.qr_section_var.get() not in section_values:
                self.qr_section_var.set("Seleccionar seccion")

        with get_connection(DB_PATH) as conn:
            rows = conn.execute("SELECT DISTINCT cargo FROM estudiantes WHERE activo = 1 ORDER BY cargo").fetchall()
        cargos = [str(r[0]) for r in rows if str(r[0]).strip()]
        values = ["Seleccionar cargo", *cargos]
        self.qr_cargo_combo.configure(values=values)
        if self.qr_cargo_var.get() not in values:
            self.qr_cargo_var.set("Seleccionar cargo")

    def _students_for_qr_scope(self):
        scope = self.qr_scope_var.get().strip()
        if scope == "Alumno seleccionado":
            dni = self._get_qr_selected_dni()
            if not dni:
                return []
            student = get_student_by_dni(dni, DB_PATH)
            return [student] if student and int(student.get("activo", 1)) == 1 else []

        students = list_students(DB_PATH, only_active=True)
        if scope == "Todos los alumnos activos":
            return students

        grade = self.qr_grade_var.get().strip()
        section = self.qr_section_var.get().strip().upper()
        gender = self.qr_gender_var.get().strip().upper()
        cargo = self.qr_cargo_var.get().strip()

        def _is_all(value: str) -> bool:
            v = (value or "").strip().lower()
            return (not v) or v.startswith("seleccionar") or v in {"todos", "todas", "todo"}

        filtered = []
        for s in students:
            if (not _is_all(grade)) and str(s.get("grado", "")).strip() != grade:
                continue
            if (not _is_all(section)) and str(s.get("seccion", "")).strip().upper() != section:
                continue
            if (not _is_all(gender)) and str(s.get("genero", "")).strip().upper() != gender:
                continue
            if (not _is_all(cargo)) and str(s.get("cargo", "")).strip() != cargo:
                continue
            filtered.append(s)
        return filtered

    def _refresh_qr_filter_summary(self):
        total = len(self._students_for_qr_scope())
        self.qr_filter_count_label.configure(text=f"Seleccionados: {total}")
        self._draw_layout_avatar_mock()

    def _on_qr_search_change(self, *_args):
        query = self.qr_search_var.get().strip()
        self._refresh_qr_student_options(query=query)

    def _get_qr_selected_dni(self) -> str:
        raw = self.qr_student_var.get().strip()
        if not raw:
            return ""
        return raw.split(" - ", 1)[0].strip()

    def _generate_qr_preview_only(self):
        dni = self._get_qr_selected_dni()
        if not dni:
            return
        student = get_student_by_dni(dni, DB_PATH)
        if not student:
            self._set_status("No se encontro el estudiante para el QR.", ok=False)
            return

        logo_path = get_setting("logo_path", "", DB_PATH)
        img = generate_student_qr_image(student, logo_path=logo_path)
        img = img.resize((170, 170), Image.Resampling.NEAREST)
        self._qr_preview_photo = ImageTk.PhotoImage(img)
        self.qr_preview_label.configure(image=self._qr_preview_photo, text="")
        full_name = f"{student.get('nombres', '')} {student.get('apellidos', '')}".strip()
        self.qr_avatar_hint.configure(text=f"Avatar: {full_name[:28]}")
        self._draw_layout_avatar_mock()

    def _layout_cards_per_page(self) -> int:
        mapping = {
            "8 por hoja (A4)": 8,
            "6 por hoja (A4)": 6,
            "1 por hoja (A4)": 1,
        }
        return int(mapping.get(self.qr_cards_layout_var.get().strip(), 8))

    def _draw_layout_avatar_mock(self):
        if not hasattr(self, "qr_layout_mock_canvas"):
            return
        cv = self.qr_layout_mock_canvas
        cv.delete("all")
        cv.update_idletasks()
        w = max(cv.winfo_width(), 240)
        h = max(cv.winfo_height(), 180)

        cards = self._layout_cards_per_page()
        if cards == 1:
            cols, rows = 1, 1
        elif cards == 6:
            cols, rows = 2, 3
        else:
            cols, rows = 2, 4

        margin = 12
        gap = 8
        inner_w = w - (margin * 2)
        inner_h = h - (margin * 2)
        card_w = (inner_w - gap * (cols - 1)) / cols
        card_h = (inner_h - gap * (rows - 1)) / rows

        selected_name = "AL"
        dni = self._get_qr_selected_dni()
        if dni:
            s = get_student_by_dni(dni, DB_PATH)
            if s:
                initials = (str(s.get("nombres", " "))[:1] + str(s.get("apellidos", " "))[:1]).upper()
                if initials.strip():
                    selected_name = initials

        cv.create_text(margin, 6, anchor="nw", fill="#93c5fd", text=f"Layout {cards} por hoja")

        total_slots = cols * rows
        for i in range(total_slots):
            c = i % cols
            r = i // cols
            x0 = margin + c * (card_w + gap)
            y0 = margin + r * (card_h + gap)
            x1 = x0 + card_w
            y1 = y0 + card_h
            cv.create_rectangle(x0, y0, x1, y1, outline="#334155", width=1)

            cv.create_rectangle(x0 + 3, y0 + 3, x0 + 14, y0 + 10, fill="#2563eb", outline="")
            cv.create_rectangle(x1 - 14, y0 + 3, x1 - 3, y0 + 10, fill="#1e3a8a", outline="")

            cx = (x0 + x1) / 2
            cy = (y0 + y1) / 2
            radius = min(card_w, card_h) * 0.18
            cv.create_oval(cx - radius, cy - radius, cx + radius, cy + radius, fill="#1d4ed8", outline="")
            cv.create_text(cx, cy, fill="#eff6ff", text=selected_name)

    def _save_qr_png(self):
        dni = self._get_qr_selected_dni()
        if not dni:
            self._set_status("Seleccione un estudiante para guardar QR.", ok=False)
            return
        student = get_student_by_dni(dni, DB_PATH)
        if not student:
            self._set_status("No se encontro el estudiante para el QR.", ok=False)
            return

        default_name = f"QR_{dni}_{student['apellidos']}_{student['nombres']}.png".replace(" ", "_")
        out = filedialog.asksaveasfilename(
            title="Guardar QR",
            defaultextension=".png",
            initialfile=default_name,
            filetypes=[("PNG", "*.png")],
        )
        if not out:
            return

        logo_path = get_setting("logo_path", "", DB_PATH)
        img = generate_student_qr_image(student, logo_path=logo_path)
        img.save(out)
        self._set_status(f"QR guardado en: {out}", ok=True)

    def _save_qr_batch_zip(self):
        students = self._students_for_qr_scope()
        if not students:
            self._set_status("No hay estudiantes para generar QRs con ese alcance/filtro.", ok=False)
            return

        out = filedialog.asksaveasfilename(
            title="Guardar ZIP de QRs",
            defaultextension=".zip",
            initialfile="QRs_filtrados.zip",
            filetypes=[("ZIP", "*.zip")],
        )
        if not out:
            return

        logo_path = get_setting("logo_path", "", DB_PATH)
        try:
            with zipfile.ZipFile(out, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                for student in students:
                    img = generate_student_qr_image(student, logo_path=logo_path)
                    safe_name = f"QR_{student['dni']}_{student['apellidos']}_{student['nombres']}.png".replace(" ", "_")
                    tmp = Path(out).with_name(safe_name)
                    img.save(tmp)
                    zf.write(tmp, arcname=safe_name)
                    tmp.unlink(missing_ok=True)
            self._set_status(f"QRs generados: {len(students)} en {out}", ok=True)
        except Exception as exc:
            self._set_status(f"Error generando ZIP de QRs: {exc}", ok=False)

    def _generate_id_cards_pdf(self):
        students = self._students_for_qr_scope()
        if not students:
            self._set_status("No hay estudiantes para generar fotocheck con ese alcance/filtro.", ok=False)
            return

        out = filedialog.asksaveasfilename(
            title="Guardar carnets PDF",
            defaultextension=".pdf",
            initialfile="Fotochecks_filtrados.pdf",
            filetypes=[("PDF", "*.pdf")],
        )
        if not out:
            return

        try:
            layout_map = {
                "8 por hoja (A4)": 8,
                "6 por hoja (A4)": 6,
                "1 por hoja (A4)": 1,
            }
            pdf = generate_id_cards_pdf(
                students=students,
                output_pdf=out,
                school_name=get_setting("school_name", "Asistencia Escolar", DB_PATH),
                logo_path=get_setting("logo_path", "", DB_PATH),
                minedu_logo_path=get_setting("minedu_logo_path", "", DB_PATH),
                place_label="Huanuco",
                cards_per_page=layout_map.get(self.qr_cards_layout_var.get().strip(), 8),
            )
            self._set_status(f"Carnets generados: {pdf}", ok=True)
        except Exception as exc:
            self._set_status(f"Error generando carnets: {exc}", ok=False)

    def _labeled_entry(self, parent, label, variable, row=None, pack=False):
        if pack:
            ctk.CTkLabel(parent, text=label, text_color=COLOR_TEXT).pack(anchor="w", padx=14)
            entry = ctk.CTkEntry(parent, textvariable=variable, fg_color="#111827", border_color="#334155", text_color=COLOR_TEXT)
            entry.pack(fill="x", padx=14, pady=(2, 8))
            return entry
        ctk.CTkLabel(parent, text=label, text_color=COLOR_TEXT).grid(row=row, column=0, sticky="w", padx=14, pady=(2, 2))
        entry = ctk.CTkEntry(parent, textvariable=variable, fg_color="#111827", border_color="#334155", text_color=COLOR_TEXT)
        entry.grid(row=row + 1, column=0, sticky="ew", padx=14, pady=(0, 8))
        return entry

    def _labeled_combo(self, parent, label, variable, values):
        ctk.CTkLabel(parent, text=label, text_color=COLOR_TEXT).pack(anchor="w", padx=14)
        combo = ctk.CTkComboBox(
            parent,
            variable=variable,
            values=values,
            state="readonly",
            fg_color="#111827",
            border_color="#334155",
            button_color="#1f2937",
            button_hover_color="#334155",
            text_color=COLOR_TEXT,
        )
        combo.pack(fill="x", padx=14, pady=(2, 8))
        return combo

    def _password_entry_with_eye(self, parent, label: str, variable: StringVar):
        ctk.CTkLabel(parent, text=label, text_color=COLOR_TEXT).pack(anchor="w", padx=10)
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=(2, 8))
        row.grid_columnconfigure(0, weight=1)

        entry = ctk.CTkEntry(row, textvariable=variable, show="*", fg_color="#111827", border_color="#334155", text_color=COLOR_TEXT)
        entry.grid(row=0, column=0, sticky="ew")
        eye = ctk.CTkButton(row, text="ojo", width=56, fg_color="#1f2937", hover_color="#334155")
        eye.grid(row=0, column=1, padx=(6, 0))
        eye.bind("<ButtonPress-1>", lambda _e: entry.configure(show=""))
        eye.bind("<ButtonRelease-1>", lambda _e: entry.configure(show="*"))
        eye.bind("<Leave>", lambda _e: entry.configure(show="*"))
        return entry

    def _set_status(self, message: str, ok: bool = True):
        color = "#0f3d2e" if ok else "#4c1d1d"
        self.status_label.configure(text=message, text_color=COLOR_TEXT, fg_color=color)

    def _register_manual(self):
        raw = self.dni_manual_var.get().strip()
        digits = "".join(ch for ch in raw if ch.isdigit())
        identifier = digits if len(digits) == 8 else ""
        if not identifier:
            selected = self.registro_search_list.curselection()
            if selected and selected[0] < len(self._registro_search_cache):
                identifier = str(self._registro_search_cache[selected[0]].get("dni", "")).strip()
            elif len(self._registro_search_cache) == 1:
                identifier = str(self._registro_search_cache[0].get("dni", "")).strip()

        if not identifier:
            self._set_status("Ingrese DNI valido o seleccione un estudiante de la lista.", ok=False)
            return
        self._register_attendance(identifier, source="manual")

    def _on_dni_manual_change(self, *_args):
        text = self.dni_manual_var.get().strip()
        self._on_registro_search_change()

        digits = "".join(ch for ch in text if ch.isdigit())
        # Barcode/QR scanners often act like keyboard input; auto-submit on exact DNI.
        if len(digits) == 8 and digits == text and not self._manual_autosubmit_pending:
            self._manual_autosubmit_pending = True
            self.after(60, self._auto_submit_manual_if_ready)

    def _on_registro_search_change(self, *_args):
        query = self.dni_manual_var.get().strip()
        rows = search_students(query, limit=60, db_path=DB_PATH)
        self._registro_search_cache = rows

        self.registro_search_list.delete(0, END)
        for s in rows:
            text = f"{s['dni']} | {s['apellidos']}, {s['nombres']} | {s['grado']}{s['seccion']} | {s['cargo']}"
            self.registro_search_list.insert(END, text)

    def _on_registro_search_select(self, _event=None):
        selected = self.registro_search_list.curselection()
        if not selected:
            return
        idx = selected[0]
        if idx >= len(self._registro_search_cache):
            return
        student = self._registro_search_cache[idx]
        self.dni_manual_var.set(student.get("dni", ""))
        self.dni_entry.focus_set()

    def _auto_submit_manual_if_ready(self):
        self._manual_autosubmit_pending = False
        text = self.dni_manual_var.get().strip()
        dni = "".join(ch for ch in text if ch.isdigit())
        if len(dni) == 8 and dni == text:
            self._register_manual()

    def _on_admin_dni_change(self, *_args):
        current = self.admin_vars["dni"].get()
        digits = "".join(ch for ch in current if ch.isdigit())[:8]
        if current != digits:
            self.admin_vars["dni"].set(digits)

    def _register_attendance(self, identifier: str, source: str = "manual"):
        teacher = get_setting("operator_name", "Soporte TIC", DB_PATH).strip() or "Soporte TIC"
        result = mark_attendance(
            identifier=identifier,
            profesor_encargado=teacher,
            source=source,
            db_path=DB_PATH,
        )
        self._set_status(result["message"], ok=result.get("ok", False))
        if result.get("ok") and result.get("estado") == "Tardanza":
            self.status_label.configure(fg_color="#7c2d12")

        if result.get("ok"):
            student = result["student"]
            self.last_student_label.configure(
                text=(
                    f"DNI: {student['dni']}\n"
                    f"Alumno: {student['nombres']} {student['apellidos']}\n"
                    f"Grado/Seccion: {student['grado']}{student['seccion']}\n"
                    f"Genero: {student['genero']}\n"
                    f"Cargo: {student['cargo']}\n"
                    f"Estado: {result['estado']}\n"
                    f"Hora: {result['hora']}\n"
                    f"Profesor: {result['profesor']}"
                )
            )
            self.dni_manual_var.set("")
            self.registro_search_list.delete(0, END)
            self._refresh_dashboard_stats()
            self._refresh_today_attendance_table()
            if hasattr(self, "dni_entry"):
                self.dni_entry.focus_set()

    def _refresh_today_attendance_table(self):
        if not hasattr(self, "today_attendance_tree"):
            return
        for item in self.today_attendance_tree.get_children():
            self.today_attendance_tree.delete(item)

        today = date.today().isoformat()
        with get_connection(DB_PATH) as conn:
            rows = conn.execute(
                """
                SELECT a.hora, e.dni, e.nombres, e.apellidos, e.grado, e.seccion, a.estado
                FROM asistencia a
                JOIN estudiantes e ON e.dni = a.estudiante_dni
                WHERE a.fecha = ?
                ORDER BY a.hora DESC
                """,
                (today,),
            ).fetchall()

        for r in rows:
            self.today_attendance_tree.insert(
                "",
                END,
                values=(
                    str(r["hora"]),
                    str(r["dni"]),
                    f"{r['apellidos']}, {r['nombres']}",
                    f"{r['grado']}{r['seccion']}",
                    str(r["estado"]),
                ),
            )

    def _start_camera(self):
        self.scanner.start()

    def _stop_camera(self):
        self.scanner.stop()
        self.camera_panel.configure(text="Camara detenida", image="")

    def _on_scanner_detect(self, identifier: str):
        self.after(0, lambda: self._register_attendance(identifier, source="scanner"))

    def _on_scanner_frame(self, frame):
        with self._frame_lock:
            self._latest_frame = frame.copy()

    def _on_scanner_error(self, message: str):
        self.after(0, lambda: self._set_status(message, ok=False))

    def _refresh_camera_panel(self):
        frame = None
        with self._frame_lock:
            if self._latest_frame is not None:
                frame = self._latest_frame
                self._latest_frame = None

        if frame is not None:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = Image.fromarray(rgb).resize((760, 460))
            self._camera_photo = ImageTk.PhotoImage(image=image)
            self.camera_panel.configure(image=self._camera_photo, text="")

        self.after(33, self._refresh_camera_panel)

    def _load_students_table(self, query: str = ""):
        if not hasattr(self, "students_tree"):
            return
        for item in self.students_tree.get_children():
            self.students_tree.delete(item)

        q = (query or "").strip()
        if q:
            rows = search_students(q, limit=1000, db_path=DB_PATH, only_active=False)
        else:
            rows = list_students(DB_PATH, only_active=False)

        grade_filter = self.admin_table_grado_var.get().strip().lower() if hasattr(self, "admin_table_grado_var") else "todos"
        section_filter = self.admin_table_seccion_var.get().strip().upper() if hasattr(self, "admin_table_seccion_var") else "TODOS"
        if grade_filter != "todos":
            rows = [r for r in rows if str(r.get("grado", "")).strip() == grade_filter]
        if section_filter != "TODOS":
            rows = [r for r in rows if str(r.get("seccion", "")).strip().upper() == section_filter]

        for student in rows:
            self.students_tree.insert(
                "",
                END,
                values=(
                    student["dni"],
                    student["nombres"],
                    student["apellidos"],
                    student["grado"],
                    student["seccion"],
                    student["genero"],
                    student["cargo"],
                    "Activo" if int(student.get("activo", 1)) == 1 else "Inactivo",
                ),
            )
        if hasattr(self, "qr_results_list"):
            self._refresh_qr_student_options()
        if hasattr(self, "admin_stats_label"):
            self._refresh_admin_quick_stats()

    def _on_admin_table_search_change(self, *_args):
        self._load_students_table(query=self.admin_table_search_var.get())

    def _on_admin_table_grade_change(self, *_args):
        if hasattr(self, "admin_table_seccion_combo"):
            values = ["todos", *self._section_values_for_grade(self.admin_table_grado_var.get(), include_all=False)]
            self.admin_table_seccion_combo.configure(values=values)
            if self.admin_table_seccion_var.get() not in values:
                self.admin_table_seccion_var.set("todos")
        self._load_students_table(query=self.admin_table_search_var.get())

    def _on_student_selected(self, _event=None):
        selected = self.students_tree.selection()
        if not selected:
            return
        values = self.students_tree.item(selected[0], "values")
        keys = ["dni", "nombres", "apellidos", "grado", "seccion", "genero", "cargo"]
        for key, value in zip(keys, values):
            self.admin_vars[key].set(str(value))

    def _clear_admin_form(self):
        self.admin_vars["dni"].set("")
        self.admin_vars["nombres"].set("")
        self.admin_vars["apellidos"].set("")
        self.admin_vars["grado"].set("1")
        secciones = self._section_values_for_grade("1", include_all=False)
        self.admin_vars["seccion"].set(secciones[0] if secciones else "A")
        self.admin_vars["genero"].set("M")
        self.admin_vars["cargo"].set("Alumno")

    def _add_student(self):
        try:
            add_student(
                dni=self.admin_vars["dni"].get(),
                nombres=self.admin_vars["nombres"].get(),
                apellidos=self.admin_vars["apellidos"].get(),
                grado=int(self.admin_vars["grado"].get()),
                seccion=self.admin_vars["seccion"].get(),
                genero=self.admin_vars["genero"].get(),
                cargo=self.admin_vars["cargo"].get(),
                db_path=DB_PATH,
            )
            self._set_status("Alumno agregado correctamente.", ok=True)
            self._refresh_dynamic_academic_options()
            self._load_students_table()
            self._clear_admin_form()
            self._refresh_dashboard_stats()
        except Exception as exc:
            self._set_status(f"Error agregando alumno: {exc}", ok=False)

    def _update_student(self):
        try:
            update_student(
                dni=self.admin_vars["dni"].get(),
                nombres=self.admin_vars["nombres"].get(),
                apellidos=self.admin_vars["apellidos"].get(),
                grado=int(self.admin_vars["grado"].get()),
                seccion=self.admin_vars["seccion"].get(),
                genero=self.admin_vars["genero"].get(),
                cargo=self.admin_vars["cargo"].get(),
                db_path=DB_PATH,
            )
            self._set_status("Alumno actualizado correctamente.", ok=True)
            self._refresh_dynamic_academic_options()
            self._load_students_table()
            self._refresh_dashboard_stats()
        except Exception as exc:
            self._set_status(f"Error actualizando alumno: {exc}", ok=False)

    def _deactivate_student(self):
        dni = self.admin_vars["dni"].get().strip()
        if not dni:
            self._set_status("Seleccione un alumno para desactivar.", ok=False)
            return

        if not messagebox.askyesno("Confirmar", "Desea desactivar este alumno?"):
            return

        try:
            set_student_active(dni, active=False, db_path=DB_PATH)
            self._set_status("Alumno desactivado correctamente.", ok=True)
            self._load_students_table()
            self._clear_admin_form()
            self._refresh_dashboard_stats()
        except Exception as exc:
            self._set_status(f"Error desactivando alumno: {exc}", ok=False)

    def _reactivate_student(self):
        dni = self.admin_vars["dni"].get().strip()
        if not dni:
            self._set_status("Seleccione un alumno para reactivar.", ok=False)
            return
        try:
            set_student_active(dni, active=True, db_path=DB_PATH)
            self._set_status("Alumno reactivado correctamente.", ok=True)
            self._load_students_table()
            self._refresh_dashboard_stats()
        except Exception as exc:
            self._set_status(f"Error reactivando alumno: {exc}", ok=False)

    def _import_students_file(self):
        path = filedialog.askopenfilename(
            title="Importar estudiantes",
            filetypes=[("Excel/CSV", "*.xlsx;*.xls;*.csv"), ("Todos", "*.*")],
        )
        if not path:
            return
        try:
            default_grado = self.import_default_grado_var.get().strip() if hasattr(self, "import_default_grado_var") else "archivo"
            default_seccion = self.import_default_seccion_var.get().strip() if hasattr(self, "import_default_seccion_var") else "archivo"
            result = import_students_from_file(
                path,
                db_path=DB_PATH,
                default_grado=int(default_grado) if default_grado and default_grado != "archivo" else None,
                default_seccion=default_seccion if default_seccion and default_seccion != "archivo" else None,
            )
            self._set_status(
                f"Importacion completa. Nuevos: {result['inserted']} | Actualizados: {result['updated']} | Omitidos: {result['skipped']}",
                ok=True,
            )
            self._refresh_dynamic_academic_options()
            self._load_students_table()
            self._refresh_dashboard_stats()
        except Exception as exc:
            self._set_status(f"Error importando archivo: {exc}", ok=False)

    def _add_section_from_admin(self):
        grado_raw = self.new_section_grade_var.get().strip() if hasattr(self, "new_section_grade_var") else ""
        seccion_raw = self.new_section_name_var.get().strip() if hasattr(self, "new_section_name_var") else ""
        if not grado_raw or not seccion_raw:
            self._set_status("Indique grado y seccion para agregar.", ok=False)
            return
        try:
            add_section_catalog(int(grado_raw), seccion_raw, db_path=DB_PATH)
            self.new_section_name_var.set("")
            self._refresh_dynamic_academic_options()
            self._set_status(f"Seccion {seccion_raw.upper()} agregada/reactivada en {grado_raw}to.", ok=True)
        except Exception as exc:
            self._set_status(f"Error agregando seccion: {exc}", ok=False)

    def _section_values_for_grade(self, grade_value: str, include_all: bool = False, include_select: bool = False):
        sections = []
        grade = (grade_value or "").strip().lower()
        if grade and grade not in {"todos", "seleccionar grado", "archivo"}:
            try:
                sections = [str(s["seccion"]) for s in list_sections(grado=int(grade), db_path=DB_PATH)]
            except Exception:
                sections = []
        if not sections:
            sections = sorted({str(s["seccion"]) for s in list_sections(db_path=DB_PATH)})
        base = []
        if include_all:
            base.append("todos")
        if include_select:
            base.append("Seleccionar seccion")
        for s in sections:
            if s not in base:
                base.append(s)
        return base

    def _refresh_dynamic_academic_options(self):
        try:
            sections = list_sections(db_path=DB_PATH)
        except Exception:
            sections = []
        self._sections_cache = sections
        grados = sorted({str(int(s["grado"])) for s in sections}) if sections else ["1", "2", "3", "4", "5"]

        if hasattr(self, "admin_grado_combo"):
            self.admin_grado_combo.configure(values=grados)
            if self.admin_vars["grado"].get() not in grados:
                self.admin_vars["grado"].set(grados[0] if grados else "1")
        if hasattr(self, "admin_seccion_combo"):
            secciones_admin = self._section_values_for_grade(self.admin_vars["grado"].get(), include_all=False)
            self.admin_seccion_combo.configure(values=secciones_admin)
            if self.admin_vars["seccion"].get() not in secciones_admin:
                self.admin_vars["seccion"].set(secciones_admin[0] if secciones_admin else "A")

        if hasattr(self, "admin_table_grado_combo"):
            values = ["todos", *grados]
            self.admin_table_grado_combo.configure(values=values)
            if self.admin_table_grado_var.get() not in values:
                self.admin_table_grado_var.set("todos")
        if hasattr(self, "admin_table_seccion_combo"):
            values = ["todos", *self._section_values_for_grade(self.admin_table_grado_var.get(), include_all=False)]
            self.admin_table_seccion_combo.configure(values=values)
            if self.admin_table_seccion_var.get() not in values:
                self.admin_table_seccion_var.set("todos")

        if hasattr(self, "report_grado_combo"):
            rep_grades = ["todos", *grados]
            self.report_grado_combo.configure(values=rep_grades)
            if self.filter_vars["grado"].get() not in rep_grades:
                self.filter_vars["grado"].set("todos")
        if hasattr(self, "report_seccion_combo"):
            rep_sections = self._section_values_for_grade(self.filter_vars["grado"].get(), include_all=True)
            self.report_seccion_combo.configure(values=rep_sections)
            if self.filter_vars["seccion"].get() not in rep_sections:
                self.filter_vars["seccion"].set("todos")

        if hasattr(self, "import_default_grado_combo"):
            import_grades = ["archivo", *grados]
            self.import_default_grado_combo.configure(values=import_grades)
            if self.import_default_grado_var.get() not in import_grades:
                self.import_default_grado_var.set("archivo")
        if hasattr(self, "new_section_grade_combo"):
            self.new_section_grade_combo.configure(values=grados)
            if self.new_section_grade_var.get() not in grados:
                self.new_section_grade_var.set(grados[0] if grados else "1")
        if hasattr(self, "sections_summary_label"):
            grouped = {}
            for row in sections:
                grouped.setdefault(int(row["grado"]), []).append(str(row["seccion"]))
            lines = []
            for g in sorted(grouped.keys()):
                sec = ", ".join(sorted(grouped[g]))
                lines.append(f"{g}to: {sec}")
            self.sections_summary_label.configure(text="\n".join(lines) if lines else "Sin secciones registradas")

        self._on_import_default_grade_change()
        self._refresh_qr_filter_options()

    def _on_admin_grade_change(self, *_args):
        if not hasattr(self, "admin_seccion_combo"):
            return
        values = self._section_values_for_grade(self.admin_vars["grado"].get(), include_all=False)
        self.admin_seccion_combo.configure(values=values)
        if self.admin_vars["seccion"].get() not in values:
            self.admin_vars["seccion"].set(values[0] if values else "A")

    def _on_report_grade_change(self, *_args):
        if not hasattr(self, "report_seccion_combo"):
            return
        values = self._section_values_for_grade(self.filter_vars["grado"].get(), include_all=True)
        self.report_seccion_combo.configure(values=values)
        if self.filter_vars["seccion"].get() not in values:
            self.filter_vars["seccion"].set("todos")

    def _on_import_default_grade_change(self, *_args):
        if not hasattr(self, "import_default_seccion_combo"):
            return
        grade = self.import_default_grado_var.get().strip()
        if not grade or grade == "archivo":
            values = ["archivo", *self._section_values_for_grade("todos", include_all=False)]
        else:
            values = ["archivo", *self._section_values_for_grade(grade, include_all=False)]
        self.import_default_seccion_combo.configure(values=values)
        if self.import_default_seccion_var.get() not in values:
            self.import_default_seccion_var.set("archivo")

    def _on_qr_grade_change(self, *_args):
        if hasattr(self, "qr_section_combo"):
            values = self._section_values_for_grade(self.qr_grade_var.get(), include_select=True)
            self.qr_section_combo.configure(values=values)
            if self.qr_section_var.get() not in values:
                self.qr_section_var.set("Seleccionar seccion")
        self._refresh_qr_filter_summary()

    def _backup_database(self):
        folder = filedialog.askdirectory(title="Seleccionar carpeta de backup")
        if not folder:
            return
        try:
            backup_file = create_database_backup(folder, db_path=DB_PATH)
            self._set_status(f"Backup creado: {backup_file}", ok=True)
        except Exception as exc:
            self._set_status(f"Error en backup: {exc}", ok=False)

    def _save_schedule_settings(self):
        try:
            set_attendance_schedule(
                entry_time=self.entry_time_var.get().strip(),
                tolerance_min=int(self.tolerance_var.get().strip() or "0"),
                db_path=DB_PATH,
            )
            set_setting("operator_name", self.operator_name_var.get().strip() or "Soporte TIC", DB_PATH)
            cutoff = get_attendance_cutoff(DB_PATH)
            self._set_status(
                f"Horario guardado. Corte tardanza: {cutoff.strftime('%H:%M')} | Operador: {get_setting('operator_name', 'Soporte TIC', DB_PATH)}",
                ok=True,
            )
        except Exception as exc:
            self._set_status(f"Error guardando horario: {exc}", ok=False)

    def _refresh_admin_quick_stats(self):
        stats = get_admin_quick_stats(DB_PATH)
        txt = (
            f"Hoy: {stats['present']}/{stats['total_active']} ({stats['ratio']}%)\n"
            f"Tardanzas del dia: {stats['tardy']}\n"
            f"Grado con mas faltas: {stats['top_absence_group']}"
        )
        self.admin_stats_label.configure(text=txt)

    def _get_picker_date(self, picker, fallback_var) -> str:
        if DateEntry and picker is not None:
            return picker.get_date().strftime("%Y-%m-%d")
        return fallback_var.get().strip()

    def _generate_report(self):
        try:
            ref_date = self._get_picker_date(getattr(self, "ref_date_picker", None), getattr(self, "ref_date_var", StringVar()))
            start_date = self._get_picker_date(getattr(self, "start_date_picker", None), getattr(self, "start_date_var", StringVar()))
            end_date = self._get_picker_date(getattr(self, "end_date_picker", None), getattr(self, "end_date_var", StringVar()))

            period_map = {
                "Dia": "day",
                "Semana": "week",
                "Mes": "month",
                "Rango": "range",
            }
            condition_map = {
                "Todos": "all",
                "Asistieron": "asistieron",
                "Faltaron": "faltaron",
            }
            period_value = period_map.get(self.filter_vars["period"].get(), "day")
            condition_value = condition_map.get(self.filter_vars["condition"].get(), "all")

            df = generate_report(
                db_path=DB_PATH,
                period=period_value,
                ref_date=ref_date,
                start_date=start_date,
                end_date=end_date,
                grado=self.filter_vars["grado"].get(),
                seccion=self.filter_vars["seccion"].get(),
                genero=self.filter_vars["genero"].get(),
                cargo=self.filter_vars["cargo"].get(),
                condition=condition_value,
            )
            self._last_report_df = df
            self._build_report_table(df.to_dict("records"))
            save_report_history(
                period=period_value,
                condition=condition_value,
                ref_date=ref_date,
                start_date=start_date,
                end_date=end_date,
                grado=self.filter_vars["grado"].get(),
                seccion=self.filter_vars["seccion"].get(),
                genero=self.filter_vars["genero"].get(),
                cargo=self.filter_vars["cargo"].get(),
                row_count=len(df),
                db_path=DB_PATH,
            )
            self._refresh_report_history_list()
            self._set_status(f"Reporte generado: {len(df)} filas.", ok=True)
        except Exception as exc:
            self._set_status(f"Error generando reporte: {exc}", ok=False)

    def _refresh_report_history_list(self):
        if not hasattr(self, "report_history_list"):
            return
        self._report_history_cache = list_report_history(limit=80, db_path=DB_PATH)
        self.report_history_list.delete(0, END)
        for item in self._report_history_cache:
            grado = (item.get("grado") or "todos").strip() or "todos"
            seccion = (item.get("seccion") or "todos").strip() or "todos"
            fecha = str(item.get("created_at", ""))
            period = str(item.get("period", "")).strip() or "day"
            condition = str(item.get("condition", "")).strip() or "all"
            rows = int(item.get("row_count", 0))
            label = f"{item['id']} | {fecha} | {period}/{condition} | {grado}-{seccion} | filas:{rows}"
            self.report_history_list.insert(END, label)

    def _open_selected_history_temp_report(self):
        if not hasattr(self, "report_history_list"):
            return
        selected = self.report_history_list.curselection()
        if not selected:
            self._set_status("Seleccione un elemento del historial.", ok=False)
            return
        idx = selected[0]
        if idx >= len(self._report_history_cache):
            self._set_status("Seleccion de historial invalida.", ok=False)
            return

        report_id = int(self._report_history_cache[idx]["id"])
        history = get_report_history(report_id, db_path=DB_PATH)
        if not history:
            self._set_status("No se encontro el reporte en historial.", ok=False)
            return

        try:
            df = generate_report(
                db_path=DB_PATH,
                period=(history.get("period") or "day").strip() or "day",
                ref_date=(history.get("ref_date") or "").strip() or None,
                start_date=(history.get("start_date") or "").strip() or None,
                end_date=(history.get("end_date") or "").strip() or None,
                grado=(history.get("grado") or "").strip() or None,
                seccion=(history.get("seccion") or "").strip() or None,
                genero=(history.get("genero") or "").strip() or None,
                cargo=(history.get("cargo") or "").strip() or None,
                condition=(history.get("condition") or "all").strip() or "all",
            )
            if df.empty:
                self._set_status("El reporte historico no tiene datos para mostrar hoy.", ok=False)
                return

            temp_dir = Path(tempfile.gettempdir()) / "registro_asistencia_reports"
            temp_dir.mkdir(parents=True, exist_ok=True)
            temp_file = temp_dir / f"historial_reporte_{report_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"

            export_report_to_pdf(
                df,
                str(temp_file),
                school_name=get_setting("school_name", "Asistencia Escolar", DB_PATH),
                logo_path=get_setting("logo_path", "", DB_PATH),
                report_type=f"Historial #{report_id}",
                generated_by=get_setting("operator_name", "Soporte TIC", DB_PATH),
            )

            os.startfile(str(temp_file))
            self._schedule_temp_file_cleanup(temp_file, delay_sec=self._temp_report_ttl_sec)
            self._set_status("Reporte historico abierto en PDF temporal (se elimina automatico).", ok=True)
        except Exception as exc:
            self._set_status(f"No se pudo abrir historial temporal: {exc}", ok=False)

    def _schedule_temp_file_cleanup(self, path: Path, delay_sec: int = 600):
        target = Path(path)

        def _delete_later():
            try:
                if target.exists():
                    target.unlink(missing_ok=True)
            except Exception:
                pass

        timer = threading.Timer(max(30, int(delay_sec)), _delete_later)
        timer.daemon = True
        timer.start()
        self._temp_cleanup_timers.append(timer)

    def _build_report_table(self, rows):
        for item in self.report_tree.get_children():
            self.report_tree.delete(item)
        for row in rows:
            self.report_tree.insert(
                "",
                END,
                values=(
                    row.get("fecha", ""),
                    row.get("hora", ""),
                    row.get("dni", ""),
                    row.get("nombres", ""),
                    row.get("apellidos", ""),
                    row.get("grado", ""),
                    row.get("seccion", ""),
                    row.get("genero", ""),
                    row.get("cargo", ""),
                    row.get("profesor_encargado", ""),
                    row.get("estado", ""),
                ),
            )

    def _export_report(self):
        if self._last_report_df is None or self._last_report_df.empty:
            self._set_status("Genere un reporte antes de exportar.", ok=False)
            return

        fmt = getattr(self, "report_export_format_var", StringVar(value="Excel (.xlsx)")).get().strip()
        export_pdf = fmt.startswith("PDF")
        default_ext = ".pdf" if export_pdf else ".xlsx"
        type_opt = [("PDF", "*.pdf")] if export_pdf else [("Excel", "*.xlsx")]

        period_name = str(self.filter_vars.get("period", StringVar(value="Reporte")).get()).strip() or "Reporte"
        condition_name = str(self.filter_vars.get("condition", StringVar(value="Todos")).get()).strip() or "Todos"
        safe_period = "".join(ch if ch.isalnum() else "_" for ch in period_name)
        safe_condition = "".join(ch if ch.isalnum() else "_" for ch in condition_name)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        initial_name = f"Reporte_{safe_period}_{safe_condition}_{stamp}{default_ext}"

        path = filedialog.asksaveasfilename(
            defaultextension=default_ext,
            filetypes=type_opt,
            title="Guardar reporte",
            initialfile=initial_name,
        )
        if not path:
            return

        try:
            school_name = get_setting("school_name", "Asistencia Escolar", DB_PATH)
            logo_path = get_setting("logo_path", "", DB_PATH)
            report_type = f"{self.filter_vars['period'].get()} / {self.filter_vars['condition'].get()}"
            operator_name = get_setting("operator_name", "Soporte TIC", DB_PATH)

            if export_pdf:
                export_report_to_pdf(
                    self._last_report_df,
                    path,
                    school_name=school_name,
                    logo_path=logo_path,
                    report_type=report_type,
                    generated_by=operator_name,
                )
            else:
                export_report_to_excel(
                    self._last_report_df,
                    path,
                    school_name=school_name,
                    logo_path=logo_path,
                )
            self._set_status(f"Reporte exportado: {path}", ok=True)
        except Exception as exc:
            self._set_status(f"Error exportando reporte: {exc}", ok=False)

    def _on_close(self):
        self.scanner.stop()
        for timer in getattr(self, "_temp_cleanup_timers", []):
            try:
                timer.cancel()
            except Exception:
                pass
        self.destroy()


if __name__ == "__main__":
    app = AttendanceApp()
    app.mainloop()
