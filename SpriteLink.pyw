# SpriteLink v13
# Windows + Python 3.10+
#
# Required packages:
#   pip install PySide6 requests cryptography Pillow
#
# Default transport:
#   https://ntfy.sh
#
# The client derives an opaque ntfy topic from the shared encryption key.
# ntfy only sees:
#   - an opaque topic name
#   - an opaque padded encrypted packet
#   - ordinary connection metadata such as IP address and request time
#
# Username, username color, hidden client identity, message timestamp,
# client message ID, and message text are compressed, padded, and encrypted.

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import ctypes
from ctypes import wintypes
from functools import lru_cache
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import queue
import re
import secrets
import subprocess
import threading
import time
import traceback
import sys
import uuid
import zlib
from urllib.parse import parse_qs, urlsplit
try:
    from PySide6.QtCore import (
        QEvent,
        QObject,
        QPoint,
        QSize,
        QTimer,
        Qt,
        QUrl,
        Signal,
    )
    from PySide6.QtGui import (
        QBrush,
        QColor,
        QCursor,
        QDesktopServices,
        QDrag,
        QFont,
        QFontMetrics,
        QIcon,
        QImage,
        QLinearGradient,
        QPainter,
        QPalette,
        QPixmap,
        QPolygon,
        QTextBlockFormat,
        QTextCharFormat,
        QTextCursor,
        QTextDocument,
        QTextFormat,
        QTextImageFormat,
        QTextOption,
    )
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QSoundEffect
    from PySide6.QtWidgets import (
        QAbstractItemView,
        QApplication,
        QCheckBox,
        QColorDialog,
        QComboBox,
        QDialog,
        QFrame,
        QFileDialog,
        QGraphicsOpacityEffect,
        QGridLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMainWindow,
        QMenu,
        QMessageBox,
        QPlainTextEdit,
        QProgressBar,
        QPushButton,
        QScrollArea,
        QSlider,
        QSizePolicy,
        QStyleFactory,
        QTextBrowser,
        QTextEdit,
        QToolTip,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: PySide6\n\nInstall it with:\n"
        "pip install PySide6 requests cryptography Pillow"
    ) from exc
from typing import Any

try:
    import requests
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: requests\n\nInstall it with:\n"
        "pip install PySide6 requests cryptography Pillow"
    ) from exc

from spritelink_update import (
    ReleaseInfo,
    UpdateError,
    download_release_installer,
    fetch_latest_release,
    parse_semantic_version,
    release_is_newer,
)

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: cryptography\n\nInstall it with:\n"
        "pip install PySide6 requests cryptography Pillow"
    ) from exc

try:
    from PIL import Image
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: Pillow\n\nInstall it with:\n"
        "pip install PySide6 requests cryptography Pillow"
    ) from exc

APP_NAME = "SpriteLink"
APP_VERSION = 1
CONFIG_FORMAT_VERSION = 20
WINDOW_ICON_PATH = Path(__file__).resolve().parent / "SL.ico"
WINDOWS_APP_USER_MODEL_ID = "SpriteLink.SpriteLink"

try:
    from spritelink_build_version import VERSION as RUNNING_VERSION
except ImportError:
    RUNNING_VERSION = "Development"

UPDATE_REPOSITORY = "glasspage/SpriteLink"
UPDATE_CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000
UPDATE_DIRECTORY = Path(
    os.environ.get("LOCALAPPDATA")
    or os.environ.get("APPDATA")
    or Path.home()
) / APP_NAME / "updates"

NOTIFICATION_BUTTON_STYLESHEET = (
    "QPushButton {"
    " background-color: #f8d8ad;"
    " color: #8a4b08;"
    " border: 1px solid #dca15d;"
    " border-radius: 3px;"
    "}"
    "QPushButton:hover { background-color: #f3c78d; }"
    "QPushButton:pressed { background-color: #efb968; }"
)

DEFAULT_SERVER_PRESET = "ntfy.sh (public)"
DEFAULT_SERVER_URL = "https://ntfy.sh"
GLOBAL_CHATROOM_ID = "global"
GLOBAL_CHATROOM_NICKNAME = "Global"
GLOBAL_CHATROOM_KEY = "Xpkri=AKDzpyRjwi^g6+*GJZ=7CUH-QjdbJA%q"
CHATROOM_SIDEBAR_WIDTH = 180
CONFIG_POPUP_MAX_WIDTH = 720
DEFAULT_MESSAGE_SOUND = "Chime"
DEFAULT_MESSAGE_SOUND_VOLUME = 100
MESSAGE_SOUND_OPTIONS = (
    "Disabled",
    "Chime Soft",
    "Chime",
    "Chime Glassy",
    "Blip",
    "Custom",
)
BUILTIN_MESSAGE_SOUND_FILES = {
    "Chime Soft": "chime_soft.wav",
    "Chime": "chime.wav",
    "Chime Glassy": "chime_glassy.wav",
    "Blip": "blip.wav",
}
CUSTOM_MESSAGE_SOUND_MAX_MS = 3000
COMPRESSED_MESSAGE_SOUND_EXTENSIONS = {".mp3", ".ogg"}

SERVER_PRESETS: dict[str, str] = {
    DEFAULT_SERVER_PRESET: DEFAULT_SERVER_URL,
    "Local ntfy server": "http://127.0.0.1:8080",
    "Custom ntfy server": "",
}

DEFAULT_THEME = "Modern (Light)"
LEGACY_BASIC_THEME = "Basic (Light)"
THEMES = (
    DEFAULT_THEME,
    "Windows Classic",
)

FOCUSED_ACTIVE_POLL_INTERVAL_SECONDS = 6.0
UNFOCUSED_ACTIVE_POLL_INTERVAL_SECONDS = 15.0
FOCUSED_BACKGROUND_POLL_INTERVAL_SECONDS = 30.0
UNFOCUSED_BACKGROUND_POLL_INTERVAL_SECONDS = 45.0
CHATROOM_SWITCH_BURST_WINDOW_SECONDS = 6.0
IMMEDIATE_CHATROOM_SWITCH_LIMIT = 2
REQUEST_TIMEOUT_SECONDS = 10
AUTO_HISTORY_SECONDS = 48 * 60 * 60
GAP_SEPARATOR_SECONDS = 6 * 60 * 60
MAX_MESSAGE_CHARS = 4000
NTFY_MAX_BODY_BYTES = 4096
NTFY_DAILY_MESSAGE_LIMIT = 250
MESSAGE_LIMIT_BAR_THRESHOLD = 50
PACKET_PADDING_BLOCK = 128
PROFILE_ICON_SIZE = 16
PROFILE_ICON_MAX_COLORS = 16
MAX_PROFILE_ICON_GIF_BYTES = 2048
MAX_IDENTITY_PRESETS = 64
MESSAGE_SIZE_DEBOUNCE_MS = 1500
CHAT_TOOLTIP_HOVER_DELAY_MS = 100
EMBEDDED_IMAGE_MAX_EDGE = 96
NSFW_IMAGE_PLACEHOLDER_SIZE = 64
TOP_ALIGNED_PROFILE_ICON_PADDING = 3
IMAGE_PREVIEW_MAX_WIDTH = CONFIG_POPUP_MAX_WIDTH - 32
IMAGE_PREVIEW_MAX_HEIGHT = 480
MAX_REMOTE_IMAGE_BYTES = 8 * 1024 * 1024
MAX_REMOTE_IMAGE_PIXELS = 16 * 1024 * 1024
IMAGE_PREVIEW_CACHE_LIMIT = 128
MAX_UNCOMPRESSED_MESSAGE_BYTES = 32 * 1024
MAX_ENCRYPTED_PACKET_CHARS = NTFY_MAX_BODY_BYTES - 1
MESSAGE_AUTH_VERSION = 1
ED25519_PRIVATE_KEY_BYTES = 32
ED25519_PUBLIC_KEY_BYTES = 32
ED25519_SIGNATURE_BYTES = 64
VISIBLE_USER_ID_CHARS = 8
SIGNED_MESSAGE_CONTEXT = "SpriteLink signed message v1"
TRUSTED_EXTENSIONLESS_IMAGE_HOSTS = (
    "images.unsplash.com",
    "pbs.twimg.com",
    "cdn.bsky.app",
)
TRUSTED_IMAGE_HOST_PATTERNS = (
    "cdn.discordapp.com",
    "media.discordapp.net",
    "upload.wikimedia.org",
    "cdn.donmai.*",
    "pbs.twimg.com",
    "i.imgur.com",
    "images.unsplash.com",
    "images.pexels.com",
    "cdn.bsky.app",
    "media.tenor.com",
    "media.giphy.com",
    "i.giphy.com",
    "static.wikia.nocookie.net",
    "avatars.githubusercontent.com",
    "user-images.githubusercontent.com",
    "raw.githubusercontent.com",
    "steamuserimages-a.akamaihd.net",
    "image.tmdb.org",
    "cdn.myanimelist.net",
)
IMAGE_LINK_EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".jfif",
)
LIKELY_NSFW_IMAGE_DOMAINS = (
    "e621.net",
    "gelbooru.com",
    "nhentai.net",
    "redgifs.com",
    "rule34.paheal.net",
    "rule34.xxx",
    "xbooru.com",
    "yande.re",
)
MESSAGE_URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
MESSAGE_ENTRY_MIN_LINES = 1
MESSAGE_ENTRY_MAX_LINES = 6
DEFAULT_MESSAGE_FONT = "Segoe UI"
DEFAULT_MESSAGE_TEXT_COLOR = "#202020"
MUTED_CONTENT_OPACITY = 0.30
MESSAGE_ROW_BACKGROUNDS = ("#ffffff", "#f5f5f5")
SELECTABLE_MESSAGE_FONTS = (
    "Arial",
    "Calibri",
    "Comic Sans MS",
    "Consolas",
    "Corbel",
    "Segoe UI",
    "Times New Roman",
    "Tahoma",
)
MESSAGE_FONT_POINT_SIZES = {
    "Arial": 13,
    "Calibri": 14,
    "Comic Sans MS": 12,
    "Consolas": 14,
    "Corbel": 14,
    "Segoe UI": 13,
    "Times New Roman": 14,
    "Tahoma": 12,
}
# Preserve receive compatibility with messages created by earlier v11 drafts.
# Neither legacy value remains selectable for new messages.
SUPPORTED_MESSAGE_FONTS = SELECTABLE_MESSAGE_FONTS + (
    "System",
    "Bahnschrift",
)

WINDOWS_CLASSIC_STYLESHEET = """
QMainWindow, QDialog, QWidget {
    background-color: #c0c0c0;
    color: #000000;
}
QLabel {
    background-color: transparent;
}
QPushButton {
    background-color: #c0c0c0;
    color: #000000;
    border-top: 2px solid #ffffff;
    border-left: 2px solid #ffffff;
    border-right: 2px solid #000000;
    border-bottom: 2px solid #000000;
    border-radius: 0px;
    padding: 3px 8px;
    min-height: 18px;
}
QPushButton:pressed, QPushButton:checked {
    border-top: 2px solid #000000;
    border-left: 2px solid #000000;
    border-right: 2px solid #ffffff;
    border-bottom: 2px solid #ffffff;
    padding-top: 4px;
    padding-left: 9px;
    padding-right: 7px;
    padding-bottom: 2px;
}
QPushButton:disabled {
    color: #808080;
}
QLineEdit, QPlainTextEdit, QTextBrowser, QListWidget, QComboBox {
    background-color: #ffffff;
    color: #000000;
    border-top: 2px solid #808080;
    border-left: 2px solid #808080;
    border-right: 2px solid #ffffff;
    border-bottom: 2px solid #ffffff;
    border-radius: 0px;
    selection-background-color: #007f82;
    selection-color: #000000;
}
QComboBox {
    padding: 2px 4px;
}
QComboBox::drop-down {
    background-color: #c0c0c0;
    border-left: 1px solid #808080;
    width: 20px;
}
QComboBox::down-arrow {
    width: 9px;
    height: 5px;
}
QComboBox QAbstractItemView, QMenu {
    background-color: #ffffff;
    color: #000000;
    border: 1px solid #000000;
    selection-background-color: #007f82;
    selection-color: #000000;
}
QListWidget::item:selected, QMenu::item:selected {
    background-color: #007f82;
    color: #000000;
}
QMenu::item:disabled {
    color: #808080;
}
QMenu::item:disabled:selected {
    background-color: #ffffff;
    color: #808080;
}
QCheckBox {
    spacing: 6px;
}
QProgressBar {
    background-color: #ffffff;
    color: #000000;
    border-top: 2px solid #808080;
    border-left: 2px solid #808080;
    border-right: 2px solid #ffffff;
    border-bottom: 2px solid #ffffff;
    border-radius: 0px;
    text-align: center;
}
QProgressBar::chunk {
    background-color: #007f82;
}
QToolTip {
    background-color: #ffffe1;
    color: #000000;
    border: 1px solid #000000;
}
QFrame[frameShape="4"], QFrame[frameShape="5"] {
    color: #808080;
}
"""

# Dark, moderately saturated colors that remain readable against the standard
# light chat background. A color is selected only when a new local
# configuration is first created.
SAFE_USERNAME_COLORS = (
    "#2f6bff",
    "#7a3fd0",
    "#b02a67",
    "#b23a2b",
    "#9a5a00",
    "#247044",
    "#00737a",
    "#315f8c",
    "#67523a",
    "#5f5ab8",
)


def optimize_profile_icon(source_path: str) -> bytes:
    """Convert a PNG to a compact, single-frame 16x16 palette GIF."""
    with Image.open(source_path) as source:
        if source.format != "PNG":
            raise ValueError("Profile icons must be PNG images.")
        source.load()
        icon = source.convert("RGBA")

    icon.thumbnail(
        (PROFILE_ICON_SIZE, PROFILE_ICON_SIZE),
        Image.Resampling.NEAREST,
    )
    canvas = Image.new(
        "RGBA",
        (PROFILE_ICON_SIZE, PROFILE_ICON_SIZE),
        (0, 0, 0, 0),
    )
    offset = (
        (PROFILE_ICON_SIZE - icon.width) // 2,
        (PROFILE_ICON_SIZE - icon.height) // 2,
    )
    canvas.alpha_composite(icon, offset)

    # GIF supports one transparent palette entry, so normalize partial alpha
    # before counting and quantizing colors.
    normalized_pixels = [
        (red, green, blue, 255)
        if alpha >= 128
        else (0, 0, 0, 0)
        for red, green, blue, alpha in canvas.getdata()
    ]
    has_transparency = any(alpha == 0 for *_rgb, alpha in normalized_pixels)
    opaque_pixels = [
        (red, green, blue)
        for red, green, blue, alpha in normalized_pixels
        if alpha == 255
    ]
    max_opaque_colors = PROFILE_ICON_MAX_COLORS - int(has_transparency)

    exact_colors = list(dict.fromkeys(opaque_pixels))
    if len(exact_colors) <= max_opaque_colors:
        palette_colors = exact_colors
        color_to_index = {
            color: index for index, color in enumerate(palette_colors)
        }
        opaque_indices = [color_to_index[color] for color in opaque_pixels]
    else:
        sample = Image.new("RGB", (len(opaque_pixels), 1))
        sample.putdata(opaque_pixels)
        quantized = sample.quantize(
            colors=max_opaque_colors,
            method=Image.Quantize.MEDIANCUT,
            dither=Image.Dither.NONE,
        )
        quantized_indices = list(quantized.getdata())
        used_indices = list(dict.fromkeys(quantized_indices))
        remap = {
            old_index: new_index
            for new_index, old_index in enumerate(used_indices)
        }
        raw_palette = quantized.getpalette() or []
        palette_colors = [
            tuple(raw_palette[index * 3:index * 3 + 3])
            for index in used_indices
        ]
        opaque_indices = [remap[index] for index in quantized_indices]

    transparency_index = len(palette_colors) if has_transparency else None
    pixel_indices: list[int] = []
    opaque_position = 0
    for _red, _green, _blue, alpha in normalized_pixels:
        if alpha == 0:
            pixel_indices.append(int(transparency_index or 0))
        else:
            pixel_indices.append(opaque_indices[opaque_position])
            opaque_position += 1

    stored_palette = list(palette_colors)
    if has_transparency:
        stored_palette.append((0, 0, 0))
    if not stored_palette:
        stored_palette.append((0, 0, 0))
    flat_palette = [component for color in stored_palette for component in color]
    flat_palette.extend([0] * (768 - len(flat_palette)))

    indexed = Image.new("P", (PROFILE_ICON_SIZE, PROFILE_ICON_SIZE))
    indexed.putpalette(flat_palette)
    indexed.putdata(pixel_indices)
    output = io.BytesIO()
    save_options: dict[str, Any] = {
        "format": "GIF",
        "optimize": True,
    }
    if transparency_index is not None:
        save_options["transparency"] = transparency_index
        save_options["disposal"] = 2
    indexed.save(output, **save_options)
    gif_data = output.getvalue()
    if len(gif_data) > MAX_PROFILE_ICON_GIF_BYTES:
        raise ValueError("The optimized profile icon is unexpectedly large.")
    return gif_data


def encode_profile_icon(gif_data: bytes) -> str:
    return base64.urlsafe_b64encode(gif_data).decode("ascii").rstrip("=")


@lru_cache(maxsize=128)
def profile_icon_tooltip_data_uri(encoded_icon: str) -> str:
    try:
        gif_data = decode_profile_icon(encoded_icon)
        with Image.open(io.BytesIO(gif_data)) as icon:
            preview = icon.convert("RGBA").resize(
                (64, 64),
                Image.Resampling.NEAREST,
            )
        output = io.BytesIO()
        preview.save(output, format="PNG", optimize=True)
        encoded_preview = base64.b64encode(output.getvalue()).decode("ascii")
        return f"data:image/png;base64,{encoded_preview}"
    except Exception:
        return ""


def decode_profile_icon(encoded: str) -> bytes:
    if not encoded:
        return b""
    if len(encoded) > ((MAX_PROFILE_ICON_GIF_BYTES * 4 + 2) // 3) + 4:
        raise ValueError("Profile icon data is too large.")
    padding = "=" * ((4 - len(encoded) % 4) % 4)
    try:
        gif_data = base64.b64decode(
            encoded + padding,
            altchars=b"-_",
            validate=True,
        )
    except Exception as exc:
        raise ValueError("Profile icon data is not valid Base64.") from exc
    if len(gif_data) > MAX_PROFILE_ICON_GIF_BYTES:
        raise ValueError("Profile icon data is too large.")
    if gif_data[:6] not in (b"GIF87a", b"GIF89a"):
        raise ValueError("Profile icon data is not a GIF image.")

    try:
        with Image.open(io.BytesIO(gif_data)) as icon:
            if icon.format != "GIF" or icon.size != (
                PROFILE_ICON_SIZE,
                PROFILE_ICON_SIZE,
            ):
                raise ValueError("Profile icons must be 16x16 GIF images.")
            if int(getattr(icon, "n_frames", 1)) != 1:
                raise ValueError("Animated profile icons are not supported.")
            rgba = icon.convert("RGBA")
            if len(set(rgba.getdata())) > PROFILE_ICON_MAX_COLORS:
                raise ValueError("Profile icons may use at most 16 colors.")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Profile icon GIF data is invalid.") from exc
    return gif_data


def normalize_profile_icon(value: Any, fallback: str = "") -> str:
    encoded = value if isinstance(value, str) else fallback
    try:
        decode_profile_icon(encoded)
    except ValueError:
        return fallback
    return encoded


def default_room_profile() -> dict[str, str]:
    return {
        "username": "User",
        "username_color": secrets.choice(SAFE_USERNAME_COLORS),
        "font": DEFAULT_MESSAGE_FONT,
        "text_color": DEFAULT_MESSAGE_TEXT_COLOR,
        "profile_icon": "",
    }


def default_identity_preset() -> dict[str, str]:
    return {
        "id": uuid.uuid4().hex,
        "username": "User",
        "username_color": secrets.choice(SAFE_USERNAME_COLORS),
        "profile_icon": "",
    }


def normalize_identity_preset(value: Any) -> dict[str, str]:
    fallback = default_identity_preset()
    raw = value if isinstance(value, dict) else {}
    preset_id = str(raw.get("id", fallback["id"])).strip()
    if not preset_id:
        preset_id = fallback["id"]
    username = str(raw.get("username", fallback["username"])).strip()
    if not username:
        username = fallback["username"]
    username_color = QColor(
        str(raw.get("username_color", fallback["username_color"]))
    )
    if not username_color.isValid():
        username_color = QColor(fallback["username_color"])
    profile_icon = normalize_profile_icon(
        raw.get("profile_icon", ""),
        "",
    )
    return {
        "id": preset_id[:64],
        "username": username[:32],
        "username_color": username_color.name(),
        "profile_icon": profile_icon,
    }


def identity_signature(value: dict[str, str]) -> tuple[str, str, str]:
    return (
        str(value.get("username", "User")),
        str(value.get("username_color", "#000000")).casefold(),
        str(value.get("profile_icon", "")),
    )


def apply_identity_preset_to_profile(
    profile: dict[str, str],
    preset: dict[str, str],
) -> None:
    profile["identity_preset_id"] = preset["id"]
    profile["username"] = preset["username"]
    profile["username_color"] = preset["username_color"]
    profile["profile_icon"] = preset["profile_icon"]


def normalize_room_profile(
    profile: Any,
    fallback: dict[str, str] | None = None,
) -> dict[str, str]:
    base = dict(fallback or default_room_profile())
    raw = profile if isinstance(profile, dict) else {}

    username = str(raw.get("username", base["username"])).strip()
    if not username:
        username = base["username"]

    username_color = QColor(
        str(raw.get("username_color", base["username_color"]))
    )
    if not username_color.isValid():
        username_color = QColor(base["username_color"])

    font = str(raw.get("font", base["font"]))
    if font not in SELECTABLE_MESSAGE_FONTS:
        font = base["font"]

    text_color = QColor(
        str(raw.get("text_color", base["text_color"]))
    )
    if not text_color.isValid():
        text_color = QColor(base["text_color"])

    profile_icon = normalize_profile_icon(
        raw.get("profile_icon", base.get("profile_icon", "")),
        str(base.get("profile_icon", "")),
    )
    identity_preset_id = str(
        raw.get(
            "identity_preset_id",
            base.get("identity_preset_id", ""),
        )
    ).strip()[:64]

    return {
        "username": username[:32],
        "username_color": username_color.name(),
        "font": font,
        "text_color": text_color.name(),
        "profile_icon": profile_icon,
        "identity_preset_id": identity_preset_id,
    }

# The former EncryptedChatClient directory is intentionally not migrated.
APP_DATA_DIR = Path(
    os.environ.get("LOCALAPPDATA")
    or os.environ.get("APPDATA")
    or Path.home()
) / APP_NAME

CONFIG_PATH = APP_DATA_DIR / "settings.bin"
DPAPI_ENTROPY = b"EncryptedChatClient-v3-config"


class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def _bytes_to_blob(data: bytes) -> tuple[DATA_BLOB, Any]:
    buffer = ctypes.create_string_buffer(data, len(data))
    blob = DATA_BLOB(
        len(data),
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)),
    )
    return blob, buffer


