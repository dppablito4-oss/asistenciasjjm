import hashlib
import os
import re
import secrets
import shutil
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

APP_NAME = "RegistroAsistenciaEscolar"


def get_app_data_dir() -> Path:
    base = os.getenv("APPDATA")
    root = Path(base) if base else (Path.home() / "AppData" / "Roaming")
    target = root / APP_NAME
    target.mkdir(parents=True, exist_ok=True)
    return target


def _legacy_db_candidates() -> List[Path]:
    candidates: List[Path] = [
        Path(__file__).resolve().parent / "colegio.db",
        Path.cwd() / "colegio.db",
    ]
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "colegio.db")

    unique: List[Path] = []
    seen = set()
    for p in candidates:
        key = str(p.resolve()) if p.exists() else str(p)
        if key in seen:
            continue
        seen.add(key)
        unique.append(p)
    return unique


DB_PATH = get_app_data_dir() / "colegio.db"
TARDY_CUTOFF = time(hour=8, minute=0)
DEFAULT_ADMIN_PASSWORD = "1111"

MALE_FIRST_NAMES = [
    "Luis", "Carlos", "Jose", "Miguel", "Jorge", "Diego", "Kevin", "Andres", "Bruno", "Ronal",
    "Mario", "Pedro", "Cesar", "Raul", "Ivan", "Marco", "Joel", "Adrian", "Fabricio", "Samuel",
]
FEMALE_FIRST_NAMES = [
    "Ana", "Maria", "Sofia", "Lucia", "Camila", "Valeria", "Daniela", "Paola", "Ruth", "Andrea",
    "Milagros", "Noemi", "Brenda", "Rocio", "Carla", "Diana", "Elena", "Fiorella", "Gina", "Melissa",
]
LAST_NAMES = [
    "Perez", "Lopez", "Garcia", "Rojas", "Quispe", "Flores", "Mamani", "Torres", "Sanchez", "Vargas",
    "Diaz", "Mendoza", "Ruiz", "Gutierrez", "Rivera", "Ramos", "Castillo", "Paredes", "Alarcon", "Silva",
]


@contextmanager
def get_connection(db_path: Path = DB_PATH):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_qr_token_schema(conn: sqlite3.Connection) -> None:
    cols = conn.execute("PRAGMA table_info(estudiantes)").fetchall()
    names = {c[1] for c in cols}
    if "qr_token" not in names:
        conn.execute("ALTER TABLE estudiantes ADD COLUMN qr_token TEXT")
    if "activo" not in names:
        conn.execute("ALTER TABLE estudiantes ADD COLUMN activo INTEGER NOT NULL DEFAULT 1")

    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_estudiantes_qr_token ON estudiantes(qr_token)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_estudiantes_activo ON estudiantes(activo)")


def _ensure_sections_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS academic_sections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            grado INTEGER NOT NULL CHECK (grado BETWEEN 1 AND 5),
            seccion TEXT NOT NULL,
            activo INTEGER NOT NULL DEFAULT 1,
            UNIQUE(grado, seccion)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sections_grado ON academic_sections(grado)")


def _seed_default_sections(conn: sqlite3.Connection) -> None:
    for g in range(1, 6):
        for s in ("A", "B", "C"):
            conn.execute(
                "INSERT OR IGNORE INTO academic_sections (grado, seccion, activo) VALUES (?, ?, 1)",
                (g, s),
            )


