import os
import re
import sys
import json
import math
import time
import ctypes
import colorsys
import importlib.util
import subprocess
import threading
import traceback
import unicodedata
from io import BytesIO
from pathlib import Path


# ---------------------------------------------------------------------------
# Pre-Launch Dependency Check (Windows 7/8/8.1 & Windows 10/11 Aware)
# ---------------------------------------------------------------------------
def is_legacy_windows() -> bool:
    if sys.platform != "win32":
        return False
    try:
        return sys.getwindowsversion().major < 10
    except Exception:
        return False


IS_LEGACY_WIN = is_legacy_windows()


def check_and_prompt_dependencies():
    if getattr(sys, "frozen", False) or "--export-icon" in sys.argv:
        return

    if IS_LEGACY_WIN:
        vital = [("PySide6", "PySide6<6.5"), ("mutagen", "mutagen"), ("PIL", "Pillow<10.3")]
        optional_pkgs = [("av", "av<11.0")]
    else:
        vital = [("PySide6", "PySide6"), ("mutagen", "mutagen"), ("PIL", "Pillow")]
        optional_pkgs = [("av", "av")]

    missing_vital = [pip for mod, pip in vital if importlib.util.find_spec(mod) is None]
    missing_opt = [pip for mod, pip in optional_pkgs if importlib.util.find_spec(mod) is None]

    if missing_vital or missing_opt:
        all_missing = missing_vital + missing_opt
        print("[NanoTune Toolbox] Missing packages:", ", ".join(all_missing))
        try:
            ans = input(f"Download and install {', '.join(all_missing)} now? [Y/n]: ").strip().lower()
        except Exception:
            ans = "y"
        if ans in ("", "y", "yes"):
            subprocess.check_call([sys.executable, "-m", "pip", "install", *all_missing])
        elif missing_vital:
            sys.exit(1)


check_and_prompt_dependencies()

from PIL import Image, ImageFilter, ImageEnhance, ImageDraw
import mutagen
from mutagen.flac import FLAC, Picture
from mutagen.mp3 import MP3
from mutagen.id3 import APIC, USLT
from mutagen.mp4 import MP4, MP4Cover

# Fast spec check (lazy-loaded after window shows so cold start is near-instant)
HAS_AV = importlib.util.find_spec("av") is not None


def get_nanotune_config_dir() -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home())
    return Path(appdata) / "NanoTune"


def read_startup_graphics_backend() -> str:
    cfg_file = get_nanotune_config_dir() / "config.json"
    if cfg_file.exists():
        try:
            data = json.loads(cfg_file.read_text(encoding="utf-8"))
            val = data.get("graphics_backend")
            if val in ("software", "dx10_1", "opengl21"):
                return val
        except Exception:
            pass
    return "software" if IS_LEGACY_WIN else "dx10_1"


SELECTED_BACKEND = read_startup_graphics_backend()
if "--export-icon" not in sys.argv:
    if SELECTED_BACKEND == "software":
        os.environ["QT_QUICK_BACKEND"] = "software"
        os.environ.pop("QT_OPENGL", None)
        os.environ.pop("QSG_RHI_BACKEND", None)
    elif SELECTED_BACKEND == "dx10_1":
        os.environ["QSG_RHI_BACKEND"] = "d3d11"
        os.environ.pop("QT_QUICK_BACKEND", None)
        os.environ.pop("QT_OPENGL", None)
    elif SELECTED_BACKEND == "opengl21":
        os.environ["QT_OPENGL"] = "desktop"
        os.environ["QSG_RHI_BACKEND"] = "opengl"
        os.environ.pop("QT_QUICK_BACKEND", None)

from PySide6.QtCore import (
    Qt, QUrl, QTimer, QSize, Signal, QRect, QRectF, QPoint, QPointF,
    QBuffer, QCoreApplication, QIODevice, QObject, QEvent,
)
from PySide6.QtGui import (
    QColor, QFont, QFontDatabase, QFontMetrics, QIcon, QImage,
    QKeySequence, QLinearGradient, QPainter, QPainterPath, QPen,
    QPixmap, QPolygonF, QShortcut, QSurfaceFormat, QSyntaxHighlighter,
    QTextCharFormat, QTextCursor,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QAbstractSpinBox, QApplication, QDialog, QFileDialog,
    QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QProgressBar, QPushButton,
    QScrollArea, QSlider, QSpinBox, QStackedWidget, QStyle,
    QStyleOptionSlider, QTextEdit, QToolButton, QVBoxLayout, QWidget,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtNetwork import QLocalSocket


# ---------------------------------------------------------------------------
# Constants & Shared NanoTune Presets
# ---------------------------------------------------------------------------
APP_ID = "nanotune.toolbox.1.2"
IPC_SERVER_NAME = "NanoTune_SingleInstance_IPC"
AUDIO_EXTENSIONS = {".flac", ".mp3", ".wav", ".m4a", ".ogg", ".opus", ".aac", ".aiff", ".aif"}
LYRIC_EXTENSIONS = {".lrc", ".elrc"}
STAR_DEFAULT_YELLOW = "#FFD83D"
STAR_DEFAULT_YELLOW_2 = "#FFE885"

FONT_PRESETS = [
    ("Georgia Editorial (Default)", ["Georgia"]),
    ("Segoe UI (Clean Native)", ["Segoe UI"]),
    ("Segoe UI Semibold", ["Segoe UI Semibold", "Segoe UI"]),
    ("Bahnschrift (Modern Geometric)", ["Bahnschrift"]),
    ("Calibri (Soft Sans)", ["Calibri"]),
    ("Cambria (Classic Serif)", ["Cambria"]),
    ("Candara (Humanist Sans)", ["Candara"]),
    ("Century Gothic (Wide Geometric)", ["Century Gothic", "Bahnschrift"]),
    ("Corbel (Clean Modern)", ["Corbel"]),
    ("Franklin Gothic Medium", ["Franklin Gothic Medium", "Arial"]),
    ("Lucida Sans Unicode", ["Lucida Sans Unicode"]),
    ("Tahoma (Crisp UI)", ["Tahoma"]),
    ("Trebuchet MS", ["Trebuchet MS"]),
    ("Verdana (Wide Legible)", ["Verdana"]),
    ("Consolas (Monospace Tech)", ["Consolas"]),
]

LRC_LINE_RE = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")
ELRC_WORD_RE = re.compile(r"<(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?>")
VOICE_PREFIX_RE = re.compile(r"^\s*v\d+\s*:\s*", re.I)


def resolve_font_family(candidates: list[str]) -> str:
    available = set(QFontDatabase.families())
    for fam in candidates:
        if fam in available:
            return fam
    return "Segoe UI"


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().strip()
    return re.sub(r"[^a-z0-9]", "", text)


def format_ms(ms: int) -> str:
    if ms <= 0:
        return "0:00"
    total_sec = int(ms // 1000)
    mins, secs = divmod(total_sec, 60)
    return f"{mins}:{secs:02d}"


def format_bytes(size: int) -> str:
    if size <= 0:
        return "—"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def color_luminance(hex_c: str) -> float:
    c = QColor(hex_c)
    return (0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()) / 255.0


def clean_artist_str(raw: str) -> str:
    return re.sub(r"\s*;\s*", ", ", raw.strip()) if raw else "Unknown Artist"


def enable_windows_dark_titlebar(hwnd: int):
    if sys.platform != "win32" or IS_LEGACY_WIN:
        return
    try:
        dwmapi = ctypes.windll.dwmapi
        value = ctypes.c_int(1)
        for attr in (20, 19):
            res = dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd), ctypes.c_uint(attr),
                ctypes.byref(value), ctypes.sizeof(value),
            )
            if res == 0:
                break
    except Exception:
        pass


# ---------------------------------------------------------------------------
# NanoTune Toolbox Icon (SNES Star + Upward-Facing Hammer & Wrench 'X')
# ---------------------------------------------------------------------------
def make_toolbox_star_icon(color_hex: str = STAR_DEFAULT_YELLOW, size: int = 64) -> QIcon:
    px = QPixmap(size, size)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    s = float(size)
    cx, cy = s * 0.48, s * 0.50
    outer_r = s * 0.42
    inner_r = s * 0.23

    pts = []
    for i in range(10):
        angle = -math.pi / 2 + i * (math.pi / 5)
        rad = outer_r if i % 2 == 0 else inner_r
        pts.append(QPointF(cx + rad * math.cos(angle), cy + rad * math.sin(angle)))

    path = QPainterPath()
    n = len(pts)
    for i in range(n):
        prev_pt = pts[(i - 1) % n]
        cur_pt = pts[i]
        next_pt = pts[(i + 1) % n]
        p_in = QPointF(cur_pt.x() * 0.72 + prev_pt.x() * 0.28, cur_pt.y() * 0.72 + prev_pt.y() * 0.28)
        p_out = QPointF(cur_pt.x() * 0.72 + next_pt.x() * 0.28, cur_pt.y() * 0.72 + next_pt.y() * 0.28)
        if i == 0:
            path.moveTo(p_in)
        else:
            path.lineTo(p_in)
        path.quadTo(cur_pt, p_out)
    path.closeSubpath()

    base = QColor(color_hex)
    darker = base.darker(155)
    grad = QLinearGradient(0, s * 0.08, 0, s * 0.88)
    grad.setColorAt(0.0, base.lighter(115))
    grad.setColorAt(1.0, base.darker(112))

    p.setBrush(grad)
    p.setPen(QPen(darker, max(1.4, s * 0.052), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawPath(path)

    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#111315"))
    eye_w = s * 0.072
    eye_h = s * 0.18
    eye_y = cy - s * 0.09
    p.drawRoundedRect(QRectF(cx - s * 0.11, eye_y, eye_w, eye_h), eye_w * 0.5, eye_w * 0.5)
    p.drawRoundedRect(QRectF(cx + s * 0.038, eye_y, eye_w, eye_h), eye_w * 0.5, eye_w * 0.5)

    p.setBrush(QColor(255, 255, 255, 225))
    sh_w = eye_w * 0.45
    p.drawEllipse(QRectF(cx - s * 0.10, eye_y + eye_h * 0.16, sh_w, sh_w))
    p.drawEllipse(QRectF(cx + s * 0.048, eye_y + eye_h * 0.16, sh_w, sh_w))

    # Crossed Hammer & Wrench forming an "X" with BOTH heads facing UPWARDS
    bx, by = s * 0.73, s * 0.74
    badge_r = s * 0.24

    p.setPen(QPen(base.darker(145), max(1.2, s * 0.035)))
    p.setBrush(QColor(15, 19, 21, 240))
    p.drawEllipse(QPointF(bx, by), badge_r, badge_r)

    p.save()
    p.translate(bx, by)

    tool_edge = QColor("#111315")
    tool_light = QColor("#EAF2F0")

    p.save()
    p.rotate(40)
    p.setPen(QPen(tool_edge, max(0.8, s * 0.018)))
    p.setBrush(tool_light)
    w_len = badge_r * 1.18
    w_thick = badge_r * 0.23
    p.drawRoundedRect(QRectF(-w_thick * 0.5, -w_len * 0.38, w_thick, w_len * 0.90), w_thick * 0.4, w_thick * 0.4)
    head_r = badge_r * 0.27
    jaw = QPainterPath()
    jaw.addEllipse(QPointF(0, -w_len * 0.38), head_r, head_r)
    notch = QPainterPath()
    notch.addRect(QRectF(-head_r * 0.42, -w_len * 0.38 - head_r * 1.05, head_r * 0.84, head_r * 1.05))
    p.drawPath(jaw.subtracted(notch))
    p.restore()

    p.save()
    p.rotate(-40)
    p.setPen(QPen(tool_edge, max(0.8, s * 0.018)))
    p.setBrush(base.lighter(115))
    h_len = badge_r * 1.18
    h_thick = badge_r * 0.22
    p.drawRoundedRect(QRectF(-h_thick * 0.5, -h_len * 0.36, h_thick, h_len * 0.88), h_thick * 0.4, h_thick * 0.4)
    p.setBrush(QColor("#FFFFFF"))
    head_w = badge_r * 0.72
    head_h = badge_r * 0.29
    p.drawRoundedRect(QRectF(-head_w * 0.5, -h_len * 0.48, head_w, head_h), head_h * 0.32, head_h * 0.32)
    p.restore()

    p.restore()
    p.end()
    return QIcon(px)


def export_toolbox_ico(out_path: str):
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    pm = make_toolbox_star_icon(STAR_DEFAULT_YELLOW, 500).pixmap(500, 500)
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    pm.save(buf, "PNG")
    img = Image.open(BytesIO(bytes(buf.data()))).convert("RGBA")
    img.save(
        out_path,
        format="ICO",
        sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)],
    )


