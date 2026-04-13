from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Iterable, Mapping, Optional

from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm, mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from qr_generator import generate_student_qr_image


def _fit_logo_image(logo_path: str, target_w: int, target_h: int) -> Optional[ImageReader]:
    if not (logo_path or "").strip():
        return None
    path = Path(logo_path)
    if (not path.exists()) or (not path.is_file()):
        return None
    img = PILImage.open(path).convert("RGB")
    img.thumbnail((target_w, target_h), PILImage.Resampling.LANCZOS)
    bio = BytesIO()
    img.save(bio, format="PNG")
    bio.seek(0)
    return ImageReader(bio)


def _fit_photo_image(photo_path: str, target_w: int, target_h: int) -> Optional[ImageReader]:
    if not (photo_path or "").strip():
        return None
    path = Path(photo_path)
    if (not path.exists()) or (not path.is_file()):
        return None
    img = PILImage.open(path).convert("RGB")
    img.thumbnail((target_w, target_h), PILImage.Resampling.LANCZOS)
    bio = BytesIO()
    img.save(bio, format="PNG")
    bio.seek(0)
    return ImageReader(bio)


def _qr_reader(student: Mapping[str, str], logo_path: str = "") -> ImageReader:
    qr_img = generate_student_qr_image(dict(student), logo_path=logo_path)
    bio = BytesIO()
    qr_img.save(bio, format="PNG")
    bio.seek(0)
    return ImageReader(bio)


def _draw_header(c: canvas.Canvas, x: float, y: float, card_w: float, card_h: float, school_name: str, minedu_logo, school_logo):
    header_h = 11 * mm
    c.setFillColor(colors.HexColor("#0f172a"))
    c.roundRect(x + 1.5, y + card_h - header_h - 1.5, card_w - 3, header_h, 4, stroke=0, fill=1)

    logo_w = 8 * mm
    logo_h = 8 * mm
    logo_y = y + card_h - header_h + 0.5 * mm

    if minedu_logo:
        c.drawImage(minedu_logo, x + 3 * mm, logo_y, width=logo_w, height=logo_h, preserveAspectRatio=True, mask="auto")
    if school_logo:
        c.drawImage(school_logo, x + card_w - 3 * mm - logo_w, logo_y, width=logo_w, height=logo_h, preserveAspectRatio=True, mask="auto")

    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 8)
    title = (school_name or "COLEGIO").upper()
    c.drawCentredString(x + card_w / 2.0, y + card_h - header_h + 3.4 * mm, title[:36])


def _draw_photo_placeholder(c: canvas.Canvas, x: float, y: float, w: float, h: float):
    c.setStrokeColor(colors.HexColor("#94a3b8"))
    c.setLineWidth(0.6)
    c.roundRect(x, y, w, h, 3, stroke=1, fill=0)
    c.setFillColor(colors.HexColor("#64748b"))
    c.circle(x + w / 2.0, y + h * 0.68, min(w, h) * 0.12, stroke=0, fill=1)
    c.roundRect(x + w * 0.28, y + h * 0.26, w * 0.44, h * 0.26, 3, stroke=0, fill=1)


def _draw_institution_block(c: canvas.Canvas, x: float, y: float, w: float, h: float, school_logo, minedu_logo):
    c.setStrokeColor(colors.HexColor("#94a3b8"))
    c.setLineWidth(0.6)
    c.roundRect(x, y, w, h, 3, stroke=1, fill=0)

    gap = 1.6 * mm
    inner_w = w - (gap * 3)
    box_w = inner_w / 2.0
    box_h = h - (gap * 2)
    left_x = x + gap
    right_x = left_x + box_w + gap
    box_y = y + gap

    c.setFillColor(colors.HexColor("#0f172a"))
    c.roundRect(left_x, box_y, box_w, box_h, 2, stroke=0, fill=1)
    c.roundRect(right_x, box_y, box_w, box_h, 2, stroke=0, fill=1)

    if school_logo:
        c.drawImage(school_logo, left_x + 1 * mm, box_y + 1 * mm, width=box_w - 2 * mm, height=box_h - 2 * mm, preserveAspectRatio=True, mask="auto")
    else:
        c.setFillColor(colors.HexColor("#cbd5e1"))
        c.setFont("Helvetica", 6)
        c.drawCentredString(left_x + box_w / 2.0, box_y + box_h / 2.0, "INSIGNIA")

    if minedu_logo:
        c.drawImage(minedu_logo, right_x + 1 * mm, box_y + 1 * mm, width=box_w - 2 * mm, height=box_h - 2 * mm, preserveAspectRatio=True, mask="auto")
    else:
        c.setFillColor(colors.HexColor("#cbd5e1"))
        c.setFont("Helvetica", 6)
        c.drawCentredString(right_x + box_w / 2.0, box_y + box_h / 2.0, "MINEDU")


