import json
from pathlib import Path
from typing import Dict, Optional

import qrcode
from PIL import Image


def build_student_qr_payload(student: Dict[str, str]) -> str:
    token = str(student.get("qr_token", "")).strip()
    if not token:
        raise ValueError("El estudiante no tiene qr_token asignado")

    # Privacy-first payload: only token, no personal data.
    payload = {
        "t": token,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def generate_student_qr_image(
    student: Dict[str, str],
    logo_path: Optional[str] = None,
    box_size: int = 12,
    border: int = 2,
) -> Image.Image:
    payload = build_student_qr_payload(student)
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=box_size,
        border=border,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white").convert("RGBA")

    if logo_path:
        path = Path(logo_path)
        if path.exists():
            logo = Image.open(path).convert("RGBA")
            max_logo = max(24, image.size[0] // 4)
            logo.thumbnail((max_logo, max_logo), Image.Resampling.LANCZOS)

            # White rounded-ish background to preserve readability.
            pad = 8
            bg = Image.new("RGBA", (logo.size[0] + pad * 2, logo.size[1] + pad * 2), (255, 255, 255, 230))
            lx = (bg.size[0] - logo.size[0]) // 2
            ly = (bg.size[1] - logo.size[1]) // 2
            bg.alpha_composite(logo, (lx, ly))

            px = (image.size[0] - bg.size[0]) // 2
            py = (image.size[1] - bg.size[1]) // 2
            image.alpha_composite(bg, (px, py))

    return image


def save_student_qr(
    student: Dict[str, str],
    output_path: str,
    logo_path: Optional[str] = None,
) -> str:
    image = generate_student_qr_image(student=student, logo_path=logo_path)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out)
    return str(out)