def make_vector_icon(name: str, color: str = "#FFFFFF", size: int = 24) -> QIcon:
    px = QPixmap(size, size)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    c = QColor(color)
    pen = QPen(c, max(1.6, size * 0.085), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    s = float(size)

    if name == "play":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawPolygon(QPolygonF([QPointF(s * 0.28, s * 0.18), QPointF(s * 0.80, s * 0.50), QPointF(s * 0.28, s * 0.82)]))
    elif name == "pause":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawRoundedRect(QRectF(s * 0.26, s * 0.22, s * 0.17, s * 0.56), 1.5, 1.5)
        p.drawRoundedRect(QRectF(s * 0.57, s * 0.22, s * 0.17, s * 0.56), 1.5, 1.5)
    elif name == "folder":
        path = QPainterPath()
        path.moveTo(s * 0.15, s * 0.28)
        path.lineTo(s * 0.40, s * 0.28)
        path.lineTo(s * 0.48, s * 0.36)
        path.lineTo(s * 0.85, s * 0.36)
        path.lineTo(s * 0.85, s * 0.78)
        path.lineTo(s * 0.15, s * 0.78)
        path.closeSubpath()
        p.drawPath(path)
    elif name == "music":
        p.drawLine(QPointF(s * 0.38, s * 0.72), QPointF(s * 0.38, s * 0.26))
        p.drawLine(QPointF(s * 0.38, s * 0.26), QPointF(s * 0.76, s * 0.20))
        p.drawLine(QPointF(s * 0.76, s * 0.20), QPointF(s * 0.76, s * 0.64))
        p.setBrush(c)
        p.drawEllipse(QPointF(s * 0.30, s * 0.74), s * 0.10, s * 0.08)
        p.drawEllipse(QPointF(s * 0.68, s * 0.66), s * 0.10, s * 0.08)
    elif name == "edit":
        p.drawLine(QPointF(s * 0.24, s * 0.76), QPointF(s * 0.72, s * 0.24))
        p.drawLine(QPointF(s * 0.20, s * 0.80), QPointF(s * 0.34, s * 0.77))
        p.drawLine(QPointF(s * 0.65, s * 0.20), QPointF(s * 0.79, s * 0.34))
    elif name == "clock":
        p.drawEllipse(QRectF(s * 0.18, s * 0.18, s * 0.64, s * 0.64))
        p.drawLine(QPointF(s * 0.50, s * 0.32), QPointF(s * 0.50, s * 0.52))
        p.drawLine(QPointF(s * 0.50, s * 0.52), QPointF(s * 0.65, s * 0.60))
    elif name == "save":
        p.drawRoundedRect(QRectF(s * 0.20, s * 0.20, s * 0.60, s * 0.60), 2.5, 2.5)
        p.drawRect(QRectF(s * 0.34, s * 0.20, s * 0.32, s * 0.20))
        p.drawRect(QRectF(s * 0.32, s * 0.54, s * 0.36, s * 0.26))
    elif name == "back":
        p.drawLine(QPointF(s * 0.78, s * 0.50), QPointF(s * 0.24, s * 0.50))
        p.drawPolyline(QPolygonF([QPointF(s * 0.46, s * 0.28), QPointF(s * 0.22, s * 0.50), QPointF(s * 0.46, s * 0.72)]))
    elif name == "bolt":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawPolygon(QPolygonF([
            QPointF(s * 0.56, s * 0.14), QPointF(s * 0.26, s * 0.54),
            QPointF(s * 0.49, s * 0.54), QPointF(s * 0.42, s * 0.86),
            QPointF(s * 0.74, s * 0.44), QPointF(s * 0.51, s * 0.44),
        ]))
    elif name == "import":
        p.drawLine(QPointF(s * 0.50, s * 0.18), QPointF(s * 0.50, s * 0.68))
        p.drawPolyline(QPolygonF([QPointF(s * 0.32, s * 0.52), QPointF(s * 0.50, s * 0.70), QPointF(s * 0.68, s * 0.52)]))
        p.drawPolyline(QPolygonF([QPointF(s * 0.20, s * 0.66), QPointF(s * 0.20, s * 0.82), QPointF(s * 0.80, s * 0.82), QPointF(s * 0.80, s * 0.66)]))
    elif name == "close":
        p.drawLine(QPointF(s * 0.26, s * 0.26), QPointF(s * 0.74, s * 0.74))
        p.drawLine(QPointF(s * 0.74, s * 0.26), QPointF(s * 0.26, s * 0.74))

    p.end()
    return QIcon(px)


# ---------------------------------------------------------------------------
# Color Extraction & Image Helpers (Matching Main NanoTune)
# ---------------------------------------------------------------------------
def extract_theme_colors(img: Image.Image) -> tuple[str, str]:
    try:
        small = img.convert("RGB").resize((48, 48), Image.Resampling.LANCZOS)
        pixels = list(small.getdata())
        total_px = len(pixels)
        buckets: dict[int, list[tuple[float, float, float]]] = {}
        chromatic_count = 0

        for r, g, b in pixels:
            h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
            if s > 0.22 and v > 0.16:
                chromatic_count += 1
                if v < 0.96:
                    b_idx = int(h * 24) % 24
                    buckets.setdefault(b_idx, []).append((h, s, v))

        if chromatic_count < total_px * 0.08 or not buckets:
            return "#E4E8EC", "#B4BCC4"

        top_bucket = max(buckets.values(), key=len)
        avg_h = sum(x[0] for x in top_bucket) / len(top_bucket)
        avg_s = min(0.88, max(0.50, sum(x[1] for x in top_bucket) / len(top_bucket)))
        avg_v = min(0.94, max(0.74, sum(x[2] for x in top_bucket) / len(top_bucket)))

        r1, g1, b1 = colorsys.hsv_to_rgb(avg_h, avg_s, avg_v)
        r2, g2, b2 = colorsys.hsv_to_rgb((avg_h + 0.08) % 1.0, max(0.45, avg_s * 0.8), min(0.96, avg_v * 1.05))
        return f"#{int(r1*255):02X}{int(g1*255):02X}{int(b1*255):02X}", f"#{int(r2*255):02X}{int(g2*255):02X}{int(b2*255):02X}"
    except Exception:
        return STAR_DEFAULT_YELLOW, STAR_DEFAULT_YELLOW_2


def build_fallback_cover(accent_hex: str = STAR_DEFAULT_YELLOW) -> Image.Image:
    w, h = 220, 220
    img = Image.new("RGB", (w, h), (14, 17, 19))
    draw = ImageDraw.Draw(img)
    c = QColor(accent_hex)
    for y in range(h):
        t = y / h
        r = int(12 + c.red() * 0.16 * (1 - t))
        g = int(14 + c.green() * 0.16 * (1 - t * 0.5))
        b = int(16 + c.blue() * 0.16 * (1 - t * 0.3))
        draw.line([(0, y), (w, y)], fill=(r, g, b))
    draw.ellipse((50, 50, 170, 170), outline=(c.red(), c.green(), c.blue()), width=4)
    return img


def pil_to_qimage(img: Image.Image) -> QImage:
    rgb = img.convert("RGBA")
    data = rgb.tobytes("raw", "RGBA")
    return QImage(data, rgb.width, rgb.height, rgb.width * 4, QImage.Format.Format_RGBA8888).copy()


def pil_to_qpixmap(img: Image.Image) -> QPixmap:
    return QPixmap.fromImage(pil_to_qimage(img))


# ---------------------------------------------------------------------------
# Metadata, Cover Art & Lyrics Readers
# ---------------------------------------------------------------------------
def extract_embedded_cover_bytes(file_path: Path) -> tuple[bytes, str]:
    try:
        ext = file_path.suffix.lower()
        if ext == ".flac":
            fl = FLAC(str(file_path))
            if fl.pictures:
                pic = fl.pictures[0]
                return bytes(pic.data), (pic.mime or "image/jpeg")
        elif ext == ".mp3":
            mp = MP3(str(file_path))
            if mp.tags:
                for tag in mp.tags.values():
                    if getattr(tag, "FrameID", "") == "APIC":
                        return bytes(tag.data), (getattr(tag, "mime", "image/jpeg") or "image/jpeg")
        elif ext == ".m4a":
            m4 = MP4(str(file_path))
            if m4.tags and "covr" in m4.tags and m4.tags["covr"]:
                cov = m4.tags["covr"][0]
                mime = "image/png" if getattr(cov, "imageformat", None) == MP4Cover.FORMAT_PNG else "image/jpeg"
                return bytes(cov), mime
        else:
            generic = mutagen.File(str(file_path))
            if generic and hasattr(generic, "pictures") and generic.pictures:
                pic = generic.pictures[0]
                return bytes(pic.data), (getattr(pic, "mime", "image/jpeg") or "image/jpeg")
    except Exception:
        pass
    return b"", ""


def extract_cover_pil(file_path: Path) -> Image.Image | None:
    raw, _mime = extract_embedded_cover_bytes(file_path)
    if raw:
        try:
            return Image.open(BytesIO(raw)).convert("RGB")
        except Exception:
            pass
    for name in ("cover.jpg", "cover.png", "folder.jpg", "folder.png", "front.jpg"):
        p = file_path.parent / name
        if p.exists():
            try:
                return Image.open(p).convert("RGB")
            except Exception:
                pass
    return None


def read_embedded_lyrics(audio_path: Path) -> tuple[str, str]:
    ext = audio_path.suffix.lower()
    try:
        if ext == ".flac":
            fl = FLAC(str(audio_path))
            if fl.tags:
                for key in ("SYNCEDLYRICS", "LYRICS", "UNSYNCEDLYRICS", "LYRICS:ENG"):
                    vals = fl.tags.get(key)
                    if vals and str(vals[0]).strip():
                        return str(vals[0]).strip(), f"FLAC Embedded ({key})"
                for k, vals in fl.tags.items():
                    if "lyric" in k.lower() and vals and str(vals[0]).strip():
                        return str(vals[0]).strip(), f"FLAC Embedded ({k})"
        elif ext == ".mp3":
            mp = MP3(str(audio_path))
            if mp.tags:
                for tag in mp.tags.values():
                    if isinstance(tag, USLT) and str(tag.text).strip():
                        return str(tag.text).strip(), "MP3 Embedded (USLT)"
        elif ext == ".m4a":
            m4 = MP4(str(audio_path))
            if m4.tags and "\xa9lyr" in m4.tags:
                txt = str(m4.tags["\xa9lyr"][0]).strip()
                if txt:
                    return txt, "M4A Embedded (©lyr)"
        else:
            generic = mutagen.File(str(audio_path))
            if generic and generic.tags:
                for k, vals in generic.tags.items():
                    if "lyric" in str(k).lower() and vals:
                        txt = str(vals[0] if isinstance(vals, list) else vals).strip()
                        if txt:
                            return txt, f"Embedded ({k})"
    except Exception:
        pass
    return "", ""


def inspect_file(file_path: Path, imported_lyrics_map: dict[str, str] | None = None) -> dict:
    suf = file_path.suffix.lower()
    st_size = file_path.stat().st_size if file_path.exists() else 0

    if suf in LYRIC_EXTENSIONS:
        raw_txt = ""
        for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                raw_txt = file_path.read_text(encoding=enc)
                break
            except Exception:
                continue
        line_cnt = len([ln for ln in raw_txt.splitlines() if ln.strip()])
        has_words = bool(ELRC_WORD_RE.search(raw_txt))
        kind = "Enhanced .ELRC (Word-Synced)" if has_words else "Standard .LRC (Line-Synced)"
        return {
            "kind": "lyrics",
            "path": str(file_path),
            "filename": file_path.name,
            "title": file_path.stem,
            "artist": kind,
            "album": f"{line_cnt} non-empty lines",
            "year": "",
            "track_no": 0,
            "duration_ms": 0,
            "sample_rate": 0,
            "bits_per_sample": 0,
            "bitrate": 0,
            "channels": 0,
            "file_size": st_size,
            "codec_detail": suf.lstrip(".").upper() + " Lyrics File",
            "format": "Word + Line Timestamps" if has_words else "Line Timestamps",
            "lyrics_status": kind,
            "lyrics_raw": raw_txt,
        }

    title = file_path.stem
    artist = "Unknown Artist"
    album_artist = ""
    album = file_path.parent.name
    year_str = ""
    track_no = 0
    duration_ms = 0
    sample_rate = 0
    bits_per_sample = 0
    bitrate = 0
    channels = 2
    ext = suf.lstrip(".").upper()
    fmt_label = ext
    codec_detail = ext

    try:
        audio = mutagen.File(str(file_path), easy=True)
        if audio is not None:
            if audio.tags:
                title = str((audio.tags.get("title") or [title])[0])
                artist = str((audio.tags.get("artist") or [artist])[0])
                album_artist = str((audio.tags.get("albumartist") or [""])[0])
                album = str((audio.tags.get("album") or [album])[0])
                raw_tn = (audio.tags.get("tracknumber") or ["0"])[0]
                m = re.match(r"(\d+)", str(raw_tn))
                if m:
                    track_no = int(m.group(1))
                for dk in ("date", "year", "originaldate"):
                    raw_d = (audio.tags.get(dk) or [""])[0]
                    if raw_d:
                        ym = re.search(r"\b(19\d{2}|20\d{2})\b", str(raw_d))
                        if ym:
                            year_str = ym.group(1)
                            break
            if hasattr(audio, "info") and audio.info:
                info = audio.info
                duration_ms = int((getattr(info, "length", 0) or 0) * 1000)
                sample_rate = int(getattr(info, "sample_rate", 0) or 0)
                bits_per_sample = int(getattr(info, "bits_per_sample", 0) or 0)
                bitrate = int(getattr(info, "bitrate", 0) or 0)
                channels = int(getattr(info, "channels", 2) or 2)

                if isinstance(audio, FLAC) or ext == "FLAC":
                    codec_detail = "FLAC (Lossless)"
                    fmt_label = f"FLAC {bits_per_sample or 16}-bit/{(sample_rate or 44100)/1000:.1f}kHz"
                elif ext == "MP3":
                    codec_detail = "MP3 (MPEG Audio)"
                    fmt_label = f"MP3 {bitrate // 1000}kbps" if bitrate else "MP3"
                elif ext == "M4A":
                    codec_detail = "M4A (AAC / ALAC)"
                    fmt_label = f"M4A {bitrate // 1000}kbps" if bitrate else "M4A"
                elif ext == "WAV":
                    codec_detail = "WAV (Uncompressed PCM)"
                    fmt_label = f"WAV {bits_per_sample or 16}-bit"
                elif ext == "OGG":
                    codec_detail = "OGG Vorbis"
                    fmt_label = f"OGG {bitrate // 1000}kbps" if bitrate else "OGG"
                elif ext == "OPUS":
                    codec_detail = "Opus Audio"
                    fmt_label = f"OPUS {bitrate // 1000}kbps" if bitrate else "OPUS"
    except Exception:
        pass

    emb_raw, emb_src = read_embedded_lyrics(file_path)
    sidecar_txt = ""
    sidecar_src = ""
    for l_ext in (".elrc", ".lrc"):
        cand = file_path.with_suffix(l_ext)
        if cand.exists():
            sidecar_src = f"Sidecar {l_ext.upper()}"
            try:
                sidecar_txt = cand.read_text(encoding="utf-8-sig")
            except Exception:
                pass
            break
    if not sidecar_src and imported_lyrics_map:
        mapped = imported_lyrics_map.get(str(file_path)) or imported_lyrics_map.get(str(file_path.resolve()))
        if mapped and Path(mapped).exists():
            sidecar_src = f"NanoTune Imported ({Path(mapped).suffix.upper()})"
            try:
                sidecar_txt = Path(mapped).read_text(encoding="utf-8-sig")
            except Exception:
                pass

    lyrics_status = emb_src or sidecar_src or "No Lyrics Found"
    return {
        "kind": "audio",
        "path": str(file_path),
        "filename": file_path.name,
        "title": title,
        "artist": clean_artist_str(artist),
        "album_artist": clean_artist_str(album_artist) if album_artist else "",
        "album": album,
        "year": year_str,
        "track_no": track_no,
        "duration_ms": duration_ms,
        "sample_rate": sample_rate,
        "bits_per_sample": bits_per_sample,
        "bitrate": bitrate,
        "channels": channels,
        "file_size": st_size,
        "codec_detail": codec_detail,
        "format": fmt_label,
        "lyrics_status": lyrics_status,
        "lyrics_raw": sidecar_txt or emb_raw,
    }


def copy_tags_and_cover(src_path: Path, dst_path: Path):
    tags_easy = {}
    try:
        src_easy = mutagen.File(str(src_path), easy=True)
        if src_easy and src_easy.tags:
            for k in ("title", "artist", "albumartist", "album", "date", "tracknumber", "discnumber", "genre"):
                val = src_easy.tags.get(k)
                if val:
                    tags_easy[k] = list(val)
    except Exception:
        pass

    if tags_easy:
        try:
            dst_easy = mutagen.File(str(dst_path), easy=True)
            if dst_easy is not None:
                if dst_easy.tags is None:
                    dst_easy.add_tags()
                for k, v in tags_easy.items():
                    try:
                        dst_easy.tags[k] = v
                    except Exception:
                        pass
                dst_easy.save()
        except Exception:
            pass

    cov_bytes, cov_mime = extract_embedded_cover_bytes(src_path)
    emb_lyrics, _ = read_embedded_lyrics(src_path)
    dst_ext = dst_path.suffix.lower()

    try:
        if dst_ext == ".flac":
            fl = FLAC(str(dst_path))
            if cov_bytes:
                pic = Picture()
                pic.type = 3
                pic.mime = cov_mime or "image/jpeg"
                pic.data = cov_bytes
                fl.clear_pictures()
                fl.add_picture(pic)
            if emb_lyrics:
                fl["LYRICS"] = emb_lyrics
            fl.save()
        elif dst_ext == ".mp3":
            mp = MP3(str(dst_path))
            if mp.tags is None:
                mp.add_tags()
            if cov_bytes:
                mp.tags.delall("APIC")
                mp.tags.add(APIC(encoding=3, mime=cov_mime or "image/jpeg", type=3, desc="Cover", data=cov_bytes))
            if emb_lyrics:
                mp.tags.delall("USLT")
                mp.tags.add(USLT(encoding=3, lang="eng", desc="", text=emb_lyrics))
            mp.save()
        elif dst_ext == ".m4a":
            m4 = MP4(str(dst_path))
            if m4.tags is None:
                m4.add_tags()
            if cov_bytes:
                fmt_cov = MP4Cover.FORMAT_PNG if "png" in (cov_mime or "").lower() else MP4Cover.FORMAT_JPEG
                m4.tags["covr"] = [MP4Cover(cov_bytes, imageformat=fmt_cov)]
            if emb_lyrics:
                m4.tags["\xa9lyr"] = [emb_lyrics]
            m4.save()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Unified Toolbox Tasks Catalogue (with Dropdown Bitrate / Bit-Depth Presets)
# ---------------------------------------------------------------------------
MP3_BITRATES = [
    {"label": "320 kbps", "bitrate": 320000, "suffix": "320k"},
    {"label": "256 kbps", "bitrate": 256000, "suffix": "256k"},
    {"label": "224 kbps", "bitrate": 224000, "suffix": "224k"},
    {"label": "192 kbps", "bitrate": 192000, "suffix": "192k"},
    {"label": "160 kbps", "bitrate": 160000, "suffix": "160k"},
    {"label": "128 kbps", "bitrate": 128000, "suffix": "128k"},
    {"label": "96 kbps", "bitrate": 96000, "suffix": "96k"},
    {"label": "64 kbps", "bitrate": 64000, "suffix": "64k"},
    {"label": "48 kbps", "bitrate": 48000, "suffix": "48k"},
    {"label": "32 kbps", "bitrate": 32000, "suffix": "32k"},
]

AAC_BITRATES = [
    {"label": "320 kbps", "bitrate": 320000, "suffix": "320k"},
    {"label": "256 kbps", "bitrate": 256000, "suffix": "256k"},
    {"label": "192 kbps", "bitrate": 192000, "suffix": "192k"},
    {"label": "160 kbps", "bitrate": 160000, "suffix": "160k"},
    {"label": "128 kbps", "bitrate": 128000, "suffix": "128k"},
    {"label": "96 kbps", "bitrate": 96000, "suffix": "96k"},
    {"label": "64 kbps", "bitrate": 64000, "suffix": "64k"},
]

FLAC_OPTIONS = [
    {"label": "16-bit", "bits_24": False, "sfmt": "s16", "sr": None, "suffix": "16-bit"},
    {"label": "24-bit", "bits_24": True, "sfmt": "s32", "sr": None, "suffix": "24-bit"},
    {"label": "16-bit / 44.1 kHz", "bits_24": False, "sfmt": "s16", "sr": 44100, "suffix": "16b-44k"},
    {"label": "24-bit / 48 kHz", "bits_24": True, "sfmt": "s32", "sr": 48000, "suffix": "24b-48k"},
    {"label": "24-bit / 96 kHz", "bits_24": True, "sfmt": "s32", "sr": 96000, "suffix": "24b-96k"},
]

ALAC_OPTIONS = [
    {"label": "16-bit", "bits_24": False, "sfmt": "s16p", "suffix": "ALAC 16-bit"},
    {"label": "24-bit", "bits_24": True, "sfmt": "s32p", "suffix": "ALAC 24-bit"},
]

WAV_OPTIONS = [
    {"label": "16-bit PCM", "codec": "pcm_s16le", "sfmt": "s16", "bits_24": False, "layout": None, "suffix": "16-bit"},
    {"label": "24-bit PCM", "codec": "pcm_s24le", "sfmt": "s32", "bits_24": True, "layout": None, "suffix": "24-bit"},
    {"label": "32-bit Float", "codec": "pcm_f32le", "sfmt": "flt", "bits_24": False, "layout": None, "suffix": "32f"},
    {"label": "Mono 16-bit", "codec": "pcm_s16le", "sfmt": "s16", "bits_24": False, "layout": "mono", "suffix": "Mono"},
]

OGG_BITRATES = [
    {"label": "320 kbps", "bitrate": 320000, "suffix": "320k"},
    {"label": "256 kbps", "bitrate": 256000, "suffix": "256k"},
    {"label": "192 kbps", "bitrate": 192000, "suffix": "192k"},
    {"label": "160 kbps", "bitrate": 160000, "suffix": "160k"},
    {"label": "128 kbps", "bitrate": 128000, "suffix": "128k"},
    {"label": "96 kbps", "bitrate": 96000, "suffix": "96k"},
]

OPUS_BITRATES = [
    {"label": "256 kbps", "bitrate": 256000, "suffix": "256k"},
    {"label": "192 kbps", "bitrate": 192000, "suffix": "192k"},
    {"label": "160 kbps", "bitrate": 160000, "suffix": "160k"},
    {"label": "128 kbps", "bitrate": 128000, "suffix": "128k"},
    {"label": "96 kbps", "bitrate": 96000, "suffix": "96k"},
    {"label": "64 kbps", "bitrate": 64000, "suffix": "64k"},
    {"label": "32 kbps", "bitrate": 32000, "suffix": "32k"},
]

AIFF_OPTIONS = [
    {"label": "16-bit PCM", "codec": "pcm_s16be", "sfmt": "s16", "bits_24": False, "suffix": "16-bit"},
    {"label": "24-bit PCM", "codec": "pcm_s24be", "sfmt": "s32", "bits_24": True, "suffix": "24-bit"},
]

TASKS = [
    # Audio Codecs
    {
        "id": "to_flac",
        "category": "audio",
        "badge": "FLAC",
        "title": "Convert to FLAC",
        "desc": "Lossless studio compression preserving metadata tags & cover art.",
        "options": FLAC_OPTIONS,
        "default_opt": 1,
    },
    {
        "id": "to_mp3",
        "category": "audio",
        "badge": "MP3",
        "title": "Convert to MP3",
        "desc": "Universal compatibility across all players & devices with ID3 tags.",
        "options": MP3_BITRATES,
        "default_opt": 0,
    },
    {
        "id": "to_m4a_aac",
        "category": "audio",
        "badge": "M4A AAC",
        "title": "Convert to M4A (AAC)",
        "desc": "More efficient quality at smaller file sizes with embedded cover art.",
        "options": AAC_BITRATES,
        "default_opt": 1,
    },
    {
        "id": "to_m4a_alac",
        "category": "audio",
        "badge": "M4A ALAC",
        "title": "Convert to M4A (ALAC)",
        "desc": "Apple Lossless compression inside an .m4a container, keeping tags & art.",
        "options": ALAC_OPTIONS,
        "default_opt": 0,
    },
    {
        "id": "to_wav",
        "category": "audio",
        "badge": "WAV",
        "title": "Convert to WAV",
        "desc": "Uncompressed studio PCM audio for DAWs, mastering & editing.",
        "options": WAV_OPTIONS,
        "default_opt": 0,
    },
    {
        "id": "to_ogg",
        "category": "audio",
        "badge": "OGG",
        "title": "Convert to OGG Vorbis",
        "desc": "Open-source efficient lossy audio inside an .ogg container.",
        "options": OGG_BITRATES,
        "default_opt": 1,
    },
    {
        "id": "to_opus",
        "category": "audio",
        "badge": "OPUS",
        "title": "Convert to OPUS",
        "desc": "Ultra-efficient modern codec resampled cleanly to 48 kHz.",
        "options": OPUS_BITRATES,
        "default_opt": 2,
    },
    {
        "id": "to_aiff",
        "category": "audio",
        "badge": "AIFF",
        "title": "Convert to AIFF",
        "desc": "Uncompressed Apple/studio PCM audio file.",
        "options": AIFF_OPTIONS,
        "default_opt": 0,
    },
    # Lyrics Tools
    {
        "id": "edit_lyrics_view",
        "category": "lyrics",
        "badge": "EDIT LYRICS",
        "title": "Open Full-View Lyrics & Timestamp Editor",
        "desc": "Full-screen blurred-cover lyrics view to type, edit, stamp & save .LRC/.ELRC.",
    },
    {
        "id": "extract_lyrics",
        "category": "lyrics",
        "badge": "EXTRACT LRC",
        "title": "Extract Embedded Lyrics (.LRC / .ELRC)",
        "desc": "Saves embedded synced, word-synced, or plain lyrics into the same folder.",
    },
    {
        "id": "elrc_to_lrc",
        "category": "lyrics",
        "badge": "ELRC → LRC",
        "title": "Smart Convert .ELRC to Standard .LRC",
        "desc": "Strips word-by-word <mm:ss.xxx> tags, preserving main sentence [mm:ss.xx] timing.",
    },
    {
        "id": "lrc_to_elrc",
        "category": "lyrics",
        "badge": "LRC → ELRC",
        "title": "Makeshift .LRC to Word-by-Word .ELRC",
        "desc": "Divides each sentence duration evenly by word count for karaoke-style .ELRC.",
    },
    {
        "id": "lrc_to_txt",
        "category": "lyrics",
        "badge": "LRC → TXT",
        "title": "Strip Timestamps to Plain Lyrics (.TXT)",
        "desc": "Removes all [mm:ss.xx] and <mm:ss.xxx> timestamps for clean readable text.",
    },
    {
        "id": "shift_lyrics",
        "category": "lyrics",
        "badge": "TIME SHIFT",
        "title": "Shift .LRC / .ELRC Timestamps (±ms Offset)",
        "desc": "Adjust every line & word timestamp earlier or later using the offset control.",
    },
    {
        "id": "embed_lyrics_tag",
        "category": "lyrics",
        "badge": "EMBED TAG",
        "title": "Embed .LRC / .ELRC File into Audio Tag",
        "desc": "Embeds a .LRC or .ELRC file directly into FLAC, MP3, or M4A metadata.",
    },
    {
        "id": "strip_embedded_lyrics",
        "category": "lyrics",
        "badge": "STRIP LYR",
        "title": "Remove Embedded Lyrics from Audio Tag",
        "desc": "Clears LYRICS / USLT / ©lyr tags inside the selected audio file(s).",
    },
    # Metadata & Cover Art
    {
        "id": "extract_cover_png",
        "category": "meta",
        "badge": "COVER PNG",
        "title": "Extract Embedded Cover Art (.PNG)",
        "desc": "Saves full-resolution embedded album artwork as a PNG in the same folder.",
    },
    {
        "id": "extract_cover_jpg",
        "category": "meta",
        "badge": "COVER JPG",
        "title": "Extract Embedded Cover Art (.JPG)",
        "desc": "Saves embedded album artwork as a high-quality JPEG in the same folder.",
    },
    {
        "id": "strip_tags_copy",
        "category": "meta",
        "badge": "CLEAN COPY",
        "title": "Export Metadata-Free Clean Copy",
        "desc": "Creates a clean copy in the same folder with all tags & artwork removed.",
    },
]


def unique_output_path(src_path: Path, ext: str, tag_suffix: str = "") -> Path:
    ext = ext if ext.startswith(".") else f".{ext}"
    base = f"{src_path.stem}{tag_suffix}"
    cand = src_path.with_name(f"{base}{ext}")
    if cand.resolve() != src_path.resolve() and not cand.exists():
        return cand
    i = 1
    while True:
        cand = src_path.with_name(f"{base}_{i}{ext}")
        if cand.resolve() != src_path.resolve() and not cand.exists():
            return cand
        i += 1


def _ts_to_ms(mins: str, secs: str, frac: str | None) -> int:
    frac_s = frac or "0"
    ms_part = int(frac_s.ljust(3, "0")[:3])
    return (int(mins) * 60 + int(secs)) * 1000 + ms_part


def _fmt_lrc_ts(ms: int) -> str:
    ms = max(0, int(round(ms)))
    mins, rem = divmod(ms, 60000)
    secs, rem_ms = divmod(rem, 1000)
    return f"[{mins:02d}:{secs:02d}.{rem_ms // 10:02d}]"


def _fmt_elrc_word_ts(ms: int) -> str:
    ms = max(0, int(round(ms)))
    mins, rem = divmod(ms, 60000)
    secs, rem_ms = divmod(rem, 1000)
    return f"<{mins:02d}:{secs:02d}.{rem_ms:03d}>"


def _shift_timestamp_match(m: re.Match, offset_ms: int, bracket_open: str, bracket_close: str) -> str:
    mins = int(m.group(1))
    secs = int(m.group(2))
    frac = m.group(3) or "00"
    ms_part = int(frac.ljust(3, "0")[:3])
    total = max(0, (mins * 60 + secs) * 1000 + ms_part + offset_ms)
    n_mins, rem = divmod(total, 60000)
    n_secs, n_ms = divmod(rem, 1000)
    if len(frac) == 3:
        return f"{bracket_open}{n_mins:02d}:{n_secs:02d}.{n_ms:03d}{bracket_close}"
    return f"{bracket_open}{n_mins:02d}:{n_secs:02d}.{n_ms // 10:02d}{bracket_close}"


def convert_elrc_to_standard_lrc(raw_text: str) -> str:
    out_lines: list[str] = []
    for raw_ln in raw_text.splitlines():
        line_matches = list(LRC_LINE_RE.finditer(raw_ln))
        word_matches = list(ELRC_WORD_RE.finditer(raw_ln))

        if line_matches:
            stamps = "".join(m.group(0) for m in line_matches)
            body = LRC_LINE_RE.sub("", raw_ln)
            body = ELRC_WORD_RE.sub("", body)
            body = VOICE_PREFIX_RE.sub("", body)
            body = re.sub(r"\s{2,}", " ", body).strip()
            out_lines.append(f"{stamps} {body}".rstrip() if body else stamps)
        elif word_matches:
            first_m = word_matches[0]
            first_ms = _ts_to_ms(first_m.group(1), first_m.group(2), first_m.group(3))
            body = ELRC_WORD_RE.sub("", raw_ln)
            body = VOICE_PREFIX_RE.sub("", body)
            body = re.sub(r"\s{2,}", " ", body).strip()
            out_lines.append(f"{_fmt_lrc_ts(first_ms)} {body}".rstrip())
        else:
            out_lines.append(raw_ln.rstrip())
    return "\n".join(out_lines)


def convert_lrc_to_makeshift_elrc(raw_text: str, track_duration_ms: int = 0) -> str:
    headers: list[str] = []
    timed_entries: list[tuple[int, str]] = []

    for raw_ln in raw_text.splitlines():
        stripped = raw_ln.strip()
        if not stripped:
            continue
        if re.match(r"^\[(ar|ti|al|by|offset|length|re|ve):.*\]$", stripped, re.I):
            headers.append(stripped)
            continue
        matches = list(LRC_LINE_RE.finditer(raw_ln))
        if matches:
            body = LRC_LINE_RE.sub("", raw_ln)
            body = ELRC_WORD_RE.sub("", body)
            body = VOICE_PREFIX_RE.sub("", body)
            body = re.sub(r"\s{2,}", " ", body).strip()
            for m in matches:
                t_ms = _ts_to_ms(m.group(1), m.group(2), m.group(3))
                timed_entries.append((t_ms, body))

    if not timed_entries:
        raise RuntimeError("No [mm:ss.xx] timed lines found to convert into .ELRC.")

    timed_entries.sort(key=lambda x: x[0])
    out_lines: list[str] = list(headers)

    for i, (t_start, body) in enumerate(timed_entries):
        line_tag = _fmt_lrc_ts(t_start)
        if not body or body == "♪":
            out_lines.append(line_tag)
            continue

        words = body.split()
        n_words = len(words)
        if i + 1 < len(timed_entries):
            raw_gap = max(350, timed_entries[i + 1][0] - t_start)
        elif track_duration_ms > t_start + 400:
            raw_gap = track_duration_ms - t_start
        else:
            raw_gap = max(1400, n_words * 420)

        natural_cap = max(2800, n_words * 750)
        bar_dur = raw_gap if raw_gap <= 9500 else min(raw_gap, natural_cap)
        step_ms = bar_dur / float(n_words)

        parts = [line_tag]
        for k, w in enumerate(words):
            w_start = t_start + k * step_ms
            parts.append(f"{_fmt_elrc_word_ts(w_start)}{w}")

        if raw_gap > bar_dur or i == len(timed_entries) - 1:
            parts.append(_fmt_elrc_word_ts(t_start + bar_dur))

        out_lines.append(" ".join(parts))

    return "\n".join(out_lines)


# ---------------------------------------------------------------------------
# Batch-Capable Background Worker
# ---------------------------------------------------------------------------
class TaskWorker(threading.Thread):
    def __init__(self, task_id: str, src_paths: list[Path], extra: dict, progress_cb, done_cb):
        super().__init__(daemon=True)
        self.task_id = task_id
        self.src_paths = src_paths
        self.extra = extra
        self.progress_cb = progress_cb
        self.done_cb = done_cb

    def run(self):
        total = max(1, len(self.src_paths))
        outputs: list[str] = []
        errors: list[str] = []

        for idx, src in enumerate(self.src_paths):
            def _sub_prog(pct: int, i=idx):
                overall = int(((i + pct / 100.0) / total) * 100.0)
                self.progress_cb(overall, f"[{i + 1}/{total}] Processing {src.name}..." if total > 1 else "")

            try:
                out_p, msg = self._execute_one(src, _sub_prog)
                if out_p is not None:
                    outputs.append(str(out_p))
                elif msg:
                    outputs.append("")
            except Exception as exc:
                errors.append(f"{src.name}: {exc}")

        if outputs and not errors:
            if total == 1:
                self.done_cb(True, outputs, f"Created {Path(outputs[0]).name}" if outputs[0] else "Done")
            else:
                self.done_cb(True, [p for p in outputs if p], f"Batch complete: {len(outputs)} files processed in original folders.")
        elif outputs and errors:
            self.done_cb(True, [p for p in outputs if p], f"Completed {len(outputs)}/{total} ({len(errors)} skipped: {errors[0]})")
        else:
            self.done_cb(False, [], errors[0] if errors else "Task failed.")

    def _transcode_audio(
        self,
        src_path: Path,
        sub_prog,
        out_ext: str,
        codec_candidates: list[str],
        mux_fmt: str | None = None,
        sample_rate: int | None = None,
        bitrate: int | None = None,
        layout: str | None = None,
        sample_fmt_override: str | None = None,
        bits_24: bool = False,
        tag_suffix: str = "",
        copy_metadata: bool = True,
    ) -> tuple[Path, str]:
        if not HAS_AV:
            raise RuntimeError("PyAV ('av') is required for audio conversion. Install it with: pip install av")
        import av

        out_path = unique_output_path(src_path, out_ext, tag_suffix=tag_suffix)
        in_container = av.open(str(src_path))
        in_stream = next((s for s in in_container.streams if s.type == "audio"), None)
        if in_stream is None:
            in_container.close()
            raise RuntimeError("No audio stream found in source file.")

        src_sr = int(in_stream.rate or 44100)
        target_sr = sample_rate or src_sr
        if out_ext == ".opus":
            target_sr = 48000
        elif out_ext == ".m4a" and "aac" in codec_candidates and target_sr > 48000:
            target_sr = 48000

        src_ch = int(getattr(in_stream, "channels", 2) or 2)
        target_layout = layout or ("mono" if src_ch == 1 else "stereo")
        total_dur_sec = float(in_container.duration / av.time_base) if in_container.duration else 0.0

        open_kwargs = {"mode": "w"}
        if mux_fmt:
            open_kwargs["format"] = mux_fmt
        out_container = av.open(str(out_path), **open_kwargs)

        out_stream = None
        last_err = None
        for cname in codec_candidates:
            try:
                out_stream = out_container.add_stream(cname, rate=target_sr)
                break
            except Exception as exc:
                last_err = exc
                out_stream = None
        if out_stream is None:
            in_container.close()
            out_container.close()
            if out_path.exists():
                out_path.unlink(missing_ok=True)
            raise RuntimeError(f"Encoder unavailable ({', '.join(codec_candidates)}): {last_err}")

        if bitrate:
            try:
                out_stream.bit_rate = bitrate
            except Exception:
                pass
        try:
            out_stream.codec_context.layout = target_layout
        except Exception:
            pass
        if sample_fmt_override:
            try:
                out_stream.codec_context.format = sample_fmt_override
            except Exception:
                pass
        if bits_24:
            try:
                out_stream.codec_context.bits_per_raw_sample = 24
            except Exception:
                pass

        enc_fmt = out_stream.codec_context.format.name if getattr(out_stream.codec_context, "format", None) else "s16"
        enc_sr = int(out_stream.codec_context.sample_rate or target_sr)
        enc_layout = out_stream.codec_context.layout.name if getattr(out_stream.codec_context, "layout", None) else target_layout
        chunk_size = int(getattr(out_stream.codec_context, "frame_size", 0) or 0)

        resampler = av.AudioResampler(format=enc_fmt, layout=enc_layout, rate=enc_sr)
        fifo = av.AudioFifo() if chunk_size > 0 else None
        pts_counter = 0

        sub_prog(5)
        for frame in in_container.decode(in_stream):
            if total_dur_sec > 0 and frame.pts is not None and in_stream.time_base is not None:
                cur_sec = float(frame.pts * in_stream.time_base)
                sub_prog(max(5, min(92, int((cur_sec / total_dur_sec) * 90))))

            for r_frame in resampler.resample(frame):
                if fifo is not None:
                    fifo.write(r_frame)
                    while fifo.samples >= chunk_size:
                        chunk = fifo.read(chunk_size)
                        chunk.pts = pts_counter
                        pts_counter += chunk.samples
                        for pkt in out_stream.encode(chunk):
                            out_container.mux(pkt)
                else:
                    r_frame.pts = pts_counter
                    pts_counter += r_frame.samples
                    for pkt in out_stream.encode(r_frame):
                        out_container.mux(pkt)

        for r_frame in resampler.resample(None):
            if fifo is not None:
                fifo.write(r_frame)
            else:
                r_frame.pts = pts_counter
                pts_counter += r_frame.samples
                for pkt in out_stream.encode(r_frame):
                    out_container.mux(pkt)

        if fifo is not None and fifo.samples > 0:
            rem = fifo.read(fifo.samples)
            if rem is not None:
                rem.pts = pts_counter
                pts_counter += rem.samples
                for pkt in out_stream.encode(rem):
                    out_container.mux(pkt)

        for pkt in out_stream.encode(None):
            out_container.mux(pkt)

        out_container.close()
        in_container.close()

        sub_prog(96)
        if copy_metadata:
            copy_tags_and_cover(src_path, out_path)
        sub_prog(100)
        return out_path, f"Created {out_path.name}"

    def _read_any_lyrics_for_source(self, src: Path) -> str:
        if src.suffix.lower() in LYRIC_EXTENSIONS:
            for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
                try:
                    return src.read_text(encoding=enc)
                except Exception:
                    continue
        raw, _ = read_embedded_lyrics(src)
        if raw:
            return raw
        for lext in (".elrc", ".lrc"):
            cand = src.with_suffix(lext)
            if cand.exists():
                for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
                    try:
                        return cand.read_text(encoding=enc)
                    except Exception:
                        continue
        return ""

    def _execute_one(self, src: Path, sub_prog) -> tuple[Path | None, str]:
        tid = self.task_id
        opt = self.extra.get("option") or {}
        same_ext = src.suffix.lower()

        if tid == "to_flac":
            suf = f" ({opt['suffix']})" if same_ext == ".flac" or opt.get("sr") else ""
            return self._transcode_audio(
                src, sub_prog, ".flac", ["flac"],
                sample_rate=opt.get("sr"),
                sample_fmt_override=opt.get("sfmt", "s16"),
                bits_24=bool(opt.get("bits_24", False)),
                tag_suffix=suf,
            )

        if tid == "to_mp3":
            br = int(opt.get("bitrate", 320000))
            suf = f" ({opt['suffix']})" if same_ext == ".mp3" else ""
            return self._transcode_audio(src, sub_prog, ".mp3", ["libmp3lame", "mp3"], bitrate=br, tag_suffix=suf)

        if tid == "to_m4a_aac":
            br = int(opt.get("bitrate", 256000))
            suf = f" (AAC {opt['suffix']})" if same_ext == ".m4a" else ""
            return self._transcode_audio(src, sub_prog, ".m4a", ["aac"], mux_fmt="ipod", bitrate=br, tag_suffix=suf)

        if tid == "to_m4a_alac":
            return self._transcode_audio(
                src, sub_prog, ".m4a", ["alac"], mux_fmt="ipod",
                sample_fmt_override=opt.get("sfmt", "s16p"),
                bits_24=bool(opt.get("bits_24", False)),
                tag_suffix=f" ({opt.get('suffix', 'ALAC')})",
            )

        if tid == "to_wav":
            suf = f" ({opt['suffix']})" if same_ext == ".wav" or opt.get("layout") == "mono" else ""
            return self._transcode_audio(
                src, sub_prog, ".wav", [opt.get("codec", "pcm_s16le")],
                sample_fmt_override=opt.get("sfmt", "s16"),
                bits_24=bool(opt.get("bits_24", False)),
                layout=opt.get("layout"),
                tag_suffix=suf,
            )

        if tid == "to_ogg":
            br = int(opt.get("bitrate", 256000))
            suf = f" ({opt['suffix']})" if same_ext == ".ogg" else ""
            return self._transcode_audio(src, sub_prog, ".ogg", ["libvorbis", "vorbis"], bitrate=br, tag_suffix=suf)

        if tid == "to_opus":
            br = int(opt.get("bitrate", 160000))
            suf = f" ({opt['suffix']})" if same_ext == ".opus" else ""
            return self._transcode_audio(src, sub_prog, ".opus", ["libopus", "opus"], sample_rate=48000, bitrate=br, tag_suffix=suf)

        if tid == "to_aiff":
            suf = f" ({opt['suffix']})" if same_ext in (".aiff", ".aif") else ""
            return self._transcode_audio(
                src, sub_prog, ".aiff", [opt.get("codec", "pcm_s16be")],
                mux_fmt="aiff",
                sample_fmt_override=opt.get("sfmt", "s16"),
                bits_24=bool(opt.get("bits_24", False)),
                tag_suffix=suf,
            )

        if tid == "strip_tags_copy":
            codec_map = {
                ".flac": (["flac"], None, None),
                ".mp3": (["libmp3lame", "mp3"], None, 320000),
                ".m4a": (["aac"], "ipod", 256000),
                ".wav": (["pcm_s16le"], None, None),
                ".ogg": (["libvorbis", "vorbis"], None, 256000),
                ".opus": (["libopus", "opus"], None, 160000),
            }
            codecs, mfmt, br = codec_map.get(same_ext, (["flac"], None, None))
            out_ext = same_ext if same_ext in codec_map else ".flac"
            return self._transcode_audio(src, sub_prog, out_ext, codecs, mux_fmt=mfmt, bitrate=br, tag_suffix=" (Clean)", copy_metadata=False)

        if tid == "extract_lyrics":
            sub_prog(30)
            raw, _ = read_embedded_lyrics(src)
            if not raw:
                raise RuntimeError("No embedded lyrics found inside this audio file.")
            is_elrc = bool(ELRC_WORD_RE.search(raw))
            out_path = unique_output_path(src, ".elrc" if is_elrc else ".lrc")
            out_path.write_text(raw, encoding="utf-8")
            sub_prog(100)
            return out_path, f"Extracted lyrics to {out_path.name}"

        if tid == "elrc_to_lrc":
            sub_prog(25)
            raw = self._read_any_lyrics_for_source(src)
            if not raw.strip():
                raise RuntimeError("No lyrics found in selected file or sidecar.")
            converted = convert_elrc_to_standard_lrc(raw)
            out_path = unique_output_path(src, ".lrc", tag_suffix=" (LineSynced)" if same_ext == ".lrc" else "")
            out_path.write_text(converted, encoding="utf-8")
            sub_prog(100)
            return out_path, f"Converted to {out_path.name}"

        if tid == "lrc_to_elrc":
            sub_prog(25)
            raw = self._read_any_lyrics_for_source(src)
            if not raw.strip():
                raise RuntimeError("No .LRC lyrics found in selected file or sidecar.")
            dur_ms = 0
            if same_ext not in LYRIC_EXTENSIONS:
                try:
                    au = mutagen.File(str(src))
                    if au and au.info:
                        dur_ms = int((getattr(au.info, "length", 0) or 0) * 1000)
                except Exception:
                    pass
            converted = convert_lrc_to_makeshift_elrc(raw, track_duration_ms=dur_ms)
            out_path = unique_output_path(src, ".elrc", tag_suffix=" (WordSynced)" if same_ext == ".elrc" else "")
            out_path.write_text(converted, encoding="utf-8")
            sub_prog(100)
            return out_path, f"Created makeshift word-synced {out_path.name}"

        if tid == "lrc_to_txt":
            sub_prog(25)
            raw = self._read_any_lyrics_for_source(src)
            if not raw.strip():
                raise RuntimeError("No lyrics found in selected file or sidecar.")
            lines_out = []
            for ln in raw.splitlines():
                if re.match(r"^\s*\[(ar|ti|al|by|offset|length|re|ve):.*\]\s*$", ln, re.I):
                    continue
                cleaned = LRC_LINE_RE.sub("", ln)
                cleaned = ELRC_WORD_RE.sub("", cleaned)
                cleaned = VOICE_PREFIX_RE.sub("", cleaned)
                lines_out.append(re.sub(r"\s{2,}", " ", cleaned).strip())
            out_path = unique_output_path(src, ".txt", tag_suffix=" (Lyrics)")
            out_path.write_text("\n".join(lines_out).strip(), encoding="utf-8")
            sub_prog(100)
            return out_path, f"Saved plain text lyrics to {out_path.name}"

        if tid == "shift_lyrics":
            sub_prog(25)
            raw = self._read_any_lyrics_for_source(src)
            if not raw.strip():
                raise RuntimeError("No lyrics found in selected file or sidecar.")
            offset_ms = int(self.extra.get("shift_ms", 0))
            lines_out = []
            for ln in raw.splitlines():
                ln2 = LRC_LINE_RE.sub(lambda m: _shift_timestamp_match(m, offset_ms, "[", "]"), ln)
                ln2 = ELRC_WORD_RE.sub(lambda m: _shift_timestamp_match(m, offset_ms, "<", ">"), ln2)
                lines_out.append(ln2)
            out_ext = same_ext if same_ext in LYRIC_EXTENSIONS else (".elrc" if ELRC_WORD_RE.search(raw) else ".lrc")
            out_path = unique_output_path(src, out_ext, tag_suffix=f" ({offset_ms:+d}ms)")
            out_path.write_text("\n".join(lines_out), encoding="utf-8")
            sub_prog(100)
            return out_path, f"Shifted timestamps by {offset_ms:+d} ms"

        if tid == "embed_lyrics_tag":
            lrc_path = Path(self.extra.get("lrc_path", ""))
            if not lrc_path.is_file():
                for lext in (".elrc", ".lrc"):
                    cand = src.with_suffix(lext)
                    if cand.exists():
                        lrc_path = cand
                        break
            if not lrc_path.is_file():
                raise RuntimeError("No .LRC / .ELRC file found to embed.")
            text = lrc_path.read_text(encoding="utf-8-sig").strip()
            sub_prog(50)
            if same_ext == ".flac":
                fl = FLAC(str(src))
                fl["LYRICS"] = text
                fl.save()
            elif same_ext == ".mp3":
                mp = MP3(str(src))
                if mp.tags is None:
                    mp.add_tags()
                mp.tags.delall("USLT")
                mp.tags.add(USLT(encoding=3, lang="eng", desc="", text=text))
                mp.save()
            elif same_ext == ".m4a":
                m4 = MP4(str(src))
                if m4.tags is None:
                    m4.add_tags()
                m4.tags["\xa9lyr"] = [text]
                m4.save()
            else:
                raise RuntimeError(f"Embedded lyrics tagging supports FLAC, MP3, and M4A (not {same_ext}).")
            sub_prog(100)
            return src, f"Embedded {lrc_path.name} into {src.name}"

        if tid == "strip_embedded_lyrics":
            sub_prog(50)
            if same_ext == ".flac":
                fl = FLAC(str(src))
                for k in list(fl.tags.keys()) if fl.tags else []:
                    if "lyric" in k.lower():
                        del fl.tags[k]
                fl.save()
            elif same_ext == ".mp3":
                mp = MP3(str(src))
                if mp.tags:
                    mp.tags.delall("USLT")
                    mp.save()
            elif same_ext == ".m4a":
                m4 = MP4(str(src))
                if m4.tags and "\xa9lyr" in m4.tags:
                    del m4.tags["\xa9lyr"]
                    m4.save()
            else:
                raise RuntimeError(f"Stripping embedded lyrics supports FLAC, MP3, and M4A (not {same_ext}).")
            sub_prog(100)
            return src, f"Removed embedded lyrics from {src.name}"

        if tid in ("extract_cover_png", "extract_cover_jpg"):
            sub_prog(40)
            pil_cov = extract_cover_pil(src)
            if pil_cov is None:
                raise RuntimeError("No embedded cover art found in this file.")
            is_png = tid == "extract_cover_png"
            out_path = unique_output_path(src, ".png" if is_png else ".jpg", tag_suffix="_cover")
            pil_cov.save(out_path, format="PNG" if is_png else "JPEG", quality=95)
            sub_prog(100)
            return out_path, f"Saved cover art to {out_path.name}"

        raise RuntimeError(f"Unknown task: {tid}")


# ---------------------------------------------------------------------------
# Event Filter & Custom Widgets
# ---------------------------------------------------------------------------
class GlobalSpacePlayPauseFilter(QObject):
    """Toggles preview Play/Pause on Space unless typing inside a text/number input."""

    def __init__(self, main_win):
        super().__init__(main_win)
        self.main_win = main_win

    def eventFilter(self, obj, event):
        etype = event.type()
        if etype in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease, QEvent.Type.ShortcutOverride):
            if event.key() == Qt.Key.Key_Space and not (
                event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier)
            ):
                fw = QApplication.focusWidget()
                if isinstance(fw, (QLineEdit, QTextEdit, QSpinBox)):
                    return False
                if etype == QEvent.Type.KeyPress and not event.isAutoRepeat():
                    self.main_win.toggle_play_pause()
                event.accept()
                return True
        return super().eventFilter(obj, event)