def _draw_card(
    c: canvas.Canvas,
    x: float,
    y: float,
    card_w: float,
    card_h: float,
    student: Mapping[str, str],
    school_name: str,
    place_label: str,
    year_label: str,
    school_logo,
    minedu_logo,
    qr_logo_path: str,
) -> None:
    c.setFillColor(colors.white)
    c.roundRect(x, y, card_w, card_h, 6, stroke=0, fill=1)
    c.setStrokeColor(colors.HexColor("#0f172a"))
    c.setLineWidth(1)
    c.roundRect(x, y, card_w, card_h, 6, stroke=1, fill=0)

    _draw_header(c, x, y, card_w, card_h, school_name, minedu_logo, school_logo)

    photo_x = x + 3 * mm
    photo_y = y + 17 * mm
    photo_w = 22 * mm
    photo_h = 27 * mm
    _draw_institution_block(c, photo_x, photo_y, photo_w, photo_h, school_logo=school_logo, minedu_logo=minedu_logo)

    qr = _qr_reader(student, logo_path=qr_logo_path)
    qr_size = 25 * mm
    qr_x = x + card_w - qr_size - 4 * mm
    qr_y = y + card_h - qr_size - 15 * mm
    c.drawImage(qr, qr_x, qr_y, width=qr_size, height=qr_size, preserveAspectRatio=True, mask="auto")

    c.setFillColor(colors.HexColor("#0f172a"))
    c.setFont("Helvetica-Bold", 8.8)
    full_name = f"{student.get('nombres', '')} {student.get('apellidos', '')}".strip().upper()
    c.drawString(photo_x, y + 13 * mm, full_name[:34])

    c.setFont("Helvetica-Bold", 8)
    grado = str(student.get("grado", "")).strip()
    seccion = str(student.get("seccion", "")).strip().upper()
    c.drawString(photo_x, y + 9 * mm, f"GRADO Y SECCION: {grado} \"{seccion}\"")

    dni = str(student.get("dni", "")).strip()
    if dni:
        c.setFont("Helvetica", 7)
        c.drawString(photo_x, y + 5.6 * mm, f"DNI: {dni}")

    c.setFont("Helvetica", 7)
    c.setFillColor(colors.HexColor("#334155"))
    c.drawRightString(x + card_w - 3 * mm, y + 3 * mm, f"{place_label} - {year_label}")
    c.setFont("Helvetica", 6.5)
    c.drawRightString(x + card_w - 3 * mm, y + 1.2 * mm, "QR seguro: token cifrado")


def generate_id_cards_pdf(
    students: Iterable[Mapping[str, str]],
    output_pdf: str,
    school_name: str,
    logo_path: str = "",
    minedu_logo_path: str = "",
    place_label: str = "Huanuco",
    cards_per_page: int = 8,
    cards_per_row: int = 2,
    cards_per_col: int = 4,
) -> str:
    out = Path(output_pdf)
    out.parent.mkdir(parents=True, exist_ok=True)

    c = canvas.Canvas(str(out), pagesize=A4)
    page_w, page_h = A4

    # Layout presets requested for A4 output.
    presets = {
        1: (1, 1),
        6: (2, 3),
        8: (2, 4),
    }
    cards_per_row, cards_per_col = presets.get(int(cards_per_page), (2, 4))
    cards_per_page = cards_per_row * cards_per_col

    if cards_per_page == 8:
        card_w = 8.5 * cm
        card_h = 5.5 * cm
    else:
        base_margin_x = 12 * mm
        base_margin_y = 12 * mm
        base_gap_x = 8 * mm
        base_gap_y = 8 * mm
        available_w = page_w - (base_margin_x * 2) - (base_gap_x * (cards_per_row - 1))
        available_h = page_h - (base_margin_y * 2) - (base_gap_y * (cards_per_col - 1))
        card_w = available_w / cards_per_row
        card_h = available_h / cards_per_col

    total_w = cards_per_row * card_w
    total_h = cards_per_col * card_h
    free_w = page_w - total_w
    free_h = page_h - total_h
    gap_x = max(6 * mm, free_w / (cards_per_row + 1))
    gap_y = max(6 * mm, free_h / (cards_per_col + 1))
    margin_x = max(6 * mm, (page_w - (cards_per_row * card_w + (cards_per_row - 1) * gap_x)) / 2)
    margin_y = max(6 * mm, (page_h - (cards_per_col * card_h + (cards_per_col - 1) * gap_y)) / 2)

    school_logo_reader = _fit_logo_image(logo_path, int(8 * mm), int(8 * mm)) if logo_path else None
    minedu_logo_reader = _fit_logo_image(minedu_logo_path, int(8 * mm), int(8 * mm)) if minedu_logo_path else None
    year_label = str(datetime.now().year)

    students = list(students)
    for idx, student in enumerate(students):
        page_pos = idx % cards_per_page
        col = page_pos % cards_per_row
        row = page_pos // cards_per_row

        x = margin_x + col * (card_w + gap_x)
        y = page_h - margin_y - card_h - row * (card_h + gap_y)

        _draw_card(
            c=c,
            x=x,
            y=y,
            card_w=card_w,
            card_h=card_h,
            student=student,
            school_name=school_name,
            place_label=place_label,
            year_label=year_label,
            school_logo=school_logo_reader,
            minedu_logo=minedu_logo_reader,
            qr_logo_path=logo_path,
        )

        if page_pos == cards_per_page - 1 and idx < len(students) - 1:
            c.showPage()

    c.save()
    return str(out)
