import json
import re
import threading
import time
from typing import Callable, Dict, Optional

import cv2
from pyzbar.pyzbar import decode


class QRScanner:
    """Realtime QR/barcode scanner with debounce and callbacks."""

    def __init__(
        self,
        camera_index: int = 0,
        cooldown_seconds: float = 2.0,
        on_detect: Optional[Callable[[str], None]] = None,
        on_frame: Optional[Callable[[any], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ):
        self.camera_index = camera_index
        self.cooldown_seconds = cooldown_seconds
        self.on_detect = on_detect
        self.on_frame = on_frame
        self.on_error = on_error

        self._capture = None
        self._thread = None
        self._running = False
        self._lock = threading.Lock()
        self._last_seen: Dict[str, float] = {}

    @staticmethod
    def extract_identifier(payload: str) -> Optional[str]:
        if not payload:
            return None

        text = payload.strip()

        # Secure QR payload: JSON with token field.
        if text.startswith("{") and text.endswith("}"):
            try:
                data = json.loads(text)
                for key in ("t", "qr_token", "token"):
                    value = str(data.get(key, "")).strip()
                    if value:
                        return value
            except Exception:
                pass

        # Legacy fallback: direct DNI in code.
        match = re.search(r"\b\d{8}\b", payload)
        if match:
            return match.group(0)

        # Direct token string (urlsafe).
        if re.fullmatch(r"[A-Za-z0-9_-]{16,}", text):
            return text

        only_digits = "".join(ch for ch in payload if ch.isdigit())
        if len(only_digits) == 8:
            return only_digits
        return None

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._capture = cv2.VideoCapture(self.camera_index)
            if not self._capture.isOpened():
                self._capture = None
                self._emit_error("No se pudo abrir la camara.")
                return
            self._running = True
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._running = False
            cap = self._capture
            self._capture = None
        if cap is not None:
            cap.release()

    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def _loop(self) -> None:
        while self.is_running():
            cap = self._capture
            if cap is None:
                break

            ok, frame = cap.read()
            if not ok:
                self._emit_error("No se pudo leer frame de camara.")
                time.sleep(0.25)
                continue

            self._process_frame(frame)
            if self.on_frame:
                self.on_frame(frame)

            # Small sleep to avoid maxing CPU while keeping smooth feed.
            time.sleep(0.01)

        self.stop()

    def _process_frame(self, frame) -> None:
        try:
            decoded_items = decode(frame)
        except Exception as exc:
            self._emit_error(f"Error decodificando codigo: {exc}")
            return

        now = time.monotonic()
        for item in decoded_items:
            try:
                payload = item.data.decode("utf-8", errors="ignore")
            except Exception:
                payload = ""
            identifier = self.extract_identifier(payload)
            if not identifier:
                continue

            last_time = self._last_seen.get(identifier, 0.0)
            if now - last_time < self.cooldown_seconds:
                continue

            self._last_seen[identifier] = now
            if self.on_detect:
                self.on_detect(identifier)

    def _emit_error(self, message: str) -> None:
        if self.on_error:
            self.on_error(message)