class TimestampHighlighter(QSyntaxHighlighter):
    """Colors [mm:ss.xx] & <mm:ss.xxx> timestamps in the song's adaptive accent color."""

    def __init__(self, document, accent_hex: str = STAR_DEFAULT_YELLOW):
        super().__init__(document)
        self.ts_fmt = QTextCharFormat()
        self.set_accent(accent_hex)

    def set_accent(self, accent_hex: str):
        self.ts_fmt.setForeground(QColor(accent_hex))
        self.ts_fmt.setFontWeight(QFont.Weight.Bold)
        self.rehighlight()

    def highlightBlock(self, text: str):
        for pat in (LRC_LINE_RE, ELRC_WORD_RE):
            for m in pat.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), self.ts_fmt)


class StackedCoversWidget(QWidget):
    """Displays 1 crisp cover cleanly (no shadow bleed), or up to 3 covers Solitaire-stacked for batches."""

    clicked = Signal()

    def __init__(self, radius: int = 12, border_width: float = 2.0, parent=None):
        super().__init__(parent)
        self.radius = radius
        self.border_width = border_width
        self.border_color = QColor(STAR_DEFAULT_YELLOW)
        self._pixmaps: list[QPixmap] = []

    def set_pixmaps(self, pms: list[QPixmap]):
        self._pixmaps = [pm for pm in pms[:3] if not pm.isNull()]
        self.setCursor(Qt.CursorShape.PointingHandCursor if len(self._pixmaps) > 1 else Qt.CursorShape.ArrowCursor)
        self.update()

    def set_border_color(self, hex_color: str):
        self.border_color = QColor(hex_color)
        self.border_color.setAlpha(175)
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and len(self._pixmaps) > 1:
            self.clicked.emit()
        super().mousePressEvent(event)

    def _draw_rounded_card(self, p: QPainter, rect: QRectF, pm: QPixmap, dim_factor: float = 1.0, draw_shadow: bool = False):
        path = QPainterPath()
        path.addRoundedRect(rect, self.radius, self.radius)

        if draw_shadow:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 125))
            p.drawRoundedRect(rect.translated(2.5, 3.0), self.radius, self.radius)

        p.save()
        p.setClipPath(path)
        if not pm.isNull():
            sz = QSize(max(1, int(math.ceil(rect.width()))), max(1, int(math.ceil(rect.height()))))
            scaled = pm.scaled(sz, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            dx = rect.x() + (rect.width() - scaled.width()) * 0.5
            dy = rect.y() + (rect.height() - scaled.height()) * 0.5
            p.drawPixmap(QPointF(dx, dy), scaled)
        else:
            p.fillRect(rect, QColor("#151A1C"))
        if dim_factor < 1.0:
            p.fillRect(rect, QColor(8, 10, 12, int((1.0 - dim_factor) * 255)))
        p.restore()

        p.setPen(QPen(self.border_color, self.border_width))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        w, h = float(self.width()), float(self.height())
        inset = self.border_width * 0.5
        if not self._pixmaps:
            self._draw_rounded_card(p, QRectF(inset, inset, w - self.border_width, h - self.border_width), QPixmap(), draw_shadow=False)
            return

        n = len(self._pixmaps)
        if n == 1:
            # No offset shadow for single cover -> clean border with zero bottom-right grey corner
            self._draw_rounded_card(p, QRectF(inset, inset, w - self.border_width, h - self.border_width), self._pixmaps[0], draw_shadow=False)
            return

        step = min(15.0, w * 0.075)
        pad = 3.5
        card_w = w - step * (n - 1) - self.border_width - pad
        card_h = h - step * (n - 1) - self.border_width - pad
        for rev_i in range(n - 1, -1, -1):
            x = inset + rev_i * step
            y = inset + (n - 1 - rev_i) * step
            dim = 1.0 if rev_i == 0 else (0.82 if rev_i == 1 else 0.66)
            self._draw_rounded_card(p, QRectF(x, y, card_w, card_h), self._pixmaps[rev_i], dim_factor=dim, draw_shadow=True)


class SeekSlider(QSlider):
    def _value_from_x(self, x_pos: float) -> int:
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        groove = self.style().subControlRect(QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderGroove, self)
        handle = self.style().subControlRect(QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderHandle, self)
        span = max(1, groove.width() - handle.width())
        rel = int(x_pos - groove.x() - handle.width() * 0.5)
        return QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), rel, span)

    def mousePressEvent(self, event):
        if not self.isEnabled():
            event.ignore()
            return
        if event.button() == Qt.MouseButton.LeftButton and self.maximum() > self.minimum():
            val = self._value_from_x(event.position().x())
            self.setValue(val)
            self.sliderPressed.emit()
            self.sliderMoved.emit(val)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not self.isEnabled():
            event.ignore()
            return
        if (event.buttons() & Qt.MouseButton.LeftButton) and self.maximum() > self.minimum():
            val = self._value_from_x(event.position().x())
            self.setValue(val)
            self.sliderMoved.emit(val)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if not self.isEnabled():
            event.ignore()
            return
        if event.button() == Qt.MouseButton.LeftButton and self.maximum() > self.minimum():
            val = self._value_from_x(event.position().x())
            self.setValue(val)
            self.sliderReleased.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class OptionPopupItem(QPushButton):
    def __init__(self, label: str, is_selected: bool, accent: str, font_family: str, parent=None):
        super().__init__(parent)
        self.opt_label = label
        self.is_selected = is_selected
        self.accent_c = QColor(accent)
        self.font_family = font_family
        self._hovered = False
        self.setFixedHeight(32)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(1.0, 1.0, self.width() - 2.0, self.height() - 2.0)
        ac = self.accent_c

        if self._hovered or self.is_selected:
            fill = QColor(ac.red(), ac.green(), ac.blue(), 42 if self._hovered else 28)
            border = QColor(ac.red(), ac.green(), ac.blue(), 220 if self._hovered else 155)
            p.setBrush(fill)
            p.setPen(QPen(border, 1.2))
            p.drawRoundedRect(r, 9.0, 9.0)
        else:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(255, 255, 255, 16), 1.0))
            p.drawRoundedRect(r, 9.0, 9.0)

        cx = 16.0
        cy = self.height() * 0.5
        circle_r = 4.6
        if self._hovered or self.is_selected:
            p.setPen(QPen(ac, 1.5))
            p.setBrush(ac if self.is_selected else QColor(ac.red(), ac.green(), ac.blue(), 70))
        else:
            p.setPen(QPen(QColor(150, 166, 163, 140), 1.3))
            p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(cx, cy), circle_r, circle_r)

        p.setFont(QFont(self.font_family, 10, QFont.Weight.Bold if (self._hovered or self.is_selected) else QFont.Weight.DemiBold))
        p.setPen(ac if (self._hovered or self.is_selected) else QColor("#DCE6E4"))
        p.drawText(QRectF(28.0, 0.0, self.width() - 36.0, float(self.height())), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.opt_label)