def _ensure_estudiantes_table_dynamic(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='estudiantes'"
    ).fetchone()
    create_sql = (row[0] if row else "") or ""
    if "seccion IN ('A', 'B', 'C')" not in create_sql:
        return

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS estudiantes_new (
            dni TEXT PRIMARY KEY,
            nombres TEXT NOT NULL,
            apellidos TEXT NOT NULL,
            grado INTEGER NOT NULL CHECK (grado BETWEEN 1 AND 5),
            seccion TEXT NOT NULL,
            genero TEXT NOT NULL CHECK (genero IN ('M', 'F')),
            cargo TEXT NOT NULL,
            qr_token TEXT UNIQUE,
            activo INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO estudiantes_new (dni, nombres, apellidos, grado, seccion, genero, cargo, qr_token, activo)
        SELECT dni, nombres, apellidos, grado, seccion, genero, cargo, qr_token, COALESCE(activo, 1)
        FROM estudiantes
        """
    )
    conn.execute("DROP TABLE estudiantes")
    conn.execute("ALTER TABLE estudiantes_new RENAME TO estudiantes")


def _ensure_admin_recovery_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_recovery_codes (
            code_hash TEXT PRIMARY KEY,
            batch_id TEXT NOT NULL,
            used INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            used_at TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_admin_recovery_batch ON admin_recovery_codes(batch_id)")


def _ensure_report_history_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS report_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            period TEXT NOT NULL,
            condition TEXT NOT NULL,
            ref_date TEXT,
            start_date TEXT,
            end_date TEXT,
            grado TEXT,
            seccion TEXT,
            genero TEXT,
            cargo TEXT,
            row_count INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_report_history_created_at ON report_history(created_at)")


def _generate_unique_qr_token(conn: sqlite3.Connection) -> str:
    while True:
        token = secrets.token_urlsafe(16)
        row = conn.execute("SELECT 1 FROM estudiantes WHERE qr_token = ? LIMIT 1", (token,)).fetchone()
        if row is None:
            return token


def init_db(db_path: Path = DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS estudiantes (
                dni TEXT PRIMARY KEY,
                nombres TEXT NOT NULL,
                apellidos TEXT NOT NULL,
                grado INTEGER NOT NULL CHECK (grado BETWEEN 1 AND 5),
                seccion TEXT NOT NULL,
                genero TEXT NOT NULL CHECK (genero IN ('M', 'F')),
                cargo TEXT NOT NULL,
                qr_token TEXT UNIQUE,
                activo INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        _ensure_estudiantes_table_dynamic(conn)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS asistencia (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                estudiante_dni TEXT NOT NULL,
                fecha TEXT NOT NULL,
                hora TEXT NOT NULL,
                profesor_encargado TEXT NOT NULL,
                estado TEXT NOT NULL CHECK (estado IN ('Asistio', 'Tardanza')),
                FOREIGN KEY (estudiante_dni) REFERENCES estudiantes(dni) ON DELETE CASCADE
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_asistencia_fecha ON asistencia(fecha)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_asistencia_dni_fecha ON asistencia(estudiante_dni, fecha)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS configuracion (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                nombre_colegio TEXT NOT NULL DEFAULT 'Asistencia Escolar',
                ruta_insignia TEXT NOT NULL DEFAULT '',
                ruta_foto_panoramica TEXT NOT NULL DEFAULT '',
                ruta_logo_minedu TEXT NOT NULL DEFAULT '',
                hora_entrada TEXT NOT NULL DEFAULT '08:00',
                minutos_tolerancia INTEGER NOT NULL DEFAULT 10,
                admin_password TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO configuracion
            (id, nombre_colegio, ruta_insignia, ruta_foto_panoramica, ruta_logo_minedu, hora_entrada, minutos_tolerancia, admin_password)
            VALUES (1, 'Asistencia Escolar', '', '', '', '08:00', 10, '')
            """
        )
        _ensure_admin_recovery_schema(conn)
        _ensure_report_history_schema(conn)
        _ensure_sections_schema(conn)
        _seed_default_sections(conn)
        _ensure_qr_token_schema(conn)


def ensure_default_settings(db_path: Path = DB_PATH) -> None:
    defaults = {
        "school_name": "Asistencia Escolar",
        "logo_path": "",
        "entry_time": "08:00",
        "tolerance_min": "10",
    }
    with get_connection(db_path) as conn:
        for key, value in defaults.items():
            conn.execute("INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)", (key, value))