def dpapi_encrypt(data: bytes) -> bytes:
    """Encrypt bytes using Windows DPAPI for the current Windows user."""
    if os.name != "nt":
        raise RuntimeError("Windows DPAPI is only available on Windows.")

    in_blob, in_buffer = _bytes_to_blob(data)
    entropy_blob, entropy_buffer = _bytes_to_blob(DPAPI_ENTROPY)
    out_blob = DATA_BLOB()

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    CRYPTPROTECT_UI_FORBIDDEN = 0x01

    result = crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        APP_NAME,
        ctypes.byref(entropy_blob),
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    )

    _ = in_buffer, entropy_buffer

    if not result:
        raise ctypes.WinError()

    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def dpapi_decrypt(data: bytes, entropy: bytes = DPAPI_ENTROPY) -> bytes:
    """Decrypt bytes using Windows DPAPI for the current Windows user."""
    if os.name != "nt":
        raise RuntimeError("Windows DPAPI is only available on Windows.")

    in_blob, in_buffer = _bytes_to_blob(data)
    entropy_blob, entropy_buffer = _bytes_to_blob(entropy)
    out_blob = DATA_BLOB()

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    CRYPTPROTECT_UI_FORBIDDEN = 0x01

    result = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        ctypes.byref(entropy_blob),
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    )

    _ = in_buffer, entropy_buffer

    if not result:
        raise ctypes.WinError()

    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def current_utc_day_number() -> int:
    return int(time.time() // (24 * 60 * 60))


def next_utc_midnight_timestamp() -> int:
    return (current_utc_day_number() + 1) * (24 * 60 * 60)


def local_reset_time_label() -> str:
    local_reset = datetime.fromtimestamp(
        next_utc_midnight_timestamp()
    ).astimezone()
    hour = local_reset.strftime("%I").lstrip("0") or "0"
    timezone_name = local_reset.tzname() or "local time"
    if " " in timezone_name:
        abbreviation = "".join(
            word[0]
            for word in timezone_name.split()
            if word
        ).upper()
        if len(abbreviation) >= 2:
            timezone_name = abbreviation
    return (
        f"{hour}:{local_reset.strftime('%M %p')} "
        f"{timezone_name}"
    )


def message_url_spans(text: str) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    for match in MESSAGE_URL_PATTERN.finditer(text):
        start, end = match.span()
        url = match.group(0)
        while url and url[-1] in ".,!?;:":
            url = url[:-1]
            end -= 1
        for opening, closing in (("(", ")"), ("[", "]"), ("{", "}")):
            while url.endswith(closing) and url.count(closing) > url.count(opening):
                url = url[:-1]
                end -= 1
        if url:
            spans.append((start, end, url))
    return spans


def is_direct_image_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    query_format = (
        parse_qs(parsed.query).get("format", [""])[0]
        .strip()
        .casefold()
    )
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    return (
        parsed.scheme.casefold() in {"http", "https"}
        and (
            parsed.path.casefold().endswith(IMAGE_LINK_EXTENSIONS)
            or f".{query_format}" in IMAGE_LINK_EXTENSIONS
            or hostname in TRUSTED_EXTENSIONLESS_IMAGE_HOSTS
        )
    )


def _image_hostname_matches_pattern(
    hostname: str,
    pattern: str,
) -> bool:
    if pattern.endswith(".*"):
        prefix = pattern[:-1]
        suffix = hostname[len(prefix):] if hostname.startswith(prefix) else ""
        return bool(suffix) and "." not in suffix
    return hostname == pattern


def is_trusted_image_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").casefold().rstrip(".")
    except ValueError:
        return False
    return (
        parsed.scheme.casefold() == "https"
        and bool(hostname)
        and any(
            _image_hostname_matches_pattern(hostname, pattern)
            for pattern in TRUSTED_IMAGE_HOST_PATTERNS
        )
    )


def is_image_url_trusted_for_sender(
    url: str,
    client_id: str,
    is_local: bool,
    trusted_user_ids: set[str],
) -> bool:
    return (
        is_local
        or client_id in trusted_user_ids
        or is_trusted_image_url(url)
    )


def generate_chatroom_key() -> str:
    # 24 random bytes encode to exactly 32 URL-safe characters.
    return secrets.token_urlsafe(24)


def is_likely_nsfw_image_url(url: str) -> bool:
    try:
        hostname = (urlsplit(url).hostname or "").casefold().rstrip(".")
    except ValueError:
        return False
    return any(
        hostname == domain or hostname.endswith(f".{domain}")
        for domain in LIKELY_NSFW_IMAGE_DOMAINS
    )


def message_contains_only_image_links(text: str) -> bool:
    return (
        bool(direct_image_urls_in_message(text))
        and not message_text_without_image_links(text).strip()
    )


def message_text_without_image_links(
    text: str,
    embedded_image_urls: set[str] | None = None,
) -> str:
    visible_parts: list[str] = []
    position = 0
    for start, end, url in message_url_spans(text):
        visible_parts.append(text[position:start])
        should_remove = (
            is_direct_image_url(url)
            and (
                embedded_image_urls is None
                or url in embedded_image_urls
            )
        )
        if not should_remove:
            visible_parts.append(text[start:end])
        position = end
    visible_parts.append(text[position:])
    return "".join(visible_parts)


def direct_image_urls_in_message(text: str) -> list[str]:
    image_urls: list[str] = []
    for _start, _end, url in message_url_spans(text):
        if is_direct_image_url(url) and url not in image_urls:
            image_urls.append(url)
    return image_urls


def _encode_identity_bytes(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode_identity_bytes(
    value: str,
    expected_length: int,
    description: str,
) -> bytes:
    if not isinstance(value, str):
        raise ValueError(f"{description} has the wrong type.")
    padding = "=" * ((4 - len(value) % 4) % 4)
    try:
        decoded = base64.b64decode(
            value + padding,
            altchars=b"-_",
            validate=True,
        )
    except Exception as exc:
        raise ValueError(f"{description} is not valid Base64.") from exc
    if len(decoded) != expected_length:
        raise ValueError(f"{description} has the wrong length.")
    return decoded


def generate_identity_private_key() -> str:
    private_key = Ed25519PrivateKey.generate()
    raw_private_key = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return _encode_identity_bytes(raw_private_key)


def normalize_identity_private_key(value: Any) -> str:
    try:
        raw_private_key = _decode_identity_bytes(
            value,
            ED25519_PRIVATE_KEY_BYTES,
            "Identity private key",
        )
        Ed25519PrivateKey.from_private_bytes(raw_private_key)
    except Exception:
        return generate_identity_private_key()
    return str(value)


def identity_public_key_bytes(encoded_private_key: str) -> bytes:
    raw_private_key = _decode_identity_bytes(
        encoded_private_key,
        ED25519_PRIVATE_KEY_BYTES,
        "Identity private key",
    )
    private_key = Ed25519PrivateKey.from_private_bytes(raw_private_key)
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def client_id_from_public_key(public_key: bytes) -> str:
    if len(public_key) != ED25519_PUBLIC_KEY_BYTES:
        raise ValueError("Identity public key has the wrong length.")
    return hashlib.sha256(
        b"SpriteLink client identity v1\0" + public_key
    ).hexdigest()


def identity_client_id(encoded_private_key: str) -> str:
    return client_id_from_public_key(
        identity_public_key_bytes(encoded_private_key)
    )


def visible_user_id(client_id: str) -> str:
    return str(client_id)[:VISIBLE_USER_ID_CHARS].upper()


def _signed_message_payload(
    message: dict[str, Any],
    encryption_key: str,
) -> bytes:
    values = [
        SIGNED_MESSAGE_CONTEXT,
        derive_ntfy_topic(encryption_key),
        message["a"],
        message["v"],
        message["i"],
        message["c"],
        message["q"],
        message["u"],
        message["k"],
        message.get("f", DEFAULT_MESSAGE_FONT),
        message.get("o", DEFAULT_MESSAGE_TEXT_COLOR),
        message.get("p", ""),
        message["t"],
        message["m"],
    ]
    return json.dumps(
        values,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def sign_message_identity(
    message: dict[str, Any],
    encryption_key: str,
    encoded_private_key: str,
) -> dict[str, Any]:
    raw_private_key = _decode_identity_bytes(
        encoded_private_key,
        ED25519_PRIVATE_KEY_BYTES,
        "Identity private key",
    )
    private_key = Ed25519PrivateKey.from_private_bytes(raw_private_key)
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    message["a"] = MESSAGE_AUTH_VERSION
    message["q"] = _encode_identity_bytes(public_key)
    message["c"] = client_id_from_public_key(public_key)
    message.pop("s", None)
    message["s"] = _encode_identity_bytes(
        private_key.sign(
            _signed_message_payload(message, encryption_key)
        )
    )
    return message


def verify_message_identity(
    message: dict[str, Any],
    encryption_key: str,
) -> None:
    if message.get("a") != MESSAGE_AUTH_VERSION:
        raise ValueError("Message authentication version is unsupported.")
    public_key = _decode_identity_bytes(
        message.get("q"),
        ED25519_PUBLIC_KEY_BYTES,
        "Identity public key",
    )
    expected_client_id = client_id_from_public_key(public_key)
    if not secrets.compare_digest(
        str(message.get("c", "")),
        expected_client_id,
    ):
        raise ValueError("Message client ID does not match its public key.")
    signature = _decode_identity_bytes(
        message.get("s"),
        ED25519_SIGNATURE_BYTES,
        "Message signature",
    )
    Ed25519PublicKey.from_public_bytes(public_key).verify(
        signature,
        _signed_message_payload(message, encryption_key),
    )


def default_config() -> dict[str, Any]:
    global_profile = default_room_profile()
    default_preset = default_identity_preset()
    apply_identity_preset_to_profile(global_profile, default_preset)
    return {
        "config_version": CONFIG_FORMAT_VERSION,
        "server_preset": DEFAULT_SERVER_PRESET,
        "server_url": DEFAULT_SERVER_URL,
        "theme": DEFAULT_THEME,
        "message_sound": DEFAULT_MESSAGE_SOUND,
        "message_sound_volume": DEFAULT_MESSAGE_SOUND_VOLUME,
        "custom_message_sound_path": "",
        "automatic_update_checks": True,
        "identity_private_key": generate_identity_private_key(),
        "chatrooms": [],
        "active_chatroom_id": GLOBAL_CHATROOM_ID,
        "room_profiles": {
            GLOBAL_CHATROOM_ID: global_profile,
        },
        "identity_presets": [default_preset],
        "muted_chatrooms": [],
        "unread_counts": {},
        "room_state": {},
        "history": {},
        "muted_users": {},
        "trusted_image_users": {},
        "collapsed_messages": {},
        "sent_message_utc_day": current_utc_day_number(),
        "sent_messages_today": 0,
    }


def _load_dpapi_config(encrypted: bytes) -> dict[str, Any]:
    # Current entropy first, then prior development-build values for migration.
    entropies = (
        DPAPI_ENTROPY,
        b"EncryptedChatClient-v2-config",
        b"EncryptedChatClient-v1-config",
    )

    last_error: Exception | None = None

    for entropy in entropies:
        try:
            decoded = dpapi_decrypt(encrypted, entropy)
            loaded = json.loads(decoded.decode("utf-8"))
            if isinstance(loaded, dict):
                return loaded
        except Exception as exc:
            last_error = exc

    if last_error:
        raise last_error

    raise ValueError("Could not decode settings.")


def load_config() -> dict[str, Any]:
    config = default_config()

    if CONFIG_PATH.exists():
        try:
            loaded = _load_dpapi_config(CONFIG_PATH.read_bytes())
            config.update(loaded)
        except Exception:
            pass

    try:
        previous_config_version = int(config.get("config_version", 0) or 0)
    except (TypeError, ValueError):
        previous_config_version = 0

    if previous_config_version < 15:
        config["message_sound"] = (
            DEFAULT_MESSAGE_SOUND
            if bool(config.get("chime_enabled", True))
            else "Disabled"
        )
    message_sound = str(
        config.get("message_sound", DEFAULT_MESSAGE_SOUND)
    )
    config["message_sound"] = (
        message_sound
        if message_sound in MESSAGE_SOUND_OPTIONS
        else DEFAULT_MESSAGE_SOUND
    )
    try:
        message_sound_volume = int(config.get(
            "message_sound_volume",
            DEFAULT_MESSAGE_SOUND_VOLUME,
        ))
    except (TypeError, ValueError):
        message_sound_volume = DEFAULT_MESSAGE_SOUND_VOLUME
    config["message_sound_volume"] = max(
        10,
        min(100, ((message_sound_volume + 5) // 10) * 10),
    )
    custom_message_sound_path = config.get(
        "custom_message_sound_path",
        "",
    )
    config["custom_message_sound_path"] = (
        custom_message_sound_path
        if isinstance(custom_message_sound_path, str)
        else ""
    )
    config["automatic_update_checks"] = bool(
        config.get("automatic_update_checks", True)
    )
    config.pop("chime_enabled", None)

    config["identity_private_key"] = normalize_identity_private_key(
        config.get("identity_private_key")
    )
    config.pop("client_id", None)
    config.pop("identities", None)

    if not isinstance(config.get("room_state"), dict):
        config["room_state"] = {}

    if not isinstance(config.get("history"), dict):
        config["history"] = {}

    if not isinstance(config.get("muted_users"), dict):
        config["muted_users"] = {}

    if not isinstance(config.get("trusted_image_users"), dict):
        config["trusted_image_users"] = {}

    if not isinstance(config.get("collapsed_messages"), dict):
        config["collapsed_messages"] = {}

    today_utc = current_utc_day_number()
    try:
        sent_message_utc_day = int(
            config.get("sent_message_utc_day", today_utc)
        )
    except (TypeError, ValueError):
        sent_message_utc_day = today_utc
    try:
        sent_messages_today = max(
            0,
            int(config.get("sent_messages_today", 0)),
        )
    except (TypeError, ValueError):
        sent_messages_today = 0
    if sent_message_utc_day != today_utc:
        sent_message_utc_day = today_utc
        sent_messages_today = 0
    config["sent_message_utc_day"] = sent_message_utc_day
    config["sent_messages_today"] = sent_messages_today

    chatrooms = config.get("chatrooms")
    if not isinstance(chatrooms, list):
        config["chatrooms"] = []
    else:
        cleaned_chatrooms = []
        seen_ids: set[str] = set()
        seen_keys = {GLOBAL_CHATROOM_KEY}
        for room in chatrooms:
            if not isinstance(room, dict):
                continue
            room_id = str(room.get("id", "")).strip()
            nickname = str(room.get("nickname", "")).strip()
            key = str(room.get("key", ""))
            if (
                not room_id
                or room_id == GLOBAL_CHATROOM_ID
                or room_id in seen_ids
                or not nickname
                or not key
                or key in seen_keys
            ):
                continue
            seen_ids.add(room_id)
            seen_keys.add(key)
            cleaned_chatrooms.append({
                "id": room_id,
                "nickname": nickname[:64],
                "key": key,
            })
        config["chatrooms"] = cleaned_chatrooms

    valid_room_ids = {
        GLOBAL_CHATROOM_ID,
        *(room["id"] for room in config["chatrooms"]),
    }
    active_chatroom_id = str(
        config.get("active_chatroom_id", GLOBAL_CHATROOM_ID)
    )
    if active_chatroom_id not in valid_room_ids:
        active_chatroom_id = GLOBAL_CHATROOM_ID
    config["active_chatroom_id"] = active_chatroom_id

    raw_profiles = config.get("room_profiles")
    if not isinstance(raw_profiles, dict):
        raw_profiles = {}

    if previous_config_version < 11:
        legacy_username = config.get("username", "User")
        legacy_username_color = config.get(
            "username_color",
            secrets.choice(SAFE_USERNAME_COLORS),
        )
        if previous_config_version < 3:
            if legacy_username == "Anonymous":
                legacy_username = "User"
            if legacy_username_color == "#4ea1ff":
                legacy_username_color = secrets.choice(SAFE_USERNAME_COLORS)
        raw_profiles = dict(raw_profiles)
        raw_profiles[GLOBAL_CHATROOM_ID] = {
            "username": legacy_username,
            "username_color": legacy_username_color,
            "font": DEFAULT_MESSAGE_FONT,
            "text_color": DEFAULT_MESSAGE_TEXT_COLOR,
        }

    global_profile = normalize_room_profile(
        raw_profiles.get(GLOBAL_CHATROOM_ID)
    )
    cleaned_profiles = {
        GLOBAL_CHATROOM_ID: global_profile,
    }
    for room_id in valid_room_ids - {GLOBAL_CHATROOM_ID}:
        cleaned_profiles[room_id] = normalize_room_profile(
            raw_profiles.get(room_id),
            global_profile,
        )

    raw_identity_presets = (
        config.get("identity_presets", [])
        if previous_config_version >= 14
        else []
    )
    if not isinstance(raw_identity_presets, list):
        raw_identity_presets = []
    identity_presets: list[dict[str, str]] = []
    seen_preset_ids: set[str] = set()
    for raw_preset in raw_identity_presets:
        preset = normalize_identity_preset(raw_preset)
        if preset["id"] in seen_preset_ids:
            continue
        seen_preset_ids.add(preset["id"])
        identity_presets.append(preset)
        if len(identity_presets) >= MAX_IDENTITY_PRESETS:
            break

    ordered_room_ids = [
        GLOBAL_CHATROOM_ID,
        *sorted(valid_room_ids - {GLOBAL_CHATROOM_ID}),
    ]
    for room_id in ordered_room_ids:
        profile = cleaned_profiles[room_id]
        selected_id = profile.get("identity_preset_id", "")
        selected_preset = next((
            preset for preset in identity_presets
            if preset["id"] == selected_id
        ), None)
        if selected_preset is None:
            signature = identity_signature(profile)
            selected_preset = next((
                preset for preset in identity_presets
                if identity_signature(preset) == signature
            ), None)
        if (
            selected_preset is None
            and len(identity_presets) < MAX_IDENTITY_PRESETS
        ):
            selected_preset = normalize_identity_preset({
                "username": profile["username"],
                "username_color": profile["username_color"],
                "profile_icon": profile["profile_icon"],
            })
            identity_presets.append(selected_preset)
        if selected_preset is None:
            if not identity_presets:
                identity_presets.append(default_identity_preset())
            selected_preset = identity_presets[0]
        apply_identity_preset_to_profile(profile, selected_preset)

    config["room_profiles"] = cleaned_profiles
    config["identity_presets"] = identity_presets
    config.pop("username", None)
    config.pop("username_color", None)

    config.pop("anti_aliased_text", None)
    theme = str(config.get("theme", DEFAULT_THEME))
    if theme == LEGACY_BASIC_THEME:
        theme = DEFAULT_THEME
    config["theme"] = theme if theme in THEMES else DEFAULT_THEME

    muted_chatrooms = config.get("muted_chatrooms")
    if not isinstance(muted_chatrooms, list):
        muted_chatrooms = []
    config["muted_chatrooms"] = sorted({
        str(room_id)
        for room_id in muted_chatrooms
        if str(room_id) in valid_room_ids
    })

    unread_counts = config.get("unread_counts")
    if not isinstance(unread_counts, dict):
        unread_counts = {}
    cleaned_unread_counts: dict[str, int] = {}
    for room_id in valid_room_ids:
        try:
            unread_count = max(
                0,
                int(unread_counts.get(room_id, 0) or 0),
            )
        except (TypeError, ValueError):
            continue
        if unread_count:
            cleaned_unread_counts[room_id] = unread_count
    config["unread_counts"] = cleaned_unread_counts

    # Chatroom keys now live only in the Chatrooms sidebar. The former single
    # key is deliberately not converted into a custom chatroom.
    config.pop("encryption_key", None)

    # Migrate the exact default from v1 to the public ntfy server.
    old_preset = str(config.get("server_preset", ""))
    old_url = str(config.get("server_url", ""))
    if (
        old_preset == "Localhost"
        and old_url.rstrip("/") == "http://127.0.0.1:8000"
    ):
        config["server_preset"] = DEFAULT_SERVER_PRESET
        config["server_url"] = DEFAULT_SERVER_URL

    # Old numeric-cursor state is not compatible with ntfy message IDs.
    config.pop("server_cursors", None)
    config.pop("last_server_id", None)
    config["config_version"] = CONFIG_FORMAT_VERSION

    return config


def save_config(config: dict[str, Any]) -> None:
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(
        config,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")

    encrypted = dpapi_encrypt(raw)
    temp_path = CONFIG_PATH.with_suffix(".tmp")
    temp_path.write_bytes(encrypted)
    os.replace(temp_path, CONFIG_PATH)


def normalize_server_url(url: str) -> str:
    return url.strip().rstrip("/")


@lru_cache(maxsize=16)
def derive_ntfy_topic(passphrase: str) -> str:
    """
    Derive a stable, opaque topic from the shared room key.

    Scrypt slows down casual dictionary guessing of the topic when a weak
    human-readable key is used. A strong room key is still recommended.
    """
    if not passphrase:
        raise ValueError("The encryption key is empty.")

    topic_bytes = hashlib.scrypt(
        passphrase.encode("utf-8"),
        salt=b"EncryptedChatClient-v2-ntfy-topic",
        n=2**14,
        r=8,
        p=1,
        dklen=24,
    )

    encoded = base64.b32encode(topic_bytes).decode("ascii").lower().rstrip("=")
    return f"e2chat-{encoded}"


def room_scope_id(server_url: str, passphrase: str) -> str:
    topic = derive_ntfy_topic(passphrase)
    material = (normalize_server_url(server_url) + "\0" + topic).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def derive_message_key(passphrase: str, salt: bytes) -> bytes:
    if not passphrase:
        raise ValueError("The encryption key is empty.")

    return hashlib.scrypt(
        passphrase.encode("utf-8"),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
        dklen=32,
    )


def make_opaque_packet(message: dict[str, Any], passphrase: str) -> str:
    """
    Compress, length-prefix, randomly pad, and encrypt one message.

    Binary layout:
      1 byte  packet version
      16 bytes random scrypt salt
      12 bytes random ChaCha20-Poly1305 nonce
      remaining authenticated ciphertext

    The plaintext is padded to a 128-byte boundary so the exact text length is
    less obvious. The final packet is URL-safe Base64 for ntfy text transport.
    """
    raw_json = json.dumps(
        message,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")

    compressed = zlib.compress(raw_json, level=9)
    framed = len(compressed).to_bytes(4, "big") + compressed

    padded_length = (
        (len(framed) + PACKET_PADDING_BLOCK - 1)
        // PACKET_PADDING_BLOCK
    ) * PACKET_PADDING_BLOCK

    if padded_length == len(framed):
        padded_length += PACKET_PADDING_BLOCK

    padded = framed + secrets.token_bytes(padded_length - len(framed))

    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(12)
    key = derive_message_key(passphrase, salt)

    ciphertext = ChaCha20Poly1305(key).encrypt(nonce, padded, None)
    packet = bytes([APP_VERSION]) + salt + nonce + ciphertext

    return base64.urlsafe_b64encode(packet).decode("ascii").rstrip("=")


def estimate_opaque_packet_size(message: dict[str, Any]) -> int:
    """
    Calculate the exact UTF-8 byte length of the packet that make_opaque_packet()
    will produce, without running Scrypt or encryption.

    ChaCha20-Poly1305 ciphertext is always plaintext length + 16 bytes, and all
    other packet fields have fixed sizes, so the final unpadded Base64 length is
    deterministic after JSON serialization, compression, and padding.
    """
    raw_json = json.dumps(
        message,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")

    compressed = zlib.compress(raw_json, level=9)
    framed_length = 4 + len(compressed)

    padded_length = (
        (framed_length + PACKET_PADDING_BLOCK - 1)
        // PACKET_PADDING_BLOCK
    ) * PACKET_PADDING_BLOCK

    if padded_length == framed_length:
        padded_length += PACKET_PADDING_BLOCK

    binary_packet_length = 1 + 16 + 12 + padded_length + 16

    full_base64_length = ((binary_packet_length + 2) // 3) * 4
    remainder = binary_packet_length % 3
    if remainder == 1:
        full_base64_length -= 2
    elif remainder == 2:
        full_base64_length -= 1

    return full_base64_length


def open_opaque_packet(packet_text: str, passphrase: str) -> dict[str, Any]:
    try:
        encoded_packet = packet_text.encode("ascii")
    except (AttributeError, UnicodeEncodeError) as exc:
        raise ValueError("Encrypted packet is not valid ASCII.") from exc
    if len(encoded_packet) > MAX_ENCRYPTED_PACKET_CHARS:
        raise ValueError("Encrypted packet is too large.")

    padding = b"=" * ((4 - len(encoded_packet) % 4) % 4)
    try:
        packet = base64.b64decode(
            encoded_packet + padding,
            altchars=b"-_",
            validate=True,
        )
    except Exception as exc:
        raise ValueError("Encrypted packet is not valid Base64.") from exc

    minimum_length = 1 + 16 + 12 + 16
    if len(packet) < minimum_length:
        raise ValueError("Encrypted packet is too short.")

    version = packet[0]
    if version != APP_VERSION:
        raise ValueError(f"Unsupported packet version: {version}")

    salt = packet[1:17]
    nonce = packet[17:29]
    ciphertext = packet[29:]

    key = derive_message_key(passphrase, salt)
    padded = ChaCha20Poly1305(key).decrypt(nonce, ciphertext, None)

    if len(padded) < 4:
        raise ValueError("Decrypted packet is malformed.")

    compressed_length = int.from_bytes(padded[:4], "big")
    if compressed_length <= 0 or compressed_length > len(padded) - 4:
        raise ValueError("Decrypted packet length is invalid.")

    compressed = padded[4:4 + compressed_length]
    decompressor = zlib.decompressobj()
    raw_json = decompressor.decompress(
        compressed,
        MAX_UNCOMPRESSED_MESSAGE_BYTES + 1,
    )
    if (
        len(raw_json) > MAX_UNCOMPRESSED_MESSAGE_BYTES
        or decompressor.unconsumed_tail
    ):
        raise ValueError("Decompressed message is too large.")
    if not decompressor.eof or decompressor.unused_data:
        raise ValueError("Compressed message data is malformed.")

    message = json.loads(raw_json.decode("utf-8"))

    if not isinstance(message, dict):
        raise ValueError("Decrypted message is not an object.")

    return message


def parse_ntfy_ndjson(response: requests.Response) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    for raw_line in response.text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue

        if isinstance(record, dict) and record.get("event") == "message":
            records.append(record)

    return records



class ValueModel:
    """Small get/set model used to keep network and UI state decoupled."""

    def __init__(self, value: Any) -> None:
        self._value = value
        self._listeners: list[Any] = []

    def get(self) -> Any:
        return self._value

    def set(self, value: Any) -> None:
        if value == self._value:
            return
        self._value = value
        for listener in tuple(self._listeners):
            listener(value)

    def bind(self, listener: Any) -> None:
        self._listeners.append(listener)
        listener(self._value)


class MessageBoxes:
    @staticmethod
    def showerror(title: str, text: str, parent: QWidget | None = None) -> None:
        QMessageBox.critical(parent, title, str(text))

    @staticmethod
    def showwarning(title: str, text: str, parent: QWidget | None = None) -> None:
        QMessageBox.warning(parent, title, str(text))

    @staticmethod
    def showinfo(title: str, text: str, parent: QWidget | None = None) -> None:
        QMessageBox.information(parent, title, str(text))

    @staticmethod
    def askyesno(title: str, text: str, parent: QWidget | None = None) -> bool:
        return QMessageBox.question(
            parent,
            title,
            str(text),
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes


messagebox = MessageBoxes()


class ComposeTextEdit(QPlainTextEdit):
    send_requested = Signal()

    def keyPressEvent(self, event: Any) -> None:
        if (
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.send_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class ClickableProgressBar(QProgressBar):
    clicked = Signal()

    def mouseReleaseEvent(self, event: Any) -> None:
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.rect().contains(event.position().toPoint())
        ):
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class ConfigOverlay(QWidget):
    dismissed = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.panel: QFrame | None = None
        self.setObjectName("configOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            "QWidget#configOverlay { background-color: rgba(0, 0, 0, 105); }"
        )

    def mousePressEvent(self, event: Any) -> None:
        if (
            self.panel is not None
            and not self.panel.geometry().contains(event.position().toPoint())
        ):
            self.dismissed.emit()
            event.accept()
            return
        super().mousePressEvent(event)


class AddChatroomDialog(QDialog):
    def __init__(
        self,
        parent: QWidget,
        *,
        title: str = "Add Chatroom",
        submit_label: str = "Add Chatroom",
        nickname: str = "",
        chatroom_key: str | None = None,
        history_note: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(430)

        layout = QVBoxLayout(self)
        form = QGridLayout()
        form.setColumnStretch(1, 1)

        form.addWidget(QLabel("Nickname"), 0, 0)
        self.nickname_entry = QLineEdit()
        self.nickname_entry.setMaxLength(64)
        self.nickname_entry.setText(nickname)
        form.addWidget(self.nickname_entry, 0, 1, 1, 2)

        form.addWidget(QLabel("Key"), 1, 0)
        self.key_entry = QLineEdit()
        self.key_entry.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_entry.setText(
            chatroom_key
            if chatroom_key is not None
            else generate_chatroom_key()
        )
        form.addWidget(self.key_entry, 1, 1)
        show_key = QCheckBox("Show")
        show_key.toggled.connect(
            lambda checked: self.key_entry.setEchoMode(
                QLineEdit.EchoMode.Normal
                if checked
                else QLineEdit.EchoMode.Password
            )
        )
        form.addWidget(show_key, 1, 2)

        key_hint = QLabel(
            "32+ characters recommended. Only share this key with others "
            "you want in the chatroom!"
            + (f" {history_note}" if history_note else "")
        )
        key_hint.setWordWrap(True)
        key_hint.setStyleSheet("color: #777777; font-size: 8pt;")
        form.addWidget(key_hint, 2, 1, 1, 2)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        buttons.addWidget(cancel_button)
        submit_button = QPushButton(submit_label)
        submit_button.setDefault(True)
        submit_button.clicked.connect(self.accept)
        buttons.addWidget(submit_button)
        layout.addLayout(buttons)

        self.nickname_entry.setFocus()

    def nickname(self) -> str:
        return self.nickname_entry.text().strip()

    def chatroom_key(self) -> str:
        return self.key_entry.text()


class ChatroomListRow(QWidget):
    def __init__(
        self,
        nickname: str,
        unread_count: int,
        muted: bool,
    ) -> None:
        super().__init__()
        self.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 1, 4, 1)
        layout.setSpacing(6)

        nickname_label = QLabel(nickname)
        layout.addWidget(nickname_label, 1)

        unread_label = QLabel(f"({unread_count})")
        unread_label.setStyleSheet(
            "color: #d97706; font-weight: 600;"
        )
        unread_label.setVisible(unread_count > 0 and not muted)
        layout.addWidget(unread_label)

        if muted:
            opacity = QGraphicsOpacityEffect(self)
            opacity.setOpacity(0.45)
            self.setGraphicsEffect(opacity)


class ChatroomListWidget(QListWidget):
    roomMoveRequested = Signal(str, int)
    dragFinished = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(
            QAbstractItemView.DragDropMode.InternalMove
        )
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._pressed_room_id: str | None = None
        self._dragged_room_id: str | None = None
        self._dragged_row = -1

    def mousePressEvent(self, event: Any) -> None:
        is_left_press = event.button() == Qt.MouseButton.LeftButton
        item = (
            self.itemAt(event.position().toPoint())
            if is_left_press
            else None
        )
        if is_left_press and item is None:
            event.accept()
            return
        if is_left_press and item is not None:
            self._pressed_room_id = str(
                item.data(Qt.ItemDataRole.UserRole)
            )
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: Any) -> None:
        if (
            event.buttons() & Qt.MouseButton.LeftButton
            and self._pressed_room_id == GLOBAL_CHATROOM_ID
        ):
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: Any) -> None:
        try:
            super().mouseReleaseEvent(event)
        finally:
            if event.button() == Qt.MouseButton.LeftButton:
                self._pressed_room_id = None

    def startDrag(self, supported_actions: Any) -> None:
        item = self.currentItem()
        if item is None:
            return
        room_id = str(item.data(Qt.ItemDataRole.UserRole))
        if room_id == GLOBAL_CHATROOM_ID:
            return
        self._dragged_room_id = room_id
        self._dragged_row = self.row(item)
        try:
            mime_data = self.model().mimeData([self.currentIndex()])
            if mime_data is None:
                return
            drag = QDrag(self)
            drag.setMimeData(mime_data)
            transparent_drag_image = QPixmap(1, 1)
            transparent_drag_image.fill(Qt.GlobalColor.transparent)
            drag.setPixmap(transparent_drag_image)
            drag.setHotSpot(QPoint(0, 0))
            drag.exec(
                supported_actions,
                Qt.DropAction.MoveAction,
            )
        finally:
            self._pressed_room_id = None
            self._dragged_room_id = None
            self._dragged_row = -1
            self.dragFinished.emit()

    def dropEvent(self, event: Any) -> None:
        if self._dragged_room_id is None or self._dragged_row < 1:
            event.ignore()
            return

        target_row = self.indexAt(event.position().toPoint()).row()
        if target_row < 0:
            insertion_row = self.count()
        elif self.dropIndicatorPosition() in (
            QAbstractItemView.DropIndicatorPosition.BelowItem,
            QAbstractItemView.DropIndicatorPosition.OnViewport,
        ):
            insertion_row = target_row + 1
        else:
            insertion_row = target_row

        insertion_row = max(1, min(self.count(), insertion_row))
        if self._dragged_row < insertion_row:
            insertion_row -= 1
        if insertion_row != self._dragged_row:
            room_id = self._dragged_room_id
            QTimer.singleShot(
                0,
                lambda: self.roomMoveRequested.emit(
                    room_id,
                    insertion_row,
                ),
            )
        # The configuration/list rebuild performs the move. Report Ignore to
        # Qt's InternalMove source so it does not also delete the dragged row.
        event.setDropAction(Qt.DropAction.IgnoreAction)
        event.accept()


class ThemeComboBox(QComboBox):
    """Draw a guaranteed-visible Classic arrow above Qt's styled control."""

    def paintEvent(self, event: Any) -> None:
        super().paintEvent(event)
        app = QApplication.instance()
        if app is None or not bool(
            app.property("spritelinkWindowsClassic")
        ):
            return

        center_x = self.width() - 11
        center_y = self.height() // 2
        arrow = QPolygon([
            QPoint(center_x - 4, center_y - 2),
            QPoint(center_x + 4, center_y - 2),
            QPoint(center_x, center_y + 3),
        ])
        painter = QPainter(self)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#000000"))
        painter.drawPolygon(arrow)
        painter.end()


class IdentityPresetSelector(QPushButton):
    presetSelected = Signal(str)
    removeRequested = Signal(str)
    newRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(130)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._suppress_next_open = False
        self.clicked.connect(self._toggle_popup)

        self.popup = QFrame(self, Qt.WindowType.Popup)
        self.popup.installEventFilter(self)
        self.popup.setFrameShape(QFrame.Shape.StyledPanel)
        popup_layout = QVBoxLayout(self.popup)
        popup_layout.setContentsMargins(4, 4, 4, 4)
        popup_layout.setSpacing(4)

        self.preset_list = QListWidget()
        self.preset_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.preset_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.preset_list.setIconSize(QSize(16, 16))
        self.preset_list.setSpacing(0)
        self.preset_list.setUniformItemSizes(True)
        self.preset_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.preset_list.itemClicked.connect(self._select_item)
        self.preset_list.customContextMenuRequested.connect(
            self._show_item_context_menu
        )
        popup_layout.addWidget(self.preset_list)

        new_button = QPushButton("New Identity")
        new_button.clicked.connect(self._request_new_identity)
        popup_layout.addWidget(new_button)

    @staticmethod
    def _preset_icon(encoded_icon: str) -> QIcon | None:
        if not encoded_icon:
            return None
        try:
            gif_data = decode_profile_icon(encoded_icon)
        except ValueError:
            return None
        pixmap = QPixmap()
        if not pixmap.loadFromData(gif_data):
            return None
        pixmap.setDevicePixelRatio(1.0)
        return QIcon(pixmap)

    def set_presets(
        self,
        presets: list[dict[str, str]],
        selected_id: str,
    ) -> None:
        self.preset_list.clear()
        selected_preset: dict[str, str] | None = None
        for preset in presets:
            item = QListWidgetItem(preset.get("username", "User"))
            item.setSizeHint(QSize(0, self._preset_row_height()))
            preset_icon = self._preset_icon(
                preset.get("profile_icon", "")
            )
            if preset_icon is not None:
                item.setIcon(preset_icon)
            item.setData(Qt.ItemDataRole.UserRole, preset["id"])
            item.setForeground(QColor(preset["username_color"]))
            self.preset_list.addItem(item)
            if preset["id"] == selected_id:
                selected_preset = preset
                self.preset_list.setCurrentItem(item)

        if selected_preset is None and presets:
            selected_preset = presets[0]
        if selected_preset is None:
            self.setText("Identity ▼")
            self.setIcon(QIcon())
            self.setStyleSheet("")
            return
        self.setText(f"{selected_preset['username']} ▼")
        selected_icon = self._preset_icon(
            selected_preset.get("profile_icon", "")
        )
        self.setIcon(selected_icon or QIcon())
        self.setStyleSheet(
            f"color: {selected_preset['username_color']};"
        )
        if self.popup.isVisible():
            self._resize_popup_to_contents()

    def _preset_row_height(self) -> int:
        return max(
            20,
            self.preset_list.fontMetrics().height() + 4,
            self.preset_list.iconSize().height() + 4,
        )

    def _toggle_popup(self) -> None:
        if self._suppress_next_open:
            self._suppress_next_open = False
            return
        if self.popup.isVisible():
            self.popup.hide()
            self._suppress_next_open = False
            return
        self._show_popup()

    def _show_popup(self) -> None:
        self._resize_popup_to_contents()
        popup_position = self.mapToGlobal(QPoint(0, self.height()))
        screen = QApplication.screenAt(popup_position)
        if (
            screen is not None
            and popup_position.y() + self.popup.height()
            > screen.availableGeometry().bottom()
        ):
            popup_position = self.mapToGlobal(
                QPoint(0, -self.popup.height())
            )
        self.popup.move(popup_position)
        self.popup.show()
        self.popup.raise_()

    def _resize_popup_to_contents(self) -> None:
        row_count = max(1, min(7, self.preset_list.count()))
        list_height = (
            self.preset_list.frameWidth() * 2
            + row_count * self._preset_row_height()
        )
        self.preset_list.setFixedHeight(list_height)
        self.popup.setFixedWidth(max(230, self.width()))
        self.popup.adjustSize()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.popup and event.type() == QEvent.Type.Hide:
            cursor_position = self.mapFromGlobal(QCursor.pos())
            self._suppress_next_open = self.rect().contains(cursor_position)
        return super().eventFilter(watched, event)

    def _select_item(self, item: QListWidgetItem) -> None:
        preset_id = str(item.data(Qt.ItemDataRole.UserRole))
        self.popup.hide()
        self.presetSelected.emit(preset_id)

    def _show_item_context_menu(self, position: QPoint) -> None:
        item = self.preset_list.itemAt(position)
        if item is None:
            return
        preset_id = str(item.data(Qt.ItemDataRole.UserRole))
        menu = QMenu(self.popup)
        remove_action = menu.addAction("Remove")
        selected = menu.exec(self.preset_list.mapToGlobal(position))
        if selected is remove_action:
            self.removeRequested.emit(preset_id)
            self._suppress_next_open = False
            if not self.popup.isVisible():
                self._show_popup()

    def _request_new_identity(self) -> None:
        self.popup.hide()
        self.newRequested.emit()


class MessageLogBrowser(QTextBrowser):
    """Complete full-width row selections across paragraph left margins."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.row_background_blocks: dict[int, QColor] = {}
        self.collapsed_fade_blocks: dict[int, QColor] = {}
        self.horizontalScrollBar().rangeChanged.connect(
            self._lock_horizontal_scroll
        )
        self.horizontalScrollBar().valueChanged.connect(
            self._lock_horizontal_scroll
        )
        self._lock_horizontal_scroll()

    def _lock_horizontal_scroll(self, *_args: Any) -> None:
        scrollbar = self.horizontalScrollBar()
        signals_were_blocked = scrollbar.blockSignals(True)
        scrollbar.setRange(0, 0)
        scrollbar.setValue(0)
        scrollbar.blockSignals(signals_were_blocked)

    def scrollContentsBy(self, _dx: int, dy: int) -> None:
        self._lock_horizontal_scroll()
        super().scrollContentsBy(0, dy)

    def paintEvent(self, event: Any) -> None:
        super().paintEvent(event)
        if not self.row_background_blocks and not self.collapsed_fade_blocks:
            return

        viewport = self.viewport()
        viewport_width = viewport.width()
        viewport_height = viewport.height()
        if viewport_width <= 0 or viewport_height <= 0:
            return

        paint_rect = event.rect()
        first_block = self.cursorForPosition(QPoint(
            0,
            max(0, paint_rect.top()),
        )).block().blockNumber()
        last_block = self.cursorForPosition(QPoint(
            max(0, viewport_width - 1),
            min(viewport_height - 1, paint_rect.bottom()),
        )).block().blockNumber()
        first_block = max(0, first_block - 1)
        last_block = min(
            self.document().blockCount() - 1,
            last_block + 1,
        )

        painter = QPainter(viewport)
        painter.setClipRegion(event.region())
        document_layout = self.document().documentLayout()
        fade_start = round(viewport_width * 0.55)
        fade_end = max(fade_start + 1, round(viewport_width * 0.98))
        fade_brushes: dict[int, QBrush] = {}

        for block_number in range(first_block, last_block + 1):
            background = self.row_background_blocks.get(block_number)
            fade_background = self.collapsed_fade_blocks.get(block_number)
            if background is None and fade_background is None:
                continue

            block = self.document().findBlockByNumber(block_number)
            if not block.isValid():
                continue
            block_cursor = QTextCursor(block)
            cursor_rect = self.cursorRect(block_cursor)
            top = cursor_rect.top()
            height = max(
                1,
                round(document_layout.blockBoundingRect(block).height()),
            )
            if top > paint_rect.bottom() or top + height < paint_rect.top():
                continue

            if background is not None:
                left_width = max(0, cursor_rect.left())
                if left_width > 0:
                    painter.fillRect(
                        0,
                        top,
                        left_width,
                        height + 1,
                        background,
                    )

            if fade_background is not None:
                color_key = int(fade_background.rgba())
                fade_brush = fade_brushes.get(color_key)
                if fade_brush is None:
                    transparent = QColor(fade_background)
                    transparent.setAlpha(0)
                    opaque = QColor(fade_background)
                    opaque.setAlpha(255)
                    gradient = QLinearGradient(
                        fade_start,
                        0,
                        fade_end,
                        0,
                    )
                    gradient.setColorAt(0.0, transparent)
                    gradient.setColorAt(1.0, opaque)
                    fade_brush = QBrush(gradient)
                    fade_brushes[color_key] = fade_brush
                painter.fillRect(
                    fade_start,
                    top,
                    max(0, viewport_width - fade_start),
                    height + 1,
                    fade_brush,
                )
        painter.end()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.close_callback: Any = None

    def closeEvent(self, event: Any) -> None:
        if self.close_callback is not None:
            self.close_callback()
        event.accept()


class EncryptedChatClient(QObject):
    def __init__(self, root: MainWindow) -> None:
        super().__init__(root)
        self.root = root
        self.root.setWindowTitle("SpriteLink")
        self.root.resize(840, 650)
        self.root.setMinimumSize(670, 500)
        self.root.installEventFilter(self)

        self.config_data = load_config()
        # Persist a newly generated or migrated signing identity before any
        # messages are created so the authenticated ID survives a crash.
        save_config(self.config_data)
        app = QApplication.instance()
        self._basic_style_name = (
            app.style().objectName() if app is not None else "Fusion"
        )
        self._basic_palette = (
            QPalette(app.palette()) if app is not None else QPalette()
        )
        self._basic_application_stylesheet = (
            app.styleSheet() if app is not None else ""
        )
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": f"{APP_NAME}/{CONFIG_FORMAT_VERSION}",
            "Accept": "application/json, application/x-ndjson",
        })

        self.stop_event = threading.Event()
        self.window_focused_event = threading.Event()
        self.window_focused_event.set()
        self.network_control_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self.ui_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.send_queue: queue.Queue[dict[str, Any]] = queue.Queue()

        self.network_thread: threading.Thread | None = None
        self.connected = False

        self.seen_client_message_ids: set[str] = set()
        self.seen_ntfy_message_ids: set[str] = set()
        self.message_log: list[dict[str, Any]] = []
        self.draft_message_id = uuid.uuid4().hex
        self.current_estimated_packet_size = 0
        self.message_size_check_pending = False
        self.rendered_message_items: dict[str, dict[str, Any]] = {}
        self.rendered_tooltips: dict[str, str] = {}
        self.rendered_image_links: dict[str, str] = {}
        self.image_preview_cache: dict[str, QImage | None] = {}
        self.pending_image_previews: set[str] = set()
        self.current_image_preview_url: str | None = None
        self.recent_chatroom_switch_times: list[float] = []
        self.image_fetch_executor = ThreadPoolExecutor(
            max_workers=3,
            thread_name_prefix="SpriteLinkImage",
        )
        self._hovered_message_id: str | None = None
        self._pending_tooltip_message_id: str | None = None
        self._pending_tooltip_global_position = QPoint()
        self._config_snapshot_at_open: tuple[Any, ...] | None = None
        self._loading_profile_controls = False
        self.active_chatroom_id = str(
            self.config_data.get("active_chatroom_id", GLOBAL_CHATROOM_ID)
        )
        self._update_window_title()
        cleared_stale_unread = (
            self.config_data.setdefault("unread_counts", {}).pop(
                self.active_chatroom_id,
                None,
            )
            is not None
        )
        self.initial_history_pending_rooms = {
            room["id"] for room in self._chatroom_definitions()
        }
        if cleared_stale_unread:
            try:
                save_config(self.config_data)
            except Exception:
                pass
        self._closing = False
        self.available_update: ReleaseInfo | None = None
        self._update_check_in_progress = False
        self._update_download_in_progress = False
        self._last_update_check_started_at = 0.0

        self.server_preset_var = ValueModel(
            self.config_data["server_preset"]
        )
        self.server_url_var = ValueModel(
            self.config_data["server_url"]
        )
        self.theme_var = ValueModel(
            self.config_data.get("theme", DEFAULT_THEME)
        )
        self.message_sound_var = ValueModel(
            str(self.config_data["message_sound"])
        )
        self.message_sound_volume_var = ValueModel(
            int(self.config_data["message_sound_volume"])
        )
        self.custom_message_sound_path_var = ValueModel(
            str(self.config_data["custom_message_sound_path"])
        )
        self.automatic_update_checks_var = ValueModel(
            bool(self.config_data.get("automatic_update_checks", True))
        )
        self.status_var = ValueModel("Connecting")

        self.profile_save_timer = QTimer(self)
        self.profile_save_timer.setSingleShot(True)
        self.profile_save_timer.timeout.connect(
            self._persist_profile_changes
        )

        self.message_resize_timer = QTimer(self)
        self.message_resize_timer.setSingleShot(True)
        self.message_resize_timer.timeout.connect(self._resize_message_entry)

        self.message_size_timer = QTimer(self)
        self.message_size_timer.setSingleShot(True)
        self.message_size_timer.timeout.connect(self._run_message_size_check)

        self.message_limit_reset_timer = QTimer(self)
        self.message_limit_reset_timer.setSingleShot(True)
        self.message_limit_reset_timer.timeout.connect(
            self._reset_daily_sent_message_count
        )

        self.message_sound_stop_timer = QTimer(self)
        self.message_sound_stop_timer.setSingleShot(True)
        self.message_sound_stop_timer.timeout.connect(
            self._stop_message_sound
        )

        self.update_check_timer = QTimer(self)
        self.update_check_timer.setInterval(UPDATE_CHECK_INTERVAL_MS)
        self.update_check_timer.timeout.connect(
            self._maybe_check_for_updates
        )
        self.message_sound_effects: dict[str, QSoundEffect] = {}
        self.active_message_sound_effect: QSoundEffect | None = None
        self.pending_message_sound_effect: QSoundEffect | None = None
        self.pending_message_sound_name = ""
        self.pending_message_sound_report_errors = False
        for filename in BUILTIN_MESSAGE_SOUND_FILES.values():
            self._message_sound_effect_for_path(
                Path(__file__).resolve().parent / "sounds" / filename
            )
        self.compressed_message_sound_audio_output: (
            QAudioOutput | None
        ) = None
        self.compressed_message_sound_player: QMediaPlayer | None = None
        self.compressed_message_sound_name = ""
        self.compressed_message_sound_report_errors = False

        self.chat_tooltip_timer = QTimer(self)
        self.chat_tooltip_timer.setSingleShot(True)
        self.chat_tooltip_timer.timeout.connect(
            self._show_pending_chat_tooltip
        )

        self.ui_queue_timer = QTimer(self)
        self.ui_queue_timer.timeout.connect(self._process_ui_queue)
        self.ui_queue_timer.start(100)

        self._apply_theme()
        self._apply_application_font_strategy()
        self._build_ui()
        self._schedule_utc_midnight_reset()
        self._apply_server_preset_state()
        self._load_saved_history_for_current_room()

        self.root.close_callback = self._on_close
        QTimer.singleShot(0, self._apply_titlebar_theme)
        QTimer.singleShot(3000, self._maybe_check_for_updates)
        self.update_check_timer.start()
        self._start_network_thread()

    def _font_style_strategy(self) -> QFont.StyleStrategy:
        return (
            QFont.StyleStrategy.NoAntialias
            if self._is_windows_classic_theme()
            else QFont.StyleStrategy.PreferDefault
        )

    def _resolved_font_family(self, font_name: str) -> str:
        if font_name == "System":
            app = QApplication.instance()
            if app is not None:
                return app.font().family()
        return (
            font_name
            if font_name in SUPPORTED_MESSAGE_FONTS
            else DEFAULT_MESSAGE_FONT
        )

    def _make_font(
        self,
        font_name: str,
        point_size: int,
        *,
        bold: bool = False,
    ) -> QFont:
        font = QFont(self._resolved_font_family(font_name), point_size)
        font.setBold(bold)
        font.setStyleStrategy(self._font_style_strategy())
        return font

    def _make_message_font(
        self,
        font_name: str,
        *,
        bold: bool = False,
    ) -> QFont:
        point_size = MESSAGE_FONT_POINT_SIZES.get(font_name, 14)
        return self._make_font(
            font_name,
            point_size,
            bold=bold,
        )

    def _apply_application_font_strategy(self) -> None:
        app = QApplication.instance()
        if app is None:
            return

        strategy = self._font_style_strategy()
        application_font = QFont(app.font())
        application_font.setStyleStrategy(strategy)
        app.setFont(application_font)
        for widget in app.allWidgets():
            widget_font = QFont(widget.font())
            widget_font.setStyleStrategy(strategy)
            widget.setFont(widget_font)
        self._refresh_message_font_combo_fonts()

    def _is_windows_classic_theme(self) -> bool:
        return self.theme_var.get() == "Windows Classic"

    def _windows_classic_palette(self) -> QPalette:
        palette = QPalette()
        colors = {
            QPalette.ColorRole.Window: "#c0c0c0",
            QPalette.ColorRole.WindowText: "#000000",
            QPalette.ColorRole.Base: "#ffffff",
            QPalette.ColorRole.AlternateBase: "#dfdfdf",
            QPalette.ColorRole.ToolTipBase: "#ffffe1",
            QPalette.ColorRole.ToolTipText: "#000000",
            QPalette.ColorRole.Text: "#000000",
            QPalette.ColorRole.Button: "#c0c0c0",
            QPalette.ColorRole.ButtonText: "#000000",
            QPalette.ColorRole.BrightText: "#ffffff",
            QPalette.ColorRole.Light: "#ffffff",
            QPalette.ColorRole.Midlight: "#dfdfdf",
            QPalette.ColorRole.Mid: "#a0a0a0",
            QPalette.ColorRole.Dark: "#808080",
            QPalette.ColorRole.Shadow: "#000000",
            QPalette.ColorRole.Highlight: "#007f82",
            QPalette.ColorRole.HighlightedText: "#000000",
            QPalette.ColorRole.Link: "#007f82",
            QPalette.ColorRole.LinkVisited: "#800080",
        }
        for role, color in colors.items():
            palette.setColor(role, QColor(color))
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.Text,
            QColor("#808080"),
        )
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.ButtonText,
            QColor("#808080"),
        )
        return palette

    def _config_panel_stylesheet(self) -> str:
        if self._is_windows_classic_theme():
            return (
                "QFrame#configPanel { background: #c0c0c0;"
                " border-top: 2px solid #ffffff;"
                " border-left: 2px solid #ffffff;"
                " border-right: 2px solid #000000;"
                " border-bottom: 2px solid #000000;"
                " border-radius: 0px; }"
            )
        return (
            "QFrame#configPanel { background: palette(window); "
            "border: 1px solid palette(mid); border-radius: 3px; }"
        )

    @staticmethod
    def _windows_colorref(color: str) -> int:
        qt_color = QColor(color)
        return (
            qt_color.red()
            | (qt_color.green() << 8)
            | (qt_color.blue() << 16)
        )

    def _apply_window_titlebar_theme(self, window: QWidget) -> None:
        if os.name != "nt" or not hasattr(ctypes, "windll"):
            return

        try:
            hwnd = wintypes.HWND(int(window.winId()))
            dwmapi = ctypes.windll.dwmapi

            def set_attribute(attribute: int, value: int) -> None:
                data = ctypes.c_uint(value)
                dwmapi.DwmSetWindowAttribute(
                    hwnd,
                    ctypes.c_uint(attribute),
                    ctypes.byref(data),
                    ctypes.sizeof(data),
                )

            # Keep both themes light, then color the Windows 11 non-client
            # frame where the DWM color attributes are supported.
            set_attribute(20, 0)  # DWMWA_USE_IMMERSIVE_DARK_MODE
            if self._is_windows_classic_theme():
                border = "#000000"
                caption = "#000080"
                text = "#ffffff"
                corner_preference = 1  # DWMWCP_DONOTROUND
            else:
                border = "#d0d0d0"
                caption = "#f0f0f0"
                text = "#000000"
                corner_preference = 0  # DWMWCP_DEFAULT

            set_attribute(33, corner_preference)  # DWMWA_WINDOW_CORNER_PREFERENCE
            set_attribute(34, self._windows_colorref(border))
            set_attribute(35, self._windows_colorref(caption))
            set_attribute(36, self._windows_colorref(text))
        except Exception:
            # Older Windows versions do not expose the color attributes.
            pass

    def _apply_titlebar_theme(self) -> None:
        self._apply_window_titlebar_theme(self.root)

    def _apply_theme(self) -> None:
        app = QApplication.instance()
        if app is None:
            return

        if self._is_windows_classic_theme():
            available_styles = {
                name.casefold(): name for name in QStyleFactory.keys()
            }
            app.setStyle(
                available_styles.get(
                    "windows",
                    available_styles.get("fusion", "Fusion"),
                )
            )
            app.setPalette(self._windows_classic_palette())
            app.setStyleSheet(WINDOWS_CLASSIC_STYLESHEET)
        else:
            available_styles = {
                name.casefold(): name for name in QStyleFactory.keys()
            }
            basic_style = available_styles.get(
                self._basic_style_name.casefold()
            )
            if basic_style is not None:
                app.setStyle(basic_style)
            app.setPalette(QPalette(self._basic_palette))
            app.setStyleSheet(self._basic_application_stylesheet)

        app.setProperty(
            "spritelinkWindowsClassic",
            self._is_windows_classic_theme(),
        )
        for widget in app.allWidgets():
            if isinstance(widget, ThemeComboBox):
                widget.update()

        if hasattr(self, "config_panel"):
            self.config_panel.setStyleSheet(
                self._config_panel_stylesheet()
            )
        if hasattr(self, "message_limit_panel"):
            self.message_limit_panel.setStyleSheet(
                self._config_panel_stylesheet()
            )
        if hasattr(self, "image_preview_panel"):
            self.image_preview_panel.setStyleSheet(
                self._config_panel_stylesheet()
            )
        if hasattr(self, "message_size_bar"):
            self._draw_message_size_bar()
        if hasattr(self, "chatrooms_toggle"):
            self._update_chatrooms_toggle_unread_style()
        if hasattr(self, "config_toggle"):
            self._update_config_toggle_update_style()
        self._apply_titlebar_theme()

    def _heading(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setFont(self._make_font("Segoe UI", 10, bold=True))
        return label

    @staticmethod
    def _separator() -> QFrame:
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        return separator

    @staticmethod
    def _description(text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        return label

    def _build_ui(self) -> None:
        central = QWidget()
        central_layout = QHBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        self.root.setCentralWidget(central)

        self._build_chatroom_sidebar(central_layout)
        self.chat_tab = QWidget()
        central_layout.addWidget(self.chat_tab, 1)

        self._build_chat_tab()
        self._build_config_popup()
        self._build_message_limit_popup()
        self._build_image_preview_popup()

    def _build_chatroom_sidebar(self, root_layout: QHBoxLayout) -> None:
        self.chatrooms_panel = QWidget()
        self.chatrooms_panel.setFixedWidth(CHATROOM_SIDEBAR_WIDTH)
        panel_layout = QVBoxLayout(self.chatrooms_panel)
        panel_layout.setContentsMargins(6, 10, 8, 10)
        panel_layout.setSpacing(7)
        panel_layout.addWidget(self._heading("Chatrooms"))

        self.chatrooms_list = ChatroomListWidget()
        self.chatrooms_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.chatrooms_list.itemClicked.connect(
            lambda item: self._activate_chatroom(
                str(item.data(Qt.ItemDataRole.UserRole))
            )
        )
        self.chatrooms_list.roomMoveRequested.connect(
            self._move_chatroom
        )
        self.chatrooms_list.dragFinished.connect(
            self._refresh_chatroom_list
        )
        self.chatrooms_list.customContextMenuRequested.connect(
            self._show_chatroom_context_menu
        )
        panel_layout.addWidget(self.chatrooms_list, 1)

        add_chatroom_button = QPushButton("Add Chatroom")
        add_chatroom_button.clicked.connect(self._add_chatroom)
        panel_layout.addWidget(add_chatroom_button)

        self.chatrooms_panel.hide()
        root_layout.addWidget(self.chatrooms_panel)
        self._refresh_chatroom_list()

    def _on_chatrooms_toggled(self, expanded: bool) -> None:
        width_delta = CHATROOM_SIDEBAR_WIDTH
        old_geometry = self.root.geometry()
        old_frame_geometry = self.root.frameGeometry()
        frame_offset_x = old_geometry.x() - old_frame_geometry.x()
        frame_offset_y = old_geometry.y() - old_frame_geometry.y()
        self.chatrooms_toggle.setText("‹" if expanded else "›")
        self.chatrooms_panel.setVisible(expanded)
        self.root.setMinimumWidth(
            self.root.minimumWidth() + (
                width_delta if expanded else -width_delta
            )
        )
        new_width = max(
            self.root.minimumWidth(),
            old_geometry.width() + (
                width_delta if expanded else -width_delta
            ),
        )
        target_frame_x = old_frame_geometry.x() + (
            -width_delta if expanded else width_delta
        )
        self.root.setGeometry(
            target_frame_x + frame_offset_x,
            old_frame_geometry.y() + frame_offset_y,
            new_width,
            old_geometry.height(),
        )

    def _chatroom_definitions(self) -> list[dict[str, str]]:
        rooms = [{
            "id": GLOBAL_CHATROOM_ID,
            "nickname": GLOBAL_CHATROOM_NICKNAME,
            "key": GLOBAL_CHATROOM_KEY,
        }]
        for room in self.config_data.get("chatrooms", []):
            if isinstance(room, dict):
                rooms.append({
                    "id": str(room["id"]),
                    "nickname": str(room["nickname"]),
                    "key": str(room["key"]),
                })
        return rooms

    def _find_chatroom(self, room_id: str) -> dict[str, str] | None:
        for room in self._chatroom_definitions():
            if room["id"] == room_id:
                return room
        return None

    def _active_chatroom(self) -> dict[str, str]:
        room = self._find_chatroom(self.active_chatroom_id)
        if room is not None:
            return room
        self.active_chatroom_id = GLOBAL_CHATROOM_ID
        self.config_data["active_chatroom_id"] = GLOBAL_CHATROOM_ID
        return self._chatroom_definitions()[0]

    def _identity_private_key(self) -> str:
        normalized = normalize_identity_private_key(
            self.config_data.get("identity_private_key")
        )
        self.config_data["identity_private_key"] = normalized
        return normalized

    def _authenticated_client_id(self) -> str:
        return identity_client_id(self._identity_private_key())

    def _update_window_title(self) -> None:
        room_name = self._active_chatroom()["nickname"]
        self.root.setWindowTitle(f"{APP_NAME} ({room_name})")

    def _identity_presets(self) -> list[dict[str, str]]:
        raw_presets = self.config_data.get("identity_presets", [])
        if not isinstance(raw_presets, list):
            raw_presets = []
        presets: list[dict[str, str]] = []
        seen_ids: set[str] = set()
        for raw_preset in raw_presets:
            preset = normalize_identity_preset(raw_preset)
            if preset["id"] in seen_ids:
                continue
            presets.append(preset)
            seen_ids.add(preset["id"])
            if len(presets) >= MAX_IDENTITY_PRESETS:
                break
        if not presets:
            presets.append(default_identity_preset())
        self.config_data["identity_presets"] = presets
        return presets

    def _identity_preset_by_id(
        self,
        preset_id: str,
    ) -> dict[str, str] | None:
        return next((
            preset for preset in self._identity_presets()
            if preset["id"] == preset_id
        ), None)

    def _sync_identity_preset_to_profiles(
        self,
        preset: dict[str, str],
    ) -> None:
        profiles = self.config_data.setdefault("room_profiles", {})
        if not isinstance(profiles, dict):
            return
        for profile in profiles.values():
            if (
                isinstance(profile, dict)
                and profile.get("identity_preset_id") == preset["id"]
            ):
                apply_identity_preset_to_profile(profile, preset)

    def _room_profile(self, room_id: str) -> dict[str, str]:
        profiles = self.config_data.setdefault("room_profiles", {})
        if not isinstance(profiles, dict):
            profiles = {}
            self.config_data["room_profiles"] = profiles

        global_profile = normalize_room_profile(
            profiles.get(GLOBAL_CHATROOM_ID)
        )
        global_preset = self._identity_preset_by_id(
            global_profile.get("identity_preset_id", "")
        ) or self._identity_presets()[0]
        apply_identity_preset_to_profile(global_profile, global_preset)
        profiles[GLOBAL_CHATROOM_ID] = global_profile
        if room_id == GLOBAL_CHATROOM_ID:
            return global_profile

        profile = normalize_room_profile(
            profiles.get(room_id),
            global_profile,
        )
        selected_preset = self._identity_preset_by_id(
            profile.get("identity_preset_id", "")
        ) or global_preset
        apply_identity_preset_to_profile(profile, selected_preset)
        profiles[room_id] = profile
        return profile

    def _active_room_profile(self) -> dict[str, str]:
        return self._room_profile(self.active_chatroom_id)

    def _active_identity_preset(self) -> dict[str, str]:
        profile = self._active_room_profile()
        preset = self._identity_preset_by_id(
            profile.get("identity_preset_id", "")
        )
        if preset is None:
            preset = self._identity_presets()[0]
            apply_identity_preset_to_profile(profile, preset)
        return preset

    def _persist_profile_changes(self) -> None:
        try:
            save_config(self.config_data)
        except Exception:
            pass

    def _schedule_profile_save(self) -> None:
        self.profile_save_timer.start(250)
        self._run_message_size_check()

    def _commit_identity_preset_changes(
        self,
        preset: dict[str, str],
    ) -> None:
        self._sync_identity_preset_to_profiles(preset)
        self._refresh_identity_preset_selector()
        self._persist_profile_changes()
        self._run_message_size_check()

    def _muted_chatroom_ids(self) -> set[str]:
        return {
            str(room_id)
            for room_id in self.config_data.get("muted_chatrooms", [])
        }

    def _is_chatroom_muted(self, room_id: str) -> bool:
        return room_id in self._muted_chatroom_ids()

    def _refresh_chatroom_list(self) -> None:
        self.chatrooms_list.clear()
        active_item: QListWidgetItem | None = None
        muted_ids = self._muted_chatroom_ids()
        unread_counts = self._unread_counts()

        for room in self._chatroom_definitions():
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, room["id"])
            if room["id"] == GLOBAL_CHATROOM_ID:
                item.setFlags(
                    item.flags() & ~Qt.ItemFlag.ItemIsDragEnabled
                )
            self.chatrooms_list.addItem(item)
            row = ChatroomListRow(
                room["nickname"],
                unread_counts.get(room["id"], 0),
                room["id"] in muted_ids,
            )
            item.setSizeHint(row.sizeHint())
            self.chatrooms_list.setItemWidget(item, row)
            if room["id"] == self.active_chatroom_id:
                active_item = item

        if active_item is not None:
            self.chatrooms_list.setCurrentItem(active_item)
        self._update_chatrooms_toggle_unread_style()

    def _move_chatroom(self, room_id: str, target_row: int) -> None:
        raw_rooms = self.config_data.get("chatrooms", [])
        if not isinstance(raw_rooms, list):
            return
        source_index = next(
            (
                index
                for index, room in enumerate(raw_rooms)
                if isinstance(room, dict)
                and str(room.get("id", "")) == room_id
            ),
            None,
        )
        if source_index is None:
            return

        original_rooms = list(raw_rooms)
        moved_room = raw_rooms.pop(source_index)
        target_index = max(0, min(len(raw_rooms), target_row - 1))
        raw_rooms.insert(target_index, moved_room)
        try:
            save_config(self.config_data)
        except Exception as exc:
            self.config_data["chatrooms"] = original_rooms
            messagebox.showerror(
                "Could not reorder chatrooms",
                str(exc),
                parent=self.root,
            )
        self._refresh_chatroom_list()

    def _update_chatrooms_toggle_unread_style(self) -> None:
        if not hasattr(self, "chatrooms_toggle"):
            return

        muted_ids = self._muted_chatroom_ids()
        has_visible_unread = any(
            room_id != self.active_chatroom_id
            and room_id not in muted_ids
            and int(unread_count or 0) > 0
            for room_id, unread_count in self._unread_counts().items()
        )
        if has_visible_unread:
            self.chatrooms_toggle.setStyleSheet(
                NOTIFICATION_BUTTON_STYLESHEET
            )
        else:
            self.chatrooms_toggle.setStyleSheet("")

    def _update_config_toggle_update_style(self) -> None:
        if not hasattr(self, "config_toggle"):
            return
        if self.available_update is not None:
            self.config_toggle.setStyleSheet(
                NOTIFICATION_BUTTON_STYLESHEET
            )
            self.config_toggle.setToolTip(
                f"SpriteLink {self.available_update.version} is available."
            )
        else:
            self.config_toggle.setStyleSheet("")
            self.config_toggle.setToolTip("")

    def _unread_counts(self) -> dict[str, int]:
        unread_counts = self.config_data.setdefault("unread_counts", {})
        if not isinstance(unread_counts, dict):
            unread_counts = {}
            self.config_data["unread_counts"] = unread_counts
        return unread_counts

    def _mark_chatroom_read(self, room_id: str) -> None:
        unread_counts = self._unread_counts()
        if room_id not in unread_counts:
            return
        unread_counts.pop(room_id, None)
        try:
            save_config(self.config_data)
        except Exception:
            pass

    def _add_chatroom(self) -> None:
        dialog = AddChatroomDialog(self.root)
        self._apply_window_titlebar_theme(dialog)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        nickname = dialog.nickname()
        key = dialog.chatroom_key()
        if not nickname:
            messagebox.showerror(
                "Cannot add chatroom",
                "The chatroom nickname cannot be empty.",
                parent=self.root,
            )
            return
        if not key:
            messagebox.showerror(
                "Cannot add chatroom",
                "The chatroom key cannot be empty.",
                parent=self.root,
            )
            return
        if any(room["key"] == key for room in self._chatroom_definitions()):
            messagebox.showerror(
                "Cannot add chatroom",
                "That chatroom key is already in your list.",
                parent=self.root,
            )
            return

        room_id = uuid.uuid4().hex
        self.config_data.setdefault("chatrooms", []).append({
            "id": room_id,
            "nickname": nickname,
            "key": key,
        })
        self.config_data.setdefault("room_profiles", {})[room_id] = dict(
            self._room_profile(GLOBAL_CHATROOM_ID)
        )
        self.initial_history_pending_rooms.add(room_id)
        try:
            save_config(self.config_data)
        except Exception as exc:
            self.config_data["chatrooms"].pop()
            self.config_data.get("room_profiles", {}).pop(room_id, None)
            self.initial_history_pending_rooms.discard(room_id)
            messagebox.showerror(
                "Could not save chatroom",
                str(exc),
                parent=self.root,
            )
            return

        self._refresh_chatroom_list()
        self._activate_chatroom(room_id)

    def _edit_chatroom(self, room_id: str) -> None:
        if room_id == GLOBAL_CHATROOM_ID:
            return
        room = next(
            (
                candidate
                for candidate in self.config_data.get("chatrooms", [])
                if isinstance(candidate, dict)
                and str(candidate.get("id", "")) == room_id
            ),
            None,
        )
        if room is None:
            return

        old_nickname = str(room.get("nickname", ""))
        old_key = str(room.get("key", ""))
        dialog = AddChatroomDialog(
            self.root,
            title="Edit Chatroom",
            submit_label="Save",
            nickname=old_nickname,
            chatroom_key=old_key,
            history_note=(
                "All locally stored history will remain here even if the "
                "chatroom key is changed."
            ),
        )
        self._apply_window_titlebar_theme(dialog)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        nickname = dialog.nickname()
        key = dialog.chatroom_key()
        if not nickname:
            messagebox.showerror(
                "Cannot edit chatroom",
                "The chatroom nickname cannot be empty.",
                parent=self.root,
            )
            return
        if not key:
            messagebox.showerror(
                "Cannot edit chatroom",
                "The chatroom key cannot be empty.",
                parent=self.root,
            )
            return
        if any(
            candidate["id"] != room_id and candidate["key"] == key
            for candidate in self._chatroom_definitions()
        ):
            messagebox.showerror(
                "Cannot edit chatroom",
                "That chatroom key is already in your list.",
                parent=self.root,
            )
            return

        key_changed = key != old_key
        was_initial_history_pending = (
            room_id in self.initial_history_pending_rooms
        )
        if key_changed and room_id == self.active_chatroom_id:
            self._persist_local_history()
        room["nickname"] = nickname
        room["key"] = key
        if key_changed:
            self.initial_history_pending_rooms.add(room_id)
        try:
            save_config(self.config_data)
        except Exception as exc:
            room["nickname"] = old_nickname
            room["key"] = old_key
            if not was_initial_history_pending:
                self.initial_history_pending_rooms.discard(room_id)
            messagebox.showerror(
                "Could not save chatroom",
                str(exc),
                parent=self.root,
            )
            return

        if room_id == self.active_chatroom_id and key_changed:
            self._switch_active_chatroom()
        else:
            if room_id == self.active_chatroom_id:
                self._update_window_title()
            self._refresh_chatroom_list()

    def _show_chatroom_context_menu(self, position: Any) -> None:
        item = self.chatrooms_list.itemAt(position)
        if item is None:
            return

        room_id = str(item.data(Qt.ItemDataRole.UserRole))
        is_muted = self._is_chatroom_muted(room_id)
        menu = QMenu(self.root)
        mute_action = menu.addAction("Unmute" if is_muted else "Mute")
        mute_action.triggered.connect(
            lambda: self._set_chatroom_muted(room_id, not is_muted)
        )
        edit_action = menu.addAction("Edit")
        edit_action.setEnabled(room_id != GLOBAL_CHATROOM_ID)
        if room_id != GLOBAL_CHATROOM_ID:
            edit_action.triggered.connect(
                lambda: self._edit_chatroom(room_id)
            )
        remove_action = menu.addAction("Remove")
        remove_action.setEnabled(room_id != GLOBAL_CHATROOM_ID)
        if room_id != GLOBAL_CHATROOM_ID:
            remove_action.triggered.connect(
                lambda: self._remove_chatroom(room_id)
            )
        menu.exec(self.chatrooms_list.mapToGlobal(position))

    def _set_chatroom_muted(self, room_id: str, muted: bool) -> None:
        muted_ids = self._muted_chatroom_ids()
        if muted:
            muted_ids.add(room_id)
        else:
            muted_ids.discard(room_id)
        self.config_data["muted_chatrooms"] = sorted(muted_ids)
        try:
            save_config(self.config_data)
        except Exception:
            pass
        self._refresh_chatroom_list()

    def _remove_chatroom(self, room_id: str) -> None:
        if room_id == GLOBAL_CHATROOM_ID:
            return

        confirmation = QMessageBox(self.root)
        confirmation.setIcon(QMessageBox.Icon.Warning)
        confirmation.setWindowTitle("Remove Chatroom")
        confirmation.setText(
            "This chatroom will be removed from your view; existing messages "
            "will stay there for other participants."
        )
        remove_button = confirmation.addButton(
            "Remove!",
            QMessageBox.ButtonRole.DestructiveRole,
        )
        confirmation.addButton(
            "Cancel",
            QMessageBox.ButtonRole.RejectRole,
        )
        confirmation.exec()
        if confirmation.clickedButton() is not remove_button:
            return

        rooms = self.config_data.get("chatrooms", [])
        self.config_data["chatrooms"] = [
            room for room in rooms
            if isinstance(room, dict) and str(room.get("id")) != room_id
        ]
        muted_ids = self._muted_chatroom_ids()
        muted_ids.discard(room_id)
        self.config_data["muted_chatrooms"] = sorted(muted_ids)
        self._unread_counts().pop(room_id, None)
        self.config_data.get("room_profiles", {}).pop(room_id, None)
        self.initial_history_pending_rooms.discard(room_id)

        if self.active_chatroom_id == room_id:
            self.active_chatroom_id = GLOBAL_CHATROOM_ID
            self.config_data["active_chatroom_id"] = GLOBAL_CHATROOM_ID
            self._switch_active_chatroom()
        else:
            try:
                save_config(self.config_data)
            except Exception:
                pass
        self._refresh_chatroom_list()

    def _activate_chatroom(self, room_id: str) -> None:
        if self._find_chatroom(room_id) is None:
            return
        self._mark_chatroom_read(room_id)
        if room_id == self.active_chatroom_id:
            self._refresh_chatroom_list()
            return
        now = time.monotonic()
        cutoff = now - CHATROOM_SWITCH_BURST_WINDOW_SECONDS
        self.recent_chatroom_switch_times = [
            switched_at
            for switched_at in self.recent_chatroom_switch_times
            if switched_at >= cutoff
        ]
        poll_immediately = (
            len(self.recent_chatroom_switch_times)
            < IMMEDIATE_CHATROOM_SWITCH_LIMIT
        )
        self.recent_chatroom_switch_times.append(now)
        self._persist_local_history()
        self.active_chatroom_id = room_id
        self.config_data["active_chatroom_id"] = room_id
        self._switch_active_chatroom(
            poll_immediately=poll_immediately
        )

    def _request_network_refresh(
        self,
        *,
        poll_immediately: bool,
    ) -> None:
        self.network_control_queue.put({
            "room_id": self.active_chatroom_id,
            "poll_immediately": poll_immediately,
        })

    def _switch_active_chatroom(
        self,
        *,
        poll_immediately: bool = True,
    ) -> None:
        self._unread_counts().pop(self.active_chatroom_id, None)
        self._update_window_title()
        try:
            save_config(self.config_data)
        except Exception:
            pass
        self._clear_visible_room()
        self._load_active_room_profile_into_controls()
        self._load_saved_history_for_current_room()
        self.connected = False
        self.status_var.set("Connecting")
        self._request_network_refresh(
            poll_immediately=poll_immediately
        )
        self.message_entry.clear()
        self._run_message_size_check()
        self._refresh_chatroom_list()

    def _build_chat_tab(self) -> None:
        layout = QVBoxLayout(self.chat_tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(7)

        status_layout = QHBoxLayout()
        status_layout.setContentsMargins(0, 0, 0, 0)

        self.chatrooms_toggle = QPushButton("›")
        self.chatrooms_toggle.setCheckable(True)
        self.chatrooms_toggle.setFixedWidth(24)
        self.chatrooms_toggle.setToolTip("Chatrooms")
        self.chatrooms_toggle.setAccessibleName("Toggle chatrooms menu")
        self.chatrooms_toggle.toggled.connect(self._on_chatrooms_toggled)
        status_layout.addWidget(self.chatrooms_toggle)
        self._update_chatrooms_toggle_unread_style()

        status_layout.addWidget(QLabel("Status:"))

        self.status_label = QLabel()
        self.status_label.setFont(
            self._make_font("Segoe UI", 9, bold=True)
        )
        self.status_var.bind(self.status_label.setText)
        status_layout.addWidget(self.status_label)
        status_layout.addStretch(1)

        self.config_toggle = QPushButton("Config")
        self.config_toggle.setCheckable(True)
        self.config_toggle.toggled.connect(self._on_config_toggled)
        self._update_config_toggle_update_style()
        status_layout.addWidget(self.config_toggle)
        layout.addLayout(status_layout)

        self.chat_content = QWidget()
        content_layout = QVBoxLayout(self.chat_content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(7)
        layout.addWidget(self.chat_content, 1)

        self.chat_display = MessageLogBrowser()
        self.chat_display.setReadOnly(True)
        self.chat_display.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.chat_display.setOpenLinks(False)
        self.chat_display.setOpenExternalLinks(False)
        self.chat_display.setUndoRedoEnabled(False)
        self.chat_display.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.chat_display.setFont(self._make_font("Segoe UI", 10))
        self.chat_display.setViewportMargins(0, 0, 0, 0)
        self.chat_display.document().setDocumentMargin(0)
        text_option = self.chat_display.document().defaultTextOption()
        text_option.setWrapMode(QTextOption.WrapMode.WrapAnywhere)
        self.chat_display.document().setDefaultTextOption(text_option)
        self.chat_display.viewport().setMouseTracking(True)
        self.chat_display.viewport().installEventFilter(self)
        content_layout.addWidget(self.chat_display, 1)

        composer_actions = QHBoxLayout()
        composer_actions.setContentsMargins(0, 0, 0, 0)
        composer_actions.setSpacing(6)
        self.identity_menu_button = QPushButton("Identity")
        self.identity_menu_button.setCheckable(True)
        self.identity_menu_button.toggled.connect(
            self._on_identity_menu_toggled
        )
        composer_actions.addWidget(self.identity_menu_button)
        self.font_menu_button = QPushButton("Font")
        self.font_menu_button.setCheckable(True)
        self.font_menu_button.toggled.connect(
            self._on_font_menu_toggled
        )
        composer_actions.addWidget(self.font_menu_button)
        composer_actions.addStretch(1)

        self.identity_menu = QFrame()
        self.identity_menu.setFrameShape(QFrame.Shape.StyledPanel)
        identity_layout = QHBoxLayout(self.identity_menu)
        identity_layout.setContentsMargins(8, 5, 8, 5)
        identity_layout.setSpacing(7)
        self.identity_preset_selector = IdentityPresetSelector(
            self.identity_menu
        )
        self.identity_preset_selector.presetSelected.connect(
            self._select_identity_preset
        )
        self.identity_preset_selector.removeRequested.connect(
            self._remove_identity_preset
        )
        self.identity_preset_selector.newRequested.connect(
            self._new_identity_preset
        )
        identity_layout.addWidget(self.identity_preset_selector)
        identity_layout.addWidget(QLabel("Name"))
        self.identity_username_entry = QLineEdit()
        self.identity_username_entry.setMaxLength(32)
        self.identity_username_entry.textChanged.connect(
            self._on_identity_username_changed
        )
        self.identity_username_entry.editingFinished.connect(
            self._normalize_identity_username_entry
        )
        identity_layout.addWidget(self.identity_username_entry, 1)
        identity_layout.addWidget(QLabel("Name color"))
        self.identity_color_preview = QLabel()
        self.identity_color_preview.setFixedSize(26, 22)
        self.identity_color_preview.setFrameShape(QFrame.Shape.Panel)
        self.identity_color_preview.setFrameShadow(QFrame.Shadow.Sunken)
        identity_layout.addWidget(self.identity_color_preview)
        identity_color_button = QPushButton("Choose...")
        identity_color_button.clicked.connect(self._choose_identity_color)
        identity_layout.addWidget(identity_color_button)
        identity_layout.addSpacing(8)
        identity_layout.addWidget(QLabel("Icon (16x16)"))
        self.profile_icon_preview = QLabel()
        self.profile_icon_preview.setFixedSize(26, 22)
        self.profile_icon_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.profile_icon_preview.setFrameShape(QFrame.Shape.Panel)
        self.profile_icon_preview.setFrameShadow(QFrame.Shadow.Sunken)
        self.profile_icon_preview.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.profile_icon_preview.customContextMenuRequested.connect(
            self._show_profile_icon_context_menu
        )
        identity_layout.addWidget(self.profile_icon_preview)
        profile_icon_button = QPushButton("Browse...")
        profile_icon_button.clicked.connect(self._choose_profile_icon)
        identity_layout.addWidget(profile_icon_button)
        self.identity_menu.hide()
        content_layout.addWidget(self.identity_menu)

        self.font_menu = QFrame()
        self.font_menu.setFrameShape(QFrame.Shape.StyledPanel)
        font_layout = QHBoxLayout(self.font_menu)
        font_layout.setContentsMargins(8, 5, 8, 5)
        font_layout.setSpacing(7)
        font_layout.addWidget(QLabel("Font"))
        self.message_font_combo = ThemeComboBox()
        self.message_font_combo.addItems(list(SELECTABLE_MESSAGE_FONTS))
        self._refresh_message_font_combo_fonts()
        self.message_font_combo.currentTextChanged.connect(
            self._on_message_font_changed
        )
        font_layout.addWidget(self.message_font_combo, 1)
        font_layout.addWidget(QLabel("Text color"))
        self.message_text_color_preview = QLabel()
        self.message_text_color_preview.setFixedSize(26, 22)
        self.message_text_color_preview.setFrameShape(QFrame.Shape.Panel)
        self.message_text_color_preview.setFrameShadow(
            QFrame.Shadow.Sunken
        )
        font_layout.addWidget(self.message_text_color_preview)
        text_color_button = QPushButton("Choose...")
        text_color_button.clicked.connect(self._choose_message_text_color)
        font_layout.addWidget(text_color_button)
        self.font_menu.hide()
        content_layout.addWidget(self.font_menu)
        content_layout.addLayout(composer_actions)

        compose_layout = QGridLayout()
        compose_layout.setContentsMargins(0, 1, 0, 0)
        compose_layout.setHorizontalSpacing(8)
        compose_layout.setVerticalSpacing(5)

        self.message_entry = ComposeTextEdit()
        self.message_entry.setFont(
            self._make_message_font(DEFAULT_MESSAGE_FONT)
        )
        self.message_entry.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.message_entry.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.message_entry.textChanged.connect(self._schedule_composer_update)
        self.message_entry.send_requested.connect(self._send_current_message)
        compose_layout.addWidget(self.message_entry, 0, 0)

        self.send_button = QPushButton("Send")
        self.send_button.clicked.connect(self._send_current_message)
        self.send_button.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Expanding,
        )
        compose_layout.addWidget(self.send_button, 0, 1)

        self.message_size_bar = ClickableProgressBar()
        self.message_size_bar.setRange(0, NTFY_MAX_BODY_BYTES)
        self.message_size_bar.setTextVisible(True)
        self.message_size_bar.setFixedHeight(18)
        self.message_size_bar.setCursor(Qt.CursorShape.PointingHandCursor)
        self.message_size_bar.setToolTip("View message and daily limits")
        self.message_size_bar.clicked.connect(
            self._show_message_limit_popup
        )
        compose_layout.addWidget(self.message_size_bar, 1, 0, 1, 2)
        content_layout.addLayout(compose_layout)

        self._load_active_room_profile_into_controls()
        self._resize_message_entry()
        self._run_message_size_check()

    @staticmethod
    def _set_button_checked(button: QPushButton, checked: bool) -> None:
        previous = button.blockSignals(True)
        button.setChecked(checked)
        button.blockSignals(previous)

    def _on_identity_menu_toggled(self, checked: bool) -> None:
        self.identity_menu.setVisible(checked)
        if checked:
            self._set_button_checked(self.font_menu_button, False)
            self.font_menu.hide()
            self.identity_username_entry.setFocus()

    def _on_font_menu_toggled(self, checked: bool) -> None:
        self.font_menu.setVisible(checked)
        if checked:
            self._set_button_checked(self.identity_menu_button, False)
            self.identity_menu.hide()
            self.message_font_combo.setFocus()

    @staticmethod
    def _set_color_preview(preview: QLabel, color: str) -> None:
        preview.setStyleSheet(f"background-color: {color};")

    def _refresh_message_font_combo_fonts(self) -> None:
        if not hasattr(self, "message_font_combo"):
            return
        for index, font_name in enumerate(SELECTABLE_MESSAGE_FONTS):
            self.message_font_combo.setItemData(
                index,
                self._make_message_font(font_name),
                Qt.ItemDataRole.FontRole,
            )
        current_font = self.message_font_combo.currentText()
        if current_font in SELECTABLE_MESSAGE_FONTS:
            self.message_font_combo.setFont(
                self._make_message_font(current_font)
            )

    def _refresh_identity_preset_selector(self) -> None:
        if not hasattr(self, "identity_preset_selector"):
            return
        profile = self._active_room_profile()
        self.identity_preset_selector.set_presets(
            self._identity_presets(),
            profile.get("identity_preset_id", ""),
        )

    def _select_identity_preset(self, preset_id: str) -> None:
        preset = self._identity_preset_by_id(preset_id)
        if preset is None:
            return
        apply_identity_preset_to_profile(
            self._active_room_profile(),
            preset,
        )
        self._load_active_room_profile_into_controls()
        self._persist_profile_changes()
        self._run_message_size_check()

    def _new_identity_preset(self) -> None:
        presets = self._identity_presets()
        if len(presets) >= MAX_IDENTITY_PRESETS:
            messagebox.showwarning(
                "Identity preset limit",
                f"SpriteLink supports up to {MAX_IDENTITY_PRESETS} identities.",
                parent=self.root,
            )
            return
        preset = default_identity_preset()
        presets.append(preset)
        self.config_data["identity_presets"] = presets
        apply_identity_preset_to_profile(
            self._active_room_profile(),
            preset,
        )
        self._load_active_room_profile_into_controls()
        self._persist_profile_changes()
        self._run_message_size_check()
        self.identity_username_entry.setFocus()
        self.identity_username_entry.selectAll()

    def _remove_identity_preset(self, preset_id: str) -> None:
        presets = self._identity_presets()
        remaining = [
            preset for preset in presets
            if preset["id"] != preset_id
        ]
        if len(remaining) == len(presets):
            return
        if not remaining:
            remaining.append(default_identity_preset())
        fallback = remaining[0]
        self.config_data["identity_presets"] = remaining
        profiles = self.config_data.setdefault("room_profiles", {})
        if isinstance(profiles, dict):
            for profile in profiles.values():
                if (
                    isinstance(profile, dict)
                    and profile.get("identity_preset_id") == preset_id
                ):
                    apply_identity_preset_to_profile(profile, fallback)
        self._load_active_room_profile_into_controls()
        self._persist_profile_changes()
        self._run_message_size_check()

    def _load_active_room_profile_into_controls(self) -> None:
        if not hasattr(self, "identity_username_entry"):
            return

        profile = self._active_room_profile()
        self._loading_profile_controls = True
        try:
            self._refresh_identity_preset_selector()
            self.identity_username_entry.setText(profile["username"])
            self.message_font_combo.setCurrentText(profile["font"])
            self.message_font_combo.setFont(
                self._make_message_font(profile["font"])
            )
            self._set_color_preview(
                self.identity_color_preview,
                profile["username_color"],
            )
            self._set_color_preview(
                self.message_text_color_preview,
                profile["text_color"],
            )
            self._update_profile_icon_preview()
        finally:
            self._loading_profile_controls = False
        self._apply_active_composer_style()

    def _update_message_entry_placeholder(self) -> None:
        if not hasattr(self, "message_entry"):
            return

        room_name = self._active_chatroom()["nickname"]
        self.message_entry.setPlaceholderText(f"Chat in {room_name}")
        placeholder_color = QColor(
            self._active_room_profile()["text_color"]
        )
        placeholder_color.setAlpha(140)
        palette = self.message_entry.palette()
        palette.setColor(
            QPalette.ColorRole.PlaceholderText,
            placeholder_color,
        )
        self.message_entry.setPalette(palette)

    def _apply_active_composer_style(self) -> None:
        if not hasattr(self, "message_entry"):
            return
        profile = self._active_room_profile()
        self.message_entry.setFont(
            self._make_message_font(profile["font"])
        )
        self.message_entry.setStyleSheet(
            f"color: {profile['text_color']};"
        )
        self._update_message_entry_placeholder()
        self._resize_message_entry()

    def _on_identity_username_changed(self, value: str) -> None:
        if self._loading_profile_controls:
            return
        preset = self._active_identity_preset()
        preset["username"] = value.strip()[:32] or "User"
        self._commit_identity_preset_changes(preset)

    def _normalize_identity_username_entry(self) -> None:
        normalized = self._active_identity_preset()["username"]
        if self.identity_username_entry.text() != normalized:
            self._loading_profile_controls = True
            try:
                self.identity_username_entry.setText(normalized)
            finally:
                self._loading_profile_controls = False

    def _choose_identity_color(self) -> None:
        preset = self._active_identity_preset()
        dialog = QColorDialog(
            QColor(preset["username_color"]),
            self.root,
        )
        dialog.setWindowTitle("Choose name color")
        dialog.setOption(
            QColorDialog.ColorDialogOption.DontUseNativeDialog,
            True,
        )
        self._apply_window_titlebar_theme(dialog)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected = dialog.currentColor()
        if not selected.isValid():
            return
        preset["username_color"] = selected.name()
        self._set_color_preview(
            self.identity_color_preview,
            selected.name(),
        )
        self._commit_identity_preset_changes(preset)

    def _choose_profile_icon(self) -> None:
        dialog = QFileDialog(self.root, "Choose icon")
        dialog.setOption(
            QFileDialog.Option.DontUseNativeDialog,
            True,
        )
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
        dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        dialog.setNameFilter("PNG images (*.png)")
        self._apply_window_titlebar_theme(dialog)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected_files = dialog.selectedFiles()
        source_path = selected_files[0] if selected_files else ""
        if not source_path:
            return

        try:
            gif_data = optimize_profile_icon(source_path)
            encoded_icon = encode_profile_icon(gif_data)
            decode_profile_icon(encoded_icon)
        except Exception as exc:
            messagebox.showerror(
                "Could not use profile icon",
                str(exc),
                parent=self.root,
            )
            return

        preset = self._active_identity_preset()
        preset["profile_icon"] = encoded_icon
        self._update_profile_icon_preview()
        self._commit_identity_preset_changes(preset)

    def _update_profile_icon_preview(self) -> None:
        if not hasattr(self, "profile_icon_preview"):
            return
        self.profile_icon_preview.clear()
        encoded_icon = self._active_identity_preset().get(
            "profile_icon",
            "",
        )
        if not encoded_icon:
            return
        try:
            gif_data = decode_profile_icon(str(encoded_icon))
        except ValueError:
            return
        pixmap = QPixmap()
        if not pixmap.loadFromData(gif_data):
            return
        pixmap.setDevicePixelRatio(1.0)
        self.profile_icon_preview.setPixmap(pixmap)

    def _show_profile_icon_context_menu(self, position: Any) -> None:
        menu = QMenu(self.profile_icon_preview)
        remove_action = menu.addAction("Remove")
        has_icon = bool(
            self._active_identity_preset().get("profile_icon", "")
        )
        remove_action.setEnabled(has_icon)
        if has_icon:
            remove_action.triggered.connect(self._remove_profile_icon)
        menu.exec(self.profile_icon_preview.mapToGlobal(position))

    def _remove_profile_icon(self) -> None:
        preset = self._active_identity_preset()
        preset["profile_icon"] = ""
        self._update_profile_icon_preview()
        self._commit_identity_preset_changes(preset)

    def _on_message_font_changed(self, value: str) -> None:
        if self._loading_profile_controls:
            return
        profile = self._active_room_profile()
        profile["font"] = (
            value if value in SELECTABLE_MESSAGE_FONTS
            else DEFAULT_MESSAGE_FONT
        )
        self.message_font_combo.setFont(
            self._make_message_font(profile["font"])
        )
        self._apply_active_composer_style()
        self._schedule_profile_save()

    def _choose_message_text_color(self) -> None:
        profile = self._active_room_profile()
        dialog = QColorDialog(
            QColor(profile["text_color"]),
            self.root,
        )
        dialog.setWindowTitle("Choose message text color")
        dialog.setOption(
            QColorDialog.ColorDialogOption.DontUseNativeDialog,
            True,
        )
        self._apply_window_titlebar_theme(dialog)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected = dialog.currentColor()
        if not selected.isValid():
            return
        profile["text_color"] = selected.name()
        self._set_color_preview(
            self.message_text_color_preview,
            selected.name(),
        )
        self._apply_active_composer_style()
        self._schedule_profile_save()

    def _build_config_popup(self) -> None:
        self.config_overlay = ConfigOverlay(self.chat_content)
        self.config_overlay.dismissed.connect(self._dismiss_config_popup)

        overlay_layout = QVBoxLayout(self.config_overlay)
        overlay_layout.setContentsMargins(36, 24, 36, 24)

        panel_row = QHBoxLayout()
        panel_row.addStretch(1)

        self.config_panel = QFrame()
        self.config_panel.setObjectName("configPanel")
        self.config_panel.setMaximumWidth(CONFIG_POPUP_MAX_WIDTH)
        self.config_panel.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.config_panel.setStyleSheet(self._config_panel_stylesheet())
        panel_row.addWidget(self.config_panel, 8)
        panel_row.addStretch(1)
        overlay_layout.addLayout(panel_row, 1)
        self.config_overlay.panel = self.config_panel

        panel_layout = QVBoxLayout(self.config_panel)
        panel_layout.setContentsMargins(10, 10, 10, 10)
        panel_layout.setSpacing(6)
        panel_layout.addWidget(self._heading("Config"))

        config_scroll = QScrollArea()
        config_scroll.setWidgetResizable(True)
        config_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.config_tab = QWidget()
        config_scroll.setWidget(self.config_tab)
        panel_layout.addWidget(config_scroll, 1)

        self._build_config_tab()
        self.config_overlay.hide()
        self.chat_content.installEventFilter(self)
        QTimer.singleShot(0, self._sync_config_overlay_geometry)

    def _build_message_limit_popup(self) -> None:
        self.message_limit_overlay = ConfigOverlay(self.chat_content)
        self.message_limit_overlay.dismissed.connect(
            self._hide_message_limit_popup
        )

        overlay_layout = QVBoxLayout(self.message_limit_overlay)
        overlay_layout.setContentsMargins(36, 24, 36, 24)
        overlay_layout.addStretch(1)

        panel_row = QHBoxLayout()
        panel_row.addStretch(1)
        self.message_limit_panel = QFrame()
        self.message_limit_panel.setObjectName("configPanel")
        self.message_limit_panel.setMinimumWidth(430)
        self.message_limit_panel.setMaximumWidth(520)
        self.message_limit_panel.setStyleSheet(
            self._config_panel_stylesheet()
        )
        panel_row.addWidget(self.message_limit_panel)
        panel_row.addStretch(1)
        overlay_layout.addLayout(panel_row)
        overlay_layout.addStretch(1)
        self.message_limit_overlay.panel = self.message_limit_panel

        panel_layout = QVBoxLayout(self.message_limit_panel)
        panel_layout.setContentsMargins(16, 14, 16, 14)
        panel_layout.setSpacing(12)
        self.message_limit_info_label = QLabel()
        self.message_limit_info_label.setWordWrap(True)
        panel_layout.addWidget(self.message_limit_info_label)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        ok_button = QPushButton("OK")
        ok_button.setDefault(True)
        ok_button.clicked.connect(self._hide_message_limit_popup)
        button_row.addWidget(ok_button)
        panel_layout.addLayout(button_row)

        self.message_limit_overlay.hide()
        QTimer.singleShot(0, self._sync_message_limit_overlay_geometry)

    def _sync_message_limit_overlay_geometry(self) -> None:
        self.message_limit_overlay.setGeometry(self.chat_content.rect())

    def _show_message_limit_popup(self) -> None:
        self.message_size_timer.stop()
        self._run_message_size_check()
        self._update_message_limit_popup()
        self._sync_message_limit_overlay_geometry()
        self.message_limit_overlay.show()
        self.message_limit_overlay.raise_()

    def _hide_message_limit_popup(self) -> None:
        self.message_limit_overlay.hide()
        self.message_entry.setFocus()

    def _update_message_limit_popup(self) -> None:
        packet_size = max(0, int(self.current_estimated_packet_size))
        messages_left = self._messages_left_today()
        self.message_limit_info_label.setText(
            f"Message filesize: {packet_size / 1024.0:.1f} KB / "
            f"{NTFY_MAX_BODY_BYTES / 1024.0:.1f} KB\n"
            f"Messages left today: {messages_left}\n\n"
            "The ntfy server only allows 250 messages output per IP per day.\n"
            "This limit is reset at 12:00 AM UTC "
            f"({local_reset_time_label()})."
        )

    def _build_image_preview_popup(self) -> None:
        self.image_preview_overlay = ConfigOverlay(self.chat_content)
        self.image_preview_overlay.dismissed.connect(
            self._hide_image_preview_popup
        )

        overlay_layout = QVBoxLayout(self.image_preview_overlay)
        overlay_layout.setContentsMargins(36, 24, 36, 24)
        overlay_layout.addStretch(1)

        panel_row = QHBoxLayout()
        panel_row.addStretch(1)
        self.image_preview_panel = QFrame()
        self.image_preview_panel.setObjectName("configPanel")
        self.image_preview_panel.setMaximumWidth(CONFIG_POPUP_MAX_WIDTH)
        self.image_preview_panel.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self.image_preview_panel.setStyleSheet(
            self._config_panel_stylesheet()
        )
        panel_row.addWidget(self.image_preview_panel, 8)
        panel_row.addStretch(1)
        overlay_layout.addLayout(panel_row)
        overlay_layout.addStretch(1)
        self.image_preview_overlay.panel = self.image_preview_panel

        panel_layout = QVBoxLayout(self.image_preview_panel)
        panel_layout.setContentsMargins(16, 14, 16, 14)
        panel_layout.setSpacing(12)
        self.image_preview_label = QLabel()
        self.image_preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_preview_label.setMinimumHeight(96)
        panel_layout.addWidget(self.image_preview_label, 1)

        button_row = QHBoxLayout()
        self.image_preview_url_label = QLabel()
        self.image_preview_url_label.setMinimumWidth(0)
        self.image_preview_url_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        button_row.addWidget(self.image_preview_url_label, 1)
        open_button = QPushButton("Open in Browser")
        open_button.clicked.connect(self._open_current_image_in_browser)
        button_row.addWidget(open_button)
        dismiss_button = QPushButton("Dismiss")
        dismiss_button.clicked.connect(self._hide_image_preview_popup)
        button_row.addWidget(dismiss_button)
        panel_layout.addLayout(button_row)

        self.image_preview_overlay.hide()
        QTimer.singleShot(0, self._sync_image_preview_overlay_geometry)

    def _sync_image_preview_overlay_geometry(self) -> None:
        self.image_preview_overlay.setGeometry(self.chat_content.rect())

    def _show_image_preview_popup(self, url: str) -> None:
        image = self.image_preview_cache.get(url)
        if not isinstance(image, QImage) or image.isNull():
            return
        self._hide_chat_tooltip()
        self.current_image_preview_url = url
        self._sync_image_preview_overlay_geometry()
        self.image_preview_overlay.show()
        self.image_preview_overlay.raise_()
        self._update_image_preview_popup()
        QTimer.singleShot(0, self._update_image_preview_popup)

    def _update_image_preview_popup(self) -> None:
        url = self.current_image_preview_url
        image = self.image_preview_cache.get(url or "")
        if not isinstance(image, QImage) or image.isNull():
            self.image_preview_label.clear()
            self.image_preview_url_label.clear()
            return
        available_width = max(
            EMBEDDED_IMAGE_MAX_EDGE,
            min(
                IMAGE_PREVIEW_MAX_WIDTH,
                self.image_preview_panel.width() - 32,
            ),
        )
        preview = image.scaled(
            available_width,
            IMAGE_PREVIEW_MAX_HEIGHT,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.image_preview_label.setPixmap(QPixmap.fromImage(preview))
        label_width = max(1, self.image_preview_url_label.width())
        self.image_preview_url_label.setText(
            self.image_preview_url_label.fontMetrics().elidedText(
                url or "",
                Qt.TextElideMode.ElideMiddle,
                label_width,
            )
        )
        self.image_preview_url_label.setToolTip(url or "")

    def _hide_image_preview_popup(self) -> None:
        self.image_preview_overlay.hide()
        self.current_image_preview_url = None
        self.image_preview_label.clear()
        self.image_preview_url_label.clear()
        self.image_preview_url_label.setToolTip("")
        self.message_entry.setFocus()

    def _open_current_image_in_browser(self) -> None:
        if self.current_image_preview_url:
            self._open_url_in_browser(self.current_image_preview_url)

    def _build_config_tab(self) -> None:
        layout = QGridLayout(self.config_tab)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(5)
        layout.setColumnStretch(1, 1)
        row = 0

        layout.addWidget(self._heading("Appearance"), row, 0, 1, 3)
        row += 1

        layout.addWidget(QLabel("Themes"), row, 0)
        self.theme_combo = ThemeComboBox()
        self.theme_combo.addItems(list(THEMES))
        self.theme_combo.setCurrentText(str(self.theme_var.get()))
        self.theme_combo.currentTextChanged.connect(self._on_theme_changed)
        self.theme_var.bind(self.theme_combo.setCurrentText)
        layout.addWidget(self.theme_combo, row, 1, 1, 2)
        row += 1

        layout.addWidget(self._separator(), row, 0, 1, 3)
        row += 1
        layout.addWidget(
            self._heading("Notifications and rendering"),
            row,
            0,
            1,
            3,
        )
        row += 1

        layout.addWidget(QLabel("Message Sound"), row, 0)
        self.message_sound_combo = ThemeComboBox()
        self.message_sound_combo.addItems(list(MESSAGE_SOUND_OPTIONS))
        self.message_sound_combo.setCurrentText(
            str(self.message_sound_var.get())
        )
        self.message_sound_combo.textActivated.connect(
            self._on_message_sound_selected
        )
        self.message_sound_var.bind(
            self.message_sound_combo.setCurrentText
        )
        layout.addWidget(self.message_sound_combo, row, 1)
        message_sound_volume_control = QWidget()
        message_sound_volume_layout = QHBoxLayout(
            message_sound_volume_control
        )
        message_sound_volume_layout.setContentsMargins(0, 0, 0, 0)
        message_sound_volume_layout.setSpacing(6)
        self.message_sound_volume_label = QLabel(
            f"Volume: {int(self.message_sound_volume_var.get())}%"
        )
        message_sound_volume_layout.addWidget(
            self.message_sound_volume_label
        )
        self.message_sound_volume_slider = QSlider(
            Qt.Orientation.Horizontal
        )
        self.message_sound_volume_slider.setRange(1, 10)
        self.message_sound_volume_slider.setSingleStep(1)
        self.message_sound_volume_slider.setPageStep(1)
        self.message_sound_volume_slider.setMinimumWidth(120)
        self.message_sound_volume_slider.setAccessibleName(
            "Message sound volume"
        )
        self.message_sound_volume_slider.setValue(
            int(self.message_sound_volume_var.get()) // 10
        )
        self.message_sound_volume_slider.setToolTip(
            f"Volume: {int(self.message_sound_volume_var.get())}%"
        )
        self.message_sound_volume_slider.valueChanged.connect(
            self._on_message_sound_volume_changed
        )
        self.message_sound_volume_var.bind(
            lambda value: self.message_sound_volume_slider.setValue(
                int(value) // 10
            )
        )
        message_sound_volume_layout.addWidget(
            self.message_sound_volume_slider,
            1,
        )
        layout.addWidget(message_sound_volume_control, row, 2)
        row += 1

        layout.addWidget(self._separator(), row, 0, 1, 3)
        row += 1
        layout.addWidget(self._heading("Version"), row, 0, 1, 3)
        row += 1

        layout.addWidget(QLabel("Current Version"), row, 0)
        self.current_version_label = QLabel(RUNNING_VERSION)
        layout.addWidget(self.current_version_label, row, 1, 1, 2)
        row += 1

        layout.addWidget(QLabel("Update Checks"), row, 0)
        self.automatic_update_checks_checkbox = QCheckBox(
            "Check automatically"
        )
        self.automatic_update_checks_checkbox.setChecked(
            bool(self.automatic_update_checks_var.get())
        )
        self.automatic_update_checks_checkbox.toggled.connect(
            self._on_automatic_update_checks_toggled
        )
        self.automatic_update_checks_var.bind(
            self.automatic_update_checks_checkbox.setChecked
        )
        layout.addWidget(
            self.automatic_update_checks_checkbox,
            row,
            1,
            1,
            2,
        )
        row += 1

        layout.addWidget(QLabel("Status"), row, 0)
        self.version_status_label = QLabel(
            "Not checked yet."
            if parse_semantic_version(RUNNING_VERSION) is not None
            else "Update checks require a packaged build."
        )
        self.version_status_label.setWordWrap(True)
        layout.addWidget(self.version_status_label, row, 1, 1, 2)
        row += 1

        version_button_row = QHBoxLayout()
        version_button_row.addStretch(1)
        self.check_for_updates_button = QPushButton("Check for Updates")
        self.check_for_updates_button.clicked.connect(
            lambda: self._check_for_updates(manual=True)
        )
        version_button_row.addWidget(self.check_for_updates_button)
        self.update_now_button = QPushButton("Update Now")
        self.update_now_button.clicked.connect(
            self._on_update_now_clicked
        )
        self.update_now_button.hide()
        version_button_row.addWidget(self.update_now_button)
        layout.addLayout(version_button_row, row, 0, 1, 3)
        row += 1

        layout.addWidget(self._separator(), row, 0, 1, 3)
        row += 1

        self.advanced_config_toggle = QPushButton("Advanced ▶")
        self.advanced_config_toggle.setCheckable(True)
        self.advanced_config_toggle.setChecked(False)
        self.advanced_config_toggle.setAccessibleName(
            "Toggle advanced settings"
        )
        self.advanced_config_toggle.toggled.connect(
            self._on_advanced_config_toggled
        )
        layout.addWidget(self.advanced_config_toggle, row, 0, 1, 3)
        row += 1

        self.advanced_config_content = QWidget()
        advanced_layout = QGridLayout(self.advanced_config_content)
        advanced_layout.setContentsMargins(8, 2, 0, 4)
        advanced_layout.setHorizontalSpacing(10)
        advanced_layout.setVerticalSpacing(5)
        advanced_layout.setColumnStretch(1, 1)
        advanced_row = 0

        advanced_layout.addWidget(
            self._heading("Server"),
            advanced_row,
            0,
            1,
            3,
        )
        advanced_row += 1

        advanced_layout.addWidget(QLabel("Preset"), advanced_row, 0)
        self.server_preset_combo = ThemeComboBox()
        self.server_preset_combo.addItems(list(SERVER_PRESETS.keys()))
        self.server_preset_combo.setCurrentText(
            str(self.server_preset_var.get())
        )
        self.server_preset_combo.currentTextChanged.connect(
            self.server_preset_var.set
        )
        self.server_preset_combo.currentTextChanged.connect(
            self._on_server_preset_changed
        )
        self.server_preset_var.bind(self.server_preset_combo.setCurrentText)
        advanced_layout.addWidget(
            self.server_preset_combo,
            advanced_row,
            1,
            1,
            2,
        )
        advanced_row += 1

        advanced_layout.addWidget(QLabel("Server URL"), advanced_row, 0)
        self.server_url_entry = QLineEdit()
        self.server_url_entry.setText(str(self.server_url_var.get()))
        self.server_url_entry.textChanged.connect(self.server_url_var.set)
        self.server_url_var.bind(self.server_url_entry.setText)
        advanced_layout.addWidget(
            self.server_url_entry,
            advanced_row,
            1,
            1,
            2,
        )
        advanced_row += 1

        advanced_layout.addWidget(
            self._description(
                "ntfy.sh is selected by default. On startup, the client "
                "requests up to 48 hours of cached encrypted history, "
                "subject to the server's actual retention period."
            ),
            advanced_row,
            0,
            1,
            3,
        )
        self.advanced_config_content.hide()
        layout.addWidget(self.advanced_config_content, row, 0, 1, 3)
        row += 1
        layout.setRowStretch(row, 1)

    def _on_advanced_config_toggled(self, expanded: bool) -> None:
        self.advanced_config_toggle.setText(
            "Advanced ▼" if expanded else "Advanced ▶"
        )
        self.advanced_config_content.setVisible(expanded)

    def _on_message_sound_selected(self, value: str) -> None:
        selected_sound = str(value)
        if selected_sound not in MESSAGE_SOUND_OPTIONS:
            return

        previous_sound = str(self.message_sound_var.get())
        if selected_sound == "Custom":
            dialog = QFileDialog(self.root, "Choose message sound")
            dialog.setOption(
                QFileDialog.Option.DontUseNativeDialog,
                True,
            )
            dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
            dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
            dialog.setNameFilter("Audio files (*.wav *.mp3 *.ogg)")
            self._apply_window_titlebar_theme(dialog)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                self.message_sound_combo.setCurrentText(previous_sound)
                return
            selected_files = dialog.selectedFiles()
            custom_path = selected_files[0] if selected_files else ""
            if not custom_path:
                self.message_sound_combo.setCurrentText(previous_sound)
                return
            self.custom_message_sound_path_var.set(custom_path)

        self.message_sound_var.set(selected_sound)
        self._play_message_sound(report_errors=True)

    def _on_message_sound_volume_changed(self, slider_value: int) -> None:
        volume_percent = max(1, min(10, int(slider_value))) * 10
        self.message_sound_volume_var.set(volume_percent)
        self.message_sound_volume_label.setText(
            f"Volume: {volume_percent}%"
        )
        self.message_sound_volume_slider.setToolTip(
            f"Volume: {volume_percent}%"
        )
        if self.active_message_sound_effect is not None:
            self.active_message_sound_effect.setVolume(
                volume_percent / 100.0
            )
        if self.compressed_message_sound_audio_output is not None:
            self.compressed_message_sound_audio_output.setVolume(
                volume_percent / 100.0
            )
        self._play_message_sound(report_errors=True)

    def _on_automatic_update_checks_toggled(self, checked: bool) -> None:
        self.automatic_update_checks_var.set(bool(checked))
        if checked and self.available_update is None:
            QTimer.singleShot(0, self._maybe_check_for_updates)

    def _maybe_check_for_updates(self) -> None:
        if not bool(self.automatic_update_checks_var.get()):
            return
        if time.monotonic() - self._last_update_check_started_at < 300:
            return
        self._check_for_updates(manual=False)

    def _check_for_updates(self, *, manual: bool) -> None:
        if self._closing or self._update_check_in_progress:
            return
        if parse_semantic_version(RUNNING_VERSION) is None:
            self.version_status_label.setText(
                "Update checks require a packaged build."
            )
            return

        self._update_check_in_progress = True
        self._last_update_check_started_at = time.monotonic()
        self.update_check_timer.start()
        self.check_for_updates_button.setEnabled(False)
        self.version_status_label.setStyleSheet("")
        self.version_status_label.setToolTip("")
        self.version_status_label.setText("Checking for updates...")
        threading.Thread(
            target=self._check_for_updates_worker,
            args=(manual,),
            daemon=True,
            name="SpriteLinkUpdateCheck",
        ).start()

    def _check_for_updates_worker(self, manual: bool) -> None:
        try:
            release = fetch_latest_release(
                UPDATE_REPOSITORY,
                current_version=RUNNING_VERSION,
            )
        except Exception as exc:
            error = str(exc) or "The update check failed."
            self.ui_queue.put((
                "update_check_result",
                {
                    "error": error,
                    "manual": manual,
                },
            ))
            return

        self.ui_queue.put((
            "update_check_result",
            {
                "release": release,
                "manual": manual,
            },
        ))

    def _handle_update_check_result(self, payload: dict[str, Any]) -> None:
        self._update_check_in_progress = False
        self.check_for_updates_button.setEnabled(
            not self._update_download_in_progress
        )

        error = str(payload.get("error", "")).strip()
        if error:
            if self.available_update is None:
                self.version_status_label.setText(
                    "Could not check for updates."
                )
                self.version_status_label.setStyleSheet("color: #b00020;")
            else:
                self.version_status_label.setText(
                    f"SpriteLink {self.available_update.version} is available."
                )
                self.version_status_label.setStyleSheet(
                    "color: #d97706; font-weight: 600;"
                )
            self.version_status_label.setToolTip(error)
            return

        release = payload.get("release")
        if not isinstance(release, ReleaseInfo):
            self.version_status_label.setText(
                "GitHub returned an invalid update response."
            )
            self.version_status_label.setStyleSheet("color: #b00020;")
            return

        self.version_status_label.setToolTip(release.page_url)
        if release_is_newer(RUNNING_VERSION, release.version):
            self.available_update = release
            self.version_status_label.setText(
                f"SpriteLink {release.version} is available."
            )
            self.version_status_label.setStyleSheet(
                "color: #d97706; font-weight: 600;"
            )
            self.update_now_button.setText(
                f"Update to {release.version}"
            )
            self.update_now_button.setToolTip(release.notes.strip())
            self.update_now_button.show()
            self._update_config_toggle_update_style()
            if self.config_overlay.isVisible():
                self.update_now_button.setFocus()
            return

        self.available_update = None
        self.version_status_label.setText("SpriteLink is up to date.")
        self.version_status_label.setStyleSheet("")
        self.update_now_button.hide()
        self.update_now_button.setToolTip("")
        self._update_config_toggle_update_style()

    def _on_update_now_clicked(self) -> None:
        release = self.available_update
        if release is None or self._update_download_in_progress:
            return
        if not messagebox.askyesno(
            "Update SpriteLink",
            f"Download and install SpriteLink {release.version} now?\n\n"
            "SpriteLink will close and reopen after the update.",
            parent=self.root,
        ):
            return
        if not self._save_settings():
            return

        self._update_download_in_progress = True
        self.update_now_button.setEnabled(False)
        self.check_for_updates_button.setEnabled(False)
        self.version_status_label.setStyleSheet("")
        self.version_status_label.setText(
            f"Downloading SpriteLink {release.version}..."
        )
        threading.Thread(
            target=self._download_update_worker,
            args=(release,),
            daemon=True,
            name="SpriteLinkUpdateDownload",
        ).start()

    def _download_update_worker(self, release: ReleaseInfo) -> None:
        try:
            installer_path = download_release_installer(
                release,
                UPDATE_DIRECTORY,
            )
        except Exception as exc:
            self.ui_queue.put((
                "update_download_failed",
                str(exc) or "The update download failed.",
            ))
            return
        self.ui_queue.put((
            "update_download_ready",
            {
                "path": str(installer_path),
                "version": release.version,
            },
        ))

    def _handle_update_download_failed(self, error: str) -> None:
        self._update_download_in_progress = False
        self.update_now_button.setEnabled(True)
        self.check_for_updates_button.setEnabled(True)
        self.version_status_label.setText("The update could not be installed.")
        self.version_status_label.setStyleSheet("color: #b00020;")
        self.version_status_label.setToolTip(error)
        messagebox.showerror(
            "Could not update SpriteLink",
            error,
            parent=self.root,
        )

    def _launch_downloaded_update(self, payload: dict[str, Any]) -> None:
        installer_path = Path(str(payload.get("path", "")))
        version = str(payload.get("version", ""))
        if not installer_path.is_file():
            self._handle_update_download_failed(
                "The downloaded installer could not be found."
            )
            return

        try:
            subprocess.Popen(
                [
                    str(installer_path),
                    "/SP-",
                    "/VERYSILENT",
                    "/SUPPRESSMSGBOXES",
                    "/NORESTART",
                    "/CLOSEAPPLICATIONS",
                ],
                cwd=str(installer_path.parent),
            )
        except OSError as exc:
            self._handle_update_download_failed(str(exc))
            return

        self.version_status_label.setText(
            f"Installing SpriteLink {version}..."
        )
        QTimer.singleShot(250, self.root.close)

    def _sync_config_overlay_geometry(self) -> None:
        self.config_overlay.setGeometry(self.chat_content.rect())

    def _config_ui_snapshot(self) -> tuple[Any, ...]:
        return (
            str(self.server_preset_var.get()),
            str(self.server_url_var.get()),
            str(self.message_sound_var.get()),
            int(self.message_sound_volume_var.get()),
            str(self.custom_message_sound_path_var.get()),
            bool(self.automatic_update_checks_var.get()),
        )

    def _set_config_toggle_checked(self, checked: bool) -> None:
        previous = self.config_toggle.blockSignals(True)
        self.config_toggle.setChecked(checked)
        self.config_toggle.blockSignals(previous)

    def _on_config_toggled(self, checked: bool) -> None:
        if checked:
            self._config_snapshot_at_open = self._config_ui_snapshot()
            self._sync_config_overlay_geometry()
            self.config_overlay.show()
            self.config_overlay.raise_()
            (
                self.update_now_button
                if self.available_update is not None
                else self.theme_combo
            ).setFocus()
            return

        self._dismiss_config_popup()

    def _dismiss_config_popup(self) -> bool:
        if not self.config_overlay.isVisible():
            self._set_config_toggle_checked(False)
            return True

        current_snapshot = self._config_ui_snapshot()
        if self._config_snapshot_at_open is not None:
            changed = current_snapshot != self._config_snapshot_at_open
            server_changed = (
                current_snapshot[:2]
                != self._config_snapshot_at_open[:2]
            )
            if changed:
                save_succeeded = (
                    self._save_and_reconnect()
                    if server_changed
                    else self._save_settings()
                )
                if not save_succeeded:
                    self._set_config_toggle_checked(True)
                    return False

        self.config_overlay.hide()
        self._set_config_toggle_checked(False)
        self._config_snapshot_at_open = None
        self.message_entry.setFocus()
        return True

    def _apply_server_preset_state(self) -> None:
        preset = str(self.server_preset_var.get())
        self.server_url_entry.setReadOnly(
            preset != "Custom ntfy server"
        )

    def _on_server_preset_changed(self, _value: Any = None) -> None:
        preset = str(self.server_preset_var.get())
        preset_url = SERVER_PRESETS.get(preset, "")

        if preset != "Custom ntfy server":
            self.server_url_var.set(preset_url)

        self._apply_server_preset_state()

    def _on_theme_changed(self, value: Any) -> None:
        theme = str(value)
        if theme not in THEMES:
            theme = DEFAULT_THEME

        self.theme_var.set(theme)
        self.config_data["theme"] = theme
        self._apply_theme()
        self._apply_application_font_strategy()
        self._apply_active_composer_style()
        if hasattr(self, "chat_display"):
            self._rerender_preserving_scroll()

        try:
            save_config(self.config_data)
        except Exception as exc:
            messagebox.showerror(
                "Could not save theme",
                str(exc),
                parent=self.root,
            )

    def _validate_current_settings(self) -> str:
        server_url = normalize_server_url(str(self.server_url_var.get()))

        if not server_url.startswith(("http://", "https://")):
            raise ValueError(
                "The server URL must begin with http:// or https://."
            )
        return server_url

    def _copy_ui_to_config(self) -> None:
        server_url = self._validate_current_settings()

        self.config_data["server_preset"] = self.server_preset_var.get()
        self.config_data["server_url"] = server_url
        theme = str(self.theme_var.get())
        self.config_data["theme"] = (
            theme if theme in THEMES else DEFAULT_THEME
        )
        message_sound = str(self.message_sound_var.get())
        self.config_data["message_sound"] = (
            message_sound
            if message_sound in MESSAGE_SOUND_OPTIONS
            else DEFAULT_MESSAGE_SOUND
        )
        self.config_data["message_sound_volume"] = max(
            10,
            min(100, int(self.message_sound_volume_var.get())),
        )
        self.config_data["custom_message_sound_path"] = str(
            self.custom_message_sound_path_var.get()
        )
        self.config_data["automatic_update_checks"] = bool(
            self.automatic_update_checks_var.get()
        )
        self.config_data.pop("chime_enabled", None)

    def _save_settings(self) -> bool:
        try:
            self._copy_ui_to_config()
            save_config(self.config_data)
        except Exception as exc:
            messagebox.showerror(
                "Could not save settings",
                str(exc),
                parent=self.root,
            )
            return False
        return True

    def _save_and_reconnect(self) -> bool:
        if not self._save_settings():
            return False

        self._clear_visible_room()
        self._apply_theme()
        self._apply_application_font_strategy()
        self._apply_active_composer_style()
        self._load_saved_history_for_current_room()
        self.initial_history_pending_rooms.update(
            room["id"] for room in self._chatroom_definitions()
        )
        self.status_var.set("Reconnecting")
        self._request_network_refresh(poll_immediately=True)
        return True

    def _build_draft_message(self, text: str) -> dict[str, Any]:
        profile = self._active_room_profile()

        message = {
            "v": APP_VERSION,
            "i": self.draft_message_id,
            "u": profile["username"],
            "k": profile["username_color"],
            "f": profile["font"],
            "o": profile["text_color"],
            "t": int(time.time()),
            "m": text,
        }
        if profile["profile_icon"]:
            message["p"] = profile["profile_icon"]
        return sign_message_identity(
            message,
            self._active_chatroom()["key"],
            self._identity_private_key(),
        )

    def _schedule_composer_update(self) -> None:
        self.message_resize_timer.start(0)
        self.message_size_check_pending = True
        self.message_size_timer.start(MESSAGE_SIZE_DEBOUNCE_MS)

        # A stale over-limit result should not block a click after the user
        # shortens the message. Send performs its own immediate exact check.
        self.send_button.setEnabled(True)
        self._draw_message_size_bar()

    def _resize_message_entry(self) -> None:
        document = self.message_entry.document()
        document.setTextWidth(max(1, self.message_entry.viewport().width()))
        line_height = max(
            1,
            QFontMetrics(self.message_entry.font()).lineSpacing(),
        )
        document_margins = int(document.documentMargin() * 2)
        document.size()  # Force wrapped line layouts to update.
        display_lines = 0
        block = document.begin()
        while block.isValid():
            display_lines += max(1, block.layout().lineCount())
            block = block.next()
        display_lines = max(1, display_lines)
        visible_lines = max(
            MESSAGE_ENTRY_MIN_LINES,
            min(MESSAGE_ENTRY_MAX_LINES, display_lines),
        )
        margins = (
            self.message_entry.frameWidth() * 2
            + document_margins
            + 2
        )
        self.message_entry.setFixedHeight(
            visible_lines * line_height + margins
        )

        if display_lines > MESSAGE_ENTRY_MAX_LINES:
            self.message_entry.ensureCursorVisible()
            scrollbar = self.message_entry.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    def _cancel_pending_message_size_check(self) -> None:
        self.message_size_timer.stop()
        self.message_size_check_pending = False

    def _run_message_size_check(self) -> None:
        self.message_size_check_pending = False
        text = self.message_entry.toPlainText()

        try:
            draft_message = self._build_draft_message(text)
            self.current_estimated_packet_size = estimate_opaque_packet_size(
                draft_message
            )
        except Exception:
            self.current_estimated_packet_size = NTFY_MAX_BODY_BYTES

        self._draw_message_size_bar()

    def _daily_sent_message_count(self) -> int:
        today_utc = current_utc_day_number()
        try:
            stored_day = int(
                self.config_data.get("sent_message_utc_day", today_utc)
            )
        except (TypeError, ValueError):
            stored_day = today_utc
            self.config_data["sent_message_utc_day"] = today_utc
            self.config_data["sent_messages_today"] = 0
        if stored_day != today_utc:
            self.config_data["sent_message_utc_day"] = today_utc
            self.config_data["sent_messages_today"] = 0
            try:
                save_config(self.config_data)
            except Exception:
                pass
            return 0
        try:
            return max(
                0,
                int(self.config_data.get("sent_messages_today", 0)),
            )
        except (TypeError, ValueError):
            self.config_data["sent_messages_today"] = 0
            return 0

    def _messages_left_today(self) -> int:
        return max(
            0,
            NTFY_DAILY_MESSAGE_LIMIT - self._daily_sent_message_count(),
        )

    def _record_successful_send(self) -> None:
        sent_messages = self._daily_sent_message_count() + 1
        self.config_data["sent_message_utc_day"] = current_utc_day_number()
        self.config_data["sent_messages_today"] = sent_messages
        try:
            save_config(self.config_data)
        except Exception:
            pass
        self._draw_message_size_bar()
        if (
            hasattr(self, "message_limit_overlay")
            and self.message_limit_overlay.isVisible()
        ):
            self._update_message_limit_popup()

    def _schedule_utc_midnight_reset(self) -> None:
        now = time.time()
        next_midnight = next_utc_midnight_timestamp()
        delay_ms = max(1000, int((next_midnight - now) * 1000) + 250)
        self.message_limit_reset_timer.start(delay_ms)

    def _reset_daily_sent_message_count(self) -> None:
        self.config_data["sent_message_utc_day"] = current_utc_day_number()
        self.config_data["sent_messages_today"] = 0
        try:
            save_config(self.config_data)
        except Exception:
            pass
        if hasattr(self, "message_size_bar"):
            self._draw_message_size_bar()
        if (
            hasattr(self, "message_limit_overlay")
            and self.message_limit_overlay.isVisible()
        ):
            self._update_message_limit_popup()
        self._schedule_utc_midnight_reset()

    def _draw_message_size_bar(self) -> None:
        packet_size = max(0, int(self.current_estimated_packet_size))
        at_or_over_limit = packet_size >= NTFY_MAX_BODY_BYTES

        self.message_size_bar.setValue(
            min(packet_size, NTFY_MAX_BODY_BYTES)
        )
        if self._is_windows_classic_theme():
            self.message_size_bar.setStyleSheet(
                "QProgressBar { background-color: #ffffff;"
                " border-top: 2px solid #808080;"
                " border-left: 2px solid #808080;"
                " border-right: 2px solid #ffffff;"
                " border-bottom: 2px solid #ffffff;"
                " border-radius: 0px; color: "
                + ("#ffffff" if at_or_over_limit else "#000000")
                + "; text-align: center; }"
                " QProgressBar::chunk { background-color: "
                + ("#800000" if at_or_over_limit else "#007f82")
                + "; }"
            )
        else:
            self.message_size_bar.setStyleSheet(
                "QProgressBar { background-color: #eeeeee; "
                "border: 1px solid #a8a8a8; color: "
                + ("#ffffff" if at_or_over_limit else "#202020")
                + "; text-align: center; } "
                "QProgressBar::chunk { background: "
                + ("#303030" if at_or_over_limit else "#b8b8b8")
                + "; }"
            )

        messages_left = self._messages_left_today()
        message_limit_suffix = (
            f"     ({messages_left} messages left)"
            if messages_left <= MESSAGE_LIMIT_BAR_THRESHOLD
            else ""
        )
        if self.message_size_check_pending:
            self.message_size_bar.setFormat(
                f"Calculating...{message_limit_suffix}"
            )
        else:
            self.message_size_bar.setFormat(
                f"{packet_size / 1024.0:.1f} KB / "
                f"{NTFY_MAX_BODY_BYTES / 1024.0:.1f} KB"
                f"{message_limit_suffix}"
            )

        self.send_button.setEnabled(
            self.message_size_check_pending or not at_or_over_limit
        )

    def _send_current_message(self) -> None:
        text = self.message_entry.toPlainText()

        if not text.strip():
            return

        if len(text) > MAX_MESSAGE_CHARS:
            messagebox.showwarning(
                "Message too long",
                f"Messages are limited to {MAX_MESSAGE_CHARS} characters.",
                parent=self.root,
            )
            return

        try:
            server_url = self._validate_current_settings()
        except Exception as exc:
            messagebox.showerror(
                "Cannot send message",
                str(exc),
                parent=self.root,
            )
            return

        encryption_key = self._active_chatroom()["key"]

        if not self.connected:
            messagebox.showwarning(
                "Not connected",
                "The client is not currently connected to the selected server.",
                parent=self.root,
            )
            return

        message = self._build_draft_message(text)
        message["t"] = int(time.time())
        sign_message_identity(
            message,
            encryption_key,
            self._identity_private_key(),
        )

        self._cancel_pending_message_size_check()
        estimated_size = estimate_opaque_packet_size(message)
        self.current_estimated_packet_size = estimated_size
        self._draw_message_size_bar()

        if estimated_size >= NTFY_MAX_BODY_BYTES:
            messagebox.showwarning(
                "Encrypted message too large",
                (
                    f"The encrypted packet would be {estimated_size:,} bytes. "
                    f"It must remain below {NTFY_MAX_BODY_BYTES:,} bytes."
                ),
                parent=self.root,
            )
            return

        try:
            packet = make_opaque_packet(message, encryption_key)
        except Exception as exc:
            messagebox.showerror(
                "Encryption failed",
                str(exc),
                parent=self.root,
            )
            return

        packet_size = len(packet.encode("utf-8"))
        if packet_size >= NTFY_MAX_BODY_BYTES:
            messagebox.showwarning(
                "Encrypted message too large",
                (
                    f"The encrypted packet is {packet_size:,} bytes. "
                    f"It must remain below {NTFY_MAX_BODY_BYTES:,} bytes."
                ),
                parent=self.root,
            )
            return

        self.send_queue.put({
            "server_url": server_url,
            "encryption_key": encryption_key,
            "room_id": self.active_chatroom_id,
            "packet": packet,
            "message": message,
        })

        self.message_entry.clear()
        self.draft_message_id = uuid.uuid4().hex
        self._resize_message_entry()
        self._run_message_size_check()
        self.seen_client_message_ids.add(message["i"])
        self._add_message_to_log(message, is_local=True)

    def _start_network_thread(self) -> None:
        if self.network_thread and self.network_thread.is_alive():
            return

        self.network_thread = threading.Thread(
            target=self._network_loop,
            name="SpriteLinkNetwork",
            daemon=True,
        )
        self.network_thread.start()

    def _network_loop(self) -> None:
        last_poll_times: dict[str, float] = {}
        last_background_poll_at: float | None = None
        force_active_poll = True
        deferred_active_poll_started_at: float | None = None
        scheduled_active_room_id = ""

        while not self.stop_event.is_set():
            latest_control: dict[str, Any] | None = None
            while True:
                try:
                    latest_control = self.network_control_queue.get_nowait()
                except queue.Empty:
                    break

            while True:
                try:
                    outbound = self.send_queue.get_nowait()
                except queue.Empty:
                    break
                self._network_send(outbound)

            now = time.monotonic()
            rooms = self._chatroom_definitions()
            active_room = next(
                (
                    room for room in rooms
                    if room["id"] == self.active_chatroom_id
                ),
                rooms[0],
            )
            active_room_id = active_room["id"]
            window_focused = self.window_focused_event.is_set()
            active_interval = (
                FOCUSED_ACTIVE_POLL_INTERVAL_SECONDS
                if window_focused
                else UNFOCUSED_ACTIVE_POLL_INTERVAL_SECONDS
            )
            background_interval = (
                FOCUSED_BACKGROUND_POLL_INTERVAL_SECONDS
                if window_focused
                else UNFOCUSED_BACKGROUND_POLL_INTERVAL_SECONDS
            )

            if latest_control is not None:
                requested_room_id = str(latest_control.get("room_id", ""))
                if requested_room_id == active_room_id:
                    self.connected = False
                    poll_immediately = bool(
                        latest_control.get("poll_immediately", False)
                    )
                    force_active_poll = poll_immediately
                    deferred_active_poll_started_at = (
                        None if poll_immediately else now
                    )
                    scheduled_active_room_id = active_room_id
                    self.ui_queue.put((
                        "status",
                        (
                            "Connecting",
                            "Applying saved configuration...",
                            active_room_id,
                        ),
                    ))

            if active_room_id != scheduled_active_room_id:
                scheduled_active_room_id = active_room_id
                force_active_poll = True
                deferred_active_poll_started_at = None

            valid_room_ids = {room["id"] for room in rooms}
            for room_id in tuple(last_poll_times):
                if room_id not in valid_room_ids:
                    last_poll_times.pop(room_id, None)

            active_last_polled = last_poll_times.get(active_room_id)
            active_poll_due = force_active_poll or (
                (
                    deferred_active_poll_started_at is None
                    or now - deferred_active_poll_started_at
                    >= active_interval
                )
                and (
                    active_last_polled is None
                    or now - active_last_polled >= active_interval
                )
            )
            background_rooms = [
                room for room in rooms
                if room["id"] != active_room_id
            ]
            background_poll_due = bool(background_rooms) and (
                last_background_poll_at is None
                or now - last_background_poll_at >= background_interval
            )

            room_to_poll: dict[str, str] | None = None
            poll_is_active = False
            if active_poll_due:
                room_to_poll = active_room
                poll_is_active = True
            elif background_poll_due:
                room_to_poll = min(
                    background_rooms,
                    key=lambda room: last_poll_times.get(
                        room["id"],
                        float("-inf"),
                    ),
                )

            if room_to_poll is not None:
                self._network_poll(
                    room_to_poll,
                    is_active=poll_is_active,
                )
                completed_at = time.monotonic()
                last_poll_times[room_to_poll["id"]] = completed_at
                if poll_is_active:
                    force_active_poll = False
                    deferred_active_poll_started_at = None
                else:
                    last_background_poll_at = completed_at

            self.stop_event.wait(0.08)

    def _network_send(self, outbound: dict[str, Any]) -> None:
        try:
            topic = derive_ntfy_topic(outbound["encryption_key"])
            response = self.session.post(
                f"{outbound['server_url']}/{topic}",
                data=outbound["packet"].encode("utf-8"),
                headers={
                    "Content-Type": "text/plain; charset=utf-8",
                    "X-Firebase": "no",
                    "X-Cache": "yes",
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except Exception as exc:
            self.ui_queue.put((
                "send_failed",
                {
                    "message": outbound["message"],
                    "error": str(exc),
                    "room_id": outbound.get("room_id"),
                },
            ))
        else:
            self.ui_queue.put((
                "send_succeeded",
                {
                    "room_id": outbound.get("room_id"),
                    "message_id": outbound["message"].get("i"),
                },
            ))

    def _current_poll_since(
        self,
        state: dict[str, Any],
        room_id: str,
    ) -> str:
        if room_id in self.initial_history_pending_rooms:
            return f"{AUTO_HISTORY_SECONDS}s"

        newest_id = state.get("newest_ntfy_id")
        if isinstance(newest_id, str) and newest_id:
            return newest_id

        return f"{AUTO_HISTORY_SECONDS}s"

    def _network_poll(
        self,
        room: dict[str, str],
        *,
        is_active: bool,
    ) -> None:
        server_url = normalize_server_url(
            str(self.config_data.get("server_url", ""))
        )
        room_id = room["id"]
        encryption_key = room["key"]

        if not server_url or not encryption_key:
            if is_active and room_id == self.active_chatroom_id:
                self.connected = False
                self.ui_queue.put((
                    "status",
                    ("Disconnected", "No chatroom is selected.", room_id),
                ))
            return

        try:
            topic = derive_ntfy_topic(encryption_key)
            scope_id = room_scope_id(server_url, encryption_key)
            state = self.config_data.setdefault("room_state", {}).setdefault(
                scope_id,
                {},
            )
            was_initial_history_scan = (
                room_id in self.initial_history_pending_rooms
            )
            since_value = self._current_poll_since(state, room_id)

            response = self.session.get(
                f"{server_url}/{topic}/json",
                params={
                    "poll": "1",
                    "since": since_value,
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            records = parse_ntfy_ndjson(response)
            if self._find_chatroom(room_id) is None:
                return
            self.initial_history_pending_rooms.discard(room_id)

            if was_initial_history_scan:
                completed_at = int(time.time())
                state["history_scan_start_time"] = max(
                    0,
                    completed_at - AUTO_HISTORY_SECONDS,
                )
                state["history_scan_completed_at"] = completed_at

            decoded_messages: list[dict[str, Any]] = []
            failed_decryptions = 0
            newest_record_id: str | None = None
            newest_record_time = int(state.get("newest_time", 0) or 0)

            for record in records:
                ntfy_id = record.get("id")
                ntfy_time = record.get("time")
                packet = record.get("message")

                if not isinstance(ntfy_id, str):
                    continue
                if not isinstance(ntfy_time, int):
                    continue
                if not isinstance(packet, str):
                    continue

                newest_record_id = ntfy_id
                newest_record_time = max(newest_record_time, ntfy_time)

                try:
                    message = open_opaque_packet(packet, encryption_key)
                    self._validate_decrypted_message(
                        message,
                        encryption_key,
                    )
                except Exception:
                    failed_decryptions += 1
                    continue

                decoded_messages.append({
                    "ntfy_id": ntfy_id,
                    "ntfy_time": ntfy_time,
                    "message": message,
                })

            if newest_record_id:
                state["newest_ntfy_id"] = newest_record_id
                state["newest_time"] = newest_record_time

            if newest_record_id or was_initial_history_scan:
                try:
                    save_config(self.config_data)
                except Exception:
                    pass

            if (
                is_active
                and room_id == self.active_chatroom_id
                and not self.connected
            ):
                self.connected = True
                self.ui_queue.put((
                    "status",
                    ("Connected", server_url, room_id),
                ))

            if decoded_messages or was_initial_history_scan:
                self.ui_queue.put((
                    "messages",
                    {
                        "items": decoded_messages,
                        "history_scan": was_initial_history_scan,
                        "room_id": room_id,
                    },
                ))

            if failed_decryptions:
                self.ui_queue.put((
                    "decrypt_failures",
                    failed_decryptions,
                ))

        except Exception as exc:
            if is_active and room_id == self.active_chatroom_id:
                self.connected = False
                self.ui_queue.put((
                    "status",
                    ("Disconnected", str(exc), room_id),
                ))

    @staticmethod
    def _validate_decrypted_message(
        message: dict[str, Any],
        encryption_key: str,
    ) -> None:
        required = {
            "a": int,
            "v": int,
            "i": str,
            "c": str,
            "q": str,
            "s": str,
            "u": str,
            "k": str,
            "t": int,
            "m": str,
        }

        for key, expected_type in required.items():
            if not isinstance(message.get(key), expected_type):
                raise ValueError(
                    f"Message field {key!r} has the wrong type."
                )

        if message["v"] != APP_VERSION:
            raise ValueError("Message version is unsupported.")
        if len(message["i"]) > 128:
            raise ValueError("Message ID is too long.")
        if not re.fullmatch(r"[0-9a-f]{64}", message["c"]):
            raise ValueError("Client ID is invalid.")
        if len(message["u"]) > 32:
            raise ValueError("Username is too long.")
        if len(message["m"]) > MAX_MESSAGE_CHARS:
            raise ValueError("Message text is too long.")
        if not QColor(message["k"]).isValid():
            raise ValueError("Username color is invalid.")

        font_name = message.get("f", DEFAULT_MESSAGE_FONT)
        if (
            not isinstance(font_name, str)
            or font_name not in SUPPORTED_MESSAGE_FONTS
        ):
            raise ValueError("Message font is unsupported.")

        text_color = message.get("o", DEFAULT_MESSAGE_TEXT_COLOR)
        if not isinstance(text_color, str) or not QColor(text_color).isValid():
            raise ValueError("Message text color is invalid.")

        profile_icon = message.get("p", "")
        if not isinstance(profile_icon, str):
            raise ValueError("Message profile icon has the wrong type.")
        if profile_icon:
            decode_profile_icon(profile_icon)

        verify_message_identity(message, encryption_key)

    def _process_ui_queue(self) -> None:
        try:
            while True:
                event_type, payload = self.ui_queue.get_nowait()

                if event_type == "status":
                    status, _detail, room_id = payload
                    if room_id == self.active_chatroom_id:
                        self.status_var.set(status)

                elif event_type == "messages":
                    room_id = str(payload.get("room_id", ""))
                    items = payload.get("items", [])
                    history_scan = bool(payload.get("history_scan", False))
                    if room_id != self.active_chatroom_id:
                        self._accept_background_messages(
                            room_id,
                            items,
                            history_scan=history_scan,
                        )
                        continue
                    added = 0

                    for item in items:
                        if self._accept_network_message(
                            item,
                            persist=False,
                            render=False,
                            play_chime=not history_scan,
                        ):
                            added += 1

                    if added:
                        self.message_log.sort(key=self._message_sort_key)
                        self._persist_local_history()
                        self._render_message_log(scroll_to_bottom=True)

                elif event_type == "send_succeeded":
                    self._record_successful_send()

                elif event_type == "send_failed":
                    if payload.get("room_id") != self.active_chatroom_id:
                        continue
                    message = payload["message"]
                    self._append_system_message(
                        f"Message could not be uploaded: {payload['error']}",
                        warning=True,
                    )
                    self._append_system_message(
                        f"Unsent text from {message['u']}: {message['m']}",
                        warning=True,
                    )

                elif event_type == "image_preview_loaded":
                    url = str(payload.get("url", ""))
                    data = payload.get("data")
                    self.pending_image_previews.discard(url)
                    image: QImage | None = None
                    if isinstance(data, bytes) and data:
                        candidate = QImage.fromData(data)
                        if not candidate.isNull():
                            candidate.setDevicePixelRatio(1.0)
                            image = candidate
                    self.image_preview_cache[url] = image
                    while (
                        len(self.image_preview_cache)
                        > IMAGE_PREVIEW_CACHE_LIMIT
                    ):
                        oldest_url = next(iter(self.image_preview_cache))
                        self.image_preview_cache.pop(oldest_url, None)
                    if image is not None and any(
                        url in str(item.get("message", {}).get("m", ""))
                        for item in self.message_log
                        if isinstance(item, dict)
                    ):
                        self._rerender_preserving_scroll()

                elif event_type == "update_check_result":
                    if isinstance(payload, dict):
                        self._handle_update_check_result(payload)

                elif event_type == "update_download_failed":
                    self._handle_update_download_failed(str(payload))

                elif event_type == "update_download_ready":
                    if isinstance(payload, dict):
                        self._launch_downloaded_update(payload)

                elif event_type == "decrypt_failures":
                    pass

        except queue.Empty:
            pass

    def _accept_background_messages(
        self,
        room_id: str,
        items: list[dict[str, Any]],
        *,
        history_scan: bool,
    ) -> None:
        room = self._find_chatroom(room_id)
        if room is None:
            return

        server_url = normalize_server_url(
            str(self.config_data.get("server_url", ""))
        )
        encryption_key = room["key"]
        if not server_url or not encryption_key:
            return

        try:
            scope_id = room_scope_id(server_url, encryption_key)
        except Exception:
            return

        all_history = self.config_data.setdefault("history", {})
        history = all_history.get(scope_id, [])
        if not isinstance(history, list):
            history = []

        seen_ntfy_ids = {
            str(entry.get("ntfy_id"))
            for entry in history
            if isinstance(entry, dict)
            and isinstance(entry.get("ntfy_id"), str)
            and entry.get("ntfy_id")
        }
        seen_client_message_ids = {
            str(entry["message"].get("i"))
            for entry in history
            if isinstance(entry, dict)
            and isinstance(entry.get("message"), dict)
            and isinstance(entry["message"].get("i"), str)
        }
        muted_user_ids = self._room_preference_ids_for_key(
            "muted_users",
            encryption_key,
        )
        added = 0
        unread_added = 0

        for item in items:
            if not isinstance(item, dict):
                continue
            ntfy_id = item.get("ntfy_id")
            message = item.get("message")
            if not isinstance(ntfy_id, str) or not isinstance(message, dict):
                continue
            client_message_id = message.get("i")
            if not isinstance(client_message_id, str):
                continue
            if ntfy_id in seen_ntfy_ids:
                continue
            if client_message_id in seen_client_message_ids:
                seen_ntfy_ids.add(ntfy_id)
                continue

            seen_ntfy_ids.add(ntfy_id)
            seen_client_message_ids.add(client_message_id)
            is_local = message.get("c") == self._authenticated_client_id()
            history.append({
                "message": message,
                "warning": None,
                "ntfy_id": ntfy_id,
                "ntfy_time": int(
                    item.get("ntfy_time", message.get("t", 0)) or 0
                ),
            })
            added += 1

            if not history_scan and not is_local:
                unread_added += 1
                if (
                    self._should_play_message_sound(room_id)
                    and not self._is_chatroom_muted(room_id)
                    and str(message.get("c", "")) not in muted_user_ids
                ):
                    self._play_message_sound()

        if not added:
            return

        history.sort(key=self._message_sort_key)
        all_history[scope_id] = history[-1000:]
        if unread_added:
            unread_counts = self._unread_counts()
            unread_counts[room_id] = (
                int(unread_counts.get(room_id, 0) or 0) + unread_added
            )

        try:
            save_config(self.config_data)
        except Exception:
            pass
        if unread_added:
            self._refresh_chatroom_list()

    def _accept_network_message(
        self,
        item: dict[str, Any],
        *,
        persist: bool = True,
        render: bool = True,
        play_chime: bool = True,
    ) -> bool:
        ntfy_id = item["ntfy_id"]
        message = item["message"]
        client_message_id = message["i"]

        if ntfy_id in self.seen_ntfy_message_ids:
            return False

        if client_message_id in self.seen_client_message_ids:
            self.seen_ntfy_message_ids.add(ntfy_id)
            return False

        self.seen_ntfy_message_ids.add(ntfy_id)
        self.seen_client_message_ids.add(client_message_id)

        is_local = message["c"] == self._authenticated_client_id()

        self._add_message_to_log(
            message,
            is_local=is_local,
            warning=None,
            ntfy_id=ntfy_id,
            ntfy_time=item["ntfy_time"],
            persist=persist,
            render=render,
        )

        if (
            play_chime
            and not is_local
            and self._should_play_message_sound(self.active_chatroom_id)
            and not self._is_chatroom_muted(self.active_chatroom_id)
            and not self._is_user_muted(str(message["c"]))
        ):
            self._play_message_sound()

        return True

    @staticmethod
    def _message_sort_key(item: dict[str, Any]) -> tuple[int, int, str]:
        message = item["message"]
        return (
            int(item.get("ntfy_time", message.get("t", 0))),
            int(message.get("t", 0)),
            str(item.get("ntfy_id") or message.get("i", "")),
        )

    def _add_message_to_log(
        self,
        message: dict[str, Any],
        *,
        is_local: bool,
        warning: str | None = None,
        ntfy_id: str | None = None,
        ntfy_time: int | None = None,
        persist: bool = True,
        render: bool = True,
    ) -> None:
        self.message_log.append({
            "message": message,
            "is_local": is_local,
            "warning": warning,
            "ntfy_id": ntfy_id,
            "ntfy_time": ntfy_time or int(message.get("t", time.time())),
        })

        self.message_log.sort(key=self._message_sort_key)
        if persist:
            self._persist_local_history()
        if render:
            self._render_message_log(scroll_to_bottom=True)

    def _load_saved_history_for_current_room(self) -> None:
        server_url = normalize_server_url(str(self.config_data.get("server_url", "")))
        encryption_key = self._active_chatroom()["key"]

        if not server_url or not encryption_key:
            return

        try:
            scope_id = room_scope_id(server_url, encryption_key)
        except Exception:
            return

        entries = self.config_data.setdefault("history", {}).get(scope_id, [])
        if not isinstance(entries, list):
            return

        for item in entries[-1000:]:
            if not isinstance(item, dict):
                continue

            message = item.get("message")
            if not isinstance(message, dict):
                continue

            try:
                self._validate_decrypted_message(
                    message,
                    encryption_key,
                )
            except Exception:
                continue

            client_message_id = message["i"]
            if client_message_id in self.seen_client_message_ids:
                continue

            ntfy_id = item.get("ntfy_id")
            if isinstance(ntfy_id, str) and ntfy_id:
                self.seen_ntfy_message_ids.add(ntfy_id)
            else:
                ntfy_id = None

            self.seen_client_message_ids.add(client_message_id)
            self.message_log.append({
                "message": message,
                "is_local": (
                    message["c"] == self._authenticated_client_id()
                ),
                "warning": None,
                "ntfy_id": ntfy_id,
                "ntfy_time": int(item.get("ntfy_time", message.get("t", 0)) or 0),
            })

        self.message_log.sort(key=self._message_sort_key)
        if self.message_log:
            self._render_message_log(scroll_to_bottom=True)

    def _persist_local_history(self) -> None:
        server_url = normalize_server_url(str(self.config_data.get("server_url", "")))
        encryption_key = self._active_chatroom()["key"]

        if not server_url or not encryption_key:
            return

        try:
            scope_id = room_scope_id(server_url, encryption_key)
        except Exception:
            return

        serializable = []
        for item in self.message_log[-1000:]:
            serializable.append({
                "message": item["message"],
                "warning": item.get("warning"),
                "ntfy_id": item.get("ntfy_id"),
                "ntfy_time": int(item.get("ntfy_time", 0) or 0),
            })

        self.config_data.setdefault("history", {})[scope_id] = serializable
        try:
            save_config(self.config_data)
        except Exception:
            pass

    @staticmethod
    def _display_timestamp_for_item(item: dict[str, Any]) -> int:
        message = item["message"]
        return int(item.get("ntfy_time", message.get("t", 0)) or 0)

    @staticmethod
    def _format_hover_timestamp(timestamp: int) -> str:
        try:
            local_time = datetime.fromtimestamp(timestamp)
            clock = local_time.strftime("%I:%M %p").lstrip("0")
            return (
                f"{local_time.strftime('%b')} {local_time.day}, "
                f"{local_time.year}; {clock}"
            )
        except Exception:
            return "Unknown time"

    @staticmethod
    def _local_datetime(timestamp: int) -> datetime | None:
        try:
            return datetime.fromtimestamp(timestamp)
        except Exception:
            return None

    def _current_room_scope_id(self) -> str | None:
        server_url = normalize_server_url(
            str(self.config_data.get("server_url", ""))
        )
        encryption_key = self._active_chatroom()["key"]

        if not server_url or not encryption_key:
            return None

        try:
            return room_scope_id(server_url, encryption_key)
        except Exception:
            return None

    def _room_preference_ids(self, category: str) -> set[str]:
        return self._room_preference_ids_for_key(
            category,
            self._active_chatroom()["key"],
        )

    def _room_preference_ids_for_key(
        self,
        category: str,
        encryption_key: str,
    ) -> set[str]:
        server_url = normalize_server_url(
            str(self.config_data.get("server_url", ""))
        )
        if not server_url or not encryption_key:
            return set()

        try:
            scope_id = room_scope_id(server_url, encryption_key)
        except Exception:
            return set()

        category_map = self.config_data.setdefault(category, {})
        raw_values = category_map.get(scope_id, [])

        if not isinstance(raw_values, list):
            raw_values = []
            category_map[scope_id] = raw_values

        return {
            str(value)
            for value in raw_values
            if isinstance(value, str) and value
        }

    def _write_room_preference_ids(
        self,
        category: str,
        values: set[str],
    ) -> None:
        scope_id = self._current_room_scope_id()
        if scope_id is None:
            return

        self.config_data.setdefault(category, {})[scope_id] = sorted(values)

        try:
            save_config(self.config_data)
        except Exception:
            pass

    def _is_user_muted(self, client_id: str) -> bool:
        return client_id in self._room_preference_ids("muted_users")

    def _set_user_muted(self, client_id: str, muted: bool) -> None:
        muted_ids = self._room_preference_ids("muted_users")

        if muted:
            muted_ids.add(client_id)
        else:
            muted_ids.discard(client_id)

        self._write_room_preference_ids("muted_users", muted_ids)
        self._rerender_preserving_scroll()

    def _set_user_image_trusted(
        self,
        client_id: str,
        trusted: bool,
    ) -> None:
        trusted_ids = self._room_preference_ids("trusted_image_users")

        if trusted:
            trusted_ids.add(client_id)
        else:
            trusted_ids.discard(client_id)

        self._write_room_preference_ids(
            "trusted_image_users",
            trusted_ids,
        )
        self._rerender_preserving_scroll()

    def _set_message_collapsed(
        self,
        message_id: str,
        collapsed: bool,
    ) -> None:
        collapsed_ids = self._room_preference_ids("collapsed_messages")

        if collapsed:
            collapsed_ids.add(message_id)
        else:
            collapsed_ids.discard(message_id)

        self._write_room_preference_ids(
            "collapsed_messages",
            collapsed_ids,
        )
        self._rerender_preserving_scroll()

    def _rerender_preserving_scroll(self) -> None:
        scrollbar = self.chat_display.verticalScrollBar()
        maximum = max(1, scrollbar.maximum())
        fraction = scrollbar.value() / maximum
        self._render_message_log(scroll_to_bottom=False)
        scrollbar = self.chat_display.verticalScrollBar()
        scrollbar.setValue(round(fraction * scrollbar.maximum()))

    def _schedule_chat_tooltip(
        self,
        message_id: str,
        global_position: QPoint,
    ) -> None:
        if message_id == self._hovered_message_id:
            if self.chat_tooltip_timer.isActive():
                self._pending_tooltip_global_position = QPoint(
                    global_position
                )
            return

        self._hide_chat_tooltip()
        self._hovered_message_id = message_id
        self._pending_tooltip_message_id = message_id
        self._pending_tooltip_global_position = QPoint(global_position)
        self.chat_tooltip_timer.start(CHAT_TOOLTIP_HOVER_DELAY_MS)

    def _show_pending_chat_tooltip(self) -> None:
        message_id = self._pending_tooltip_message_id
        self._pending_tooltip_message_id = None
        if not message_id or message_id != self._hovered_message_id:
            return
        tooltip = self.rendered_tooltips.get(message_id, "")
        if tooltip:
            QToolTip.showText(
                self._pending_tooltip_global_position,
                tooltip,
                self.chat_display.viewport(),
            )

    def _hide_chat_tooltip(self) -> None:
        self.chat_tooltip_timer.stop()
        self._pending_tooltip_message_id = None
        self._hovered_message_id = None
        QToolTip.hideText()
        app = QApplication.instance()
        if app is not None:
            for widget in app.topLevelWidgets():
                if widget.windowType() == Qt.WindowType.ToolTip:
                    widget.hide()

    @staticmethod
    def _open_url_in_browser(url: str) -> None:
        parsed = QUrl(url)
        if parsed.isValid() and parsed.scheme().casefold() in {"http", "https"}:
            QDesktopServices.openUrl(parsed)

    def _image_url_from_anchor(self, anchor: str) -> str | None:
        prefix = "spritelink-image:"
        if not anchor.startswith(prefix):
            return None
        return self.rendered_image_links.get(anchor[len(prefix):])

    def _link_url_from_anchor(self, anchor: str) -> str | None:
        image_url = self._image_url_from_anchor(anchor)
        if image_url:
            return image_url
        parsed = QUrl(anchor)
        if parsed.isValid() and parsed.scheme().casefold() in {"http", "https"}:
            return anchor
        return None

    def _show_link_context_menu(
        self,
        url: str,
        global_position: QPoint,
    ) -> None:
        menu = QMenu(self.root)
        copy_action = menu.addAction("Copy Link")
        copy_action.triggered.connect(
            lambda: QApplication.clipboard().setText(url)
        )
        menu.exec(global_position)

    def _schedule_image_preview_fetch(self, url: str) -> None:
        if (
            url in self.image_preview_cache
            or url in self.pending_image_previews
            or self._closing
        ):
            return
        self.pending_image_previews.add(url)
        try:
            self.image_fetch_executor.submit(
                self._fetch_remote_image_preview,
                url,
            )
        except RuntimeError:
            self.pending_image_previews.discard(url)

    def _fetch_remote_image_preview(self, url: str) -> None:
        image_data: bytes | None = None
        try:
            with requests.get(
                url,
                headers={
                    "User-Agent": f"{APP_NAME}/{CONFIG_FORMAT_VERSION}",
                    "Accept": "image/*",
                },
                stream=True,
                timeout=REQUEST_TIMEOUT_SECONDS,
                allow_redirects=False,
            ) as response:
                response.raise_for_status()
                content_type = response.headers.get(
                    "Content-Type",
                    "",
                ).split(";", 1)[0].strip().casefold()
                if content_type and not content_type.startswith("image/"):
                    raise ValueError("The link did not return an image.")
                content_length = response.headers.get("Content-Length")
                if (
                    content_length
                    and int(content_length) > MAX_REMOTE_IMAGE_BYTES
                ):
                    raise ValueError("The linked image is too large.")

                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_content(64 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_REMOTE_IMAGE_BYTES:
                        raise ValueError("The linked image is too large.")
                    chunks.append(chunk)
                candidate = b"".join(chunks)

            with Image.open(io.BytesIO(candidate)) as remote_image:
                width, height = remote_image.size
                if (
                    width <= 0
                    or height <= 0
                    or width * height > MAX_REMOTE_IMAGE_PIXELS
                ):
                    raise ValueError("The linked image dimensions are too large.")
                remote_image.verify()
            image_data = candidate
        except Exception:
            image_data = None

        if not self._closing:
            self.ui_queue.put((
                "image_preview_loaded",
                {"url": url, "data": image_data},
            ))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (
            watched is self.root
            and hasattr(self, "window_focused_event")
        ):
            if event.type() == QEvent.Type.WindowActivate:
                self.window_focused_event.set()
            elif event.type() == QEvent.Type.WindowDeactivate:
                self.window_focused_event.clear()

        if (
            watched is getattr(self, "chat_content", None)
            and event.type() == QEvent.Type.Resize
            and hasattr(self, "config_overlay")
        ):
            self._sync_config_overlay_geometry()
            if hasattr(self, "message_limit_overlay"):
                self._sync_message_limit_overlay_geometry()
            if hasattr(self, "image_preview_overlay"):
                self._sync_image_preview_overlay_geometry()
                if self.image_preview_overlay.isVisible():
                    self._update_image_preview_popup()

        if (
            hasattr(self, "chat_display")
            and watched is self.chat_display.viewport()
        ):
            if event.type() == QEvent.Type.MouseMove:
                anchor = self.chat_display.anchorAt(event.position().toPoint())
                message_id = self._message_id_from_anchor(anchor)

                if anchor:
                    self.chat_display.viewport().setCursor(
                        Qt.CursorShape.PointingHandCursor
                    )
                    if message_id:
                        self._schedule_chat_tooltip(
                            message_id,
                            event.globalPosition().toPoint(),
                        )
                    else:
                        self._hide_chat_tooltip()
                else:
                    self.chat_display.viewport().setCursor(
                        Qt.CursorShape.IBeamCursor
                    )
                    self._hide_chat_tooltip()

            elif event.type() == QEvent.Type.Leave:
                self._hide_chat_tooltip()

            elif event.type() in (
                QEvent.Type.MouseButtonPress,
                QEvent.Type.MouseButtonRelease,
                QEvent.Type.MouseButtonDblClick,
            ):
                if event.button() == Qt.MouseButton.LeftButton:
                    anchor = self.chat_display.anchorAt(
                        event.position().toPoint()
                    )
                    if self._message_id_from_anchor(anchor):
                        # User links are hover/context targets, not clickable
                        # navigation. Consuming the click prevents Qt from
                        # drawing a focus outline around the icon or username.
                        return True
                    image_url = self._image_url_from_anchor(anchor)
                    if image_url:
                        if event.type() == QEvent.Type.MouseButtonRelease:
                            self._show_image_preview_popup(image_url)
                        return True
                    link_url = self._link_url_from_anchor(anchor)
                    if link_url:
                        if event.type() == QEvent.Type.MouseButtonRelease:
                            self._open_url_in_browser(link_url)
                        return True

            elif event.type() == QEvent.Type.ContextMenu:
                anchor = self.chat_display.anchorAt(event.pos())
                message_id = self._message_id_from_anchor(anchor)
                item = self.rendered_message_items.get(message_id or "")
                if item is not None:
                    self._show_username_context_menu(
                        item,
                        event.globalPos(),
                    )
                else:
                    link_url = self._link_url_from_anchor(anchor)
                    if link_url:
                        self._hide_chat_tooltip()
                        self._show_link_context_menu(
                            link_url,
                            event.globalPos(),
                        )
                # Never show QTextBrowser's generic Copy/Copy Link/Select All
                # menu in the log viewport.
                return True

        return super().eventFilter(watched, event)

    @staticmethod
    def _message_id_from_anchor(anchor: str) -> str | None:
        prefix = "spritelink:"
        if anchor.startswith(prefix):
            return anchor[len(prefix):]
        return None

    def _blend_toward_chat_background(
        self,
        color: str,
        amount: float = 1.0 - MUTED_CONTENT_OPACITY,
    ) -> str:
        amount = max(0.0, min(1.0, amount))
        foreground = QColor(color)
        background = self.chat_display.palette().color(
            self.chat_display.backgroundRole()
        )

        if not foreground.isValid():
            foreground = QColor("#a0a0a0")

        blended = QColor(
            round(foreground.red() * (1.0 - amount) + background.red() * amount),
            round(foreground.green() * (1.0 - amount) + background.green() * amount),
            round(foreground.blue() * (1.0 - amount) + background.blue() * amount),
        )
        return blended.name()

    def _collapsed_message_preview(
        self,
        text: str,
    ) -> str:
        has_embedded_images = bool(direct_image_urls_in_message(text))
        visible_text = (
            message_text_without_image_links(text)
            if has_embedded_images
            else text
        )
        normalized = " ".join(visible_text.split())
        if has_embedded_images:
            normalized = (
                f"[image] {normalized}"
                if normalized
                else "[image]"
            )

        return normalized

    def _insert_profile_icon(
        self,
        cursor: QTextCursor,
        encoded_icon: str,
        message_id: str,
        *,
        align_top: bool = False,
        opacity: float = 1.0,
    ) -> bool:
        if not encoded_icon:
            return False
        try:
            gif_data = decode_profile_icon(encoded_icon)
        except ValueError:
            return False

        try:
            # Let Qt detect GIF from its signature. Some PySide6 Windows
            # builds reject an explicit format argument despite advertising
            # that overload.
            image = QImage.fromData(gif_data)
        except (TypeError, ValueError):
            return False
        if image.isNull():
            return False
        image.setDevicePixelRatio(1.0)

        displayed_image = image
        resource_suffix = ""
        if align_top:
            padded_image = QImage(
                image.width(),
                image.height() + TOP_ALIGNED_PROFILE_ICON_PADDING,
                QImage.Format.Format_ARGB32,
            )
            padded_image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(padded_image)
            painter.drawImage(
                0,
                TOP_ALIGNED_PROFILE_ICON_PADDING,
                image,
            )
            painter.end()
            padded_image.setDevicePixelRatio(1.0)
            displayed_image = padded_image
            resource_suffix = "-top-padded"

        opacity = max(0.0, min(1.0, opacity))
        if opacity < 1.0:
            faded_image = QImage(
                displayed_image.width(),
                displayed_image.height(),
                QImage.Format.Format_ARGB32_Premultiplied,
            )
            faded_image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(faded_image)
            painter.setOpacity(opacity)
            painter.drawImage(0, 0, displayed_image)
            painter.end()
            faded_image.setDevicePixelRatio(1.0)
            displayed_image = faded_image
            resource_suffix += f"-opacity-{round(opacity * 100)}"

        resource_name = (
            "spritelink-profile-icon:"
            + hashlib.sha256(gif_data).hexdigest()
            + resource_suffix
        )
        resource_url = QUrl(resource_name)
        cursor.document().addResource(
            QTextDocument.ResourceType.ImageResource,
            resource_url,
            displayed_image,
        )
        image_format = QTextImageFormat()
        image_format.setName(resource_url.toString())
        image_format.setWidth(displayed_image.width())
        image_format.setHeight(displayed_image.height())
        image_format.setAnchor(True)
        image_format.setAnchorHref(f"spritelink:{message_id}")
        # Inline images participate in Qt's automatic line-height calculation,
        # so a short text line expands to the icon's native 16-pixel height.
        image_format.setVerticalAlignment(
            QTextCharFormat.VerticalAlignment.AlignTop
            if align_top
            else QTextCharFormat.VerticalAlignment.AlignMiddle
        )
        cursor.insertImage(image_format)
        return True

    def _show_username_context_menu(
        self,
        item: dict[str, Any],
        global_position: Any,
    ) -> None:
        QToolTip.hideText()
        message = item["message"]
        client_id = str(message["c"])
        message_id = str(message["i"])
        is_local = bool(item.get("is_local", False))

        muted_ids = self._room_preference_ids("muted_users")
        collapsed_ids = self._room_preference_ids("collapsed_messages")
        trusted_image_ids = self._room_preference_ids(
            "trusted_image_users"
        )
        is_muted = client_id in muted_ids
        is_manually_collapsed = message_id in collapsed_ids
        trusts_images = client_id in trusted_image_ids

        menu = QMenu(self.root)
        mute_action = menu.addAction(
            "Unmute User" if is_muted else "Mute User"
        )
        mute_action.setEnabled(not is_local)
        if not is_local:
            mute_action.triggered.connect(
                lambda: self._set_user_muted(client_id, not is_muted)
            )

        trust_images_action = menu.addAction("Trust Images from User")
        trust_images_action.setCheckable(True)
        trust_images_action.setChecked(is_local or trusts_images)
        trust_images_action.setEnabled(not is_local)
        if not is_local:
            trust_images_action.toggled.connect(
                lambda checked: self._set_user_image_trusted(
                    client_id,
                    checked,
                )
            )

        collapse_action = menu.addAction(
            "Expand Message"
            if is_manually_collapsed
            else "Collapse Message"
        )
        collapse_action.setEnabled(not is_muted)
        if not is_muted:
            collapse_action.triggered.connect(
                lambda: self._set_message_collapsed(
                    message_id,
                    not is_manually_collapsed,
                )
            )

        menu.exec(global_position)

    def _text_format(
        self,
        color: str,
        *,
        bold: bool = False,
        anchor: str | None = None,
        font_name: str = DEFAULT_MESSAGE_FONT,
        align_top: bool = False,
        top_align_height: int = 0,
    ) -> QTextCharFormat:
        formatting = QTextCharFormat()
        formatting.setForeground(QColor(color))
        formatting.setFont(
            self._make_message_font(font_name, bold=bold)
        )
        if anchor:
            formatting.setAnchor(True)
            formatting.setAnchorHref(anchor)
            formatting.setFontUnderline(False)
        if align_top and top_align_height > 0:
            font_height = max(1, QFontMetrics(formatting.font()).height())
            upward_pixels = max(
                0.0,
                (top_align_height - font_height) / 2.0,
            )
            formatting.setBaselineOffset(
                upward_pixels * 100.0 / font_height
            )
        return formatting

    def _insert_message_text_with_links(
        self,
        cursor: QTextCursor,
        text: str,
        body_color: str,
        font_name: str,
        *,
        muted: bool,
        embedded_image_urls: set[str],
        align_top: bool,
        top_align_height: int,
    ) -> None:
        position = 0
        for start, end, url in message_url_spans(text):
            if start > position:
                cursor.insertText(
                    text[position:start],
                    self._text_format(
                        body_color,
                        font_name=font_name,
                        align_top=align_top,
                        top_align_height=top_align_height,
                    ),
                )
            if url in embedded_image_urls:
                position = end
                continue
            link_color = "#0000ee" if self._is_windows_classic_theme() else "#0066cc"
            if muted:
                link_color = self._blend_toward_chat_background(link_color)
            link_format = self._text_format(
                link_color,
                anchor=url,
                font_name=font_name,
                align_top=align_top,
                top_align_height=top_align_height,
            )
            link_format.setFontUnderline(True)
            cursor.insertText(text[start:end], link_format)
            position = end
        if position < len(text):
            cursor.insertText(
                text[position:],
                self._text_format(
                    body_color,
                    font_name=font_name,
                    align_top=align_top,
                    top_align_height=top_align_height,
                ),
            )

    def _insert_embedded_image_preview(
        self,
        cursor: QTextCursor,
        url: str,
    ) -> bool:
        image = self.image_preview_cache.get(url)
        if not isinstance(image, QImage) or image.isNull():
            return False
        if is_likely_nsfw_image_url(url):
            preview = self._likely_nsfw_image_placeholder()
        else:
            preview = image.scaled(
                EMBEDDED_IMAGE_MAX_EDGE,
                EMBEDDED_IMAGE_MAX_EDGE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        token = hashlib.sha256(url.encode("utf-8")).hexdigest()
        self.rendered_image_links[token] = url
        resource_url = QUrl(f"spritelink-chat-image-resource:{token}")
        cursor.document().addResource(
            QTextDocument.ResourceType.ImageResource,
            resource_url,
            preview,
        )
        image_format = QTextImageFormat()
        image_format.setName(resource_url.toString())
        image_format.setWidth(preview.width())
        image_format.setHeight(preview.height())
        image_format.setVerticalAlignment(
            QTextCharFormat.VerticalAlignment.AlignTop
        )
        image_format.setAnchor(True)
        image_format.setAnchorHref(f"spritelink-image:{token}")
        cursor.insertImage(image_format)
        return True

    def _likely_nsfw_image_placeholder(self) -> QImage:
        placeholder = QImage(
            NSFW_IMAGE_PLACEHOLDER_SIZE,
            NSFW_IMAGE_PLACEHOLDER_SIZE,
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        placeholder.fill(QColor("#eee8df"))
        painter = QPainter(placeholder)
        painter.setRenderHint(
            QPainter.RenderHint.TextAntialiasing,
            not self._is_windows_classic_theme(),
        )
        painter.setPen(QColor("#c29a70"))
        painter.drawRect(placeholder.rect().adjusted(0, 0, -1, -1))
        painter.setPen(QColor("#704825"))
        painter.setFont(self._make_font("Segoe UI", 8, bold=True))
        painter.drawText(
            placeholder.rect().adjusted(4, 4, -4, -4),
            Qt.AlignmentFlag.AlignCenter
            | Qt.TextFlag.TextWordWrap,
            "Likely NSFW",
        )
        painter.end()
        return placeholder

    def _insert_log_separator(
        self,
        cursor: QTextCursor,
        text: str,
        background_color: str,
        row_selections: list[QTextEdit.ExtraSelection],
    ) -> bool:
        previous_block = cursor.block().previous()
        if (
            previous_block.isValid()
            and previous_block.text().startswith("————— ")
        ):
            # Consecutive separators represent the same empty span. Keep only
            # the newest one without adding another striped row.
            replacement = QTextCursor(previous_block)
            replacement.movePosition(
                QTextCursor.MoveOperation.StartOfBlock
            )
            replacement.movePosition(
                QTextCursor.MoveOperation.EndOfBlock,
                QTextCursor.MoveMode.KeepAnchor,
            )
            replacement.insertText(text, self._text_format("#777777"))
            return False

        cursor.insertBlock()
        separator_block = QTextBlockFormat()
        separator_block.setAlignment(Qt.AlignmentFlag.AlignCenter)
        separator_block.setTopMargin(7)
        separator_block.setBottomMargin(7)
        cursor.setBlockFormat(separator_block)
        cursor.insertText(text, self._text_format("#777777"))

        selection = QTextEdit.ExtraSelection()
        selection.cursor = QTextCursor(cursor.block())
        selection.cursor.clearSelection()
        selection.format.setBackground(QColor(background_color))
        selection.format.setProperty(
            QTextFormat.Property.FullWidthSelection,
            True,
        )
        row_selections.append(selection)

        cursor.insertBlock()
        cursor.setBlockFormat(QTextBlockFormat())
        return True

    def _render_message_log(self, *, scroll_to_bottom: bool) -> None:
        self._hide_chat_tooltip()
        self.rendered_message_items.clear()
        self.rendered_tooltips.clear()
        self.rendered_image_links.clear()
        self.chat_display.clear()
        self.chat_display.row_background_blocks.clear()
        self.chat_display.collapsed_fade_blocks.clear()

        cursor = self.chat_display.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        muted_ids = self._room_preference_ids("muted_users")
        collapsed_ids = self._room_preference_ids("collapsed_messages")
        trusted_image_user_ids = self._room_preference_ids(
            "trusted_image_users"
        )
        row_selections: list[QTextEdit.ExtraSelection] = []
        previous_timestamp: int | None = None
        previous_local_date: Any = None
        first_item = True
        stripe_index = 0

        for item in self.message_log:
            current_timestamp = self._display_timestamp_for_item(item)
            current_local_datetime = self._local_datetime(current_timestamp)
            current_local_date = (
                current_local_datetime.date()
                if current_local_datetime is not None
                else None
            )

            if (
                not first_item
                and previous_local_date is not None
                and current_local_date is not None
                and current_local_date != previous_local_date
            ):
                if self._insert_log_separator(
                    cursor,
                    "————— "
                    f"{current_local_datetime.strftime('%b')} "
                    f"{current_local_datetime.day}, "
                    f"{current_local_datetime.year}"
                    " —————",
                    MESSAGE_ROW_BACKGROUNDS[
                        stripe_index % len(MESSAGE_ROW_BACKGROUNDS)
                    ],
                    row_selections,
                ):
                    stripe_index += 1
            elif (
                previous_timestamp is not None
                and current_timestamp - previous_timestamp
                >= GAP_SEPARATOR_SECONDS
            ):
                gap_seconds = current_timestamp - previous_timestamp
                gap_hours = max(6, int((gap_seconds / 3600.0) + 0.5))
                if self._insert_log_separator(
                    cursor,
                    f"————— {gap_hours} hours later —————",
                    MESSAGE_ROW_BACKGROUNDS[
                        stripe_index % len(MESSAGE_ROW_BACKGROUNDS)
                    ],
                    row_selections,
                ):
                    stripe_index += 1
            elif not first_item:
                cursor.insertBlock()

            self._insert_message_item(
                cursor,
                item,
                muted_ids=muted_ids,
                collapsed_ids=collapsed_ids,
                trusted_image_user_ids=trusted_image_user_ids,
                background_color=MESSAGE_ROW_BACKGROUNDS[
                    stripe_index % len(MESSAGE_ROW_BACKGROUNDS)
                ],
                row_selections=row_selections,
            )
            stripe_index += 1
            first_item = False
            previous_timestamp = current_timestamp
            previous_local_date = current_local_date

        self.chat_display.row_background_blocks = {
            selection.cursor.block().blockNumber(): QColor(
                selection.format.background().color()
            )
            for selection in row_selections
            if selection.cursor.block().isValid()
        }
        self.chat_display.setExtraSelections(row_selections)
        self.chat_display.horizontalScrollBar().setValue(0)

        if scroll_to_bottom:
            scrollbar = self.chat_display.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    def _insert_message_item(
        self,
        cursor: QTextCursor,
        item: dict[str, Any],
        *,
        muted_ids: set[str],
        collapsed_ids: set[str],
        trusted_image_user_ids: set[str],
        background_color: str,
        row_selections: list[QTextEdit.ExtraSelection],
    ) -> None:
        message_start_position = cursor.position()

        message = item["message"]
        timestamp = self._display_timestamp_for_item(item)
        username = str(message["u"])
        original_color = str(message["k"])
        font_name = str(message.get("f", DEFAULT_MESSAGE_FONT))
        original_text_color = str(
            message.get("o", DEFAULT_MESSAGE_TEXT_COLOR)
        )
        profile_icon = str(message.get("p", ""))
        text = str(message["m"])
        message_id = str(message["i"])
        client_id = str(message["c"])
        user_id_preview = visible_user_id(client_id)

        is_muted = client_id in muted_ids and not item["is_local"]
        is_collapsed = is_muted or message_id in collapsed_ids
        if is_collapsed:
            collapsed_block_format = cursor.blockFormat()
            collapsed_block_format.setNonBreakableLines(True)
            cursor.setBlockFormat(collapsed_block_format)

        candidate_image_urls = direct_image_urls_in_message(text)
        embedded_image_url_set = {
            url
            for url in candidate_image_urls
            if is_image_url_trusted_for_sender(
                url,
                client_id,
                bool(item["is_local"]),
                trusted_image_user_ids,
            )
        }
        has_untrusted_image = any(
            url not in embedded_image_url_set
            for url in candidate_image_urls
        )
        display_text = (
            self._collapsed_message_preview(text)
            if is_collapsed
            else text
        )
        if has_untrusted_image:
            display_text = "[untrusted image] " + display_text
        image_urls = (
            []
            if is_collapsed
            else [
                url
                for url in direct_image_urls_in_message(display_text)
                if url in embedded_image_url_set
            ]
        )

        username_color = (
            self._blend_toward_chat_background(original_color)
            if is_muted
            else original_color
        )
        if not QColor(username_color).isValid():
            username_color = (
                self._blend_toward_chat_background("#4ea1ff")
                if is_muted
                else "#4ea1ff"
            )

        status_suffix = ""
        if item["is_local"]:
            status_suffix = " (you)"
        elif is_muted:
            status_suffix = " (muted)"

        if not QColor(original_text_color).isValid():
            original_text_color = DEFAULT_MESSAGE_TEXT_COLOR
        body_color = (
            self._blend_toward_chat_background(original_text_color)
            if is_muted
            else original_text_color
        )
        suffix_color = (
            self._blend_toward_chat_background("#777777")
            if is_muted
            else "#777777"
        )

        self.rendered_message_items[message_id] = item
        hover_timestamp = self._format_hover_timestamp(timestamp)
        tooltip_icon_uri = profile_icon_tooltip_data_uri(profile_icon)
        if tooltip_icon_uri:
            self.rendered_tooltips[message_id] = (
                '<div align="center">'
                f'<img src="{tooltip_icon_uri}" width="64" height="64">'
                f"<br>{hover_timestamp}<br>"
                f"User ID: {user_id_preview}"
                "</div>"
            )
        else:
            self.rendered_tooltips[message_id] = (
                f"{hover_timestamp}\n"
                f"User ID: {user_id_preview}"
            )

        top_align_height = 0
        if (
            not is_collapsed
            and bool(image_urls)
            and not message_text_without_image_links(
                display_text,
                set(image_urls),
            ).strip()
        ):
            for url in image_urls:
                cached_image = self.image_preview_cache.get(url)
                if (
                    isinstance(cached_image, QImage)
                    and not cached_image.isNull()
                ):
                    if is_likely_nsfw_image_url(url):
                        preview_height = NSFW_IMAGE_PLACEHOLDER_SIZE
                    else:
                        preview_height = cached_image.scaled(
                            EMBEDDED_IMAGE_MAX_EDGE,
                            EMBEDDED_IMAGE_MAX_EDGE,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        ).height()
                    top_align_height = max(top_align_height, preview_height)
        align_message_top = top_align_height > 0

        has_profile_icon = self._insert_profile_icon(
            cursor,
            profile_icon,
            message_id,
            align_top=align_message_top,
            opacity=MUTED_CONTENT_OPACITY if is_muted else 1.0,
        )
        if has_profile_icon:
            cursor.insertText(
                " ",
                self._text_format(
                    body_color,
                    anchor=f"spritelink:{message_id}",
                    font_name=font_name,
                    align_top=align_message_top,
                    top_align_height=top_align_height,
                ),
            )
        cursor.insertText(
            username,
            self._text_format(
                username_color,
                bold=True,
                anchor=f"spritelink:{message_id}",
                font_name=font_name,
                align_top=align_message_top,
                top_align_height=top_align_height,
            ),
        )
        if status_suffix:
            cursor.insertText(
                status_suffix,
                self._text_format(
                    suffix_color,
                    font_name=font_name,
                    align_top=align_message_top,
                    top_align_height=top_align_height,
                ),
            )
        cursor.insertText(
            ": ",
            self._text_format(
                body_color,
                font_name=font_name,
                align_top=align_message_top,
                top_align_height=top_align_height,
            ),
        )

        visible_text_without_images = message_text_without_image_links(
            display_text,
            set(image_urls),
        )
        add_image_line_break = (
            bool(image_urls)
            and bool(visible_text_without_images.strip())
            and not visible_text_without_images.rstrip(" \t").endswith(
                ("\n", "\r")
            )
        )
        for image_url in image_urls:
            self._schedule_image_preview_fetch(image_url)
        if is_collapsed:
            cursor.insertText(
                display_text,
                self._text_format(
                    body_color,
                    font_name=font_name,
                    align_top=align_message_top,
                    top_align_height=top_align_height,
                ),
            )
        else:
            self._insert_message_text_with_links(
                cursor,
                display_text,
                body_color,
                font_name,
                muted=is_muted,
                embedded_image_urls=set(image_urls),
                align_top=align_message_top,
                top_align_height=top_align_height,
            )
        cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
        if add_image_line_break:
            cursor.insertBlock()
        for image_url in image_urls:
            self._insert_embedded_image_preview(cursor, image_url)

        if item.get("warning") and not is_collapsed:
            cursor.insertBlock()
            cursor.insertText(
                str(item["warning"]),
                self._text_format("#b00020", font_name=font_name),
            )

        # Explicit newlines create additional QTextBlocks. A full-width extra
        # selection paints each block to the viewport edges independently of
        # the paragraph margins that keep the text itself padded.
        document = cursor.document()
        block = document.findBlock(message_start_position)
        final_block_number = cursor.block().blockNumber()
        while block.isValid() and block.blockNumber() <= final_block_number:
            block_cursor = QTextCursor(block)
            block_format = block.blockFormat()
            block_format.setLeftMargin(10)
            block_format.setRightMargin(10)
            block_cursor.setBlockFormat(block_format)

            selection = QTextEdit.ExtraSelection()
            selection.cursor = block_cursor
            selection.cursor.clearSelection()
            selection.format.setBackground(QColor(background_color))
            selection.format.setProperty(
                QTextFormat.Property.FullWidthSelection,
                True,
            )
            row_selections.append(selection)
            block = block.next()

        if is_collapsed:
            collapsed_block = document.findBlock(message_start_position)
            if collapsed_block.isValid():
                self.chat_display.collapsed_fade_blocks[
                    collapsed_block.blockNumber()
                ] = QColor(background_color)

    def _append_system_message(
        self,
        text: str,
        warning: bool = False,
    ) -> None:
        display_time = time.strftime("%H:%M:%S")
        cursor = self.chat_display.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if not self.chat_display.document().isEmpty():
            cursor.insertBlock()
        system_block = cursor.blockFormat()
        system_block.setBackground(QColor("#ffffff"))
        system_block.setLeftMargin(10)
        system_block.setRightMargin(10)
        cursor.setBlockFormat(system_block)
        cursor.insertText(
            f"[{display_time}] ",
            self._text_format("#777777"),
        )
        cursor.insertText(
            text,
            self._text_format("#b00020" if warning else "#a06000"),
        )
        scrollbar = self.chat_display.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _clear_visible_room(self) -> None:
        self._hide_chat_tooltip()
        if self.image_preview_overlay.isVisible():
            self._hide_image_preview_popup()
        self.seen_client_message_ids.clear()
        self.seen_ntfy_message_ids.clear()
        self.message_log.clear()
        self.rendered_message_items.clear()
        self.rendered_tooltips.clear()
        self.rendered_image_links.clear()
        self.chat_display.row_background_blocks.clear()
        self.chat_display.collapsed_fade_blocks.clear()
        self.chat_display.setExtraSelections([])
        self.chat_display.clear()

    def _should_play_message_sound(self, room_id: str) -> bool:
        return (
            str(self.message_sound_var.get()) != "Disabled"
            and (
                room_id != self.active_chatroom_id
                or not self.window_focused_event.is_set()
            )
        )

    def _message_sound_path(self, sound_name: str) -> Path | None:
        if sound_name == "Custom":
            custom_path = str(
                self.custom_message_sound_path_var.get()
            ).strip()
            return Path(custom_path) if custom_path else None

        filename = BUILTIN_MESSAGE_SOUND_FILES.get(sound_name)
        if filename is None:
            return None
        return Path(__file__).resolve().parent / "sounds" / filename

    def _message_sound_effect_for_path(
        self,
        sound_path: Path,
    ) -> QSoundEffect:
        resolved_path = str(sound_path.resolve())
        effect = self.message_sound_effects.get(resolved_path)
        if effect is not None:
            return effect

        effect = QSoundEffect(self)
        effect.setLoopCount(1)
        effect.setVolume(
            int(self.message_sound_volume_var.get()) / 100.0
        )
        effect.statusChanged.connect(
            lambda effect=effect: (
                self._on_message_sound_effect_status_changed(effect)
            )
        )
        self.message_sound_effects[resolved_path] = effect
        effect.setSource(QUrl.fromLocalFile(resolved_path))
        return effect

    def _start_message_sound_effect(
        self,
        effect: QSoundEffect,
        sound_name: str,
    ) -> None:
        effect.stop()
        effect.setVolume(
            int(self.message_sound_volume_var.get()) / 100.0
        )
        self.active_message_sound_effect = effect
        effect.play()
        if sound_name == "Custom":
            self.message_sound_stop_timer.start(
                CUSTOM_MESSAGE_SOUND_MAX_MS
            )

    def _on_message_sound_effect_status_changed(
        self,
        effect: QSoundEffect,
    ) -> None:
        if effect is not self.pending_message_sound_effect:
            return
        if effect.isLoaded():
            sound_name = self.pending_message_sound_name
            self.pending_message_sound_effect = None
            self.pending_message_sound_name = ""
            self.pending_message_sound_report_errors = False
            self._start_message_sound_effect(effect, sound_name)
            return
        if effect.status() != QSoundEffect.Status.Error:
            return

        report_errors = self.pending_message_sound_report_errors
        self.pending_message_sound_effect = None
        self.pending_message_sound_name = ""
        self.pending_message_sound_report_errors = False
        if report_errors and not self._closing:
            messagebox.showerror(
                "Could not play message sound",
                "The selected WAV file could not be loaded.",
                parent=self.root,
            )

    def _ensure_compressed_message_sound_player(
        self,
    ) -> tuple[QMediaPlayer, QAudioOutput]:
        player = self.compressed_message_sound_player
        audio_output = self.compressed_message_sound_audio_output
        if player is not None and audio_output is not None:
            return player, audio_output

        audio_output = QAudioOutput(self)
        audio_output.setVolume(
            int(self.message_sound_volume_var.get()) / 100.0
        )
        player = QMediaPlayer(self)
        player.setAudioOutput(audio_output)
        player.playbackStateChanged.connect(
            self._on_compressed_message_sound_state_changed
        )
        player.errorOccurred.connect(
            self._on_compressed_message_sound_error
        )
        self.compressed_message_sound_audio_output = audio_output
        self.compressed_message_sound_player = player
        return player, audio_output

    def _play_compressed_message_sound(
        self,
        sound_path: Path,
        sound_name: str,
        *,
        report_errors: bool,
    ) -> None:
        player, audio_output = (
            self._ensure_compressed_message_sound_player()
        )
        audio_output.setVolume(
            int(self.message_sound_volume_var.get()) / 100.0
        )
        self.compressed_message_sound_name = sound_name
        self.compressed_message_sound_report_errors = report_errors
        sound_url = QUrl.fromLocalFile(str(sound_path.resolve()))
        if player.source() != sound_url:
            player.setSource(sound_url)
        else:
            player.setPosition(0)
        player.play()

    def _on_compressed_message_sound_state_changed(
        self,
        state: QMediaPlayer.PlaybackState,
    ) -> None:
        if (
            state == QMediaPlayer.PlaybackState.PlayingState
            and self.compressed_message_sound_name == "Custom"
        ):
            self.message_sound_stop_timer.start(
                CUSTOM_MESSAGE_SOUND_MAX_MS
            )

    def _on_compressed_message_sound_error(self, *_args: Any) -> None:
        report_errors = self.compressed_message_sound_report_errors
        self.compressed_message_sound_name = ""
        self.compressed_message_sound_report_errors = False
        if report_errors and not self._closing:
            player = self.compressed_message_sound_player
            error_text = (
                player.errorString() if player is not None else ""
            ) or (
                "The selected audio file could not be played."
            )
            messagebox.showerror(
                "Could not play message sound",
                error_text,
                parent=self.root,
            )

    def _stop_message_sound(self) -> None:
        self.message_sound_stop_timer.stop()
        self.pending_message_sound_effect = None
        self.pending_message_sound_name = ""
        self.pending_message_sound_report_errors = False
        if self.active_message_sound_effect is not None:
            self.active_message_sound_effect.stop()
            self.active_message_sound_effect = None
        if self.compressed_message_sound_player is not None:
            self.compressed_message_sound_player.stop()
        self.compressed_message_sound_name = ""
        self.compressed_message_sound_report_errors = False

    def _play_message_sound(self, *, report_errors: bool = False) -> None:
        sound_name = str(self.message_sound_var.get())
        self._stop_message_sound()
        sound_path = self._message_sound_path(sound_name)
        if sound_path is None:
            return
        try:
            if not sound_path.is_file():
                raise FileNotFoundError(
                    f"Message sound not found: {sound_path}"
                )
            if (
                sound_path.suffix.casefold()
                in COMPRESSED_MESSAGE_SOUND_EXTENSIONS
            ):
                self._play_compressed_message_sound(
                    sound_path,
                    sound_name,
                    report_errors=report_errors,
                )
                return
            effect = self._message_sound_effect_for_path(sound_path)
            if effect.isLoaded():
                self._start_message_sound_effect(effect, sound_name)
            else:
                self.pending_message_sound_effect = effect
                self.pending_message_sound_name = sound_name
                self.pending_message_sound_report_errors = report_errors
                if effect.status() == QSoundEffect.Status.Error:
                    self._on_message_sound_effect_status_changed(effect)
        except Exception as exc:
            if report_errors:
                messagebox.showerror(
                    "Could not play message sound",
                    str(exc),
                    parent=self.root,
                )

    def _on_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        QToolTip.hideText()
        self.update_check_timer.stop()
        self.message_sound_stop_timer.stop()
        self._stop_message_sound()

        try:
            self._copy_ui_to_config()
            save_config(self.config_data)
        except Exception:
            pass

        self.stop_event.set()
        self.ui_queue_timer.stop()
        self.image_fetch_executor.shutdown(
            wait=False,
            cancel_futures=True,
        )

def _write_crash_log(error_text: str) -> Path | None:
    try:
        APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
        crash_path = APP_DATA_DIR / "crash.log"
        crash_path.write_text(error_text, encoding="utf-8")
        return crash_path
    except Exception:
        return None



def main() -> None:
    if os.name == "nt":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                WINDOWS_APP_USER_MODEL_ID
            )
        except Exception:
            pass
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("SpriteLink")
    window_icon = QIcon(str(WINDOW_ICON_PATH))
    if not window_icon.isNull():
        app.setWindowIcon(window_icon)
    root = MainWindow()
    if not window_icon.isNull():
        root.setWindowIcon(window_icon)

    def report_callback_exception(
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_traceback: Any,
    ) -> None:
        error_text = "".join(
            traceback.format_exception(
                exc_type,
                exc_value,
                exc_traceback,
            )
        )
        crash_path = _write_crash_log(error_text)
        location = (
            f"\n\nThe full traceback was saved to:\n{crash_path}"
            if crash_path is not None
            else ""
        )
        messagebox.showerror(
            "SpriteLink error",
            f"An unexpected error occurred.{location}\n\n{error_text[-2200:]}",
            parent=root,
        )

    sys.excepthook = report_callback_exception

    if os.name != "nt":
        messagebox.showerror(
            "Windows required",
            "This version uses Windows DPAPI and must be run on Windows.",
            parent=root,
        )
        return

    try:
        EncryptedChatClient(root)
    except Exception:
        error_text = traceback.format_exc()
        crash_path = _write_crash_log(error_text)
        location = (
            f"\n\nThe full traceback was saved to:\n{crash_path}"
            if crash_path is not None
            else ""
        )
        messagebox.showerror(
            "SpriteLink startup error",
            f"The client could not start.{location}\n\n{error_text[-2200:]}",
            parent=root,
        )
        return

    root.show()
    app.exec()


if __name__ == "__main__":
    main()