class OptionPopupMenu(QDialog):
    optionChosen = Signal(int)

    def __init__(self, options: list[dict], selected_idx: int, accent: str, font_family: str, anchor_btn: QWidget):
        super().__init__(anchor_btn, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.accent_c = QColor(accent)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)

        card = QFrame()
        card.setObjectName("popupCard")
        c = self.accent_c
        card.setStyleSheet(f"""
            QFrame#popupCard {{
                background-color: rgba(13, 18, 20, 246);
                border: 1px solid rgba({c.red()}, {c.green()}, {c.blue()}, 145);
                border-radius: 14px;
            }}
        """)
        c_lay = QVBoxLayout(card)
        c_lay.setContentsMargins(7, 7, 7, 7)
        c_lay.setSpacing(4)

        for idx, opt in enumerate(options):
            btn = OptionPopupItem(opt["label"], idx == selected_idx, accent, font_family)
            btn.setMinimumWidth(148)
            btn.clicked.connect(lambda _c=False, i=idx: self._pick(i))
            c_lay.addWidget(btn)

        outer.addWidget(card)
        self.adjustSize()

        g_pos = anchor_btn.mapToGlobal(QPoint(anchor_btn.width() - self.width() + 4, anchor_btn.height() + 4))
        scr = QApplication.screenAt(g_pos) or QApplication.primaryScreen()
        if scr is not None:
            avail = scr.availableGeometry()
            if g_pos.y() + self.height() > avail.bottom() - 8:
                g_pos.setY(anchor_btn.mapToGlobal(QPoint(0, -self.height() - 4)).y())
            g_pos.setX(max(avail.left() + 8, min(g_pos.x(), avail.right() - self.width() - 8)))
        self.move(g_pos)

    def _pick(self, idx: int):
        self.optionChosen.emit(idx)
        self.accept()