def seed_students(db_path: Path = DB_PATH) -> None:
    sample_students = [
        ("12345678", "Ana", "Lopez", 1, "A", "F", "Alumno"),
        ("23456789", "Luis", "Perez", 2, "B", "M", "Alumno"),
        ("34567890", "Maria", "Rojas", 3, "C", "F", "Brigadier"),
        ("45678901", "Jose", "Quispe", 4, "A", "M", "Policia Escolar"),
        ("56789012", "Sofia", "Garcia", 5, "B", "F", "Alumno"),
    ]
    with get_connection(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) AS total FROM estudiantes").fetchone()["total"]
        if count > 0:
            return
        for row in sample_students:
            conn.execute(
                """
                INSERT INTO estudiantes (dni, nombres, apellidos, grado, seccion, genero, cargo, qr_token, activo)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (*row, _generate_unique_qr_token(conn)),
            )


def ensure_student_qr_tokens(db_path: Path = DB_PATH) -> None:
    with get_connection(db_path) as conn:
        _ensure_qr_token_schema(conn)
        rows = conn.execute("SELECT dni FROM estudiantes WHERE qr_token IS NULL OR qr_token = ''").fetchall()
        for row in rows:
            conn.execute("UPDATE estudiantes SET qr_token = ? WHERE dni = ?", (_generate_unique_qr_token(conn), row["dni"]))


def bootstrap_database(db_path: Path = DB_PATH) -> None:
    db_path = Path(db_path)
    if not db_path.exists():
        for candidate in _legacy_db_candidates():
            if not candidate.exists():
                continue
            try:
                if candidate.resolve() == db_path.resolve():
                    continue
            except Exception:
                pass
            try:
                db_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(candidate, db_path)
                break
            except Exception:
                pass
    init_db(db_path)
    seed_students(db_path)
    ensure_default_settings(db_path)
    ensure_student_qr_tokens(db_path)


def save_report_history(
    period: str,
    condition: str,
    ref_date: Optional[str],
    start_date: Optional[str],
    end_date: Optional[str],
    grado: Optional[str],
    seccion: Optional[str],
    genero: Optional[str],
    cargo: Optional[str],
    row_count: int,
    db_path: Path = DB_PATH,
) -> int:
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_connection(db_path) as conn:
        _ensure_report_history_schema(conn)
        cur = conn.execute(
            """
            INSERT INTO report_history
            (created_at, period, condition, ref_date, start_date, end_date, grado, seccion, genero, cargo, row_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                created_at,
                (period or "").strip(),
                (condition or "").strip(),
                (ref_date or "").strip(),
                (start_date or "").strip(),
                (end_date or "").strip(),
                (grado or "").strip(),
                (seccion or "").strip(),
                (genero or "").strip(),
                (cargo or "").strip(),
                max(0, int(row_count)),
            ),
        )
        return int(cur.lastrowid)


def list_report_history(limit: int = 100, db_path: Path = DB_PATH) -> List[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        _ensure_report_history_schema(conn)
        rows = conn.execute(
            """
            SELECT id, created_at, period, condition, ref_date, start_date, end_date, grado, seccion, genero, cargo, row_count
            FROM report_history
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
    return [dict(r) for r in rows]


def get_report_history(report_id: int, db_path: Path = DB_PATH) -> Optional[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        _ensure_report_history_schema(conn)
        row = conn.execute(
            """
            SELECT id, created_at, period, condition, ref_date, start_date, end_date, grado, seccion, genero, cargo, row_count
            FROM report_history
            WHERE id = ?
            LIMIT 1
            """,
            (int(report_id),),
        ).fetchone()
    return dict(row) if row else None


def _legacy_key_to_config_column(key: str) -> Optional[str]:
    return {
        "school_name": "nombre_colegio",
        "logo_path": "ruta_insignia",
        "entry_time": "hora_entrada",
        "tolerance_min": "minutos_tolerancia",
        "panoramic_path": "ruta_foto_panoramica",
        "minedu_logo_path": "ruta_logo_minedu",
    }.get(key)


def get_config(db_path: Path = DB_PATH) -> Dict[str, Any]:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM configuracion WHERE id = 1").fetchone()
    return dict(row) if row else {}


def update_config(values: Dict[str, Any], db_path: Path = DB_PATH) -> None:
    if not values:
        return
    allowed = {
        "nombre_colegio",
        "ruta_insignia",
        "ruta_foto_panoramica",
        "ruta_logo_minedu",
        "hora_entrada",
        "minutos_tolerancia",
        "admin_password",
    }
    filtered = {k: v for k, v in values.items() if k in allowed}
    if not filtered:
        return

    set_sql = ", ".join([f"{k} = ?" for k in filtered.keys()])
    params = [filtered[k] for k in filtered.keys()] + [1]
    with get_connection(db_path) as conn:
        conn.execute(f"UPDATE configuracion SET {set_sql} WHERE id = ?", params)


def get_setting(key: str, default: str = "", db_path: Path = DB_PATH) -> str:
    col = _legacy_key_to_config_column(key)
    if col:
        cfg = get_config(db_path)
        value = cfg.get(col)
        return default if value is None else str(value)
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return default if row is None else str(row["value"])


def set_setting(key: str, value: str, db_path: Path = DB_PATH) -> None:
    col = _legacy_key_to_config_column(key)
    if col:
        update_config({col: value}, db_path=db_path)
        return
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO app_settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )


def validate_admin_password_strength(password: str) -> bool:
    pw = password or ""
    if len(pw) < 6:
        return False
    if not re.search(r"[A-Z]", pw):
        return False
    if not re.search(r"[a-z]", pw):
        return False
    if not re.search(r"\d", pw):
        return False
    return True


def _hash_password(password: str) -> str:
    return hashlib.sha256((password or "").encode("utf-8")).hexdigest()


def has_admin_password(db_path: Path = DB_PATH) -> bool:
    cfg = get_config(db_path)
    return bool(str(cfg.get("admin_password", "")).strip())


def set_admin_password_generic(db_path: Path = DB_PATH) -> None:
    # Clave de emergencia/arranque; debe forzar cambio posterior.
    update_config({"admin_password": _hash_password(DEFAULT_ADMIN_PASSWORD)}, db_path=db_path)


def is_default_admin_password(db_path: Path = DB_PATH) -> bool:
    cfg = get_config(db_path)
    stored = str(cfg.get("admin_password", "")).strip()
    if not stored:
        return False
    return stored in {_hash_password(DEFAULT_ADMIN_PASSWORD), DEFAULT_ADMIN_PASSWORD}


def set_admin_password(password: str, db_path: Path = DB_PATH) -> None:
    if not validate_admin_password_strength(password):
        raise ValueError("La contraseña debe tener minimo 6 caracteres, mayusculas, minusculas y numeros")
    update_config({"admin_password": _hash_password(password)}, db_path=db_path)


def verify_admin_password(password: str, db_path: Path = DB_PATH) -> bool:
    cfg = get_config(db_path)
    stored = str(cfg.get("admin_password", "")).strip()
    if not stored:
        return False

    candidate_hash = _hash_password(password)
    if stored == candidate_hash:
        return True

    # Compatibilidad con instalaciones antiguas que guardaron texto plano.
    if stored == (password or ""):
        update_config({"admin_password": candidate_hash}, db_path=db_path)
        return True
    return False


def change_admin_password(current_password: str, new_password: str, db_path: Path = DB_PATH) -> None:
    if not verify_admin_password(current_password, db_path=db_path):
        raise ValueError("La contrasena actual no es correcta")
    set_admin_password(new_password, db_path=db_path)


def _normalize_recovery_code(code: str) -> str:
    raw = (code or "").strip().upper()
    return "".join(ch for ch in raw if ch.isalnum())


def _hash_recovery_code(code: str) -> str:
    return hashlib.sha256(_normalize_recovery_code(code).encode("utf-8")).hexdigest()


def _new_recovery_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    parts = []
    for _ in range(3):
        token = "".join(secrets.choice(alphabet) for _ in range(4))
        parts.append(token)
    return "-".join(parts)


def generate_admin_recovery_codes(output_txt: str, count: int = 10, db_path: Path = DB_PATH) -> Dict[str, Any]:
    qty = int(count)
    if qty <= 0 or qty > 200:
        raise ValueError("Cantidad invalida. Use entre 1 y 200")

    out = Path(output_txt)
    out.parent.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    batch_id = datetime.now().strftime("BATCH_%Y%m%d_%H%M%S")

    codes: List[str] = []
    seen = set()
    while len(codes) < qty:
        code = _new_recovery_code()
        norm = _normalize_recovery_code(code)
        if norm in seen:
            continue
        seen.add(norm)
        codes.append(code)

    with get_connection(db_path) as conn:
        _ensure_admin_recovery_schema(conn)
        # Invalida cualquier lote anterior (usado o no usado).
        conn.execute("DELETE FROM admin_recovery_codes")
        for code in codes:
            conn.execute(
                """
                INSERT INTO admin_recovery_codes (code_hash, batch_id, used, created_at, used_at)
                VALUES (?, ?, 0, ?, NULL)
                """,
                (_hash_recovery_code(code), batch_id, created_at),
            )

    lines = [
        "CODIGOS DE RECUPERACION ADMIN (UN SOLO USO)",
        f"Generado: {created_at}",
        f"Lote: {batch_id}",
        "IMPORTANTE: Al generar un nuevo lote, todos los codigos anteriores quedan invalidados.",
        "",
    ]
    for idx, code in enumerate(codes, start=1):
        lines.append(f"{idx:02d}. {code}")

    out.write_text("\n".join(lines), encoding="utf-8")
    return {"file": str(out), "count": qty, "batch_id": batch_id}


def consume_admin_recovery_code(code: str, db_path: Path = DB_PATH) -> bool:
    code_hash = _hash_recovery_code(code)
    if not code_hash:
        return False

    with get_connection(db_path) as conn:
        _ensure_admin_recovery_schema(conn)
        row = conn.execute(
            "SELECT code_hash, used FROM admin_recovery_codes WHERE code_hash = ? LIMIT 1",
            (code_hash,),
        ).fetchone()
        if row is None or int(row["used"]) == 1:
            return False
        conn.execute(
            "UPDATE admin_recovery_codes SET used = 1, used_at = ? WHERE code_hash = ?",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), code_hash),
        )
    return True


def reset_admin_password_with_recovery_code(recovery_code: str, db_path: Path = DB_PATH) -> None:
    if not consume_admin_recovery_code(recovery_code, db_path=db_path):
        raise ValueError("Codigo de recuperacion invalido o ya usado")
    set_admin_password_generic(db_path=db_path)


def get_attendance_cutoff(db_path: Path = DB_PATH) -> time:
    cfg = get_config(db_path)
    entry_str = str(cfg.get("hora_entrada", "08:00"))
    tolerance_str = str(cfg.get("minutos_tolerancia", "10"))
    try:
        hh, mm = entry_str.split(":", 1)
        base_minutes = int(hh) * 60 + int(mm)
    except Exception:
        base_minutes = 8 * 60
    try:
        tolerance = max(0, int(tolerance_str))
    except Exception:
        tolerance = 10
    total = base_minutes + tolerance
    return time(hour=(total // 60) % 24, minute=total % 60)


def set_attendance_schedule(entry_time: str, tolerance_min: int, db_path: Path = DB_PATH) -> None:
    parts = (entry_time or "08:00").strip().split(":")
    if len(parts) != 2:
        raise ValueError("Hora invalida. Use formato HH:MM")
    hh = int(parts[0])
    mm = int(parts[1])
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError("Hora invalida. Use formato HH:MM")
    update_config({"hora_entrada": f"{hh:02d}:{mm:02d}", "minutos_tolerancia": max(0, int(tolerance_min))}, db_path=db_path)


def _normalize_dni(dni: str) -> str:
    return "".join(ch for ch in (dni or "") if ch.isdigit())


def _normalize_section(seccion: str) -> str:
    return (seccion or "").strip().upper()


def add_section_catalog(grado: int, seccion: str, db_path: Path = DB_PATH) -> None:
    g = int(grado)
    if not (1 <= g <= 5):
        raise ValueError("Grado invalido. Use 1..5")
    s = _normalize_section(seccion)
    if not s:
        raise ValueError("Seccion invalida")
    with get_connection(db_path) as conn:
        _ensure_sections_schema(conn)
        conn.execute(
            """
            INSERT INTO academic_sections (grado, seccion, activo)
            VALUES (?, ?, 1)
            ON CONFLICT(grado, seccion) DO UPDATE SET activo = 1
            """,
            (g, s),
        )


def list_sections(grado: Optional[int] = None, db_path: Path = DB_PATH) -> List[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        _ensure_sections_schema(conn)
        if grado is None:
            rows = conn.execute(
                "SELECT grado, seccion, activo FROM academic_sections WHERE activo = 1 ORDER BY grado, seccion"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT grado, seccion, activo FROM academic_sections WHERE activo = 1 AND grado = ? ORDER BY seccion",
                (int(grado),),
            ).fetchall()
    return [dict(r) for r in rows]


def _ensure_section_exists(conn: sqlite3.Connection, grado: int, seccion: str) -> None:
    _ensure_sections_schema(conn)
    s = _normalize_section(seccion)
    conn.execute(
        """
        INSERT INTO academic_sections (grado, seccion, activo)
        VALUES (?, ?, 1)
        ON CONFLICT(grado, seccion) DO UPDATE SET activo = 1
        """,
        (int(grado), s),
    )


def get_student_by_dni(dni: str, db_path: Path = DB_PATH) -> Optional[Dict[str, Any]]:
    normalized = _normalize_dni(dni)
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM estudiantes WHERE dni = ?", (normalized,)).fetchone()
    return dict(row) if row else None


def get_student_by_identifier(identifier: str, lookup_mode: str = "auto", db_path: Path = DB_PATH) -> Optional[Dict[str, Any]]:
    raw = (identifier or "").strip()
    if not raw:
        return None
    if lookup_mode == "manual":
        dni_key = _normalize_dni(raw)
        token_key = "__NO_TOKEN__"
    elif lookup_mode == "scanner":
        dni_key = ""
        token_key = raw
    else:
        dni_key = _normalize_dni(raw)
        token_key = raw

    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM estudiantes WHERE (dni = ? OR qr_token = ?) AND activo = 1 LIMIT 1",
            (dni_key, token_key),
        ).fetchone()
    return dict(row) if row else None


def list_students(db_path: Path = DB_PATH, only_active: bool = True) -> List[Dict[str, Any]]:
    where = "WHERE activo = 1" if only_active else ""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT dni, nombres, apellidos, grado, seccion, genero, cargo, qr_token, activo FROM estudiantes {where} ORDER BY apellidos, nombres"
        ).fetchall()
    return [dict(r) for r in rows]


def search_students(query: str, limit: int = 100, db_path: Path = DB_PATH, only_active: bool = True) -> List[Dict[str, Any]]:
    q = (query or "").strip()
    if not q:
        return list_students(db_path=db_path, only_active=only_active)
    q_like = f"%{q}%"
    digits = "".join(ch for ch in q if ch.isdigit())
    active_sql = " AND activo = 1" if only_active else ""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT dni, nombres, apellidos, grado, seccion, genero, cargo, qr_token, activo
            FROM estudiantes
            WHERE (
                dni LIKE ?
                OR qr_token LIKE ?
                OR nombres LIKE ?
                OR apellidos LIKE ?
                OR CAST(grado AS TEXT) LIKE ?
                OR seccion LIKE ?
                OR genero LIKE ?
                OR cargo LIKE ?
            )
            """ + active_sql + """
            ORDER BY apellidos, nombres
            LIMIT ?
            """,
            (
                f"%{digits}%" if digits else q_like,
                q_like,
                q_like,
                q_like,
                q_like,
                q_like,
                q_like,
                q_like,
                int(limit),
            ),
        ).fetchall()
    return [dict(r) for r in rows]


def add_student(dni: str, nombres: str, apellidos: str, grado: int, seccion: str, genero: str, cargo: str, db_path: Path = DB_PATH) -> None:
    normalized = _normalize_dni(dni)
    if len(normalized) != 8:
        raise ValueError("El DNI debe tener 8 digitos")
    with get_connection(db_path) as conn:
        _ensure_section_exists(conn, int(grado), _normalize_section(seccion))
        conn.execute(
            """
            INSERT INTO estudiantes (dni, nombres, apellidos, grado, seccion, genero, cargo, qr_token, activo)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                normalized,
                (nombres or "").strip(),
                (apellidos or "").strip(),
                int(grado),
                _normalize_section(seccion),
                (genero or "").strip().upper(),
                (cargo or "").strip(),
                _generate_unique_qr_token(conn),
            ),
        )


def update_student(dni: str, nombres: str, apellidos: str, grado: int, seccion: str, genero: str, cargo: str, db_path: Path = DB_PATH) -> None:
    normalized = _normalize_dni(dni)
    with get_connection(db_path) as conn:
        _ensure_section_exists(conn, int(grado), _normalize_section(seccion))
        cur = conn.execute(
            """
            UPDATE estudiantes
            SET nombres = ?, apellidos = ?, grado = ?, seccion = ?, genero = ?, cargo = ?
            WHERE dni = ?
            """,
            (
                (nombres or "").strip(),
                (apellidos or "").strip(),
                int(grado),
                _normalize_section(seccion),
                (genero or "").strip().upper(),
                (cargo or "").strip(),
                normalized,
            ),
        )
        if cur.rowcount == 0:
            raise ValueError("Estudiante no encontrado")


def set_student_active(dni: str, active: bool, db_path: Path = DB_PATH) -> None:
    normalized = _normalize_dni(dni)
    with get_connection(db_path) as conn:
        cur = conn.execute("UPDATE estudiantes SET activo = ? WHERE dni = ?", (1 if active else 0, normalized))
        if cur.rowcount == 0:
            raise ValueError("Estudiante no encontrado")


def delete_student(dni: str, db_path: Path = DB_PATH) -> None:
    normalized = _normalize_dni(dni)
    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM estudiantes WHERE dni = ?", (normalized,))


def attendance_exists_for_day(dni: str, date_str: str, db_path: Path = DB_PATH) -> bool:
    normalized = _normalize_dni(dni)
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT 1 FROM asistencia WHERE estudiante_dni = ? AND fecha = ? LIMIT 1", (normalized, date_str)).fetchone()
    return row is not None


def mark_attendance(identifier: str, profesor_encargado: str, when: Optional[datetime] = None, source: str = "manual", db_path: Path = DB_PATH) -> Dict[str, Any]:
    raw = (identifier or "").strip()
    if source == "manual":
        normalized = _normalize_dni(raw)
        if len(normalized) != 8:
            return {"ok": False, "message": "DNI invalido. Debe tener 8 digitos."}
        lookup = normalized
    else:
        if not raw:
            return {"ok": False, "message": "Codigo no autorizado."}
        lookup = raw

    student = get_student_by_identifier(lookup, lookup_mode=source, db_path=db_path)
    if not student:
        if source == "manual":
            return {"ok": False, "message": "DNI no existe en la base de datos."}
        return {"ok": False, "message": "Codigo no autorizado."}

    now = when or datetime.now()
    date_str = now.date().isoformat()
    time_str = now.strftime("%H:%M:%S")
    if attendance_exists_for_day(student["dni"], date_str, db_path=db_path):
        return {"ok": False, "message": "Asistencia duplicada: el estudiante ya marco hoy.", "student": student}

    status = "Tardanza" if now.time() > get_attendance_cutoff(db_path=db_path) else "Asistio"
    teacher = (profesor_encargado or "").strip()
    if not teacher:
        return {"ok": False, "message": "Ingrese profesor encargado."}

    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO asistencia (estudiante_dni, fecha, hora, profesor_encargado, estado)
            VALUES (?, ?, ?, ?, ?)
            """,
            (student["dni"], date_str, time_str, teacher, status),
        )

    return {
        "ok": True,
        "message": f"Registro exitoso: {student['nombres']} {student['apellidos']} ({status}).",
        "student": student,
        "estado": status,
        "fecha": date_str,
        "hora": time_str,
        "profesor": teacher,
    }


def fetch_attendance_between(start_date: str, end_date: str, db_path: Path = DB_PATH) -> List[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT a.id, a.estudiante_dni, a.fecha, a.hora, a.profesor_encargado, a.estado,
                   e.nombres, e.apellidos, e.grado, e.seccion, e.genero, e.cargo
            FROM asistencia a
            JOIN estudiantes e ON e.dni = a.estudiante_dni
            WHERE a.fecha BETWEEN ? AND ?
            ORDER BY a.fecha, a.hora
            """,
            (start_date, end_date),
        ).fetchall()
    return [dict(r) for r in rows]


def import_students_from_file(file_path: str, db_path: Path = DB_PATH, default_grado: Optional[int] = None, default_seccion: Optional[str] = None) -> Dict[str, int]:
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError("Archivo no encontrado")
    df = pd.read_excel(path) if path.suffix.lower() in {".xlsx", ".xls"} else pd.read_csv(path)

    col_map = {str(c).strip().lower(): c for c in df.columns}
    required = ["dni", "nombres", "apellidos", "genero"]
    missing = [c for c in required if c not in col_map]
    if missing:
        raise ValueError(f"Faltan columnas requeridas: {', '.join(missing)}")

    has_grado_col = "grado" in col_map
    has_seccion_col = "seccion" in col_map
    if not has_grado_col and default_grado is None:
        raise ValueError("Falta columna 'grado' o default_grado")
    if not has_seccion_col and default_seccion is None:
        raise ValueError("Falta columna 'seccion' o default_seccion")

    inserted, updated, skipped = 0, 0, 0
    with get_connection(db_path) as conn:
        _ensure_qr_token_schema(conn)
        for _, row in df.iterrows():
            dni = _normalize_dni(str(row[col_map["dni"]]))
            if len(dni) != 8:
                skipped += 1
                continue
            nombres = str(row[col_map["nombres"]]).strip()
            apellidos = str(row[col_map["apellidos"]]).strip()
            try:
                grado = int(str(row[col_map["grado"]]).strip()) if has_grado_col else int(default_grado)
            except Exception:
                skipped += 1
                continue
            seccion = _normalize_section(str(row[col_map["seccion"]])) if has_seccion_col else _normalize_section(str(default_seccion or ""))
            genero = str(row[col_map["genero"]]).strip().upper()
            cargo = str(row[col_map["cargo"]]).strip() if "cargo" in col_map else "Alumno"
            if not cargo:
                cargo = "Alumno"

            exists = conn.execute("SELECT dni FROM estudiantes WHERE dni = ?", (dni,)).fetchone()
            _ensure_section_exists(conn, int(grado), seccion)
            if exists:
                conn.execute(
                    """
                    UPDATE estudiantes
                    SET nombres = ?, apellidos = ?, grado = ?, seccion = ?, genero = ?, cargo = ?, activo = 1
                    WHERE dni = ?
                    """,
                    (nombres, apellidos, grado, seccion, genero, cargo, dni),
                )
                updated += 1
            else:
                conn.execute(
                    """
                    INSERT INTO estudiantes (dni, nombres, apellidos, grado, seccion, genero, cargo, qr_token, activo)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (dni, nombres, apellidos, grado, seccion, genero, cargo, _generate_unique_qr_token(conn)),
                )
                inserted += 1

    return {"inserted": inserted, "updated": updated, "skipped": skipped, "total": inserted + updated}


def create_database_backup(destination_folder: str, db_path: Path = DB_PATH) -> str:
    target_dir = Path(destination_folder)
    target_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = target_dir / f"colegio_backup_{ts}.db"
    shutil.copy2(db_path, out)
    return str(out)


def get_admin_quick_stats(db_path: Path = DB_PATH, day: Optional[str] = None) -> Dict[str, Any]:
    today = day or datetime.now().date().isoformat()
    with get_connection(db_path) as conn:
        total_active = conn.execute("SELECT COUNT(1) FROM estudiantes WHERE activo = 1").fetchone()[0]
        present = conn.execute("SELECT COUNT(DISTINCT estudiante_dni) FROM asistencia WHERE fecha = ?", (today,)).fetchone()[0]
        tardy = conn.execute("SELECT COUNT(1) FROM asistencia WHERE fecha = ? AND estado = 'Tardanza'", (today,)).fetchone()[0]
        row = conn.execute(
            """
            WITH presentes AS (SELECT DISTINCT estudiante_dni FROM asistencia WHERE fecha = ?)
            SELECT e.grado, e.seccion, COUNT(1) AS faltas
            FROM estudiantes e
            LEFT JOIN presentes p ON p.estudiante_dni = e.dni
            WHERE e.activo = 1 AND p.estudiante_dni IS NULL
            GROUP BY e.grado, e.seccion
            ORDER BY faltas DESC, e.grado, e.seccion
            LIMIT 1
            """,
            (today,),
        ).fetchone()

    ratio = 0.0 if total_active <= 0 else (present / total_active) * 100.0
    top = "-"
    if row:
        top = f"{row['grado']}to {row['seccion']} ({row['faltas']} faltas)"
    return {
        "total_active": int(total_active),
        "present": int(present),
        "ratio": round(ratio, 1),
        "tardy": int(tardy),
        "top_absence_group": top,
    }


def list_students_by_section(seccion: str, db_path: Path = DB_PATH, only_active: bool = True) -> List[Dict[str, Any]]:
    section = (seccion or "").strip().upper()
    if not section:
        return []
    active_sql = "AND activo = 1" if only_active else ""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT dni, nombres, apellidos, grado, seccion, genero, cargo, qr_token, activo
            FROM estudiantes
            WHERE seccion = ? {active_sql}
            ORDER BY grado, apellidos, nombres
            """,
            (section,),
        ).fetchall()
    return [dict(r) for r in rows]


def build_test_students(total: int = 100, male_ratio: float = 0.45) -> List[tuple]:
    if total <= 0:
        return []
    male_target = int(round(total * male_ratio))
    students: List[tuple] = []
    dni_base = 70000000
    for idx in range(total):
        dni = str(dni_base + idx + 1)
        grade = (idx % 5) + 1
        section = "A" if idx % 2 == 0 else "B"
        if idx < male_target:
            genero = "M"
            nombres = MALE_FIRST_NAMES[idx % len(MALE_FIRST_NAMES)]
        else:
            genero = "F"
            nombres = FEMALE_FIRST_NAMES[idx % len(FEMALE_FIRST_NAMES)]
        apellidos = f"{LAST_NAMES[idx % len(LAST_NAMES)]} {LAST_NAMES[(idx + 7) % len(LAST_NAMES)]}"
        cargo = "Alumno"
        if idx % 17 == 0:
            cargo = "Brigadier"
        elif idx % 23 == 0:
            cargo = "Policia Escolar"
        students.append((dni, nombres, apellidos, grade, section, genero, cargo))
    return students


def replace_with_test_students(total: int = 100, db_path: Path = DB_PATH) -> Dict[str, int]:
    rows = build_test_students(total=total, male_ratio=0.45)
    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM asistencia")
        conn.execute("DELETE FROM estudiantes")
        for row in rows:
            conn.execute(
                """
                INSERT INTO estudiantes (dni, nombres, apellidos, grado, seccion, genero, cargo, qr_token, activo)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (*row, _generate_unique_qr_token(conn)),
            )
    males = sum(1 for r in rows if r[5] == "M")
    females = len(rows) - males
    return {"total": len(rows), "males": males, "females": females}


def clear_all_data(db_path: Path = DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM asistencia")
        conn.execute("DELETE FROM estudiantes")