class OptionPillButton(QPushButton):
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.accent_c = QColor(STAR_DEFAULT_YELLOW)
        self.accent2_c = QColor(STAR_DEFAULT_YELLOW_2)
        self.font_family = "Georgia"
        self._hovered = False
        self.setFixedHeight(28)
        self.setMinimumWidth(114)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)

    def set_theme(self, accent: str, accent2: str, font_family: str):
        self.accent_c = QColor(accent)
        self.accent2_c = QColor(accent2)
        self.font_family = font_family
        self.update()

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(1.0, 1.0, self.width() - 2.0, self.height() - 2.0)
        ac = self.accent_c
        ac2 = self.accent2_c

        if not self.isEnabled():
            fill = QColor(255, 255, 255, 10)
            border = QColor(255, 255, 255, 28)
            txt_c = QColor("#677674")
        elif self._hovered:
            fill = QColor(ac.red(), ac.green(), ac.blue(), 52)
            border = QColor(ac.red(), ac.green(), ac.blue(), 220)
            txt_c = ac2
        else:
            fill = QColor(ac.red(), ac.green(), ac.blue(), 32)
            border = QColor(ac.red(), ac.green(), ac.blue(), 128)
            txt_c = ac2

        p.setBrush(fill)
        p.setPen(QPen(border, 1.2))
        p.drawRoundedRect(r, 11.0, 11.0)

        p.setFont(QFont(self.font_family, 9, QFont.Weight.Bold))
        p.setPen(txt_c)
        text_r = QRectF(10.0, 0.0, self.width() - 28.0, float(self.height()))
        elided = QFontMetrics(p.font()).elidedText(self.text(), Qt.TextElideMode.ElideRight, int(text_r.width()))
        p.drawText(text_r, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignCenter, elided)

        ax = self.width() - 15.0
        ay = self.height() * 0.45
        p.setPen(QPen(txt_c, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.drawPolyline(QPolygonF([QPointF(ax - 3.8, ay), QPointF(ax, ay + 3.8), QPointF(ax + 3.8, ay)]))


class TaskRowWidget(QFrame):
    rowClicked = Signal()
    rowDoubleClicked = Signal()
    optionChanged = Signal(str, int)

    def __init__(self, task: dict, selected_opt_idx: int, applicable: bool, accent: str, accent2: str, font_family: str, parent=None):
        super().__init__(parent)
        self.task = task
        self.task_id = task["id"]
        self.options = task.get("options") or []
        self.selected_opt_idx = selected_opt_idx
        self.applicable = applicable
        self.is_selected = False
        self._hovered = False
        self.accent_c = QColor(accent)
        self.accent2_c = QColor(accent2)
        self.font_family = font_family

        self.setFixedHeight(60)
        self.setMouseTracking(True)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 7, 12, 7)
        lay.setSpacing(10)

        txt_col = QVBoxLayout()
        txt_col.setSpacing(2)
        self.title_lbl = QLabel(task["title"])
        self.title_lbl.setMinimumWidth(0)
        self.title_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self.desc_lbl = QLabel(task["desc"])
        self.desc_lbl.setMinimumWidth(0)
        self.desc_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        txt_col.addStretch()
        txt_col.addWidget(self.title_lbl)
        txt_col.addWidget(self.desc_lbl)
        txt_col.addStretch()
        lay.addLayout(txt_col, 1)

        self.opt_btn: OptionPillButton | None = None
        self.badge_lbl: QLabel | None = None

        if self.options:
            cur_label = self.options[max(0, min(len(self.options) - 1, selected_opt_idx))]["label"]
            self.opt_btn = OptionPillButton(cur_label, self)
            self.opt_btn.setEnabled(applicable)
            self.opt_btn.clicked.connect(self._open_options_popup)
            lay.addWidget(self.opt_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        else:
            self.badge_lbl = QLabel(task["badge"])
            self.badge_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.badge_lbl.setFixedSize(98, 25)
            self.badge_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            lay.addWidget(self.badge_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        self.apply_theme(accent, accent2, font_family)

    def set_selected(self, selected: bool):
        if self.is_selected != selected:
            self.is_selected = selected
            self._refresh_visuals()

    def apply_theme(self, accent: str, accent2: str, font_family: str):
        self.accent_c = QColor(accent)
        self.accent2_c = QColor(accent2)
        self.font_family = font_family
        if self.opt_btn is not None:
            self.opt_btn.set_theme(accent, accent2, font_family)
        self._refresh_visuals()

    def _refresh_visuals(self):
        ac = self.accent_c
        ac2 = self.accent2_c
        fam = self.font_family

        if not self.applicable:
            t_col = "#677674"
            d_col = "#505E5C"
        elif self.is_selected or self._hovered:
            t_col = ac.name() if self.is_selected else "#FFFFFF"
            d_col = "#A7B9B6"
        else:
            t_col = "#FFFFFF"
            d_col = "#92A5A2"

        self.title_lbl.setFont(QFont(fam, 11, QFont.Weight.Bold if self.is_selected else QFont.Weight.DemiBold))
        self.title_lbl.setStyleSheet(f"color: {t_col}; background: transparent;")
        self.desc_lbl.setFont(QFont(fam, 9))
        self.desc_lbl.setStyleSheet(f"color: {d_col}; background: transparent;")

        if self.badge_lbl is not None:
            if self.applicable:
                self.badge_lbl.setStyleSheet(
                    f"background-color: rgba({ac2.red()}, {ac2.green()}, {ac2.blue()}, 34); "
                    f"border: 1px solid rgba({ac2.red()}, {ac2.green()}, {ac2.blue()}, 125); "
                    f"border-radius: 10px; color: {ac2.name()}; font-size: 9px; font-weight: 800;"
                )
            else:
                self.badge_lbl.setStyleSheet(
                    "background-color: rgba(255, 255, 255, 10); border: 1px solid rgba(255, 255, 255, 24); "
                    "border-radius: 10px; color: #5F6E6B; font-size: 9px; font-weight: 700;"
                )
        self._elide_texts()
        self.update()

    def _elide_texts(self):
        avail = max(100, self.title_lbl.width())
        self.title_lbl.setText(QFontMetrics(self.title_lbl.font()).elidedText(self.task["title"], Qt.TextElideMode.ElideRight, avail))
        self.desc_lbl.setText(QFontMetrics(self.desc_lbl.font()).elidedText(self.task["desc"], Qt.TextElideMode.ElideRight, avail))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide_texts()

    def enterEvent(self, event):
        self._hovered = True
        self._refresh_visuals()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._refresh_visuals()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.rowClicked.emit()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.rowDoubleClicked.emit()
        super().mouseDoubleClickEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(2.0, 2.0, self.width() - 4.0, self.height() - 4.0)
        ac = self.accent_c

        if self.is_selected:
            p.setBrush(QColor(ac.red(), ac.green(), ac.blue(), 46))
            p.setPen(QPen(QColor(ac.red(), ac.green(), ac.blue(), 165), 1.2))
            p.drawRoundedRect(r, 11.0, 11.0)
        elif self._hovered and self.applicable:
            p.setBrush(QColor(255, 255, 255, 16))
            p.setPen(QPen(QColor(ac.red(), ac.green(), ac.blue(), 85), 1.0))
            p.drawRoundedRect(r, 11.0, 11.0)

    def _open_options_popup(self):
        self.rowClicked.emit()
        if not self.options or self.opt_btn is None:
            return
        popup = OptionPopupMenu(self.options, self.selected_opt_idx, self.accent_c.name(), self.font_family, self.opt_btn)
        popup.optionChosen.connect(self._on_option_chosen)
        popup.exec()

    def _on_option_chosen(self, idx: int):
        self.selected_opt_idx = idx
        if self.opt_btn is not None and 0 <= idx < len(self.options):
            self.opt_btn.setText(self.options[idx]["label"])
        self.optionChanged.emit(self.task_id, idx)


class LibraryPickerDialog(QDialog):
    def __init__(self, tracks: list[dict], accent_hex: str, icon: QIcon, parent=None):
        super().__init__(parent)
        self.all_tracks = tracks
        self.selected_paths: list[str] = []
        self.setWindowTitle("NanoTune Library — Pick One or Multiple Files")
        self.setWindowIcon(icon)
        self.resize(580, 480)
        self.setModal(True)

        c1 = QColor(accent_hex)
        btn_fg = "#07100B" if color_luminance(accent_hex) > 0.45 else "#FFFFFF"
        self.setStyleSheet(f"""
            QDialog {{ background-color: #0D1112; color: #EAF0EE; border: 1px solid #2A3236; }}
            QLineEdit {{
                background-color: #13191B; color: #EAF0EE; border: 1px solid #2A3638;
                border-radius: 15px; padding: 7px 14px; font-size: 13px;
            }}
            QLineEdit:focus {{ border-color: {accent_hex}; }}
            QListWidget {{
                background-color: #121719; color: #EAF0EE; border: 1px solid #243032;
                border-radius: 10px; padding: 6px; outline: none;
            }}
            QListWidget::item {{ padding: 7px 10px; border-radius: 6px; }}
            QListWidget::item:selected {{ background-color: rgba({c1.red()}, {c1.green()}, {c1.blue()}, 75); color: #FFFFFF; }}
            QPushButton#primaryBtn {{
                background-color: {accent_hex}; color: {btn_fg}; border: none;
                border-radius: 14px; padding: 8px 20px; font-size: 13px; font-weight: 800;
            }}
            QPushButton#ghostBtn {{
                background-color: #171E20; color: #9FB0AE; border: 1px solid #2A3638;
                border-radius: 14px; padding: 8px 18px; font-size: 13px;
            }}
        """)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(10)

        hdr = QLabel(f"NanoTune Library ({len(tracks)} tracks) — Hold Ctrl/Shift to multi-select")
        hdr.setStyleSheet("color: #FFFFFF; font-size: 15px; font-weight: 800;")
        lay.addWidget(hdr)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search title, artist, album, or filename...")
        self.search_box.textChanged.connect(self._populate)
        lay.addWidget(self.search_box)

        self.list_w = QListWidget()
        self.list_w.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list_w.itemDoubleClicked.connect(self._choose_selected)
        self.list_w.itemSelectionChanged.connect(self._update_btn_label)
        lay.addWidget(self.list_w, 1)

        btns = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("ghostBtn")
        cancel_btn.clicked.connect(self.reject)
        self.open_btn = QPushButton("Select Files")
        self.open_btn.setObjectName("primaryBtn")
        self.open_btn.clicked.connect(self._choose_selected)
        btns.addWidget(cancel_btn)
        btns.addStretch()
        btns.addWidget(self.open_btn)
        lay.addLayout(btns)

        self._populate()

    def showEvent(self, event):
        super().showEvent(event)
        enable_windows_dark_titlebar(int(self.winId()))

    def _populate(self):
        q = normalize_text(self.search_box.text())
        self.list_w.clear()
        for tr in self.all_tracks:
            label = f"{tr.get('title', Path(tr['path']).stem)}  —  {tr.get('artist', 'Unknown')}  ({tr.get('album', '')})"
            if q and q not in normalize_text(label + tr["path"]):
                continue
            it = QListWidgetItem(label)
            it.setData(Qt.ItemDataRole.UserRole, tr["path"])
            self.list_w.addItem(it)
        self._update_btn_label()

    def _update_btn_label(self):
        cnt = len(self.list_w.selectedItems())
        self.open_btn.setText(f"Select ({cnt})" if cnt > 1 else "Select File")

    def _choose_selected(self):
        items = self.list_w.selectedItems()
        if not items and self.list_w.currentItem() is not None:
            items = [self.list_w.currentItem()]
        if items:
            self.selected_paths = [str(it.data(Qt.ItemDataRole.UserRole)) for it in items]
            self.accept()


# ---------------------------------------------------------------------------
# Main NanoTune Toolbox Window
# ---------------------------------------------------------------------------
class NanoTuneToolbox(QMainWindow):
    taskProgress = Signal(int, str)
    taskFinished = Signal(bool, list, str)
    libraryReady = Signal(list)

    def __init__(self, startup_files: list[str] | None = None):
        super().__init__()
        self.nt_dir = get_nanotune_config_dir()
        self.nt_config_path = self.nt_dir / "config.json"
        self.nt_cache_path = self.nt_dir / "library_cache.json"
        self.nt_config = self.load_nanotune_config()
        self.nanotune_installed = self.nt_config_path.exists()

        self.active_font_family = self.resolve_configured_font()
        self.lyric_font_size = int(self.nt_config.get("lyric_font_size", 19))
        self.accent_color = STAR_DEFAULT_YELLOW
        self.accent_color_2 = STAR_DEFAULT_YELLOW_2
        self.app_icon = make_toolbox_star_icon(STAR_DEFAULT_YELLOW, 64)

        self.default_pil = build_fallback_cover(STAR_DEFAULT_YELLOW)
        self.cover_pil = self.default_pil
        self.cover_pixmap = pil_to_qpixmap(self.default_pil)
        self.blurred_pixmap = QPixmap()
        self._bg_scaled_cache = QPixmap()
        self._bg_cache_size = QSize()

        self.batch_infos: list[dict] = []
        self.preview_idx: int = 0
        self.last_output_paths: list[str] = []
        self.selected_category: str = "all"
        self.task_selected_options: dict[str, int] = {
            t["id"]: int(t.get("default_opt", 0)) for t in TASKS if t.get("options")
        }
        self.task_row_widgets: list[TaskRowWidget] = []

        self.nanotune_tracks: list[dict] = []
        self.is_running_task: bool = False
        self.seeking_active: bool = False
        self.is_playing: bool = False

        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(float(self.nt_config.get("volume", 0.85)))
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.positionChanged.connect(self.on_position_changed)
        self.player.durationChanged.connect(self.on_duration_changed)
        self.player.playbackStateChanged.connect(self.on_playback_state_changed)
        self.player.mediaStatusChanged.connect(self.on_media_status_changed)

        self.taskProgress.connect(self.on_task_progress)
        self.taskFinished.connect(self.on_task_finished)
        self.libraryReady.connect(self.on_library_ready)

        self._space_filter = GlobalSpacePlayPauseFilter(self)
        QApplication.instance().installEventFilter(self._space_filter)

        screen = QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QRect(0, 0, 1280, 720)
        target_w = max(840, min(1160, int(avail.width() * 0.72)))
        target_h = max(595, min(775, int(avail.height() * 0.795)))

        self.setWindowTitle("NanoTune Toolbox")
        self.setWindowIcon(self.app_icon)
        self.setMinimumSize(760, 520)
        self.resize(target_w, target_h)
        self.move(avail.x() + (avail.width() - target_w) // 2, avail.y() + max(8, (avail.height() - target_h) // 2))
        self.setAcceptDrops(True)

        QShortcut(QKeySequence("Ctrl+T"), self, activated=self.stamp_current_time_in_editor)

        self.build_ui()
        self.rebuild_blurred_background()
        self.apply_theme_colors(STAR_DEFAULT_YELLOW, STAR_DEFAULT_YELLOW_2)
        self.populate_tasks()
        self.update_responsive_layouts()

        QTimer.singleShot(80, self.auto_import_nanotune_directories)
        if HAS_AV:
            QTimer.singleShot(250, lambda: threading.Thread(target=lambda: __import__("av"), daemon=True).start())

        valid_startup = [Path(f) for f in (startup_files or []) if Path(f).is_file()]
        if valid_startup:
            QTimer.singleShot(120, lambda: self.load_files(valid_startup))

    def closeEvent(self, event):
        if hasattr(self, "_space_filter") and QApplication.instance() is not None:
            QApplication.instance().removeEventFilter(self._space_filter)
        super().closeEvent(event)

    def load_nanotune_config(self) -> dict:
        defaults = {
            "music_folders": [],
            "imported_files": [],
            "imported_lyrics": {},
            "adaptive_colors": True,
            "recursive_scan": True,
            "blur_radius": 34,
            "always_blur_bg": True,
            "lyric_font_size": 19,
            "font_index": 0,
            "volume": 0.85,
            "graphics_backend": SELECTED_BACKEND,
        }
        if self.nt_config_path.exists():
            try:
                data = json.loads(self.nt_config_path.read_text(encoding="utf-8"))
                defaults.update(data)
            except Exception:
                pass
        defaults["font_index"] = max(0, min(len(FONT_PRESETS) - 1, int(defaults.get("font_index", 0))))
        return defaults

    def save_nanotune_config(self):
        self.nt_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.nt_config_path.write_text(json.dumps(self.nt_config, indent=2), encoding="utf-8")
        except Exception:
            pass

    def resolve_configured_font(self) -> str:
        idx = max(0, min(len(FONT_PRESETS) - 1, int(self.nt_config.get("font_index", 0))))
        return resolve_font_family(FONT_PRESETS[idx][1])

    # -----------------------------------------------------------------------
    # Auto-Import Directories From Main NanoTune (Non-blocking)
    # -----------------------------------------------------------------------
    def auto_import_nanotune_directories(self):
        folders = [f for f in self.nt_config.get("music_folders", []) if f and Path(f).exists()]
        imported = [f for f in self.nt_config.get("imported_files", []) if f and Path(f).is_file()]
        recursive = bool(self.nt_config.get("recursive_scan", True))

        def _worker():
            tracks: list[dict] = []
            seen: set[str] = set()
            cache = {}
            if self.nt_cache_path.exists():
                try:
                    cache = json.loads(self.nt_cache_path.read_text(encoding="utf-8"))
                except Exception:
                    cache = {}

            def _add_path(fp: Path):
                if not (fp.is_file() and fp.suffix.lower() in AUDIO_EXTENSIONS):
                    return
                norm = str(fp.resolve()).lower()
                if norm in seen:
                    return
                seen.add(norm)
                c = cache.get(str(fp))
                if c:
                    tracks.append({"path": str(fp), "title": c.get("title", fp.stem), "artist": c.get("artist", "Unknown"), "album": c.get("album", "")})
                else:
                    tracks.append({"path": str(fp), "title": fp.stem, "artist": fp.parent.name, "album": ""})

            for fd in folders:
                p = Path(fd)
                it = p.rglob("*") if recursive else p.iterdir()
                for f in it:
                    _add_path(f)
            for imp in imported:
                _add_path(Path(imp))

            tracks.sort(key=lambda t: t["title"].lower())
            self.libraryReady.emit(tracks)

        threading.Thread(target=_worker, daemon=True).start()

    def on_library_ready(self, tracks: list[dict]):
        self.nanotune_tracks = tracks
        if hasattr(self, "library_btn"):
            if tracks:
                self.library_btn.setText(f" NanoTune Library ({len(tracks)})")
                self.library_btn.setEnabled(True)
            else:
                self.library_btn.setText(" NanoTune Library (Empty)")
                self.library_btn.setEnabled(False)

    # -----------------------------------------------------------------------
    # UI Construction (Page 0: Toolbox | Page 1: Full-View Lyrics Editor)
    # -----------------------------------------------------------------------
    def build_ui(self):
        root = QWidget()
        root.setObjectName("rootWidget")
        self.setCentralWidget(root)

        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Top Header Strip
        header = QFrame()
        header.setObjectName("topHeader")
        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(20, 12, 20, 12)
        h_lay.setSpacing(12)

        self.brand_badge = QLabel()
        self.brand_badge.setObjectName("brandBadge")
        self.brand_badge.setFixedSize(42, 42)
        self.brand_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        h_lay.addWidget(self.brand_badge)

        t_col = QVBoxLayout()
        t_col.setSpacing(1)
        self.lbl_app = QLabel("NANOTUNE TOOLBOX")
        self.lbl_app.setObjectName("brandTitle")
        self.lbl_sub = QLabel("AUDIO & LYRICS CONVERTER")
        self.lbl_sub.setObjectName("accentTag")
        t_col.addWidget(self.lbl_app)
        t_col.addWidget(self.lbl_sub)
        h_lay.addLayout(t_col)
        h_lay.addStretch()

        self.library_btn = QPushButton(" NanoTune Library (Loading...)")
        self.library_btn.setObjectName("secondaryBtn")
        self.library_btn.setEnabled(False)
        self.library_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.library_btn.clicked.connect(self.open_library_picker)

        self.browse_btn = QPushButton(" Open File(s)...")
        self.browse_btn.setObjectName("primaryBtn")
        self.browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.browse_btn.clicked.connect(self.browse_input_files)

        h_lay.addWidget(self.library_btn)
        h_lay.addWidget(self.browse_btn)
        outer.addWidget(header)

        # Central Stacked Widget (Toolbox View <-> Full-View Lyrics Editor)
        self.main_stack = QStackedWidget()
        self.main_stack.setObjectName("transparentStack")
        self.main_stack.addWidget(self.build_toolbox_page())
        self.main_stack.addWidget(self.build_lyrics_editor_page())
        outer.addWidget(self.main_stack, 1)

        # Bottom Playback Preview Bar + Illuminated "Import to NanoTune" Button
        bar = QFrame()
        bar.setObjectName("playerBar")
        bar.setFixedHeight(74)
        b_lay = QHBoxLayout(bar)
        b_lay.setContentsMargins(20, 10, 20, 10)
        b_lay.setSpacing(14)

        self.time_cur_lbl = QLabel("0:00")
        self.time_cur_lbl.setFixedWidth(38)
        self.time_cur_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.time_cur_lbl.setStyleSheet("color: #9EB0AD; font-size: 11px;")

        self.seek_slider = SeekSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setRange(0, 0)
        self.seek_slider.setEnabled(False)
        self.seek_slider.sliderPressed.connect(lambda: setattr(self, "seeking_active", True))
        self.seek_slider.sliderMoved.connect(lambda val: self.time_cur_lbl.setText(format_ms(val)))
        self.seek_slider.sliderReleased.connect(self.on_seek_released)

        self.time_tot_lbl = QLabel("0:00")
        self.time_tot_lbl.setFixedWidth(38)
        self.time_tot_lbl.setStyleSheet("color: #9EB0AD; font-size: 11px;")

        self.play_btn = QPushButton()
        self.play_btn.setObjectName("playCircleBtn")
        self.play_btn.setFixedSize(38, 38)
        self.play_btn.setIconSize(QSize(17, 17))
        self.play_btn.setEnabled(False)
        self.play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_btn.setToolTip("Preview Playback (Space)")
        self.play_btn.clicked.connect(self.toggle_play_pause)

        self.import_btn = QPushButton(" Import to NanoTune")
        self.import_btn.setObjectName("importBtn")
        self.import_btn.setEnabled(False)
        self.import_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.import_btn.clicked.connect(self.import_result_to_nanotune)

        b_lay.addWidget(self.time_cur_lbl)
        b_lay.addWidget(self.seek_slider, 1)
        b_lay.addWidget(self.time_tot_lbl)
        b_lay.addWidget(self.play_btn)
        b_lay.addSpacing(8)
        b_lay.addWidget(self.import_btn)
        outer.addWidget(bar)

        for btn in self.findChildren(QPushButton):
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setAutoDefault(False)

    def build_toolbox_page(self) -> QWidget:
        page = QWidget()
        body = QHBoxLayout(page)
        body.setContentsMargins(18, 12, 18, 12)
        body.setSpacing(14)

        # Left Panel: File Details
        self.left_card = QFrame()
        self.left_card.setObjectName("glassCard")
        self.left_card.setFixedWidth(315)
        lc_wrap = QVBoxLayout(self.left_card)
        lc_wrap.setContentsMargins(6, 10, 6, 10)

        left_scroll = QScrollArea()
        left_scroll.setObjectName("transparentScroll")
        left_scroll.setAcceptDrops(False)
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        left_inner = QWidget()
        left_inner.setObjectName("leftInner")
        left_inner.setStyleSheet("QWidget#leftInner { background: transparent; }")
        lc_outer = QVBoxLayout(left_inner)
        lc_outer.setContentsMargins(10, 6, 10, 6)
        lc_outer.setSpacing(10)

        self.cover_widget = StackedCoversWidget(radius=14, border_width=2.0)
        self.cover_widget.setFixedSize(210, 210)
        self.cover_widget.set_pixmaps([self.cover_pixmap])
        self.cover_widget.clicked.connect(self.cycle_batch_preview)
        lc_outer.addWidget(self.cover_widget, alignment=Qt.AlignmentFlag.AlignCenter)

        self.batch_pill_lbl = QLabel("")
        self.batch_pill_lbl.setObjectName("accentTag")
        self.batch_pill_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.batch_pill_lbl.setVisible(False)
        lc_outer.addWidget(self.batch_pill_lbl)

        self.title_lbl = QLabel("Drop Audio or .LRC / .ELRC")
        self.title_lbl.setWordWrap(True)
        self.title_lbl.setStyleSheet("color: #FFFFFF; font-size: 18px; font-weight: 800;")

        self.artist_lbl = QLabel("Drag & drop single or multiple files, or browse above")
        self.artist_lbl.setWordWrap(True)
        self.artist_lbl.setStyleSheet("color: #B4C2C0; font-size: 12px; padding-bottom: 4px;")

        lc_outer.addWidget(self.title_lbl)
        lc_outer.addWidget(self.artist_lbl)

        details_box = QFrame()
        details_box.setObjectName("detailsCard")
        grid = QGridLayout(details_box)
        grid.setContentsMargins(12, 10, 12, 10)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)

        self.detail_labels: dict[str, QLabel] = {}
        fields = [
            ("Format", "format"),
            ("Audio", "audio"),
            ("Bitrate", "bitrate"),
            ("Size", "size"),
            ("Album", "album"),
            ("Lyrics", "lyrics"),
            ("Output", "output"),
        ]
        for row_i, (lbl_txt, key) in enumerate(fields):
            k_lbl = QLabel(lbl_txt)
            k_lbl.setStyleSheet("color: #849794; font-size: 11px; font-weight: 700;")
            v_lbl = QLabel("—")
            v_lbl.setStyleSheet("color: #E5EFED; font-size: 11px; font-weight: 600;")
            grid.addWidget(k_lbl, row_i, 0, Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(v_lbl, row_i, 1, Qt.AlignmentFlag.AlignVCenter)
            self.detail_labels[key] = v_lbl
        grid.setColumnStretch(1, 1)
        lc_outer.addWidget(details_box)

        self.quick_edit_lyrics_btn = QPushButton(" Edit Lyrics")
        self.quick_edit_lyrics_btn.setObjectName("secondaryBtn")
        self.quick_edit_lyrics_btn.setEnabled(False)
        self.quick_edit_lyrics_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.quick_edit_lyrics_btn.clicked.connect(self.open_full_lyrics_editor)
        lc_outer.addWidget(self.quick_edit_lyrics_btn)
        lc_outer.addStretch()

        left_scroll.setWidget(left_inner)
        lc_wrap.addWidget(left_scroll)
        body.addWidget(self.left_card)

        # Right Panel: Tasks List & Execution
        right_card = QFrame()
        right_card.setObjectName("glassCard")
        rc_lay = QVBoxLayout(right_card)
        rc_lay.setContentsMargins(16, 14, 16, 14)
        rc_lay.setSpacing(10)

        self.top_tools_grid = QGridLayout()
        self.top_tools_grid.setContentsMargins(0, 0, 0, 0)
        self.top_tools_grid.setHorizontalSpacing(8)
        self.top_tools_grid.setVerticalSpacing(8)

        self.cats_wrap = QWidget()
        cw_lay = QHBoxLayout(self.cats_wrap)
        cw_lay.setContentsMargins(0, 0, 0, 0)
        cw_lay.setSpacing(6)

        self.cat_buttons: dict[str, QPushButton] = {}
        for cid, label in (("all", "All Tasks"), ("audio", "Audio Codecs"), ("lyrics", "Lyrics"), ("meta", "Cover && Tags")):
            b = QPushButton(label)
            b.setObjectName("pillBtn")
            b.setCheckable(True)
            b.setChecked(cid == "all")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _c=False, c=cid: self.set_category(c))
            cw_lay.addWidget(b)
            self.cat_buttons[cid] = b

        self.task_search = QLineEdit()
        self.task_search.setPlaceholderText("Filter tasks...")
        self.task_search.setMinimumWidth(140)
        self.task_search.setMaximumWidth(220)
        self.attach_clear_action(self.task_search)
        self.task_search.textChanged.connect(self.populate_tasks)

        self.top_tools_grid.addWidget(self.cats_wrap, 0, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.top_tools_grid.addWidget(self.task_search, 0, 1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.top_tools_grid.setColumnStretch(0, 1)
        rc_lay.addLayout(self.top_tools_grid)

        self.task_list = QListWidget()
        self.task_list.setObjectName("taskList")
        self.task_list.setAcceptDrops(False)
        self.task_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.task_list.currentItemChanged.connect(self.on_selected_task_changed)
        rc_lay.addWidget(self.task_list, 1)

        # Clean Accent-Themed Offset Pill Strip (No ugly native square arrows!)
        self.shift_row_widget = QFrame()
        self.shift_row_widget.setObjectName("shiftStrip")
        sr_lay = QHBoxLayout(self.shift_row_widget)
        sr_lay.setContentsMargins(12, 7, 12, 7)
        sr_lay.setSpacing(8)

        sr_lbl = QLabel("Lyrics Time-Shift Offset:")
        sr_lbl.setStyleSheet("color: #DCE6E4; font-size: 12px; font-weight: 700; background: transparent;")

        self.shift_minus_btn = QPushButton("− 100ms")
        self.shift_minus_btn.setObjectName("pillBtn")
        self.shift_minus_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.shift_minus_btn.clicked.connect(lambda: self.shift_spin.setValue(self.shift_spin.value() - 100))

        self.shift_spin = QSpinBox()
        self.shift_spin.setObjectName("cleanShiftSpin")
        self.shift_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.shift_spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.shift_spin.setRange(-30000, 30000)
        self.shift_spin.setSingleStep(50)
        self.shift_spin.setValue(250)
        self.shift_spin.setSuffix(" ms")
        self.shift_spin.setFixedWidth(96)

        self.shift_plus_btn = QPushButton("+ 100ms")
        self.shift_plus_btn.setObjectName("pillBtn")
        self.shift_plus_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.shift_plus_btn.clicked.connect(lambda: self.shift_spin.setValue(self.shift_spin.value() + 100))

        sr_lay.addWidget(sr_lbl)
        sr_lay.addSpacing(4)
        sr_lay.addWidget(self.shift_minus_btn)
        sr_lay.addWidget(self.shift_spin)
        sr_lay.addWidget(self.shift_plus_btn)
        sr_lay.addStretch()
        self.shift_row_widget.setVisible(False)
        rc_lay.addWidget(self.shift_row_widget)

        exec_row = QHBoxLayout()
        exec_row.setSpacing(12)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(8)

        self.status_lbl = QLabel("Select file(s) and choose a task to begin.")
        self.status_lbl.setStyleSheet("color: #B4C2C0; font-size: 12px;")
        self.status_lbl.setMinimumWidth(0)

        status_col = QVBoxLayout()
        status_col.setSpacing(5)
        status_col.addWidget(self.status_lbl)
        status_col.addWidget(self.progress_bar)

        self.run_btn = QPushButton(" Run Task")
        self.run_btn.setObjectName("primaryBtn")
        self.run_btn.setEnabled(False)
        self.run_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.run_btn.clicked.connect(self.run_selected_task)

        exec_row.addLayout(status_col, 1)
        exec_row.addWidget(self.run_btn)
        rc_lay.addLayout(exec_row)

        body.addWidget(right_card, 1)
        return page

    def build_lyrics_editor_page(self) -> QWidget:
        """Full-view blurred-backdrop lyrics & timestamp editor mimicking NanoTune's lyrics view."""
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(18, 12, 18, 12)
        lay.setSpacing(10)

        # Toolbar Card
        toolbar = QFrame()
        toolbar.setObjectName("glassCard")
        tb_lay = QHBoxLayout(toolbar)
        tb_lay.setContentsMargins(14, 10, 14, 10)
        tb_lay.setSpacing(10)

        self.ed_back_btn = QPushButton(" Back to Toolbox")
        self.ed_back_btn.setObjectName("secondaryBtn")
        self.ed_back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ed_back_btn.clicked.connect(self.close_full_lyrics_editor)

        self.ed_track_lbl = QLabel("LYRICS EDITOR")
        self.ed_track_lbl.setObjectName("accentTag")
        self.ed_track_lbl.setMinimumWidth(0)

        self.ed_stamp_btn = QPushButton(" Stamp Time (Ctrl+T)")
        self.ed_stamp_btn.setObjectName("secondaryBtn")
        self.ed_stamp_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ed_stamp_btn.setToolTip("Inserts current playback timestamp [MM:SS.xx] at line start and moves down")
        self.ed_stamp_btn.clicked.connect(self.stamp_current_time_in_editor)

        self.ed_save_lrc_btn = QPushButton(" Save .LRC / .ELRC")
        self.ed_save_lrc_btn.setObjectName("primaryBtn")
        self.ed_save_lrc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ed_save_lrc_btn.clicked.connect(lambda: self.save_editor_lyrics(to_file=True, to_tag=False))

        self.ed_embed_btn = QPushButton(" Embed in Tag")
        self.ed_embed_btn.setObjectName("primaryBtn")
        self.ed_embed_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ed_embed_btn.clicked.connect(lambda: self.save_editor_lyrics(to_file=False, to_tag=True))

        tb_lay.addWidget(self.ed_back_btn)
        tb_lay.addWidget(self.ed_track_lbl, 1)
        tb_lay.addWidget(self.ed_stamp_btn)
        tb_lay.addWidget(self.ed_save_lrc_btn)
        tb_lay.addWidget(self.ed_embed_btn)
        lay.addWidget(toolbar)

        # Fullscreen Lyrics Canvas (Transparent over Blurred Cover Backdrop)
        canvas = QFrame()
        canvas.setObjectName("lyricsCanvasCard")
        c_lay = QVBoxLayout(canvas)
        c_lay.setContentsMargins(24, 16, 24, 16)
        c_lay.setSpacing(6)

        self.ed_hint_lbl = QLabel(
            "Scroll, type, edit, or delete timestamps freely. Press Play below to preview timing & stamp with Ctrl+T."
        )
        self.ed_hint_lbl.setStyleSheet("color: #9FB2AF; font-size: 12px; background: transparent;")
        c_lay.addWidget(self.ed_hint_lbl)

        self.full_lyrics_edit = QTextEdit()
        self.full_lyrics_edit.setObjectName("fullLyricsEdit")
        self.full_lyrics_edit.setAcceptRichText(False)
        self.full_lyrics_edit.setPlaceholderText(
            "[00:12.40] Paste or type lyrics here...\n"
            "[00:16.85] Use 'Stamp Time (Ctrl+T)' while playing below to sync lines!"
        )
        self.ts_highlighter = TimestampHighlighter(self.full_lyrics_edit.document(), self.accent_color)
        c_lay.addWidget(self.full_lyrics_edit, 1)

        lay.addWidget(canvas, 1)
        return page

    def attach_clear_action(self, line_edit: QLineEdit):
        clear_act = line_edit.addAction(make_vector_icon("close", "#9EB0AD", 14), QLineEdit.ActionPosition.TrailingPosition)
        clear_act.setVisible(False)
        clear_act.triggered.connect(line_edit.clear)
        line_edit.textChanged.connect(lambda txt, act=clear_act: act.setVisible(bool(txt)))
        for tb in line_edit.findChildren(QToolButton):
            tb.setCursor(Qt.CursorShape.PointingHandCursor)

    def update_responsive_layouts(self):
        lw = max(255, min(320, int(self.width() * 0.33)))
        self.left_card.setFixedWidth(lw)
        cov_sz = max(130, min(190, int((self.height() - 280) * 0.42)))
        self.cover_widget.setFixedSize(cov_sz, cov_sz)

        self.top_tools_grid.removeWidget(self.cats_wrap)
        self.top_tools_grid.removeWidget(self.task_search)
        if self.width() < 960:
            self.task_search.setMaximumWidth(16777215)
            self.top_tools_grid.addWidget(self.cats_wrap, 0, 0, 1, 2, Qt.AlignmentFlag.AlignLeft)
            self.top_tools_grid.addWidget(self.task_search, 1, 0, 1, 2)
        else:
            self.task_search.setMaximumWidth(210)
            self.top_tools_grid.addWidget(self.cats_wrap, 0, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self.top_tools_grid.addWidget(self.task_search, 0, 1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    # -----------------------------------------------------------------------
    # Blurred Album Cover Backdrop & Dynamic Adaptive Theme
    # -----------------------------------------------------------------------
    def rebuild_blurred_background(self):
        radius = int(self.nt_config.get("blur_radius", 34))
        small = self.cover_pil.resize((420, 420), Image.Resampling.LANCZOS)
        blurred = small.filter(ImageFilter.GaussianBlur(radius=max(6, radius // 2)))
        darkened = ImageEnhance.Brightness(blurred).enhance(0.56)
        saturated = ImageEnhance.Color(darkened).enhance(1.25)
        self.blurred_pixmap = pil_to_qpixmap(saturated)
        self._bg_scaled_cache = QPixmap()
        self.update()

    def ensure_bg_cache(self) -> QPixmap:
        sz = self.size()
        if sz.width() <= 0 or sz.height() <= 0:
            return QPixmap()
        if not self._bg_scaled_cache.isNull() and self._bg_cache_size == sz:
            return self._bg_scaled_cache

        pm = QPixmap(sz)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if bool(self.nt_config.get("always_blur_bg", True)) and not self.blurred_pixmap.isNull():
            scaled = self.blurred_pixmap.scaled(
                sz, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation
            )
            p.drawPixmap((sz.width() - scaled.width()) // 2, (sz.height() - scaled.height()) // 2, scaled)
            ac = QColor(self.accent_color)
            grad = QLinearGradient(0, 0, sz.width(), sz.height())
            grad.setColorAt(0.0, QColor(8, 11, 12, 102))
            grad.setColorAt(0.5, QColor(ac.red() // 11, ac.green() // 11, ac.blue() // 11, 82))
            grad.setColorAt(1.0, QColor(6, 9, 10, 145))
            p.fillRect(QRect(0, 0, sz.width(), sz.height()), grad)
        else:
            grad = QLinearGradient(0, 0, sz.width(), sz.height())
            grad.setColorAt(0.0, QColor("#0B0D0E"))
            grad.setColorAt(1.0, QColor("#090E10"))
            p.fillRect(QRect(0, 0, sz.width(), sz.height()), grad)
        p.end()
        self._bg_scaled_cache = pm
        self._bg_cache_size = sz
        return pm

    def paintEvent(self, event):
        painter = QPainter(self)
        bg = self.ensure_bg_cache()
        if not bg.isNull():
            painter.drawPixmap(0, 0, bg)
        super().paintEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        enable_windows_dark_titlebar(int(self.winId()))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._bg_scaled_cache = QPixmap()
        self.update_responsive_layouts()

    def refresh_all_icons(self):
        ac = self.accent_color
        dark_on_btn = "#06120A" if color_luminance(ac) > 0.45 else "#FFFFFF"

        self.app_icon = make_toolbox_star_icon(ac, 64)
        self.setWindowIcon(self.app_icon)
        self.brand_badge.setPixmap(self.app_icon.pixmap(32, 32))

        self.library_btn.setIcon(make_vector_icon("music", "#EAF2F0", 15))
        self.browse_btn.setIcon(make_vector_icon("folder", dark_on_btn, 15))
        self.quick_edit_lyrics_btn.setIcon(make_vector_icon("edit", "#EAF2F0", 14))
        self.run_btn.setIcon(make_vector_icon("bolt", dark_on_btn, 15))
        self.play_btn.setIcon(make_vector_icon("pause" if self.is_playing else "play", "#07120B", 17))
        self.import_btn.setIcon(
            make_vector_icon("import", dark_on_btn if self.import_btn.isEnabled() else "#6F7B79", 16)
        )

        self.ed_back_btn.setIcon(make_vector_icon("back", "#EAF2F0", 15))
        self.ed_stamp_btn.setIcon(make_vector_icon("clock", "#EAF2F0", 15))
        self.ed_save_lrc_btn.setIcon(make_vector_icon("save", dark_on_btn, 15))
        self.ed_embed_btn.setIcon(make_vector_icon("save", dark_on_btn, 15))

    def apply_theme_colors(self, accent: str, accent2: str):
        self.accent_color = accent
        self.accent_color_2 = accent2
        c1 = QColor(accent)
        rgb1 = f"{c1.red()}, {c1.green()}, {c1.blue()}"
        btn_fg = "#07100B" if color_luminance(accent) > 0.45 else "#FFFFFF"
        fam = self.active_font_family
        lyr_sz = max(15, min(26, self.lyric_font_size))

        css = f"""
            * {{ font-family: "{fam}", "Segoe UI", sans-serif; }}
            QWidget#rootWidget {{ background: transparent; }}
            QStackedWidget#transparentStack {{ background: transparent; }}
            QScrollArea#transparentScroll {{ background: transparent; border: none; }}
            QFrame#topHeader {{
                background-color: rgba(8, 11, 12, 200);
                border-bottom: 1px solid rgba(255, 255, 255, 20);
            }}
            QLabel#brandBadge {{
                background-color: rgba(16, 20, 22, 225);
                border-radius: 11px;
                border: 1px solid rgba({rgb1}, 125);
            }}
            QLabel#brandTitle {{
                color: #FFFFFF; font-size: 15px; font-weight: 900; letter-spacing: 0.9px;
            }}
            QLabel#accentTag {{
                color: {accent2}; font-size: 10px; font-weight: 800; letter-spacing: 1.4px;
            }}
            QFrame#glassCard {{
                background-color: rgba(12, 16, 18, 152);
                border: 1px solid rgba(255, 255, 255, 24);
                border-radius: 14px;
            }}
            QFrame#lyricsCanvasCard {{
                background-color: rgba(12, 16, 18, 132);
                border: 1px solid rgba({rgb1}, 75);
                border-radius: 16px;
            }}
            QFrame#detailsCard {{
                background-color: rgba(10, 14, 15, 170);
                border: 1px solid rgba(255, 255, 255, 20);
                border-radius: 10px;
            }}
            QFrame#shiftStrip {{
                background-color: rgba({rgb1}, 24);
                border: 1px solid rgba({rgb1}, 95);
                border-radius: 12px;
            }}
            QFrame#playerBar {{
                background-color: rgba(7, 10, 11, 232);
                border-top: 1px solid rgba(255, 255, 255, 22);
            }}
            QPushButton#primaryBtn {{
                background-color: {accent};
                color: {btn_fg};
                border: 1px solid {accent};
                border-radius: 15px;
                padding: 8px 18px;
                font-size: 13px;
                font-weight: 800;
            }}
            QPushButton#primaryBtn:hover {{
                background-color: {accent2};
                border-color: {accent2};
            }}
            QPushButton#primaryBtn:disabled {{
                background-color: rgba(255, 255, 255, 14);
                color: #677371;
                border-color: rgba(255, 255, 255, 18);
            }}
            QPushButton#importBtn {{
                background-color: {accent};
                color: {btn_fg};
                border: 1px solid {accent2};
                border-radius: 16px;
                padding: 9px 20px;
                font-size: 13px;
                font-weight: 800;
            }}
            QPushButton#importBtn:hover {{
                background-color: {accent2};
                border-color: #FFFFFF;
            }}
            QPushButton#importBtn:disabled {{
                background-color: rgba(30, 36, 38, 195);
                color: #6B7775;
                border: 1px solid rgba(255, 255, 255, 22);
            }}
            QPushButton#secondaryBtn, QPushButton#pillBtn {{
                background-color: rgba(255, 255, 255, 15);
                color: #EAF2F0;
                border: 1px solid rgba(255, 255, 255, 26);
                border-radius: 14px;
                padding: 6px 13px;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton#secondaryBtn:hover, QPushButton#pillBtn:hover {{
                background-color: rgba({rgb1}, 34);
                border-color: rgba({rgb1}, 165);
                color: {accent2};
            }}
            QPushButton#secondaryBtn:disabled {{
                color: #5D6A68;
                border-color: rgba(255, 255, 255, 14);
            }}
            QPushButton#pillBtn:checked {{
                background-color: rgba({rgb1}, 48);
                border-color: {accent};
                color: #FFFFFF;
                font-weight: 700;
            }}
            QPushButton#playCircleBtn {{
                background-color: #FFFFFF; border: 1px solid #FFFFFF; border-radius: 19px;
            }}
            QPushButton#playCircleBtn:hover {{
                background-color: {accent}; border-color: {accent};
            }}
            QPushButton#playCircleBtn:disabled {{
                background-color: #353E40; border-color: #353E40;
            }}
            QLineEdit {{
                background-color: rgba(12, 17, 19, 190);
                color: #EAF0EE;
                border: 1px solid rgba(255, 255, 255, 28);
                border-radius: 14px;
                padding: 6px 12px;
                font-size: 12px;
            }}
            QLineEdit:focus {{ border-color: {accent}; }}
            QSpinBox#cleanShiftSpin {{
                background-color: rgba(12, 17, 19, 220);
                color: {accent2};
                border: 1px solid rgba({rgb1}, 145);
                border-radius: 14px;
                padding: 5px 10px;
                font-size: 12px;
                font-weight: 800;
            }}
            QSpinBox#cleanShiftSpin:focus {{
                border-color: {accent};
                color: #FFFFFF;
            }}
            QTextEdit#fullLyricsEdit {{
                background: transparent;
                color: #F2F6F5;
                border: none;
                font-family: "{fam}", "Segoe UI", sans-serif;
                font-size: {lyr_sz}px;
                font-weight: 700;
                selection-background-color: rgba({rgb1}, 95);
                selection-color: #FFFFFF;
            }}
            QListWidget#taskList {{
                background-color: rgba(10, 14, 16, 130);
                border: 1px solid rgba(255, 255, 255, 20);
                border-radius: 12px;
                padding: 6px;
                outline: none;
            }}
            QListWidget#taskList::item {{
                background: transparent;
                border: none;
            }}
            QProgressBar {{
                background-color: rgba(255, 255, 255, 24);
                border: none;
                border-radius: 4px;
            }}
            QProgressBar::chunk {{
                background-color: {accent};
                border-radius: 4px;
            }}
            QSlider::groove:horizontal {{
                height: 5px; background: rgba(255, 255, 255, 38); border-radius: 2px;
            }}
            QSlider::sub-page:horizontal {{
                background: {accent}; border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: #FFFFFF; border: none;
                width: 12px; height: 12px; margin: -4px 0; border-radius: 6px;
            }}
            QSlider::groove:horizontal:disabled {{
                height: 5px; background: #2D3436; border-radius: 2px;
            }}
            QSlider::handle:horizontal:disabled {{
                background: #6B7775; width: 10px; height: 10px; margin: -3px 0; border-radius: 5px;
            }}
            QScrollBar:vertical {{ background: transparent; width: 8px; margin: 6px 2px; }}
            QScrollBar::handle:vertical {{
                background: rgba(255, 255, 255, 45); border-radius: 4px; min-height: 28px;
            }}
            QScrollBar::handle:vertical:hover {{ background: rgba({rgb1}, 160); }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
        """
        self.setStyleSheet(css)
        self.cover_widget.set_border_color(accent)
        self.ts_highlighter.set_accent(accent)
        for rw in self.task_row_widgets:
            rw.apply_theme(accent, accent2, fam)
        self._bg_scaled_cache = QPixmap()
        self.refresh_all_icons()
        self.update()

    # -----------------------------------------------------------------------
    # Drag & Drop + Single / Multi-File Selection & Solitaire Cover Stack
    # -----------------------------------------------------------------------
    def _supported_urls(self, mime) -> list[Path]:
        out: list[Path] = []
        if not mime.hasUrls():
            return out
        for u in mime.urls():
            if u.isLocalFile():
                p = Path(u.toLocalFile())
                if p.is_file() and p.suffix.lower() in (AUDIO_EXTENSIONS | LYRIC_EXTENSIONS):
                    out.append(p)
        return out

    def dragEnterEvent(self, event):
        if self._supported_urls(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self._supported_urls(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        paths = self._supported_urls(event.mimeData())
        if paths:
            self.load_files(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def browse_input_files(self):
        exts = " ".join(f"*{e}" for e in sorted(AUDIO_EXTENSIONS | LYRIC_EXTENSIONS))
        start_dir = ""
        folders = self.nt_config.get("music_folders", [])
        if folders and Path(folders[0]).exists():
            start_dir = folders[0]
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Audio or Lyrics File(s)",
            start_dir,
            f"Audio & Lyrics ({exts});;All Files (*.*)",
        )
        if paths:
            self.load_files([Path(p) for p in paths])

    def open_library_picker(self):
        if not self.nanotune_tracks:
            return
        dlg = LibraryPickerDialog(self.nanotune_tracks, self.accent_color, self.app_icon, self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected_paths:
            self.load_files([Path(p) for p in dlg.selected_paths])

    def load_files(self, file_paths: list[Path]):
        valid = [p for p in file_paths if p.is_file()]
        if not valid:
            return

        self.player.stop()
        self.player.setSource(QUrl())
        self.is_playing = False
        self.last_output_paths = []
        self.import_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.main_stack.setCurrentIndex(0)

        imported_lyrics = self.nt_config.get("imported_lyrics")
        self.batch_infos = [inspect_file(p, imported_lyrics) for p in valid]
        self.preview_idx = 0

        self._refresh_active_preview()

        if len(valid) == 1:
            self.status_lbl.setText(f"Loaded: {valid[0].name}  (outputs to same directory)")
        else:
            self.status_lbl.setText(f"Batch ready: {len(valid)} files selected  (each outputs to its source directory)")

        self.populate_tasks()
        self.refresh_all_icons()

    def cycle_batch_preview(self):
        if len(self.batch_infos) <= 1:
            return
        self.player.stop()
        self.player.setSource(QUrl())
        self.is_playing = False
        self.preview_idx = (self.preview_idx + 1) % len(self.batch_infos)
        self._refresh_active_preview()
        self.refresh_all_icons()

    def _refresh_active_preview(self):
        if not self.batch_infos:
            return
        info = self.batch_infos[self.preview_idx]
        file_path = Path(info["path"])
        n_files = len(self.batch_infos)

        front_pil = extract_cover_pil(file_path) if info["kind"] == "audio" else None
        self.cover_pil = front_pil if front_pil is not None else self.default_pil
        self.cover_pixmap = pil_to_qpixmap(self.cover_pil)

        stack_pms = [self.cover_pixmap]
        if n_files > 1:
            for offset in range(1, min(3, n_files)):
                other_idx = (self.preview_idx + offset) % n_files
                other_info = self.batch_infos[other_idx]
                op = extract_cover_pil(Path(other_info["path"])) if other_info["kind"] == "audio" else None
                stack_pms.append(pil_to_qpixmap(op if op is not None else self.default_pil))

        self.cover_widget.set_pixmaps(stack_pms)
        if n_files > 1:
            self.cover_widget.setToolTip("Click cover stack to cycle preview track")
            self.batch_pill_lbl.setText(f"BATCH MODE  •  {n_files} FILES  ({self.preview_idx + 1}/{n_files})")
            self.batch_pill_lbl.setVisible(True)
        else:
            self.cover_widget.setToolTip("")
            self.batch_pill_lbl.setVisible(False)

        self.rebuild_blurred_background()

        if info["kind"] == "audio" and bool(self.nt_config.get("adaptive_colors", True)):
            ac1, ac2 = extract_theme_colors(self.cover_pil)
            self.apply_theme_colors(ac1, ac2)
        else:
            self.apply_theme_colors(STAR_DEFAULT_YELLOW, STAR_DEFAULT_YELLOW_2)

        self.title_lbl.setText(info["title"])
        alb_txt = f"{info['artist']}  —  {info['album']}" if info["album"] else info["artist"]
        self.artist_lbl.setText(QFontMetrics(self.artist_lbl.font()).elidedText(alb_txt, Qt.TextElideMode.ElideRight, 460))

        def _elide(txt: str) -> str:
            return QFontMetrics(self.detail_labels["format"].font()).elidedText(txt, Qt.TextElideMode.ElideRight, 175)

        self.quick_edit_lyrics_btn.setEnabled(True)

        if info["kind"] == "audio":
            sr = f"{info['sample_rate']/1000:.1f} kHz" if info["sample_rate"] else "—"
            bps = f"{info['bits_per_sample']}-bit" if info["bits_per_sample"] else "Lossy"
            ch = "Stereo" if info["channels"] == 2 else ("Mono" if info["channels"] == 1 else f"{info['channels']}ch")
            br = f"{info['bitrate'] // 1000} kbps" if info["bitrate"] else "Variable"
            tn = f" #{info['track_no']:02d}" if info["track_no"] else ""
            yr = f" ({info['year']})" if info["year"] else ""

            self.detail_labels["format"].setText(_elide(info["codec_detail"]))
            self.detail_labels["audio"].setText(_elide(f"{sr} • {bps} • {ch}"))
            self.detail_labels["bitrate"].setText(br)
            self.detail_labels["size"].setText(format_bytes(info["file_size"]) if n_files == 1 else f"{n_files} files")
            self.detail_labels["album"].setText(_elide(f"{info['album']}{tn}{yr}"))
            self.detail_labels["lyrics"].setText(_elide(info["lyrics_status"]))
            self.detail_labels["output"].setText(_elide("Each file's folder" if n_files > 1 else str(file_path.parent)))

            self.player.setSource(QUrl.fromLocalFile(str(file_path)))
            self.seek_slider.setEnabled(True)
            self.seek_slider.setRange(0, max(0, info["duration_ms"]))
            self.seek_slider.setValue(0)
            self.time_cur_lbl.setText("0:00")
            self.time_tot_lbl.setText(format_ms(info["duration_ms"]))
            self.play_btn.setEnabled(True)
        else:
            self.detail_labels["format"].setText(_elide(info["codec_detail"]))
            self.detail_labels["audio"].setText(_elide(info["format"]))
            self.detail_labels["bitrate"].setText("Text")
            self.detail_labels["size"].setText(format_bytes(info["file_size"]))
            self.detail_labels["album"].setText(_elide(info["album"]))
            self.detail_labels["lyrics"].setText(_elide(info["lyrics_status"]))
            self.detail_labels["output"].setText(_elide("Each file's folder" if n_files > 1 else str(file_path.parent)))

            self.seek_slider.setEnabled(False)
            self.seek_slider.setRange(0, 0)
            self.time_cur_lbl.setText("0:00")
            self.time_tot_lbl.setText("0:00")
            self.play_btn.setEnabled(False)

    # -----------------------------------------------------------------------
    # Full-View Blurred-Backdrop Lyrics Editor
    # -----------------------------------------------------------------------
    def open_full_lyrics_editor(self):
        if not self.batch_infos:
            return
        # Never auto-play inside editor; let user browse/edit freely & press Play if desired
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()

        info = self.batch_infos[self.preview_idx]
        self.ed_track_lbl.setText(f"EDITING LYRICS  •  {info['title'].upper()}")
        self.full_lyrics_edit.setPlainText(info.get("lyrics_raw", ""))
        self.full_lyrics_edit.setExtraSelections([])

        is_audio = info["kind"] == "audio"
        self.ed_stamp_btn.setEnabled(is_audio)
        self.ed_embed_btn.setEnabled(is_audio and Path(info["path"]).suffix.lower() in (".flac", ".mp3", ".m4a"))

        self.main_stack.setCurrentIndex(1)
        self.full_lyrics_edit.setFocus()

    def close_full_lyrics_editor(self):
        self.full_lyrics_edit.setExtraSelections([])
        self.main_stack.setCurrentIndex(0)

    def stamp_current_time_in_editor(self):
        if self.main_stack.currentIndex() != 1:
            return
        pos = max(0, int(self.player.position()))
        mins, rem = divmod(pos, 60000)
        secs, rem_ms = divmod(rem, 1000)
        stamp = f"[{mins:02d}:{secs:02d}.{rem_ms // 10:02d}] "

        cursor = self.full_lyrics_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.StartOfLine)
        # Replace an existing leading [mm:ss.xx] if present on this line, otherwise insert cleanly
        line_txt = cursor.block().text()
        m = LRC_LINE_RE.match(line_txt)
        if m:
            cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, m.end())
            if cursor.block().text()[m.end():].startswith(" "):
                cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, 1)
        cursor.insertText(stamp)
        cursor.movePosition(QTextCursor.MoveOperation.Down)
        self.full_lyrics_edit.setTextCursor(cursor)
        self.full_lyrics_edit.setFocus()

    def _highlight_playing_line_in_editor(self, pos_ms: int):
        if self.main_stack.currentIndex() != 1:
            return
        doc = self.full_lyrics_edit.document()
        best_block = None
        best_ms = -1
        block = doc.begin()
        while block.isValid():
            txt = block.text()
            for m in LRC_LINE_RE.finditer(txt):
                t_ms = _ts_to_ms(m.group(1), m.group(2), m.group(3))
                if t_ms <= pos_ms and t_ms >= best_ms:
                    best_ms = t_ms
                    best_block = block
            block = block.next()

        if best_block is not None:
            sel = QTextEdit.ExtraSelection()
            ac = QColor(self.accent_color)
            sel.format.setBackground(QColor(ac.red(), ac.green(), ac.blue(), 42))
            sel.format.setProperty(QTextCharFormat.Property.FullWidthSelection, True)
            cur = QTextCursor(best_block)
            cur.clearSelection()
            sel.cursor = cur
            self.full_lyrics_edit.setExtraSelections([sel])
        else:
            self.full_lyrics_edit.setExtraSelections([])

    def save_editor_lyrics(self, to_file: bool, to_tag: bool):
        if not self.batch_infos:
            return
        info = self.batch_infos[self.preview_idx]
        src_path = Path(info["path"])
        text = self.full_lyrics_edit.toPlainText().strip()
        if not text:
            QMessageBox.information(self, "NanoTune Toolbox", "Lyrics box is empty.")
            return

        outputs: list[str] = []
        saved_notes: list[str] = []

        if to_file:
            is_elrc = bool(ELRC_WORD_RE.search(text))
            target_ext = ".elrc" if is_elrc else ".lrc"
            out_path = src_path if src_path.suffix.lower() in LYRIC_EXTENSIONS else src_path.with_suffix(target_ext)
            out_path.write_text(text, encoding="utf-8")
            outputs.append(str(out_path))
            saved_notes.append(f"Saved {out_path.name}")

        if to_tag and info["kind"] == "audio":
            was_playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            resume_pos = self.player.position()
            self.player.stop()
            self.player.setSource(QUrl())
            ext = src_path.suffix.lower()
            try:
                if ext == ".flac":
                    fl = FLAC(str(src_path))
                    fl["LYRICS"] = text
                    fl.save()
                elif ext == ".mp3":
                    mp = MP3(str(src_path))
                    if mp.tags is None:
                        mp.add_tags()
                    mp.tags.delall("USLT")
                    mp.tags.add(USLT(encoding=3, lang="eng", desc="", text=text))
                    mp.save()
                elif ext == ".m4a":
                    m4 = MP4(str(src_path))
                    if m4.tags is None:
                        m4.add_tags()
                    m4.tags["\xa9lyr"] = [text]
                    m4.save()
                outputs.append(str(src_path))
                saved_notes.append(f"Embedded into {src_path.name}")
            except Exception as exc:
                QMessageBox.warning(self, "Embed Failed", str(exc))
            finally:
                self.player.setSource(QUrl.fromLocalFile(str(src_path)))
                if resume_pos > 0:
                    self.player.setPosition(resume_pos)
                if was_playing:
                    self.player.play()

        if outputs:
            self.last_output_paths = outputs
            self.import_btn.setEnabled(True)
            self.batch_infos[self.preview_idx] = inspect_file(src_path, self.nt_config.get("imported_lyrics"))
            self.detail_labels["lyrics"].setText(self.batch_infos[self.preview_idx]["lyrics_status"])
            self.status_lbl.setText("Done: " + " & ".join(saved_notes))
            self.refresh_all_icons()
            QMessageBox.information(self, "Lyrics Saved", "\n".join(saved_notes))

    # -----------------------------------------------------------------------
    # Tasks Population & Execution
    # -----------------------------------------------------------------------
    def set_category(self, cid: str):
        self.selected_category = cid
        for k, btn in self.cat_buttons.items():
            btn.setChecked(k == cid)
        self.populate_tasks()

    def _is_task_applicable(self, task: dict) -> bool:
        if not self.batch_infos:
            return False
        has_audio = any(inf["kind"] == "audio" for inf in self.batch_infos)
        tid = task["id"]
        if not has_audio:
            return tid in ("edit_lyrics_view", "elrc_to_lrc", "lrc_to_elrc", "lrc_to_txt", "shift_lyrics")
        return True

    def populate_tasks(self):
        q = normalize_text(self.task_search.text()) if hasattr(self, "task_search") else ""
        prev_tid = None
        if self.task_list.currentItem() is not None:
            d = self.task_list.currentItem().data(Qt.ItemDataRole.UserRole)
            if isinstance(d, dict):
                prev_tid = d.get("id")

        self.task_list.clear()
        self.task_row_widgets.clear()
        restore_row = 0

        for task in TASKS:
            if self.selected_category != "all" and task["category"] != self.selected_category:
                continue
            if q and q not in normalize_text(task["title"] + task["desc"] + task["badge"]):
                continue

            applicable = self._is_task_applicable(task)
            opt_idx = self.task_selected_options.get(task["id"], int(task.get("default_opt", 0)))

            it = QListWidgetItem()
            it.setSizeHint(QSize(0, 60))
            it.setData(Qt.ItemDataRole.UserRole, {"id": task["id"], "applicable": applicable, "task": task})
            self.task_list.addItem(it)

            rw = TaskRowWidget(task, opt_idx, applicable, self.accent_color, self.accent_color_2, self.active_font_family)
            rw.rowClicked.connect(lambda item=it: self.task_list.setCurrentItem(item))
            rw.rowDoubleClicked.connect(self.run_selected_task)
            rw.optionChanged.connect(self._on_task_option_changed)
            self.task_list.setItemWidget(it, rw)
            self.task_row_widgets.append(rw)

            if task["id"] == prev_tid:
                restore_row = self.task_list.count() - 1

        if self.task_list.count() > 0:
            self.task_list.setCurrentRow(restore_row)
        self.on_selected_task_changed()

    def _on_task_option_changed(self, task_id: str, opt_idx: int):
        self.task_selected_options[task_id] = opt_idx

    def on_selected_task_changed(self):
        cur_it = self.task_list.currentItem()
        for row_i in range(self.task_list.count()):
            it = self.task_list.item(row_i)
            w = self.task_list.itemWidget(it)
            if isinstance(w, TaskRowWidget):
                w.set_selected(it is cur_it)

        data = cur_it.data(Qt.ItemDataRole.UserRole) if cur_it is not None else None
        has_valid = isinstance(data, dict) and bool(data.get("applicable", False)) and not self.is_running_task
        self.run_btn.setEnabled(has_valid)
        self.shift_row_widget.setVisible(isinstance(data, dict) and data.get("id") == "shift_lyrics")

    def run_selected_task(self):
        if self.is_running_task or not self.batch_infos:
            return
        it = self.task_list.currentItem()
        data = it.data(Qt.ItemDataRole.UserRole) if it is not None else None
        if not isinstance(data, dict) or not data.get("applicable", False):
            return

        tid = data["id"]
        if tid == "edit_lyrics_view":
            self.open_full_lyrics_editor()
            return

        task_def = data["task"]
        src_paths = [Path(inf["path"]) for inf in self.batch_infos]

        extra: dict = {"shift_ms": self.shift_spin.value()}
        if task_def.get("options"):
            opt_idx = self.task_selected_options.get(tid, 0)
            opts = task_def["options"]
            extra["option"] = opts[max(0, min(len(opts) - 1, opt_idx))]

        if tid == "embed_lyrics_tag" and len(src_paths) == 1:
            cand = None
            for lext in (".elrc", ".lrc"):
                p = src_paths[0].with_suffix(lext)
                if p.exists():
                    cand = p
                    break
            lrc_file, _ = QFileDialog.getOpenFileName(
                self,
                "Choose .LRC or .ELRC File to Embed",
                str(cand.parent if cand else src_paths[0].parent),
                "Lyrics Files (*.lrc *.elrc);;All Files (*.*)",
            )
            if not lrc_file:
                return
            extra["lrc_path"] = lrc_file

        if tid in ("embed_lyrics_tag", "strip_embedded_lyrics"):
            self.player.stop()
            self.player.setSource(QUrl())
            self.is_playing = False

        self.is_running_task = True
        self.run_btn.setEnabled(False)
        self.import_btn.setEnabled(False)
        self.last_output_paths = []
        self.progress_bar.setValue(4)
        self.status_lbl.setText(f"Running: {task_def['title']}...")
        self.refresh_all_icons()

        TaskWorker(
            tid,
            src_paths,
            extra,
            lambda val, st_txt: self.taskProgress.emit(val, st_txt),
            lambda ok, out_list, msg: self.taskFinished.emit(ok, out_list, msg),
        ).start()

    def on_task_progress(self, val: int, status_text: str):
        self.progress_bar.setValue(max(0, min(100, val)))
        if status_text:
            self.status_lbl.setText(status_text)

    def on_task_finished(self, ok: bool, out_paths: list, msg: str):
        self.is_running_task = False
        self.on_selected_task_changed()

        if ok:
            self.progress_bar.setValue(100)
            self.status_lbl.setText(f"Done: {msg}")
            valid_outs = [p for p in out_paths if p and Path(p).exists()]
            importable = [p for p in valid_outs if Path(p).suffix.lower() in (AUDIO_EXTENSIONS | LYRIC_EXTENSIONS)]
            if importable:
                self.last_output_paths = importable
                self.import_btn.setEnabled(True)
                self.import_btn.setToolTip(
                    f"Import {len(importable)} files into NanoTune"
                    if len(importable) > 1
                    else f"Import {Path(importable[0]).name} into NanoTune"
                )
            if self.batch_infos:
                cur_p = Path(self.batch_infos[self.preview_idx]["path"])
                if cur_p.exists():
                    self.batch_infos[self.preview_idx] = inspect_file(cur_p, self.nt_config.get("imported_lyrics"))
                    self.detail_labels["lyrics"].setText(self.batch_infos[self.preview_idx]["lyrics_status"])
                    if self.batch_infos[self.preview_idx]["kind"] == "audio" and self.player.source().isEmpty():
                        self.player.setSource(QUrl.fromLocalFile(str(cur_p)))
        else:
            self.progress_bar.setValue(0)
            self.status_lbl.setText(f"Failed: {msg}")
            QMessageBox.warning(self, "Task Error", msg)

        self.refresh_all_icons()

    # -----------------------------------------------------------------------
    # Import Result(s) to NanoTune (Shared Config + Live IPC)
    # -----------------------------------------------------------------------
    def import_result_to_nanotune(self):
        valid = [Path(p).resolve() for p in self.last_output_paths if Path(p).exists()]
        if not valid:
            return

        self.nt_config = self.load_nanotune_config()
        imported_files = list(self.nt_config.get("imported_files", []))
        norm_existing = {str(Path(x).resolve()).lower() for x in imported_files if Path(x).exists()}
        imported_lyrics = dict(self.nt_config.get("imported_lyrics") or {})

        audio_added = 0
        for out_p in valid:
            suf = out_p.suffix.lower()
            if suf in AUDIO_EXTENSIONS:
                if str(out_p).lower() not in norm_existing:
                    imported_files.append(str(out_p))
                    norm_existing.add(str(out_p).lower())
                audio_added += 1
            elif suf in LYRIC_EXTENSIONS and len(self.batch_infos) >= 1 and self.batch_infos[self.preview_idx]["kind"] == "audio":
                src_audio = Path(self.batch_infos[self.preview_idx]["path"])
                imported_lyrics[str(src_audio)] = str(out_p)
                imported_lyrics[str(src_audio.resolve())] = str(out_p)

        self.nt_config["imported_files"] = imported_files
        self.nt_config["imported_lyrics"] = imported_lyrics
        self.save_nanotune_config()
        if audio_added:
            self.auto_import_nanotune_directories()

        sent_ipc = False
        sock = QLocalSocket()
        sock.connectToServer(IPC_SERVER_NAME)
        if sock.waitForConnected(250):
            sock.write(json.dumps([str(p) for p in valid]).encode("utf-8"))
            sock.waitForBytesWritten(300)
            sock.disconnectFromServer()
            sent_ipc = True

        label = f"{len(valid)} files" if len(valid) > 1 else valid[0].name
        if sent_ipc:
            self.status_lbl.setText(f"Imported {label} directly into running NanoTune!")
        elif self.nanotune_installed:
            self.status_lbl.setText(f"Saved {label} to NanoTune library (ready on next launch).")
        else:
            self.status_lbl.setText(f"Saved {label} to shared NanoTune config folder.")

    # -----------------------------------------------------------------------
    # Basic Lightweight Test Playback
    # -----------------------------------------------------------------------
    def toggle_play_pause(self):
        if not self.play_btn.isEnabled():
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def on_playback_state_changed(self, state):
        self.is_playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.refresh_all_icons()

    def on_media_status_changed(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.player.setPosition(0)
            self.is_playing = False
            self.refresh_all_icons()

    def on_duration_changed(self, duration: int):
        if duration > 0:
            self.seek_slider.setRange(0, duration)
            self.time_tot_lbl.setText(format_ms(duration))

    def on_position_changed(self, pos: int):
        if not self.seeking_active:
            self.seek_slider.setValue(pos)
            self.time_cur_lbl.setText(format_ms(pos))
        if self.main_stack.currentIndex() == 1:
            self._highlight_playing_line_in_editor(pos)

    def on_seek_released(self):
        self.seeking_active = False
        pos = self.seek_slider.value()
        self.player.setPosition(pos)
        if self.main_stack.currentIndex() == 1:
            self._highlight_playing_line_in_editor(pos)


def main():
    if "--export-icon" in sys.argv:
        idx = sys.argv.index("--export-icon")
        out_path = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "assets/nanotune_toolbox.ico"
        app = QApplication(sys.argv)
        export_toolbox_ico(out_path)
        sys.exit(0)

    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except Exception:
            pass

    if SELECTED_BACKEND == "opengl21":
        QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_UseDesktopOpenGL)
        fmt = QSurfaceFormat()
        fmt.setVersion(2, 1)
        fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.NoProfile)
        QSurfaceFormat.setDefaultFormat(fmt)

    startup_files = [
        str(Path(a).resolve())
        for a in sys.argv[1:]
        if not a.startswith("-") and Path(a).is_file() and Path(a).suffix.lower() in (AUDIO_EXTENSIONS | LYRIC_EXTENSIONS)
    ]

    app = QApplication(sys.argv)
    app.setApplicationName("NanoTune Toolbox")
    win = NanoTuneToolbox(startup_files=startup_files)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    try:
        main()
    except Exception:
        err_text = traceback.format_exc()
        print(err_text)
        try:
            input("\n[NanoTune Toolbox] Error occurred. Press Enter to exit...")
        except Exception:
            pass