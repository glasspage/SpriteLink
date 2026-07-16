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
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from functools import lru_cache
from html.parser import HTMLParser
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
import unicodedata
import uuid
import zlib
from urllib.parse import parse_qs, urljoin, urlsplit
try:
    from PySide6.QtCore import (
        QBuffer,
        QByteArray,
        QEvent,
        QIODevice,
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
        QMovie,
        QPainter,
        QPalette,
        QPixmap,
        QPixmapCache,
        QPolygon,
        QTextBlockFormat,
        QTextCharFormat,
        QTextCursor,
        QTextDocument,
        QTextFormat,
        QTextImageFormat,
        QTextOption,
    )
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
        QSystemTrayIcon,
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

QAudioOutput: Any = None
QMediaPlayer: Any = None
QSoundEffect: Any = None
QVideoSink: Any = None


def ensure_qt_multimedia_loaded() -> None:
    """Load Qt's multimedia backend only when audio or video is first used."""
    global QAudioOutput, QMediaPlayer, QSoundEffect, QVideoSink
    if QMediaPlayer is not None:
        return
    from PySide6.QtMultimedia import (
        QAudioOutput as AudioOutput,
        QMediaPlayer as MediaPlayer,
        QSoundEffect as SoundEffect,
        QVideoSink as VideoSink,
    )
    QAudioOutput = AudioOutput
    QMediaPlayer = MediaPlayer
    QSoundEffect = SoundEffect
    QVideoSink = VideoSink


try:
    import requests
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: requests\n\nInstall it with:\n"
        "pip install PySide6 requests cryptography Pillow"
    ) from exc

from spritelink_update import (
    ReleaseInfo,
    download_release_installer,
    fetch_latest_release,
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
CONFIG_FORMAT_VERSION = 23
WINDOW_ICON_PATH = Path(__file__).resolve().parent / "SL.ico"
WINDOWS_APP_USER_MODEL_ID = "SpriteLink.SpriteLink"

try:
    from spritelink_build_version import VERSION as RUNNING_VERSION
except ImportError:
    RUNNING_VERSION = "Development"

UPDATE_REPOSITORY = "glasspage/SpriteLink"
UPDATE_CHECK_INTERVAL_MS = 30 * 60 * 1000
CONNECTION_ERROR_DELAY_MS = 15 * 1000
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
WINDOWS_CLASSIC_NOTIFICATION_BUTTON_STYLESHEET = (
    "QPushButton {"
    " background-color: #f8d8ad;"
    " color: #000000;"
    " border-top: 2px solid #ffffff;"
    " border-left: 2px solid #ffffff;"
    " border-right: 2px solid #000000;"
    " border-bottom: 2px solid #000000;"
    " border-radius: 0px;"
    " padding: 3px 8px;"
    " min-height: 18px;"
    "}"
    "QPushButton:hover { background-color: #f3c78d; }"
    "QPushButton:pressed, QPushButton:checked {"
    " background-color: #efb968;"
    " border-top: 2px solid #000000;"
    " border-left: 2px solid #000000;"
    " border-right: 2px solid #ffffff;"
    " border-bottom: 2px solid #ffffff;"
    " padding-top: 4px;"
    " padding-left: 9px;"
    " padding-right: 7px;"
    " padding-bottom: 2px;"
    "}"
)


def notification_button_stylesheet(windows_classic: bool) -> str:
    return (
        WINDOWS_CLASSIC_NOTIFICATION_BUTTON_STYLESHEET
        if windows_classic
        else NOTIFICATION_BUTTON_STYLESHEET
    )


def chatroom_connection_label(
    room_name: str,
    *,
    connection_error: bool,
) -> str:
    suffix = " - Connection error" if connection_error else ""
    return f"{room_name}{suffix}"


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
MESSAGE_HISTORY_OPTIONS = (100, 500, 1000, 10000)
DEFAULT_MESSAGE_HISTORY_LIMIT = 1000
BACKGROUND_HISTORY_PRUNE_INTERVAL_MS = 60 * 60 * 1000
TRAY_NOTIFICATION_OUTLINE_COLOR = "#ff7a00"

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

FOCUSED_POLL_INTERVAL_SECONDS = 6.0
UNFOCUSED_POLL_INTERVAL_SECONDS = 9.0
TRAY_POLL_INTERVAL_SECONDS = 12.0
# Focused catch-up polls use most of ntfy's sustained request allowance.
# Leave the remaining budget for reconnecting the long-lived stream.
SUBSCRIPTION_RECONNECT_DELAY_SECONDS = 30.0
SUBSCRIPTION_READ_TIMEOUT_SECONDS = 75
SUBSCRIPTION_RECONNECT_BACKFILL_SECONDS = 2 * 60
MAX_SUBSCRIPTION_SIGNAL_QUEUE = 1024
CHATROOM_SWITCH_BURST_WINDOW_SECONDS = 6.0
IMMEDIATE_CHATROOM_SWITCH_LIMIT = 2
CHATROOM_SWITCH_REPAYMENT_BACKGROUND_POLLS = 6
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
MAX_REMOTE_IMAGE_BYTES = 50 * 1024 * 1024
MAX_MEDIA_PAGE_HTML_BYTES = 2 * 1024 * 1024
MAX_REMOTE_IMAGE_PIXELS = 16 * 1024 * 1024
MAX_ANIMATED_IMAGE_PIXELS = 4 * 1024 * 1024
MAX_ANIMATED_IMAGE_FRAMES = 500
IMAGE_PREVIEW_CACHE_LIMIT = 128
MAX_REMOTE_MEDIA_CACHE_BYTES = 64 * 1024 * 1024
MAX_ACTIVE_ANIMATED_MEDIA = 12
INLINE_MEDIA_NO_UPSCALE_EDGE = 64
VIEWPORT_MEDIA_PRELOAD_SCREENS = 1
VIEWPORT_MEDIA_UPDATE_DELAY_MS = 100
MAX_UNCOMPRESSED_MESSAGE_BYTES = 32 * 1024
MAX_ENCRYPTED_PACKET_CHARS = NTFY_MAX_BODY_BYTES - 1
MAX_DECRYPT_ATTEMPTS_PER_POLL = 25
MESSAGE_CLOCK_TOLERANCE_SECONDS = 10 * 60
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
    "res.cloudinary.com",
    "images.ctfassets.net",
    "cdn.sanity.io",
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
    "tenor.com",
    "www.tenor.com",
    "media.tenor.com",
    "c.tenor.com",
    "giphy.com",
    "www.giphy.com",
    "media.giphy.com",
    "media0.giphy.com",
    "media1.giphy.com",
    "media2.giphy.com",
    "media3.giphy.com",
    "media4.giphy.com",
    "i.giphy.com",
    "imgur.com",
    "www.imgur.com",
    "klipy.com",
    "*.klipy.com",
    "res.cloudinary.com",
    "images.ctfassets.net",
    "cdn.sanity.io",
    "live.staticflickr.com",
    "cdn.pixabay.com",
    "static.wikia.nocookie.net",
    "avatars.githubusercontent.com",
    "user-images.githubusercontent.com",
    "raw.githubusercontent.com",
    "steamuserimages-a.akamaihd.net",
    "image.tmdb.org",
    "cdn.myanimelist.net",
    "e621.net",
    "*.e621.net",
    "gelbooru.com",
    "*.gelbooru.com",
    "nhentai.net",
    "*.nhentai.net",
    "redgifs.com",
    "*.redgifs.com",
    "rule34.paheal.net",
    "*.paheal.net",
    "rule34.xxx",
    "*.rule34.xxx",
    "xbooru.com",
    "*.xbooru.com",
    "yande.re",
    "*.yande.re",
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
TENOR_MEDIA_HOSTS = (
    "media.tenor.com",
    "c.tenor.com",
)
TENOR_VIDEO_EXTENSIONS = (
    ".mp4",
    ".webm",
)
LOOPING_VIDEO_HOST_PATTERNS = (
    "media.tenor.com",
    "c.tenor.com",
    "*.klipy.com",
    "media.giphy.com",
    "media0.giphy.com",
    "media1.giphy.com",
    "media2.giphy.com",
    "media3.giphy.com",
    "media4.giphy.com",
    "*.redgifs.com",
)
TRUSTED_MEDIA_PAGE_PATHS = {
    "tenor.com": ("/view/",),
    "www.tenor.com": ("/view/",),
    "klipy.com": ("/gifs/",),
    "www.klipy.com": ("/gifs/",),
    "giphy.com": ("/gifs/", "/stickers/"),
    "www.giphy.com": ("/gifs/", "/stickers/"),
    "imgur.com": ("/",),
    "www.imgur.com": ("/",),
    "redgifs.com": ("/watch/", "/ifr/"),
    "www.redgifs.com": ("/watch/", "/ifr/"),
}
MEDIA_PAGE_METADATA_KEYS = (
    "og:video:secure_url",
    "og:video:url",
    "og:video",
    "twitter:player:stream",
    "og:image:secure_url",
    "og:image:url",
    "og:image",
    "twitter:image",
)
LIKELY_NSFW_IMAGE_DOMAINS = (
    "e621.net",
    "gelbooru.com",
    "nhentai.net",
    "redgifs.com",
    "paheal.net",
    "rule34.xxx",
    "xbooru.com",
    "yande.re",
)
MESSAGE_URL_PATTERN = re.compile(r"https://[^\s<>\"']+", re.IGNORECASE)
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
    """Convert a PNG or JPEG to a compact, single-frame 16x16 palette GIF."""
    with Image.open(source_path) as source:
        if source.format not in {"PNG", "JPEG"}:
            raise ValueError("Profile icons must be PNG or JPG images.")
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


FORBIDDEN_USERNAME_UNICODE_CATEGORIES = frozenset({
    "Cc",  # Control characters, including tabs and CR/LF.
    "Cf",  # Formatting controls, including zero-width and bidi controls.
    "Cs",  # Lone UTF-16 surrogate code points.
    "Zl",  # Unicode line separator.
    "Zp",  # Unicode paragraph separator.
})
FORBIDDEN_USERNAME_BIDI_CLASSES = frozenset({
    "LRE",
    "RLE",
    "LRO",
    "RLO",
    "PDF",
    "LRI",
    "RLI",
    "FSI",
    "PDI",
})


def is_forbidden_username_character(character: str) -> bool:
    codepoint = ord(character)
    return (
        unicodedata.category(character)
        in FORBIDDEN_USERNAME_UNICODE_CATEGORIES
        or unicodedata.bidirectional(character)
        in FORBIDDEN_USERNAME_BIDI_CLASSES
        or codepoint == 0x034F  # Combining grapheme joiner.
        or 0x180B <= codepoint <= 0x180F
        or 0xFE00 <= codepoint <= 0xFE0F
        or 0xE0100 <= codepoint <= 0xE01EF
    )


def sanitize_username(value: Any, fallback: str = "User") -> str:
    cleaned = "".join(
        character
        for character in str(value)
        if not is_forbidden_username_character(character)
    ).strip()
    return cleaned[:32] or fallback


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
    username = sanitize_username(
        raw.get("username", fallback["username"]),
        fallback["username"],
    )
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
        "username": username,
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

    username = sanitize_username(
        raw.get("username", base["username"]),
        base["username"],
    )

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
        "username": username,
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


@lru_cache(maxsize=4096)
def _cached_message_url_spans(
    text: str,
) -> tuple[tuple[int, int, str], ...]:
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
    return tuple(spans)


def message_url_spans(text: str) -> list[tuple[int, int, str]]:
    return list(_cached_message_url_spans(text))


@dataclass(frozen=True)
class RichTextRun:
    start: int
    end: int
    bold: bool = False
    italic: bool = False
    underline: bool = False


RICH_TEXT_TAG_PATTERN = re.compile(r"</?([biu])>")
RICH_TEXT_TAG_ORDER = ("b", "i", "u")


@lru_cache(maxsize=4096)
def parse_message_rich_text(
    text: str,
) -> tuple[str, tuple[RichTextRun, ...]]:
    """Parse SpriteLink's small formatting language without accepting HTML."""
    parts: list[str] = []
    runs: list[RichTextRun] = []
    active = {tag: 0 for tag in RICH_TEXT_TAG_ORDER}
    plain_length = 0
    position = 0

    def append_text(value: str) -> None:
        nonlocal plain_length
        if not value:
            return
        start = plain_length
        parts.append(value)
        plain_length += len(value)
        style = (
            active["b"] > 0,
            active["i"] > 0,
            active["u"] > 0,
        )
        if runs and (
            runs[-1].end == start
            and (
                runs[-1].bold,
                runs[-1].italic,
                runs[-1].underline,
            ) == style
        ):
            previous = runs[-1]
            runs[-1] = RichTextRun(
                previous.start,
                plain_length,
                *style,
            )
        else:
            runs.append(RichTextRun(start, plain_length, *style))

    for match in RICH_TEXT_TAG_PATTERN.finditer(text):
        append_text(text[position:match.start()])
        tag = match.group(1)
        is_closing = text[match.start() + 1] == "/"
        if is_closing:
            if active[tag] > 0:
                active[tag] -= 1
            else:
                append_text(match.group(0))
        else:
            active[tag] += 1
        position = match.end()
    append_text(text[position:])
    return "".join(parts), tuple(runs)


def message_plain_text(text: str) -> str:
    return parse_message_rich_text(text)[0]


def serialize_message_rich_text(
    segments: list[tuple[str, bool, bool, bool]],
) -> str:
    """Serialize visibly formatted composer segments into safe message tags."""
    output: list[str] = []
    active_tags: list[str] = []
    for text, bold, italic, underline in segments:
        desired_tags = [
            tag
            for tag, enabled in zip(
                RICH_TEXT_TAG_ORDER,
                (bold, italic, underline),
            )
            if enabled
        ]
        common = 0
        while (
            common < len(active_tags)
            and common < len(desired_tags)
            and active_tags[common] == desired_tags[common]
        ):
            common += 1
        for tag in reversed(active_tags[common:]):
            output.append(f"</{tag}>")
        for tag in desired_tags[common:]:
            output.append(f"<{tag}>")
        active_tags = desired_tags
        output.append(text)
    for tag in reversed(active_tags):
        output.append(f"</{tag}>")
    return "".join(output)


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
        parsed.scheme.casefold() == "https"
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
    if pattern.startswith("*."):
        return hostname.endswith(pattern[1:])
    return hostname == pattern


def _url_has_video_extension(parsed: Any) -> bool:
    query_format = (
        parse_qs(parsed.query).get("format", [""])[0]
        .strip()
        .casefold()
    )
    return (
        parsed.path.casefold().endswith(TENOR_VIDEO_EXTENSIONS)
        or f".{query_format}" in TENOR_VIDEO_EXTENSIONS
    )


def is_tenor_video_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    return (
        parsed.scheme.casefold() == "https"
        and hostname in TENOR_MEDIA_HOSTS
        and _url_has_video_extension(parsed)
    )


def is_trusted_looping_video_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    return (
        parsed.scheme.casefold() == "https"
        and _url_has_video_extension(parsed)
        and any(
            _image_hostname_matches_pattern(hostname, pattern)
            for pattern in LOOPING_VIDEO_HOST_PATTERNS
        )
    )


def is_supported_media_page_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    prefixes = TRUSTED_MEDIA_PAGE_PATHS.get(hostname, ())
    path = parsed.path or "/"
    return (
        parsed.scheme.casefold() == "https"
        and bool(prefixes)
        and any(path.startswith(prefix) for prefix in prefixes)
        and path != "/"
    )


@lru_cache(maxsize=4096)
def is_embeddable_media_url(url: str) -> bool:
    return (
        is_direct_image_url(url)
        or is_trusted_looping_video_url(url)
        or is_supported_media_page_url(url)
    )


@lru_cache(maxsize=4096)
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


@lru_cache(maxsize=4096)
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
            is_embeddable_media_url(url)
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
        if is_embeddable_media_url(url) and url not in image_urls:
            image_urls.append(url)
    return image_urls


def message_text_with_untrusted_images_hidden(
    text: str,
    untrusted_urls: set[str],
) -> str:
    visible_text = message_text_without_image_links(
        text,
        untrusted_urls,
    )
    visible_text = re.sub(r"[ \t]+", " ", visible_text).strip()
    return (
        f"[untrusted image] {visible_text}"
        if visible_text
        else "[untrusted image]"
    )


class MediaPageMetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.values: dict[str, str] = {}

    def handle_starttag(
        self,
        tag: str,
        attributes: list[tuple[str, str | None]],
    ) -> None:
        if tag.casefold() != "meta":
            return
        values = {
            str(name).casefold(): str(value or "").strip()
            for name, value in attributes
        }
        key = (
            values.get("property")
            or values.get("name")
            or ""
        ).casefold()
        content = values.get("content", "")
        if (
            key in MEDIA_PAGE_METADATA_KEYS
            and content
            and key not in self.values
        ):
            self.values[key] = content


def resolve_media_url_from_page(
    page_url: str,
    html_text: str,
) -> tuple[str, str] | None:
    parser = MediaPageMetadataParser()
    try:
        parser.feed(html_text)
        parser.close()
    except Exception:
        return None

    for key in MEDIA_PAGE_METADATA_KEYS:
        candidate = parser.values.get(key, "")
        if not candidate:
            continue
        resolved_url = urljoin(page_url, candidate)
        try:
            parsed = urlsplit(resolved_url)
        except ValueError:
            continue
        if (
            parsed.scheme.casefold() != "https"
            or not is_trusted_image_url(resolved_url)
        ):
            continue
        media_hint = (
            "video"
            if "video" in key or key == "twitter:player:stream"
            else "image"
        )
        return resolved_url, media_hint
    return None


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
    normalized_client_id = str(client_id).strip()
    if not re.fullmatch(r"[0-9a-fA-F]{64}", normalized_client_id):
        return ""
    digest = bytes.fromhex(normalized_client_id)
    return (
        base64.b32encode(digest)
        .decode("ascii")
        .rstrip("=")[:VISIBLE_USER_ID_CHARS]
    )


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


def normalize_message_history_limit(value: Any) -> int:
    try:
        candidate = int(value)
    except (TypeError, ValueError):
        candidate = DEFAULT_MESSAGE_HISTORY_LIMIT
    return (
        candidate
        if candidate in MESSAGE_HISTORY_OPTIONS
        else DEFAULT_MESSAGE_HISTORY_LIMIT
    )


def prune_local_history_map(
    history_by_scope: dict[str, Any],
    maximum_messages: int,
    *,
    excluded_scope_id: str | None = None,
) -> bool:
    limit = normalize_message_history_limit(maximum_messages)
    changed = False
    for scope_id, entries in list(history_by_scope.items()):
        if scope_id == excluded_scope_id or not isinstance(entries, list):
            continue
        if len(entries) > limit:
            history_by_scope[scope_id] = entries[-limit:]
            changed = True
    return changed


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
        "message_history_limit": DEFAULT_MESSAGE_HISTORY_LIMIT,
        "minimize_to_tray": False,
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
    config["message_history_limit"] = normalize_message_history_limit(
        config.get("message_history_limit", DEFAULT_MESSAGE_HISTORY_LIMIT)
    )
    config["minimize_to_tray"] = bool(
        config.get("minimize_to_tray", False)
    )
    stored_history = config.get("history")
    if not isinstance(stored_history, dict):
        stored_history = {}
    config["history"] = stored_history
    prune_local_history_map(
        stored_history,
        config["message_history_limit"],
    )
    config.pop("automatic_update_checks", None)
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


def newest_first_ntfy_records(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    def sort_key(
        indexed_record: tuple[int, dict[str, Any]],
    ) -> tuple[int, int]:
        record_index, record = indexed_record
        record_time = record.get("time")
        if type(record_time) is not int:
            record_time = -1
        return record_time, record_index

    return [
        record
        for _index, record in sorted(
            enumerate(records),
            key=sort_key,
            reverse=True,
        )
    ]


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


class DaemonTaskPool:
    """Small fixed-size worker pool whose tasks cannot hold process exit."""

    def __init__(self, max_workers: int, thread_name_prefix: str) -> None:
        self._tasks: queue.Queue[Any] = queue.Queue()
        self._lock = threading.Lock()
        self._shutdown = False
        self._threads = [
            threading.Thread(
                target=self._worker,
                name=f"{thread_name_prefix}_{worker_index}",
                daemon=True,
            )
            for worker_index in range(max_workers)
        ]
        for worker in self._threads:
            worker.start()

    def submit(self, function: Any, *args: Any, **kwargs: Any) -> None:
        with self._lock:
            if self._shutdown:
                raise RuntimeError("Worker pool is shut down.")
            self._tasks.put((function, args, kwargs))

    def _worker(self) -> None:
        while True:
            task = self._tasks.get()
            try:
                if task is None:
                    return
                function, args, kwargs = task
                try:
                    function(*args, **kwargs)
                except Exception:
                    pass
            finally:
                self._tasks.task_done()

    def shutdown(
        self,
        *,
        wait: bool,
        cancel_futures: bool,
    ) -> None:
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
            if cancel_futures:
                while True:
                    try:
                        pending_task = self._tasks.get_nowait()
                    except queue.Empty:
                        break
                    else:
                        self._tasks.task_done()
                        if pending_task is None:
                            break
            for _worker in self._threads:
                self._tasks.put(None)

        if wait:
            for worker in self._threads:
                worker.join()


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

    def to_message_text(self) -> str:
        segments: list[tuple[str, bool, bool, bool]] = []
        block = self.document().begin()
        first_block = True
        while block.isValid():
            if not first_block:
                segments.append(("\n", False, False, False))
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                if fragment.isValid():
                    formatting = fragment.charFormat()
                    segments.append((
                        fragment.text(),
                        formatting.fontWeight() >= QFont.Weight.Bold,
                        formatting.fontItalic(),
                        formatting.fontUnderline(),
                    ))
                iterator += 1
            first_block = False
            block = block.next()
        return serialize_message_rich_text(segments)

    def apply_formatting(self, style: str, enabled: bool) -> None:
        formatting = QTextCharFormat()
        if style == "bold":
            formatting.setFontWeight(
                QFont.Weight.Bold if enabled else QFont.Weight.Normal
            )
        elif style == "italic":
            formatting.setFontItalic(enabled)
        elif style == "underline":
            formatting.setFontUnderline(enabled)
        else:
            return

        cursor = self.textCursor()
        if cursor.hasSelection():
            cursor.mergeCharFormat(formatting)
            self.setTextCursor(cursor)
        else:
            self.mergeCurrentCharFormat(formatting)

    def clear_formatting_state(self) -> None:
        formatting = QTextCharFormat()
        formatting.setFontWeight(QFont.Weight.Normal)
        formatting.setFontItalic(False)
        formatting.setFontUnderline(False)
        self.mergeCurrentCharFormat(formatting)

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
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 1, 4, 1)
        layout.setSpacing(6)

        nickname_label = QLabel(nickname, self)
        layout.addWidget(nickname_label, 1)

        unread_label = QLabel(f"({unread_count})", self)
        unread_label.setStyleSheet(
            "color: #d97706; font-weight: 600;"
        )
        layout.addWidget(unread_label)
        unread_label.setVisible(unread_count > 0 and not muted)

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


def vertical_range_is_near_viewport(
    top: int,
    bottom: int,
    viewport_height: int,
) -> bool:
    visible_height = max(1, int(viewport_height))
    preload_margin = (
        visible_height * VIEWPORT_MEDIA_PRELOAD_SCREENS
    )
    return (
        int(bottom) >= -preload_margin
        and int(top) <= visible_height + preload_margin
    )


def polling_interval_seconds(
    *,
    window_focused: bool,
    tray_suspended: bool,
) -> float:
    if tray_suspended:
        return TRAY_POLL_INTERVAL_SECONDS
    if window_focused:
        return FOCUSED_POLL_INTERVAL_SECONDS
    return UNFOCUSED_POLL_INTERVAL_SECONDS


def network_idle_wait_seconds(
    *,
    now: float,
    last_global_poll_at: float | None,
    poll_interval: float,
) -> float:
    if last_global_poll_at is None:
        return 0.0
    ready_at = last_global_poll_at + max(0.0, poll_interval)
    return max(0.0, min(60.0, ready_at - now))


def immediate_poll_borrowed_seconds(
    *,
    now: float,
    last_global_poll_at: float | None,
    poll_interval: float,
) -> float:
    if last_global_poll_at is None:
        return 0.0
    interval = max(0.0, poll_interval)
    return max(
        0.0,
        min(interval, last_global_poll_at + interval - now),
    )


def background_repayment_delay_seconds(
    *,
    borrowed_seconds: float,
    checks_remaining: int,
) -> float:
    if checks_remaining <= 0:
        return 0.0
    return max(0.0, borrowed_seconds) / checks_remaining


def next_poll_room_id(
    *,
    room_ids: list[str],
    active_room_id: str,
    urgent_room_ids: set[str],
    last_poll_times: dict[str, float],
    now: float,
    poll_interval: float,
) -> str | None:
    if not room_ids:
        return None
    valid_urgent_room_ids = urgent_room_ids.intersection(room_ids)

    active_last_poll = last_poll_times.get(active_room_id)
    if active_last_poll is None:
        return active_room_id
    latest_poll_time = max(last_poll_times.values(), default=float("-inf"))
    active_was_last_polled = active_last_poll >= latest_poll_time
    active_poll_deadline = max(0.0, poll_interval) * 2
    if (
        active_room_id in valid_urgent_room_ids
        and not active_was_last_polled
    ):
        return active_room_id
    if now - active_last_poll >= active_poll_deadline:
        return active_room_id

    # Finish the first manual pass before stream activity can favor rooms
    # that have already been checked.
    never_polled_room_ids = [
        room_id for room_id in room_ids
        if room_id not in last_poll_times
    ]
    if never_polled_room_ids:
        if (
            active_room_id in valid_urgent_room_ids
            and active_room_id in never_polled_room_ids
        ):
            return active_room_id
        return never_polled_room_ids[0]

    oldest_room_id = min(
        room_ids,
        key=lambda room_id: last_poll_times[room_id],
    )
    manual_check_deadline = (
        max(0.0, poll_interval) * max(1, len(room_ids))
    )
    # Once the active-room reservation is satisfied, an overdue room gets the
    # next global request slot so passive background checks keep progressing.
    if (
        now - last_poll_times[oldest_room_id]
        >= manual_check_deadline
    ):
        return oldest_room_id

    background_urgent_room_ids = (
        valid_urgent_room_ids - {active_room_id}
    )
    if background_urgent_room_ids:
        return min(
            background_urgent_room_ids,
            key=lambda room_id: last_poll_times[room_id],
        )
    return oldest_room_id


def poll_message_should_notify(
    item: dict[str, Any],
    *,
    history_scan: bool,
    notification_started_at: int,
) -> bool:
    if not history_scan:
        return True
    ntfy_time = item.get("ntfy_time")
    return (
        type(ntfy_time) is int
        and ntfy_time >= notification_started_at
    )


def subscription_room_ids(
    record: dict[str, Any],
    topic_rooms: dict[str, tuple[str, ...]],
) -> tuple[str, ...]:
    if record.get("event") != "message":
        return ()
    topic = record.get("topic")
    if not isinstance(topic, str):
        return ()
    return topic_rooms.get(topic, ())


class ThemeComboBox(QComboBox):
    """Config combo box with Classic styling and no wheel changes."""

    def wheelEvent(self, event: Any) -> None:
        # Let a containing scroll area handle the wheel without changing the
        # selected setting under the pointer.
        event.ignore()

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
                right_width = max(
                    0,
                    round(block.blockFormat().rightMargin()),
                )
                if right_width > 0:
                    painter.fillRect(
                        max(0, viewport_width - right_width),
                        top,
                        right_width,
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
        should_close = True
        if self.close_callback is not None:
            should_close = bool(self.close_callback())
        if should_close:
            event.accept()
        else:
            event.ignore()


@dataclass
class RemoteMediaPreview:
    source_url: str
    data: bytes
    kind: str
    frame: QImage | None


class AnimatedMediaController(QObject):
    frame_ready = Signal(str, object)

    def __init__(
        self,
        url: str,
        media: RemoteMediaPreview,
        parent: QObject,
    ) -> None:
        super().__init__(parent)
        self.url = url
        self.media = media
        self._buffer = QBuffer(self)
        self._buffer.setData(QByteArray(media.data))
        self._buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        self._movie: QMovie | None = None
        self._player: QMediaPlayer | None = None
        self._video_sink: QVideoSink | None = None

    def start(self) -> None:
        if self.media.kind == "animated_gif":
            self._start_gif()
        elif self.media.kind == "looping_video":
            self._start_video()

    def _start_gif(self) -> None:
        movie = QMovie(self._buffer, QByteArray(), self)
        movie.setCacheMode(QMovie.CacheMode.CacheNone)
        movie.frameChanged.connect(self._on_movie_frame_changed)
        movie.finished.connect(self._restart_gif)
        self._movie = movie
        movie.start()

    def _restart_gif(self) -> None:
        movie = self._movie
        if movie is None:
            return
        movie.stop()
        self._buffer.seek(0)
        movie.start()

    def _on_movie_frame_changed(self, _frame_number: int) -> None:
        movie = self._movie
        if movie is not None:
            self._emit_frame(movie.currentImage())

    def _start_video(self) -> None:
        ensure_qt_multimedia_loaded()
        video_sink = QVideoSink(self)
        video_sink.videoFrameChanged.connect(self._on_video_frame_changed)
        player = QMediaPlayer(self)
        player.setVideoSink(video_sink)
        player.setLoops(QMediaPlayer.Loops.Infinite)
        player.setSourceDevice(
            self._buffer,
            QUrl(self.media.source_url or self.url),
        )
        self._video_sink = video_sink
        self._player = player
        player.play()

    def _on_video_frame_changed(self, video_frame: Any) -> None:
        self._emit_frame(video_frame.toImage())

    def _emit_frame(self, frame: QImage) -> None:
        if (
            frame.isNull()
            or frame.width() <= 0
            or frame.height() <= 0
            or frame.width() * frame.height() > MAX_REMOTE_IMAGE_PIXELS
        ):
            return
        emitted_frame = frame.copy()
        emitted_frame.setDevicePixelRatio(1.0)
        self.frame_ready.emit(self.url, emitted_frame)

    def stop(self) -> None:
        movie = self._movie
        self._movie = None
        if movie is not None:
            movie.stop()
            try:
                movie.frameChanged.disconnect(
                    self._on_movie_frame_changed
                )
                movie.finished.disconnect(self._restart_gif)
            except (RuntimeError, TypeError):
                pass
            movie.deleteLater()

        player = self._player
        self._player = None
        video_sink = self._video_sink
        self._video_sink = None
        if player is not None:
            player.stop()
            player.setVideoSink(None)
            player.setSource(QUrl())
            player.deleteLater()
        if video_sink is not None:
            try:
                video_sink.videoFrameChanged.disconnect(
                    self._on_video_frame_changed
                )
            except (RuntimeError, TypeError):
                pass
            video_sink.deleteLater()

        self._buffer.close()
        self._buffer.setData(QByteArray())


class EncryptedChatClient(QObject):
    ui_event_available = Signal()

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
        self.subscription_session = requests.Session()
        self.subscription_session.headers.update({
            "User-Agent": f"{APP_NAME}/{CONFIG_FORMAT_VERSION}",
            "Accept": "application/x-ndjson, application/json",
        })

        self.stop_event = threading.Event()
        self.network_wakeup_event = threading.Event()
        self.window_focused_event = threading.Event()
        self.window_focused_event.set()
        self.tray_mode_event = threading.Event()
        self.subscription_refresh_event = threading.Event()
        self.subscription_response_lock = threading.Lock()
        self.subscription_response: requests.Response | None = None
        self.network_control_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self.ui_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.send_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self.subscription_room_queue: queue.Queue[str] = queue.Queue(
            maxsize=MAX_SUBSCRIPTION_SIGNAL_QUEUE
        )
        self.pending_ntfy_poll_batches: dict[
            str,
            dict[str, Any],
        ] = {}

        self.network_thread: threading.Thread | None = None
        self.subscription_thread: threading.Thread | None = None
        self.connected = False

        self.seen_client_message_ids: set[str] = set()
        self.seen_ntfy_message_ids: set[str] = set()
        self.message_log: list[dict[str, Any]] = []
        self.draft_message_id = uuid.uuid4().hex
        self.current_estimated_packet_size = 0
        self.message_size_check_pending = False
        self.rendered_message_items: dict[str, dict[str, Any]] = {}
        self.rendered_image_links: dict[str, str] = {}
        self.rendered_image_positions: dict[str, list[int]] = {}
        self.rendered_image_candidates: dict[str, list[int]] = {}
        self.viewport_embedded_image_urls: set[str] = set()
        self.image_preview_cache: dict[str, RemoteMediaPreview | None] = {}
        self.pending_image_previews: set[str] = set()
        self.animated_media_controllers: dict[
            str,
            AnimatedMediaController,
        ] = {}
        self.last_inline_animation_frame_at: dict[str, float] = {}
        self.current_image_preview_url: str | None = None
        self.recent_chatroom_switch_times: list[float] = []
        self.image_fetch_executor = DaemonTaskPool(
            max_workers=3,
            thread_name_prefix="SpriteLinkImage",
        )
        self._hovered_message_id: str | None = None
        self._pending_tooltip_message_id: str | None = None
        self._pending_tooltip_global_position = QPoint()
        self._config_snapshot_at_open: tuple[Any, ...] | None = None
        self._message_font_cache: dict[tuple[str, bool, bool], QFont] = {}
        self._loading_profile_controls = False
        self._connection_error_visible = False
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
        self.notification_started_at = int(time.time())
        if cleared_stale_unread:
            try:
                save_config(self.config_data)
            except Exception:
                pass
        self._closing = False
        self._force_quit = False
        self._tray_quit_pending = False
        self._minimized_to_tray = False
        self._tray_ui_suspended = False
        self.available_update: ReleaseInfo | None = None
        self._update_check_in_progress = False
        self._update_download_in_progress = False

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
        self.message_history_limit_var = ValueModel(
            int(self.config_data["message_history_limit"])
        )
        self.minimize_to_tray_var = ValueModel(
            bool(self.config_data["minimize_to_tray"])
        )
        self.status_var = ValueModel("Connecting")

        self.connection_error_timer = QTimer(self)
        self.connection_error_timer.setSingleShot(True)
        self.connection_error_timer.setInterval(CONNECTION_ERROR_DELAY_MS)
        self.connection_error_timer.timeout.connect(
            self._show_connection_error_if_still_disconnected
        )

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

        self.background_history_prune_timer = QTimer(self)
        self.background_history_prune_timer.setInterval(
            BACKGROUND_HISTORY_PRUNE_INTERVAL_MS
        )
        self.background_history_prune_timer.timeout.connect(
            self._prune_background_local_histories
        )

        self.update_check_timer = QTimer(self)
        self.update_check_timer.setInterval(UPDATE_CHECK_INTERVAL_MS)
        self.update_check_timer.timeout.connect(
            self._check_for_updates
        )
        self.message_sound_effects: dict[str, QSoundEffect] = {}
        self.active_message_sound_effect: QSoundEffect | None = None
        self.pending_message_sound_effect: QSoundEffect | None = None
        self.pending_message_sound_name = ""
        self.pending_message_sound_report_errors = False
        self.compressed_message_sound_audio_output: (
            QAudioOutput | None
        ) = None
        self.compressed_message_sound_player: QMediaPlayer | None = None
        self.compressed_message_sound_name = ""
        self.compressed_message_sound_report_errors = False

        self.viewport_media_timer = QTimer(self)
        self.viewport_media_timer.setSingleShot(True)
        self.viewport_media_timer.timeout.connect(
            self._update_viewport_media
        )

        self.chat_tooltip_timer = QTimer(self)
        self.chat_tooltip_timer.setSingleShot(True)
        self.chat_tooltip_timer.timeout.connect(
            self._show_pending_chat_tooltip
        )

        self.ui_event_available.connect(self._process_ui_queue)

        self._apply_theme()
        self._apply_application_font_strategy()
        self._build_ui()
        self._build_tray_icon()
        self._schedule_utc_midnight_reset()
        self._apply_server_preset_state()
        self._load_saved_history_for_current_room()

        self.root.close_callback = self._on_close
        QTimer.singleShot(0, self._apply_titlebar_theme)
        QTimer.singleShot(0, self._check_for_updates)
        self.update_check_timer.start()
        self.background_history_prune_timer.start()
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
        cache_key = (
            font_name,
            bool(bold),
            self._is_windows_classic_theme(),
        )
        cached = self._message_font_cache.get(cache_key)
        if cached is None:
            point_size = MESSAGE_FONT_POINT_SIZES.get(font_name, 14)
            cached = self._make_font(
                font_name,
                point_size,
                bold=bold,
            )
            self._message_font_cache[cache_key] = cached
        return cached

    def _apply_application_font_strategy(self) -> None:
        self._message_font_cache.clear()
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

    def _exec_themed_file_dialog(self, dialog: QFileDialog) -> int:
        # Native Windows dialogs are created when exec() starts. Apply once to
        # the wrapper and once from the nested event loop after it is visible.
        self._apply_window_titlebar_theme(dialog)
        QTimer.singleShot(
            0,
            lambda: self._apply_window_titlebar_theme(dialog),
        )
        return int(dialog.exec())

    def _build_tray_notification_icon(self, base_icon: QIcon) -> QIcon:
        outlined_icon = QIcon()
        outline_color = QColor(TRAY_NOTIFICATION_OUTLINE_COLOR)
        for icon_size in (16, 20, 24, 32, 48):
            inner_size = max(1, icon_size - 2)
            source = base_icon.pixmap(
                inner_size,
                inner_size,
            ).toImage().convertToFormat(
                QImage.Format.Format_ARGB32
            )
            canvas = QImage(
                icon_size,
                icon_size,
                QImage.Format.Format_ARGB32,
            )
            canvas.fill(Qt.GlobalColor.transparent)

            for source_y in range(source.height()):
                for source_x in range(source.width()):
                    source_color = source.pixelColor(source_x, source_y)
                    if source_color.alpha() == 0:
                        continue
                    center_x = source_x + 1
                    center_y = source_y + 1
                    for offset_y in (-1, 0, 1):
                        for offset_x in (-1, 0, 1):
                            if offset_x == 0 and offset_y == 0:
                                continue
                            canvas.setPixelColor(
                                center_x + offset_x,
                                center_y + offset_y,
                                outline_color,
                            )

            for source_y in range(source.height()):
                for source_x in range(source.width()):
                    source_color = source.pixelColor(source_x, source_y)
                    if source_color.alpha() != 0:
                        canvas.setPixelColor(
                            source_x + 1,
                            source_y + 1,
                            source_color,
                        )
            outlined_icon.addPixmap(QPixmap.fromImage(canvas))
        return outlined_icon

    def _build_tray_icon(self) -> None:
        self._tray_normal_icon = QIcon(str(WINDOW_ICON_PATH))
        if self._tray_normal_icon.isNull():
            self._tray_normal_icon = self.root.windowIcon()
        self._tray_notification_icon = (
            self._build_tray_notification_icon(self._tray_normal_icon)
            if not self._tray_normal_icon.isNull()
            else QIcon()
        )
        self.tray_icon = QSystemTrayIcon(
            self._tray_normal_icon,
            self.root,
        )
        self.tray_icon.setToolTip(APP_NAME)
        self.tray_menu = QMenu()
        show_action = self.tray_menu.addAction("Show SpriteLink")
        show_action.triggered.connect(self._restore_from_tray)
        self.tray_minimize_action = self.tray_menu.addAction(
            "Minimize to Tray"
        )
        self.tray_minimize_action.setCheckable(True)
        self.tray_minimize_action.toggled.connect(
            self._on_tray_minimize_toggled
        )
        self.minimize_to_tray_var.bind(
            self._sync_tray_minimize_action
        )
        self.tray_menu.addSeparator()
        exit_action = self.tray_menu.addAction("Exit")
        exit_action.triggered.connect(self._quit_from_tray)
        self.tray_icon.setContextMenu(self.tray_menu)
        self.tray_icon.activated.connect(
            self._on_tray_icon_activated
        )
        if self._tray_available():
            self.tray_icon.show()
            if self._has_unread_messages():
                self._mark_tray_notification()

    def _tray_available(self) -> bool:
        return (
            QSystemTrayIcon.isSystemTrayAvailable()
            and not self._tray_normal_icon.isNull()
        )

    def _can_minimize_to_tray(self) -> bool:
        return (
            bool(self.minimize_to_tray_var.get())
            and self._tray_available()
        )

    def _sync_tray_minimize_action(self, enabled: Any) -> None:
        previous = self.tray_minimize_action.blockSignals(True)
        self.tray_minimize_action.setChecked(bool(enabled))
        self.tray_minimize_action.blockSignals(previous)

    def _on_tray_minimize_toggled(self, enabled: bool) -> None:
        previous = bool(
            self.config_data.get("minimize_to_tray", False)
        )
        self.minimize_to_tray_var.set(bool(enabled))
        self.config_data["minimize_to_tray"] = bool(enabled)
        try:
            save_config(self.config_data)
        except Exception as exc:
            self.config_data["minimize_to_tray"] = previous
            self.minimize_to_tray_var.set(previous)
            messagebox.showerror(
                "Could not save settings",
                str(exc),
                parent=self.root,
            )

    def _pause_animated_media(self) -> None:
        for controller in self.animated_media_controllers.values():
            controller.stop()
            controller.deleteLater()
        self.animated_media_controllers.clear()
        self.last_inline_animation_frame_at.clear()

    def _sync_window_activity(self) -> None:
        if (
            self._tray_ui_suspended
            or not self.root.isVisible()
            or self.root.isMinimized()
        ):
            self._pause_animated_media()
            return
        for url in self.rendered_image_positions:
            media = self.image_preview_cache.get(url)
            if isinstance(media, RemoteMediaPreview):
                self._ensure_animated_media_controller(url, media)

    def _suspend_for_tray(self) -> None:
        if self._tray_ui_suspended:
            return
        self._tray_ui_suspended = True
        self.tray_mode_event.set()
        self.network_wakeup_event.set()
        self.viewport_media_timer.stop()
        self._hide_chat_tooltip()
        if self.image_preview_overlay.isVisible():
            self._hide_image_preview_popup()
        self._pause_animated_media()
        self._release_message_sound_resources()
        self.image_preview_cache.clear()
        self.rendered_message_items.clear()
        self.rendered_image_links.clear()
        self.rendered_image_positions.clear()
        self.rendered_image_candidates.clear()
        self.viewport_embedded_image_urls.clear()
        self.chat_display.row_background_blocks.clear()
        self.chat_display.collapsed_fade_blocks.clear()
        self.chat_display.setExtraSelections([])
        self._reset_chat_document()
        self._message_font_cache.clear()
        profile_icon_tooltip_data_uri.cache_clear()
        _cached_message_url_spans.cache_clear()
        is_embeddable_media_url.cache_clear()
        is_trusted_image_url.cache_clear()
        is_likely_nsfw_image_url.cache_clear()
        QPixmapCache.clear()
        self.root.setUpdatesEnabled(False)

    def _resume_from_tray(self) -> None:
        if not self._tray_ui_suspended:
            return
        self._tray_ui_suspended = False
        self.tray_mode_event.clear()
        self.network_wakeup_event.set()
        self.root.setUpdatesEnabled(True)
        self._render_message_log(scroll_to_bottom=True)
        self._sync_window_activity()

    def _hide_to_tray(self) -> None:
        self._minimized_to_tray = True
        self._clear_tray_notification_if_no_unread()
        self._suspend_for_tray()
        self.root.hide()

    def _restore_from_tray(self) -> None:
        was_suspended = self._tray_ui_suspended
        self._minimized_to_tray = False
        self._clear_tray_notification_if_no_unread()
        self.root.showNormal()
        self.root.raise_()
        self.root.activateWindow()
        if was_suspended:
            QTimer.singleShot(0, self._resume_from_tray)
        else:
            QTimer.singleShot(0, self._sync_window_activity)

    def _clear_tray_notification(self) -> None:
        if not self._tray_normal_icon.isNull():
            self.tray_icon.setIcon(self._tray_normal_icon)
        self.tray_icon.setToolTip(APP_NAME)

    def _has_unread_messages(self) -> bool:
        for unread_count in self._unread_counts().values():
            try:
                if int(unread_count) > 0:
                    return True
            except (TypeError, ValueError):
                continue
        return False

    def _clear_tray_notification_if_no_unread(self) -> None:
        if not self._has_unread_messages():
            self._clear_tray_notification()

    def _mark_tray_notification(self) -> None:
        if not self.tray_icon.isVisible():
            return
        if not self._tray_notification_icon.isNull():
            self.tray_icon.setIcon(self._tray_notification_icon)
        self.tray_icon.setToolTip(f"{APP_NAME} — new messages")

    def _on_tray_icon_activated(
        self,
        reason: QSystemTrayIcon.ActivationReason,
    ) -> None:
        if reason in {
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        }:
            self._restore_from_tray()

    def _quit_from_tray(self) -> None:
        if self._closing or self._tray_quit_pending:
            return
        self._force_quit = True
        self._tray_quit_pending = True
        # Let the native tray menu finish handling its action before closing
        # its owner window and stopping the Qt event loop.
        QTimer.singleShot(0, self._finish_quit_from_tray)

    def _finish_quit_from_tray(self) -> None:
        self._tray_quit_pending = False
        self.root.close()
        if not self._closing:
            return
        app = QApplication.instance()
        if app is not None:
            app.quit()

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
        if hasattr(self, "update_button"):
            self._update_update_button_style()
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
        self.root.setWindowTitle(APP_NAME)
        self._update_connection_status_label()

    def _update_connection_status_label(self) -> None:
        if not hasattr(self, "status_label"):
            return
        room_name = self._active_chatroom()["nickname"]
        self.status_label.setText(chatroom_connection_label(
            room_name,
            connection_error=self._connection_error_visible,
        ))

    def _connection_error_timer_should_run(self) -> bool:
        return (
            str(self.status_var.get()) != "Connected"
            and self.window_focused_event.is_set()
            and self.root.isActiveWindow()
        )

    def _sync_connection_error_timer(self) -> None:
        if str(self.status_var.get()) == "Connected":
            self.connection_error_timer.stop()
            self._connection_error_visible = False
        elif not self._connection_error_timer_should_run():
            self.connection_error_timer.stop()
        elif (
            not self._connection_error_visible
            and not self.connection_error_timer.isActive()
        ):
            self.connection_error_timer.start()
        self._update_connection_status_label()

    def _show_connection_error_if_still_disconnected(self) -> None:
        if self._connection_error_timer_should_run():
            self._connection_error_visible = True
        self._update_connection_status_label()

    def _on_connection_status_changed(self, _status: Any) -> None:
        self._sync_connection_error_timer()

    def _restart_connection_error_delay(self) -> None:
        self.connection_error_timer.stop()
        self._sync_connection_error_timer()

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
                self.chatrooms_list,
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
                notification_button_stylesheet(
                    self._is_windows_classic_theme()
                )
            )
        else:
            self.chatrooms_toggle.setStyleSheet("")

    def _update_config_toggle_update_style(self) -> None:
        if not hasattr(self, "config_toggle"):
            return
        if self.available_update is not None:
            self.config_toggle.setStyleSheet(
                notification_button_stylesheet(
                    self._is_windows_classic_theme()
                )
            )
            self.config_toggle.setToolTip(
                f"SpriteLink {self.available_update.version} is available."
            )
        else:
            self.config_toggle.setStyleSheet("")
            self.config_toggle.setToolTip("")

    def _update_update_button_style(self) -> None:
        if not hasattr(self, "update_button"):
            return
        if (
            self.available_update is not None
            and self.update_button.isEnabled()
        ):
            self.update_button.setStyleSheet(
                notification_button_stylesheet(
                    self._is_windows_classic_theme()
                )
            )
        else:
            self.update_button.setStyleSheet("")

    def _unread_counts(self) -> dict[str, int]:
        unread_counts = self.config_data.setdefault("unread_counts", {})
        if not isinstance(unread_counts, dict):
            unread_counts = {}
            self.config_data["unread_counts"] = unread_counts
        return unread_counts

    def _mark_chatroom_read(self, room_id: str) -> None:
        unread_counts = self._unread_counts()
        if room_id not in unread_counts:
            self._clear_tray_notification_if_no_unread()
            return
        unread_counts.pop(room_id, None)
        self._clear_tray_notification_if_no_unread()
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

        self._request_subscription_refresh()
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

        if key_changed:
            self._request_subscription_refresh()
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
        self._clear_tray_notification_if_no_unread()
        self.config_data.get("room_profiles", {}).pop(room_id, None)
        self.initial_history_pending_rooms.discard(room_id)
        self._request_subscription_refresh()

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
        self.network_wakeup_event.set()

    def _switch_active_chatroom(
        self,
        *,
        poll_immediately: bool = True,
    ) -> None:
        self._unread_counts().pop(self.active_chatroom_id, None)
        self._clear_tray_notification_if_no_unread()
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
        self._restart_connection_error_delay()
        self._request_network_refresh(
            poll_immediately=poll_immediately
        )
        self.message_entry.clear()
        self.message_entry.clear_formatting_state()
        self._sync_formatting_buttons()
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

        self.status_label = QLabel()
        self.status_label.setFont(
            self._make_font("Segoe UI", 9, bold=True)
        )
        self.status_var.bind(self._on_connection_status_changed)
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
        self.chat_display.verticalScrollBar().valueChanged.connect(
            lambda _value: self.viewport_media_timer.start(
                VIEWPORT_MEDIA_UPDATE_DELAY_MS
            )
        )
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
        self.formatting_menu_button = QPushButton("Formatting")
        self.formatting_menu_button.setCheckable(True)
        self.formatting_menu_button.toggled.connect(
            self._on_formatting_menu_toggled
        )
        composer_actions.addWidget(self.formatting_menu_button)
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

        self.formatting_menu = QFrame()
        self.formatting_menu.setFrameShape(QFrame.Shape.StyledPanel)
        formatting_layout = QHBoxLayout(self.formatting_menu)
        formatting_layout.setContentsMargins(8, 5, 8, 5)
        formatting_layout.setSpacing(7)
        self.bold_format_button = QPushButton("Bold")
        self.italic_format_button = QPushButton("Italic")
        self.underline_format_button = QPushButton("Underline")
        bold_button_font = QFont(self.bold_format_button.font())
        bold_button_font.setBold(True)
        self.bold_format_button.setFont(bold_button_font)
        italic_button_font = QFont(self.italic_format_button.font())
        italic_button_font.setItalic(True)
        self.italic_format_button.setFont(italic_button_font)
        underline_button_font = QFont(self.underline_format_button.font())
        underline_button_font.setUnderline(True)
        self.underline_format_button.setFont(underline_button_font)
        for button, style in (
            (self.bold_format_button, "bold"),
            (self.italic_format_button, "italic"),
            (self.underline_format_button, "underline"),
        ):
            button.setCheckable(True)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(
                lambda checked, selected_style=style: (
                    self._toggle_composer_formatting(
                        selected_style,
                        checked,
                    )
                )
            )
            formatting_layout.addWidget(button)
        formatting_layout.addStretch(1)
        self.formatting_menu.hide()
        content_layout.addWidget(self.formatting_menu)
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
        self.message_entry.cursorPositionChanged.connect(
            self._sync_formatting_buttons
        )
        self.message_entry.selectionChanged.connect(
            self._sync_formatting_buttons
        )
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
            self._set_button_checked(self.formatting_menu_button, False)
            self.formatting_menu.hide()
            self.identity_username_entry.setFocus()

    def _on_font_menu_toggled(self, checked: bool) -> None:
        self.font_menu.setVisible(checked)
        if checked:
            self._set_button_checked(self.identity_menu_button, False)
            self.identity_menu.hide()
            self._set_button_checked(self.formatting_menu_button, False)
            self.formatting_menu.hide()
            self.message_font_combo.setFocus()

    def _on_formatting_menu_toggled(self, checked: bool) -> None:
        self.formatting_menu.setVisible(checked)
        if checked:
            self._set_button_checked(self.identity_menu_button, False)
            self.identity_menu.hide()
            self._set_button_checked(self.font_menu_button, False)
            self.font_menu.hide()
            self.message_entry.setFocus()
            self._sync_formatting_buttons()

    def _toggle_composer_formatting(
        self,
        style: str,
        enabled: bool,
    ) -> None:
        self.message_entry.apply_formatting(style, enabled)
        self.message_entry.setFocus()
        self._sync_formatting_buttons()

    def _sync_formatting_buttons(self) -> None:
        if not hasattr(self, "bold_format_button"):
            return
        formatting = self.message_entry.currentCharFormat()
        states = (
            (
                self.bold_format_button,
                formatting.fontWeight() >= QFont.Weight.Bold,
            ),
            (self.italic_format_button, formatting.fontItalic()),
            (self.underline_format_button, formatting.fontUnderline()),
        )
        for button, checked in states:
            self._set_button_checked(button, checked)

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
        preset["username"] = sanitize_username(value)
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
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
        dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        dialog.setNameFilter(
            "Image files (*.png *.jpg *.jpeg);;PNG images (*.png);;"
            "JPEG images (*.jpg *.jpeg)"
        )
        if (
            self._exec_themed_file_dialog(dialog)
            != QDialog.DialogCode.Accepted
        ):
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
        self.image_preview_button_row = button_row
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

    def _ensure_animated_media_controller(
        self,
        url: str,
        media: RemoteMediaPreview,
    ) -> None:
        if media.kind not in {"animated_gif", "looping_video"}:
            return
        if url in self.animated_media_controllers:
            return

        while (
            len(self.animated_media_controllers)
            >= MAX_ACTIVE_ANIMATED_MEDIA
        ):
            oldest_url = next(iter(self.animated_media_controllers))
            controller = self.animated_media_controllers.pop(oldest_url)
            controller.stop()
            controller.deleteLater()
            self.last_inline_animation_frame_at.pop(oldest_url, None)

        controller = AnimatedMediaController(url, media, self)
        controller.frame_ready.connect(self._on_animated_media_frame)
        self.animated_media_controllers[url] = controller
        controller.start()

    def _on_animated_media_frame(
        self,
        url: str,
        frame_value: object,
    ) -> None:
        if not isinstance(frame_value, QImage) or frame_value.isNull():
            return
        media = self.image_preview_cache.get(url)
        if not isinstance(media, RemoteMediaPreview):
            return
        media.frame = frame_value

        if self.current_image_preview_url == url:
            self._set_large_image_preview_frame(frame_value)

        if is_likely_nsfw_image_url(url):
            return
        image_positions = self.rendered_image_positions.get(url, [])
        if not image_positions:
            return

        preview = self._scaled_inline_media_frame(
            media,
            frame_value,
        )
        token = hashlib.sha256(url.encode("utf-8")).hexdigest()
        resource_url = QUrl(f"spritelink-chat-image-resource:{token}")
        document = self.chat_display.document()
        document.addResource(
            QTextDocument.ResourceType.ImageResource,
            resource_url,
            preview,
        )
        for position in image_positions:
            document.markContentsDirty(position, 1)
        self.chat_display.viewport().update()

    def _set_large_image_preview_frame(self, image: QImage) -> None:
        overlay_layout = self.image_preview_overlay.layout()
        panel_layout = self.image_preview_panel.layout()
        overlay_margins = overlay_layout.contentsMargins()
        panel_margins = panel_layout.contentsMargins()
        panel_frame = self.image_preview_panel.frameWidth()

        available_width = max(
            1,
            min(
                IMAGE_PREVIEW_MAX_WIDTH,
                self.image_preview_panel.contentsRect().width()
                - panel_margins.left()
                - panel_margins.right(),
            ),
        )
        vertical_chrome = (
            overlay_margins.top()
            + overlay_margins.bottom()
            + panel_margins.top()
            + panel_margins.bottom()
            + panel_layout.spacing()
            + self.image_preview_button_row.sizeHint().height()
            + panel_frame * 2
        )
        available_height = max(
            1,
            min(
                IMAGE_PREVIEW_MAX_HEIGHT,
                self.image_preview_overlay.height() - vertical_chrome,
            ),
        )
        preview = image.scaled(
            available_width,
            available_height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.image_preview_label.clear()
        self.image_preview_label.setPixmap(QPixmap.fromImage(preview))

    def _show_image_preview_popup(self, url: str) -> None:
        media = self.image_preview_cache.get(url)
        if not isinstance(media, RemoteMediaPreview):
            return
        self._hide_chat_tooltip()
        self.current_image_preview_url = url
        self._ensure_animated_media_controller(url, media)
        self._sync_image_preview_overlay_geometry()
        self.image_preview_overlay.show()
        self.image_preview_overlay.raise_()
        self._update_image_preview_popup()
        QTimer.singleShot(0, self._update_image_preview_popup)

    def _update_image_preview_popup(self) -> None:
        url = self.current_image_preview_url
        media = self.image_preview_cache.get(url or "")
        if not isinstance(media, RemoteMediaPreview):
            self.image_preview_label.clear()
            self.image_preview_url_label.clear()
            return
        self._ensure_animated_media_controller(url or "", media)
        if isinstance(media.frame, QImage) and not media.frame.isNull():
            self._set_large_image_preview_frame(media.frame)
        else:
            self.image_preview_label.setPixmap(QPixmap())
            self.image_preview_label.setText("Loading...")
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
        url = self.current_image_preview_url
        self.image_preview_overlay.hide()
        self.current_image_preview_url = None
        self.image_preview_label.clear()
        self.image_preview_url_label.clear()
        self.image_preview_url_label.setToolTip("")
        if url and url not in self.rendered_image_positions:
            controller = self.animated_media_controllers.pop(url, None)
            if controller is not None:
                controller.stop()
                controller.deleteLater()
            self.last_inline_animation_frame_at.pop(url, None)
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
            self._heading("Behavior"),
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

        layout.addWidget(QLabel("Message History"), row, 0)
        self.message_history_combo = ThemeComboBox()
        self.message_history_combo.addItems([
            str(option) for option in MESSAGE_HISTORY_OPTIONS
        ])
        self.message_history_combo.setCurrentText(
            str(self.message_history_limit_var.get())
        )
        self.message_history_combo.currentTextChanged.connect(
            lambda value: self.message_history_limit_var.set(
                normalize_message_history_limit(value)
            )
        )
        self.message_history_limit_var.bind(
            lambda value: self.message_history_combo.setCurrentText(
                str(value)
            )
        )
        layout.addWidget(self.message_history_combo, row, 1, 1, 2)
        row += 1

        self.minimize_to_tray_checkbox = QCheckBox("Minimize to Tray")
        self.minimize_to_tray_checkbox.setChecked(
            bool(self.minimize_to_tray_var.get())
        )
        self.minimize_to_tray_checkbox.setEnabled(
            QSystemTrayIcon.isSystemTrayAvailable()
        )
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.minimize_to_tray_checkbox.setToolTip(
                "The Windows notification area is unavailable."
            )
        self.minimize_to_tray_checkbox.toggled.connect(
            self.minimize_to_tray_var.set
        )
        self.minimize_to_tray_var.bind(
            self.minimize_to_tray_checkbox.setChecked
        )
        layout.addWidget(
            self.minimize_to_tray_checkbox,
            row,
            0,
            1,
            3,
        )
        row += 1

        layout.addWidget(self._separator(), row, 0, 1, 3)
        row += 1
        layout.addWidget(self._heading("Version"), row, 0, 1, 3)
        row += 1

        self.current_version_label = QLabel(
            f"Current Version: {RUNNING_VERSION}"
        )
        layout.addWidget(self.current_version_label, row, 0, 1, 3)
        row += 1

        self.latest_version_label = QLabel("Latest Version: ---")
        layout.addWidget(self.latest_version_label, row, 0, 1, 3)
        row += 1

        self.update_button = QPushButton("Up-to-date")
        self.update_button.setEnabled(False)
        self.update_button.clicked.connect(self._on_update_now_clicked)
        layout.addWidget(
            self.update_button,
            row,
            0,
            1,
            3,
            Qt.AlignmentFlag.AlignLeft,
        )
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
            dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
            dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
            dialog.setNameFilter("Audio files (*.wav *.mp3 *.ogg)")
            if (
                self._exec_themed_file_dialog(dialog)
                != QDialog.DialogCode.Accepted
            ):
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

    def _check_for_updates(self) -> None:
        if (
            self._closing
            or self._update_check_in_progress
            or self._update_download_in_progress
        ):
            return

        self._update_check_in_progress = True
        self.update_check_timer.start()
        self.update_button.setText("Checking...")
        self.update_button.setEnabled(False)
        self.update_button.setToolTip("")
        self._update_update_button_style()
        threading.Thread(
            target=self._check_for_updates_worker,
            daemon=True,
            name="SpriteLinkUpdateCheck",
        ).start()

    def _check_for_updates_worker(self) -> None:
        try:
            release = fetch_latest_release(
                UPDATE_REPOSITORY,
                current_version=RUNNING_VERSION,
            )
        except Exception:
            self._queue_ui_event((
                "update_check_result",
                {"error": True},
            ))
            return

        self._queue_ui_event((
            "update_check_result",
            {"release": release},
        ))

    def _show_no_available_update(self) -> None:
        self.available_update = None
        self.update_button.setText("Up-to-date")
        self.update_button.setEnabled(False)
        self.update_button.setToolTip("")
        self._update_update_button_style()
        self._update_config_toggle_update_style()

    def _handle_update_check_result(self, payload: dict[str, Any]) -> None:
        self._update_check_in_progress = False

        if payload.get("error"):
            self.latest_version_label.setText("Latest Version: ---")
            self.latest_version_label.setToolTip("")
            self._show_no_available_update()
            return

        release = payload.get("release")
        if not isinstance(release, ReleaseInfo):
            self.latest_version_label.setText("Latest Version: ---")
            self.latest_version_label.setToolTip("")
            self._show_no_available_update()
            return

        self.latest_version_label.setText(
            f"Latest Version: {release.version}"
        )
        self.latest_version_label.setToolTip(release.page_url)
        if release_is_newer(RUNNING_VERSION, release.version):
            self.available_update = release
            self.update_button.setText("Update")
            self.update_button.setEnabled(True)
            self.update_button.setToolTip(release.notes.strip())
            self._update_update_button_style()
            self._update_config_toggle_update_style()
            if self.config_overlay.isVisible():
                self.update_button.setFocus()
            return

        self._show_no_available_update()

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
        self.update_button.setText("Downloading...")
        self.update_button.setEnabled(False)
        self._update_update_button_style()
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
            self._queue_ui_event((
                "update_download_failed",
                str(exc) or "The update download failed.",
            ))
            return
        self._queue_ui_event((
            "update_download_ready",
            {
                "path": str(installer_path),
                "version": release.version,
            },
        ))

    def _handle_update_download_failed(self, error: str) -> None:
        self._update_download_in_progress = False
        self.update_button.setText("Update")
        self.update_button.setEnabled(self.available_update is not None)
        self.update_button.setToolTip(error)
        self._update_update_button_style()
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

        self.update_button.setText("Installing...")
        self.update_button.setEnabled(False)
        self._update_update_button_style()
        self._force_quit = True
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
            int(self.message_history_limit_var.get()),
            bool(self.minimize_to_tray_var.get()),
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
                self.update_button
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
        self.config_data["message_history_limit"] = (
            normalize_message_history_limit(
                self.message_history_limit_var.get()
            )
        )
        self.config_data["minimize_to_tray"] = bool(
            self.minimize_to_tray_var.get()
        )
        self.config_data.pop("automatic_update_checks", None)
        self.config_data.pop("chime_enabled", None)

    def _save_settings(self) -> bool:
        try:
            self._copy_ui_to_config()
            self._apply_message_history_limit()
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
        self._restart_connection_error_delay()
        self._request_subscription_refresh()
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
        text = self.message_entry.to_message_text()

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
        plain_text = self.message_entry.toPlainText()
        text = self.message_entry.to_message_text()

        if not plain_text.strip():
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
        self.network_wakeup_event.set()

        self.message_entry.clear()
        self.message_entry.clear_formatting_state()
        self._sync_formatting_buttons()
        self.draft_message_id = uuid.uuid4().hex
        self._resize_message_entry()
        self._run_message_size_check()
        self.seen_client_message_ids.add(message["i"])
        self._add_message_to_log(message, is_local=True)

    def _subscription_snapshot(
        self,
    ) -> tuple[str, dict[str, tuple[str, ...]]]:
        server_url = normalize_server_url(
            str(self.config_data.get("server_url", ""))
        )
        if not server_url:
            return "", {}

        topic_room_lists: dict[str, list[str]] = {}
        for room in self._chatroom_definitions():
            encryption_key = room.get("key", "")
            if not encryption_key:
                continue
            try:
                topic = derive_ntfy_topic(encryption_key)
            except Exception:
                continue
            topic_room_lists.setdefault(topic, []).append(room["id"])

        return server_url, {
            topic: tuple(room_ids)
            for topic, room_ids in topic_room_lists.items()
        }

    def _request_subscription_refresh(self) -> None:
        self.subscription_refresh_event.set()
        with self.subscription_response_lock:
            response = self.subscription_response
        if response is not None:
            try:
                response.close()
            except Exception:
                pass

    def _start_network_thread(self) -> None:
        if not self.network_thread or not self.network_thread.is_alive():
            self.network_thread = threading.Thread(
                target=self._network_loop,
                name="SpriteLinkNetwork",
                daemon=True,
            )
            self.network_thread.start()

        if (
            not self.subscription_thread
            or not self.subscription_thread.is_alive()
        ):
            self.subscription_thread = threading.Thread(
                target=self._subscription_loop,
                name="SpriteLinkSubscription",
                daemon=True,
            )
            self.subscription_thread.start()

    def _subscription_loop(self) -> None:
        while not self.stop_event.is_set():
            self.subscription_refresh_event.clear()
            server_url, topic_rooms = self._subscription_snapshot()
            if not server_url or not topic_rooms:
                self.subscription_refresh_event.wait(
                    SUBSCRIPTION_RECONNECT_DELAY_SECONDS
                )
                continue

            topics = ",".join(topic_rooms)
            response: requests.Response | None = None
            try:
                response = self.subscription_session.get(
                    f"{server_url}/{topics}/json",
                    params={
                        "since": (
                            f"{SUBSCRIPTION_RECONNECT_BACKFILL_SECONDS}s"
                        ),
                    },
                    stream=True,
                    timeout=(
                        REQUEST_TIMEOUT_SECONDS,
                        SUBSCRIPTION_READ_TIMEOUT_SECONDS,
                    ),
                )
                response.raise_for_status()
                with self.subscription_response_lock:
                    self.subscription_response = response

                for raw_line in response.iter_lines():
                    if (
                        self.stop_event.is_set()
                        or self.subscription_refresh_event.is_set()
                    ):
                        break
                    if not raw_line:
                        continue
                    if isinstance(raw_line, bytes):
                        line = raw_line.decode("utf-8", errors="replace")
                    else:
                        line = str(raw_line)
                    if len(line) > MAX_ENCRYPTED_PACKET_CHARS + 2048:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(record, dict):
                        continue

                    queued_signal = False
                    for room_id in subscription_room_ids(
                        record,
                        topic_rooms,
                    ):
                        try:
                            self.subscription_room_queue.put_nowait(room_id)
                        except queue.Full:
                            break
                        else:
                            queued_signal = True
                    if queued_signal:
                        self.network_wakeup_event.set()

                    current_snapshot = self._subscription_snapshot()
                    if current_snapshot != (server_url, topic_rooms):
                        break

            except Exception:
                pass
            finally:
                with self.subscription_response_lock:
                    if self.subscription_response is response:
                        self.subscription_response = None
                if response is not None:
                    try:
                        response.close()
                    except Exception:
                        pass

            if not self.stop_event.is_set():
                self.subscription_refresh_event.wait(
                    0.0
                    if self.subscription_refresh_event.is_set()
                    else SUBSCRIPTION_RECONNECT_DELAY_SECONDS
                )

    def _network_loop(self) -> None:
        last_poll_times: dict[str, float] = {}
        last_global_poll_at: float | None = None
        urgent_room_ids: set[str] = set()
        scheduled_active_room_id = ""
        forced_active_poll_room_id = ""
        background_delay_debt = 0.0
        background_repayment_checks = 0
        background_delay_until: float | None = None
        background_delay_slice = 0.0

        while not self.stop_event.is_set():
            latest_control: dict[str, Any] | None = None
            while True:
                try:
                    latest_control = self.network_control_queue.get_nowait()
                except queue.Empty:
                    break

            while True:
                try:
                    urgent_room_ids.add(
                        self.subscription_room_queue.get_nowait()
                    )
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
            poll_interval = polling_interval_seconds(
                window_focused=self.window_focused_event.is_set(),
                tray_suspended=self.tray_mode_event.is_set(),
            )

            if active_room_id != scheduled_active_room_id:
                scheduled_active_room_id = active_room_id
                urgent_room_ids.add(active_room_id)
            if (
                forced_active_poll_room_id
                and forced_active_poll_room_id != active_room_id
            ):
                forced_active_poll_room_id = ""

            if latest_control is not None:
                requested_room_id = str(latest_control.get("room_id", ""))
                if requested_room_id == active_room_id:
                    self.connected = False
                    poll_immediately = bool(
                        latest_control.get("poll_immediately", False)
                    )
                    if poll_immediately:
                        urgent_room_ids.add(active_room_id)
                        forced_active_poll_room_id = active_room_id
                        background_delay_debt += (
                            immediate_poll_borrowed_seconds(
                                now=now,
                                last_global_poll_at=last_global_poll_at,
                                poll_interval=poll_interval,
                            )
                        )
                        background_repayment_checks = max(
                            background_repayment_checks,
                            CHATROOM_SWITCH_REPAYMENT_BACKGROUND_POLLS,
                        )
                        background_delay_until = None
                        background_delay_slice = 0.0
                    else:
                        last_poll_times[active_room_id] = now
                    self._queue_ui_event((
                        "status",
                        (
                            "Connecting",
                            "Applying saved configuration...",
                            active_room_id,
                        ),
                    ))

            valid_room_ids = {room["id"] for room in rooms}
            for room_id in tuple(last_poll_times):
                if room_id not in valid_room_ids:
                    last_poll_times.pop(room_id, None)
            urgent_room_ids.intersection_update(valid_room_ids)

            force_active_poll = (
                forced_active_poll_room_id == active_room_id
            )
            global_poll_due = force_active_poll or (
                last_global_poll_at is None
                or now - last_global_poll_at >= poll_interval
            )
            room_to_poll: dict[str, str] | None = None
            repay_background_after_poll = False
            if global_poll_due:
                room_id_to_poll = (
                    active_room_id
                    if force_active_poll
                    else next_poll_room_id(
                        room_ids=[room["id"] for room in rooms],
                        active_room_id=active_room_id,
                        urgent_room_ids=urgent_room_ids,
                        last_poll_times=last_poll_times,
                        now=now,
                        poll_interval=poll_interval,
                    )
                )
                room_to_poll = next(
                    (
                        room for room in rooms
                        if room["id"] == room_id_to_poll
                    ),
                    None,
                )
                if (
                    room_to_poll is not None
                    and room_to_poll["id"] != active_room_id
                    and background_delay_debt > 0.0
                    and background_repayment_checks > 0
                ):
                    if background_delay_until is None:
                        background_delay_slice = (
                            background_repayment_delay_seconds(
                                borrowed_seconds=background_delay_debt,
                                checks_remaining=(
                                    background_repayment_checks
                                ),
                            )
                        )
                        background_delay_until = (
                            now + background_delay_slice
                        )
                    if now < background_delay_until:
                        room_to_poll = None
                    else:
                        repay_background_after_poll = True

            if room_to_poll is not None:
                poll_is_active = room_to_poll["id"] == active_room_id
                self._network_poll(
                    room_to_poll,
                    is_active=poll_is_active,
                )
                completed_at = time.monotonic()
                last_global_poll_at = completed_at
                last_poll_times[room_to_poll["id"]] = completed_at
                urgent_room_ids.discard(room_to_poll["id"])
                if poll_is_active:
                    forced_active_poll_room_id = ""
                    background_delay_until = None
                    background_delay_slice = 0.0
                elif repay_background_after_poll:
                    background_delay_debt = max(
                        0.0,
                        background_delay_debt - background_delay_slice,
                    )
                    background_repayment_checks -= 1
                    background_delay_until = None
                    background_delay_slice = 0.0

            wait_now = time.monotonic()
            if room_to_poll is None and background_delay_until is not None:
                wait_seconds = max(
                    0.0,
                    min(60.0, background_delay_until - wait_now),
                )
            else:
                wait_seconds = network_idle_wait_seconds(
                    now=wait_now,
                    last_global_poll_at=last_global_poll_at,
                    poll_interval=poll_interval,
                )
            self.network_wakeup_event.wait(max(0.01, wait_seconds))
            self.network_wakeup_event.clear()

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
            self._queue_ui_event((
                "send_failed",
                {
                    "message": outbound["message"],
                    "error": str(exc),
                    "room_id": outbound.get("room_id"),
                },
            ))
        else:
            self._queue_ui_event((
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
                self._queue_ui_event((
                    "status",
                    ("Disconnected", "No chatroom is selected.", room_id),
                ))
            return

        try:
            topic = derive_ntfy_topic(encryption_key)
            scope_id = room_scope_id(server_url, encryption_key)
            for pending_scope_id, pending_batch in tuple(
                self.pending_ntfy_poll_batches.items()
            ):
                if (
                    pending_scope_id != scope_id
                    and pending_batch.get("room_id") == room_id
                ):
                    self.pending_ntfy_poll_batches.pop(
                        pending_scope_id,
                        None,
                    )

            state = self.config_data.setdefault("room_state", {}).setdefault(
                scope_id,
                {},
            )
            was_initial_history_scan = (
                room_id in self.initial_history_pending_rooms
            )
            response = self.session.get(
                f"{server_url}/{topic}/json",
                params={
                    "poll": "1",
                    "since": self._current_poll_since(state, room_id),
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            fetched_records = newest_first_ntfy_records(
                parse_ntfy_ndjson(response)
            )
            if self._find_chatroom(room_id) is None:
                return

            pending_batch = self.pending_ntfy_poll_batches.get(scope_id)
            if pending_batch is None:
                pending_batch = {
                    "room_id": room_id,
                    "records": {},
                    "polled_ids": set(),
                    "newest_ntfy_id": None,
                    "newest_time": int(
                        state.get("newest_time", 0) or 0
                    ),
                }
                self.pending_ntfy_poll_batches[scope_id] = pending_batch

            pending_records = pending_batch.get("records")
            if not isinstance(pending_records, dict):
                pending_records = {}
                pending_batch["records"] = pending_records
            polled_ids = pending_batch.get("polled_ids")
            if not isinstance(polled_ids, set):
                polled_ids = set()
                pending_batch["polled_ids"] = polled_ids

            fetched_newest_id: str | None = None
            fetched_newest_time = -1
            for record in fetched_records:
                ntfy_id = record.get("id")
                ntfy_time = record.get("time")
                if (
                    not isinstance(ntfy_id, str)
                    or not ntfy_id
                    or type(ntfy_time) is not int
                ):
                    continue
                if fetched_newest_id is None:
                    fetched_newest_id = ntfy_id
                    fetched_newest_time = ntfy_time
                if ntfy_id not in polled_ids:
                    pending_records[ntfy_id] = record

            stored_newest_time = int(
                pending_batch.get("newest_time", 0) or 0
            )
            if (
                fetched_newest_id is not None
                and fetched_newest_time >= stored_newest_time
            ):
                pending_batch["newest_ntfy_id"] = fetched_newest_id
                pending_batch["newest_time"] = fetched_newest_time

            records_to_poll = newest_first_ntfy_records(
                list(pending_records.values())
            )
            decoded_messages: list[dict[str, Any]] = []
            failed_decryptions = 0
            decrypt_attempts = 0

            for record in records_to_poll:
                ntfy_id = record["id"]
                ntfy_time = record["time"]
                packet = record.get("message")

                if not isinstance(packet, str):
                    polled_ids.add(ntfy_id)
                    pending_records.pop(ntfy_id, None)
                    continue
                if decrypt_attempts >= MAX_DECRYPT_ATTEMPTS_PER_POLL:
                    break

                decrypt_attempts += 1
                polled_ids.add(ntfy_id)
                pending_records.pop(ntfy_id, None)
                try:
                    message = open_opaque_packet(packet, encryption_key)
                    self._validate_decrypted_message(
                        message,
                        encryption_key,
                        ntfy_time=ntfy_time,
                    )
                except Exception:
                    failed_decryptions += 1
                    continue

                decoded_messages.append({
                    "ntfy_id": ntfy_id,
                    "ntfy_time": ntfy_time,
                    "message": message,
                })

            batch_complete = not pending_records
            if batch_complete:
                self.pending_ntfy_poll_batches.pop(scope_id, None)
                newest_record_id = pending_batch.get("newest_ntfy_id")
                if isinstance(newest_record_id, str) and newest_record_id:
                    state["newest_ntfy_id"] = newest_record_id
                    state["newest_time"] = int(
                        pending_batch.get("newest_time", 0) or 0
                    )

                if was_initial_history_scan:
                    self.initial_history_pending_rooms.discard(room_id)
                    completed_at = int(time.time())
                    state["history_scan_start_time"] = max(
                        0,
                        completed_at - AUTO_HISTORY_SECONDS,
                    )
                    state["history_scan_completed_at"] = completed_at

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
                self._queue_ui_event((
                    "status",
                    ("Connected", server_url, room_id),
                ))

            if (
                decoded_messages
                or (was_initial_history_scan and batch_complete)
            ):
                self._queue_ui_event((
                    "messages",
                    {
                        "items": decoded_messages,
                        "history_scan": was_initial_history_scan,
                        "room_id": room_id,
                    },
                ))

            if failed_decryptions:
                self._queue_ui_event((
                    "decrypt_failures",
                    failed_decryptions,
                ))

        except Exception as exc:
            if is_active and room_id == self.active_chatroom_id:
                self.connected = False
                self._queue_ui_event((
                    "status",
                    ("Disconnected", str(exc), room_id),
                ))


    @staticmethod
    def _validate_decrypted_message(
        message: dict[str, Any],
        encryption_key: str,
        *,
        ntfy_time: int | None = None,
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
        if message["u"] != sanitize_username(message["u"], ""):
            raise ValueError("Username contains unsupported characters.")
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

        if ntfy_time is not None:
            if type(ntfy_time) is not int:
                raise ValueError("Ntfy record time has the wrong type.")
            if (
                abs(message["t"] - ntfy_time)
                > MESSAGE_CLOCK_TOLERANCE_SECONDS
            ):
                raise ValueError(
                    "Signed message time differs too far from Ntfy time."
                )

    def _queue_ui_event(self, event: tuple[str, Any]) -> None:
        self.ui_queue.put(event)
        self.ui_event_available.emit()

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
                            play_chime=poll_message_should_notify(
                                item,
                                history_scan=history_scan,
                                notification_started_at=(
                                    self.notification_started_at
                                ),
                            ),
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
                        "Unsent text from "
                        f"{message['u']}: "
                        f"{message_plain_text(str(message['m']))}",
                        warning=True,
                    )

                elif event_type == "image_preview_loaded":
                    url = str(payload.get("url", ""))
                    data = payload.get("data")
                    kind = str(payload.get("kind", ""))
                    source_url = str(payload.get("source_url", url))
                    self.pending_image_previews.discard(url)
                    if self._tray_ui_suspended:
                        continue
                    media: RemoteMediaPreview | None = None
                    if isinstance(data, bytes) and data:
                        if kind == "looping_video":
                            media = RemoteMediaPreview(
                                source_url,
                                data,
                                kind,
                                None,
                            )
                        elif kind in {"static_image", "animated_gif"}:
                            candidate = QImage.fromData(data)
                            if not candidate.isNull():
                                candidate.setDevicePixelRatio(1.0)
                                media = RemoteMediaPreview(
                                    source_url,
                                    data,
                                    kind,
                                    candidate,
                                )
                    self.image_preview_cache[url] = media
                    while (
                        len(self.image_preview_cache)
                        > IMAGE_PREVIEW_CACHE_LIMIT
                        or sum(
                            len(cached.data)
                            for cached in self.image_preview_cache.values()
                            if isinstance(cached, RemoteMediaPreview)
                        )
                        > MAX_REMOTE_MEDIA_CACHE_BYTES
                    ):
                        oldest_url = next(iter(self.image_preview_cache))
                        self.image_preview_cache.pop(oldest_url, None)
                        controller = (
                            self.animated_media_controllers.pop(
                                oldest_url,
                                None,
                            )
                        )
                        if controller is not None:
                            controller.stop()
                            controller.deleteLater()
                        self.last_inline_animation_frame_at.pop(
                            oldest_url,
                            None,
                        )
                    if (
                        media is not None
                        and url in self.viewport_embedded_image_urls
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

            should_notify = poll_message_should_notify(
                item,
                history_scan=history_scan,
                notification_started_at=self.notification_started_at,
            )
            if should_notify and not is_local:
                unread_added += 1
                sender_id = str(message.get("c", ""))
                is_muted_notification = (
                    self._is_chatroom_muted(room_id)
                    or sender_id in muted_user_ids
                )
                if not is_muted_notification:
                    self._mark_tray_notification()
                if (
                    self._should_play_message_sound(room_id)
                    and not is_muted_notification
                ):
                    self._play_message_sound()

        if not added:
            return

        history.sort(key=self._message_sort_key)
        # Inactive rooms are pruned by the hourly maintenance timer.
        all_history[scope_id] = history
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

        is_muted_notification = (
            self._is_chatroom_muted(self.active_chatroom_id)
            or self._is_user_muted(str(message["c"]))
        )
        if play_chime and not is_local and not is_muted_notification:
            self._mark_tray_notification()
            if self._should_play_message_sound(self.active_chatroom_id):
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
        history_limit = self._message_history_limit()
        if len(self.message_log) > history_limit:
            del self.message_log[:-history_limit]
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

        for item in entries[-self._message_history_limit():]:
            if not isinstance(item, dict):
                continue

            message = item.get("message")
            if not isinstance(message, dict):
                continue

            stored_ntfy_time = int(
                item.get("ntfy_time", message.get("t", 0)) or 0
            )
            try:
                self._validate_decrypted_message(
                    message,
                    encryption_key,
                    ntfy_time=(
                        stored_ntfy_time
                        if stored_ntfy_time > 0
                        else None
                    ),
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
                "ntfy_time": stored_ntfy_time,
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
        history_limit = self._message_history_limit()
        for item in self.message_log[-history_limit:]:
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

    def _message_history_limit(self) -> int:
        value = (
            self.message_history_limit_var.get()
            if hasattr(self, "message_history_limit_var")
            else self.config_data.get(
                "message_history_limit",
                DEFAULT_MESSAGE_HISTORY_LIMIT,
            )
        )
        return normalize_message_history_limit(value)

    def _apply_message_history_limit(self) -> None:
        history_limit = self._message_history_limit()
        self.config_data["message_history_limit"] = history_limit
        prune_local_history_map(
            self.config_data.setdefault("history", {}),
            history_limit,
        )
        if len(self.message_log) > history_limit:
            del self.message_log[:-history_limit]
            if hasattr(self, "chat_display"):
                self._render_message_log(scroll_to_bottom=True)

    def _prune_background_local_histories(self) -> None:
        history_by_scope = self.config_data.setdefault("history", {})
        active_scope_id = self._current_room_scope_id()
        if prune_local_history_map(
            history_by_scope,
            self._message_history_limit(),
            excluded_scope_id=active_scope_id,
        ):
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
        item = self.rendered_message_items.get(message_id)
        tooltip = self._tooltip_for_message_item(item) if item else ""
        if tooltip:
            QToolTip.showText(
                self._pending_tooltip_global_position,
                tooltip,
                self.chat_display.viewport(),
            )

    def _tooltip_for_message_item(
        self,
        item: dict[str, Any],
    ) -> str:
        message = item["message"]
        hover_timestamp = self._format_hover_timestamp(
            self._display_timestamp_for_item(item)
        )
        user_id_preview = visible_user_id(str(message["c"]))
        tooltip_icon_uri = profile_icon_tooltip_data_uri(
            str(message.get("p", ""))
        )
        if tooltip_icon_uri:
            return (
                '<div align="center">'
                f'<img src="{tooltip_icon_uri}" width="64" height="64">'
                f"<br>{hover_timestamp}<br>"
                f"User ID: {user_id_preview}"
                "</div>"
            )
        return f"{hover_timestamp}\nUser ID: {user_id_preview}"

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
        if parsed.isValid() and parsed.scheme().casefold() == "https":
            QDesktopServices.openUrl(parsed)

    def _image_url_from_anchor(self, anchor: str) -> str | None:
        prefix = "spritelink-image:"
        if not anchor.startswith(prefix):
            return None
        image_url = self.rendered_image_links.get(anchor[len(prefix):])
        if not image_url:
            return None
        try:
            parsed = urlsplit(image_url)
        except ValueError:
            return None
        if parsed.scheme.casefold() != "https":
            return None
        return image_url

    def _link_url_from_anchor(self, anchor: str) -> str | None:
        image_url = self._image_url_from_anchor(anchor)
        if image_url:
            return image_url
        parsed = QUrl(anchor)
        if parsed.isValid() and parsed.scheme().casefold() == "https":
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

    def _update_viewport_media(self) -> None:
        if (
            self._closing
            or self._tray_ui_suspended
            or not hasattr(self, "chat_display")
        ):
            return
        viewport = self.chat_display.viewport()
        viewport_height = max(1, viewport.height())
        document = self.chat_display.document()
        maximum_position = max(0, document.characterCount() - 1)
        desired_urls: set[str] = set()

        for url, positions in self.rendered_image_candidates.items():
            for position in positions:
                cursor = QTextCursor(document)
                cursor.setPosition(
                    max(0, min(int(position), maximum_position))
                )
                rect = self.chat_display.cursorRect(cursor)
                if vertical_range_is_near_viewport(
                    rect.top(),
                    rect.bottom(),
                    viewport_height,
                ):
                    desired_urls.add(url)
                    break

        self.viewport_embedded_image_urls = desired_urls
        for url in desired_urls:
            self._schedule_image_preview_fetch(url)

        cached_desired_urls = {
            url
            for url in desired_urls
            if isinstance(
                self.image_preview_cache.get(url),
                RemoteMediaPreview,
            )
        }
        currently_rendered_urls = set(self.rendered_image_positions)
        if cached_desired_urls != currently_rendered_urls:
            self._rerender_preserving_scroll()

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
        media_kind: str | None = None
        source_url = url

        def read_limited_response(
            response: Any,
            maximum_bytes: int,
        ) -> bytes:
            content_length = response.headers.get("Content-Length")
            if (
                content_length
                and int(content_length) > maximum_bytes
            ):
                raise ValueError("The linked media is too large.")
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_content(64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > maximum_bytes:
                    raise ValueError("The linked media is too large.")
                chunks.append(chunk)
            return b"".join(chunks)

        try:
            resolved_from_page = is_supported_media_page_url(url)
            resolved_media_hint = ""
            if resolved_from_page:
                with requests.get(
                    url,
                    headers={
                        "User-Agent": f"{APP_NAME}/{CONFIG_FORMAT_VERSION}",
                        "Accept": "text/html, application/xhtml+xml",
                    },
                    stream=True,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                    allow_redirects=True,
                ) as page_response:
                    page_response.raise_for_status()
                    final_page_url = str(page_response.url)
                    if not is_supported_media_page_url(final_page_url):
                        raise ValueError(
                            "The provider page redirected outside its site."
                        )
                    page_content_type = page_response.headers.get(
                        "Content-Type",
                        "",
                    ).split(";", 1)[0].strip().casefold()
                    if (
                        page_content_type
                        and page_content_type not in {
                            "text/html",
                            "application/xhtml+xml",
                        }
                    ):
                        raise ValueError(
                            "The provider link did not return a media page."
                        )
                    page_data = read_limited_response(
                        page_response,
                        MAX_MEDIA_PAGE_HTML_BYTES,
                    )
                    page_encoding = page_response.encoding or "utf-8"

                resolved_media = resolve_media_url_from_page(
                    final_page_url,
                    page_data.decode(page_encoding, errors="replace"),
                )
                if resolved_media is None:
                    raise ValueError(
                        "The provider page did not advertise embeddable media."
                    )
                source_url, resolved_media_hint = resolved_media

            looping_video = (
                resolved_media_hint == "video"
                or is_trusted_looping_video_url(source_url)
            )
            with requests.get(
                source_url,
                headers={
                    "User-Agent": f"{APP_NAME}/{CONFIG_FORMAT_VERSION}",
                    "Accept": "image/*, video/mp4, video/webm;q=0.9",
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
                if content_type.startswith("video/"):
                    if not (
                        resolved_from_page
                        or is_trusted_looping_video_url(source_url)
                    ):
                        raise ValueError(
                            "The link did not return trusted looping video."
                        )
                    if content_type not in {"video/mp4", "video/webm"}:
                        raise ValueError(
                            "The link returned unsupported video."
                        )
                    looping_video = True
                elif content_type.startswith("image/"):
                    looping_video = False
                elif content_type not in {"", "application/octet-stream"}:
                    raise ValueError(
                        "The link did not return supported media."
                    )
                candidate = read_limited_response(
                    response,
                    MAX_REMOTE_IMAGE_BYTES,
                )

            if looping_video:
                parsed_url = urlsplit(source_url)
                query_format = (
                    parse_qs(parsed_url.query).get("format", [""])[0]
                    .strip()
                    .casefold()
                )
                is_webm = (
                    parsed_url.path.casefold().endswith(".webm")
                    or query_format == "webm"
                    or content_type == "video/webm"
                )
                if is_webm:
                    valid_container = candidate.startswith(
                        b"\x1a\x45\xdf\xa3"
                    )
                else:
                    valid_container = (
                        len(candidate) >= 12
                        and candidate[4:8] == b"ftyp"
                    )
                if not valid_container:
                    raise ValueError(
                        "The looping video container is invalid."
                    )
                media_kind = "looping_video"
            else:
                with Image.open(io.BytesIO(candidate)) as remote_image:
                    width, height = remote_image.size
                    if (
                        width <= 0
                        or height <= 0
                        or width * height > MAX_REMOTE_IMAGE_PIXELS
                    ):
                        raise ValueError(
                            "The linked image dimensions are too large."
                        )
                    frame_count = int(
                        getattr(remote_image, "n_frames", 1)
                    )
                    is_animated_gif = (
                        remote_image.format == "GIF"
                        and bool(getattr(remote_image, "is_animated", False))
                        and frame_count > 1
                    )
                    if is_animated_gif and (
                        width * height > MAX_ANIMATED_IMAGE_PIXELS
                        or frame_count > MAX_ANIMATED_IMAGE_FRAMES
                    ):
                        raise ValueError(
                            "The animated image is too large."
                        )
                    remote_image.verify()
                media_kind = (
                    "animated_gif"
                    if is_animated_gif
                    else "static_image"
                )
            image_data = candidate
        except Exception:
            image_data = None
            media_kind = None
            source_url = url

        if not self._closing:
            self._queue_ui_event((
                "image_preview_loaded",
                {
                    "url": url,
                    "source_url": source_url,
                    "data": image_data,
                    "kind": media_kind,
                },
            ))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (
            watched is self.root
            and hasattr(self, "window_focused_event")
        ):
            if event.type() == QEvent.Type.WindowActivate:
                self.window_focused_event.set()
                self.network_wakeup_event.set()
                self._sync_connection_error_timer()
                if hasattr(self, "tray_icon"):
                    self._clear_tray_notification_if_no_unread()
            elif event.type() == QEvent.Type.WindowDeactivate:
                self.window_focused_event.clear()
                self.network_wakeup_event.set()
                self._sync_connection_error_timer()
            elif event.type() == QEvent.Type.WindowStateChange:
                QTimer.singleShot(0, self._sync_window_activity)

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
            if event.type() == QEvent.Type.Resize:
                self.viewport_media_timer.start(
                    VIEWPORT_MEDIA_UPDATE_DELAY_MS
                )

            elif event.type() == QEvent.Type.MouseMove:
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
        italic: bool = False,
        underline: bool = False,
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
        formatting.setFontItalic(italic)
        formatting.setFontUnderline(underline)
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
        plain_text, rich_runs = parse_message_rich_text(text)
        url_spans = message_url_spans(plain_text)
        link_color = (
            "#0000ee"
            if self._is_windows_classic_theme()
            else "#0066cc"
        )
        if muted:
            link_color = self._blend_toward_chat_background(link_color)

        for run in rich_runs:
            position = run.start

            def insert_chunk(
                start: int,
                end: int,
                anchor: str | None = None,
            ) -> None:
                if end <= start:
                    return
                formatting = self._text_format(
                    link_color if anchor else body_color,
                    bold=run.bold,
                    italic=run.italic,
                    underline=run.underline,
                    anchor=anchor,
                    font_name=font_name,
                    align_top=align_top,
                    top_align_height=top_align_height,
                )
                if anchor:
                    formatting.setFontUnderline(True)
                cursor.insertText(plain_text[start:end], formatting)

            for start, end, url in url_spans:
                if end <= run.start:
                    continue
                if start >= run.end:
                    break
                overlap_start = max(start, run.start)
                overlap_end = min(end, run.end)
                insert_chunk(position, overlap_start)
                if url not in embedded_image_urls:
                    insert_chunk(overlap_start, overlap_end, url)
                position = overlap_end
            insert_chunk(position, run.end)

    @staticmethod
    def _scaled_inline_media_frame(
        media: RemoteMediaPreview,
        frame: QImage,
    ) -> QImage:
        preserve_native_size = (
            frame.width() <= INLINE_MEDIA_NO_UPSCALE_EDGE
            and frame.height() <= INLINE_MEDIA_NO_UPSCALE_EDGE
        )
        if preserve_native_size:
            preview = frame.copy()
        else:
            preview = frame.scaled(
                EMBEDDED_IMAGE_MAX_EDGE,
                EMBEDDED_IMAGE_MAX_EDGE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        if media.kind != "looping_video" or preserve_native_size:
            return preview

        canvas = QImage(
            EMBEDDED_IMAGE_MAX_EDGE,
            EMBEDDED_IMAGE_MAX_EDGE,
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        painter.drawImage(
            (canvas.width() - preview.width()) // 2,
            (canvas.height() - preview.height()) // 2,
            preview,
        )
        painter.end()
        return canvas

    def _embedded_media_preview(
        self,
        url: str,
        media: RemoteMediaPreview,
    ) -> QImage:
        if is_likely_nsfw_image_url(url):
            return self._likely_nsfw_image_placeholder()
        if isinstance(media.frame, QImage) and not media.frame.isNull():
            return self._scaled_inline_media_frame(media, media.frame)
        return self._animated_media_loading_placeholder()

    def _insert_embedded_image_preview(
        self,
        cursor: QTextCursor,
        url: str,
    ) -> bool:
        media = self.image_preview_cache.get(url)
        if not isinstance(media, RemoteMediaPreview):
            return False
        if not is_likely_nsfw_image_url(url):
            self._ensure_animated_media_controller(url, media)
        preview = self._embedded_media_preview(url, media)
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
        image_position = cursor.position()
        cursor.insertImage(image_format)
        self.rendered_image_positions.setdefault(url, []).append(
            image_position
        )
        return True

    def _animated_media_loading_placeholder(self) -> QImage:
        placeholder = QImage(
            EMBEDDED_IMAGE_MAX_EDGE,
            EMBEDDED_IMAGE_MAX_EDGE,
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        placeholder.fill(QColor("#ececec"))
        painter = QPainter(placeholder)
        painter.setRenderHint(
            QPainter.RenderHint.TextAntialiasing,
            not self._is_windows_classic_theme(),
        )
        painter.setPen(QColor("#aaaaaa"))
        painter.drawRect(placeholder.rect().adjusted(0, 0, -1, -1))
        painter.setPen(QColor("#555555"))
        painter.setFont(self._make_font("Segoe UI", 8, bold=True))
        painter.drawText(
            placeholder.rect().adjusted(4, 4, -4, -4),
            Qt.AlignmentFlag.AlignCenter,
            "Loading...",
        )
        painter.end()
        return placeholder

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

    def _reset_chat_document(self) -> None:
        document = QTextDocument(self.chat_display)
        document.setDocumentMargin(0)
        text_option = document.defaultTextOption()
        text_option.setWrapMode(QTextOption.WrapMode.WrapAnywhere)
        document.setDefaultTextOption(text_option)
        self.chat_display.setDocument(document)

    def _render_message_log(self, *, scroll_to_bottom: bool) -> None:
        if self._tray_ui_suspended:
            return
        self._hide_chat_tooltip()
        self.rendered_message_items.clear()
        self.rendered_image_links.clear()
        self.rendered_image_positions.clear()
        self.rendered_image_candidates.clear()
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

        active_animated_urls = set(self.rendered_image_positions)
        if self.current_image_preview_url:
            active_animated_urls.add(self.current_image_preview_url)
        for url in list(self.animated_media_controllers):
            if url in active_animated_urls:
                continue
            controller = self.animated_media_controllers.pop(url)
            controller.stop()
            controller.deleteLater()
            self.last_inline_animation_frame_at.pop(url, None)

        if scroll_to_bottom:
            scrollbar = self.chat_display.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
        self.viewport_media_timer.start(
            VIEWPORT_MEDIA_UPDATE_DELAY_MS
        )

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
        # Never allow a previous message's character format to carry over.
        cursor.setCharFormat(QTextCharFormat())
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
        plain_text = message_plain_text(text)
        message_id = str(message["i"])
        client_id = str(message["c"])
        user_id_preview = visible_user_id(client_id)

        is_muted = client_id in muted_ids and not item["is_local"]
        is_collapsed = is_muted or message_id in collapsed_ids
        if is_collapsed:
            collapsed_block_format = cursor.blockFormat()
            collapsed_block_format.setNonBreakableLines(True)
            cursor.setBlockFormat(collapsed_block_format)

        candidate_image_urls = direct_image_urls_in_message(plain_text)
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
        untrusted_image_urls = {
            url
            for url in candidate_image_urls
            if url not in embedded_image_url_set
        }
        has_untrusted_image = bool(untrusted_image_urls)
        if is_collapsed:
            display_text = self._collapsed_message_preview(plain_text)
            if has_untrusted_image:
                display_text = (
                    "[untrusted image] "
                    + display_text
                )
        elif has_untrusted_image:
            display_text = message_text_with_untrusted_images_hidden(
                text,
                untrusted_image_urls,
            )
        else:
            display_text = text
        plain_display_text = message_plain_text(display_text)
        image_urls = (
            []
            if is_collapsed
            else [
                url
                for url in candidate_image_urls
                if url in embedded_image_url_set
            ]
        )
        for image_url in image_urls:
            self.rendered_image_candidates.setdefault(
                image_url,
                [],
            ).append(message_start_position)
        active_image_urls = [
            image_url
            for image_url in image_urls
            if (
                image_url in self.viewport_embedded_image_urls
                and isinstance(
                    self.image_preview_cache.get(image_url),
                    RemoteMediaPreview,
                )
            )
        ]

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
        top_align_height = 0
        if (
            not is_collapsed
            and bool(active_image_urls)
            and not message_text_without_image_links(
                plain_display_text,
                set(image_urls),
            ).strip()
        ):
            for url in active_image_urls:
                cached_media = self.image_preview_cache.get(url)
                if isinstance(cached_media, RemoteMediaPreview):
                    preview_height = self._embedded_media_preview(
                        url,
                        cached_media,
                    ).height()
                    top_align_height = max(
                        top_align_height,
                        preview_height,
                    )
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
                bold=False,
                italic=False,
                underline=False,
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
            plain_display_text,
            set(image_urls),
        )
        add_image_line_break = (
            bool(active_image_urls)
            and bool(visible_text_without_images.strip())
            and not visible_text_without_images.rstrip(" \t").endswith(
                ("\n", "\r")
            )
        )
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
        for image_url in active_image_urls:
            self._insert_embedded_image_preview(cursor, image_url)

        if item.get("warning") and not is_collapsed:
            cursor.insertBlock()
            cursor.insertText(
                str(item["warning"]),
                self._text_format("#b00020", font_name=font_name),
            )

        # Explicit newlines create additional QTextBlocks. Give every block
        # the same row color and left edge so continuations align with the
        # profile icon, or with the username when no icon is present.
        document = cursor.document()
        block = document.findBlock(message_start_position)
        final_block_number = cursor.block().blockNumber()
        while block.isValid() and block.blockNumber() <= final_block_number:
            block_cursor = QTextCursor(block)
            block_format = block.blockFormat()
            block_format.setLeftMargin(10)
            block_format.setTextIndent(0)
            block_format.setRightMargin(10)
            block_format.setBackground(QColor(background_color))
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
        self.rendered_image_links.clear()
        self.rendered_image_positions.clear()
        self.rendered_image_candidates.clear()
        self.viewport_embedded_image_urls.clear()
        self.viewport_media_timer.stop()
        for controller in self.animated_media_controllers.values():
            controller.stop()
            controller.deleteLater()
        self.animated_media_controllers.clear()
        self.last_inline_animation_frame_at.clear()
        self.chat_display.row_background_blocks.clear()
        self.chat_display.collapsed_fade_blocks.clear()
        self.chat_display.setExtraSelections([])
        self._reset_chat_document()

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
        ensure_qt_multimedia_loaded()
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
        effect.playingChanged.connect(
            lambda effect=effect: (
                self._on_message_sound_effect_playing_changed(effect)
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

    def _on_message_sound_effect_playing_changed(
        self,
        effect: QSoundEffect,
    ) -> None:
        if self._tray_ui_suspended and not effect.isPlaying():
            QTimer.singleShot(
                0,
                self._release_message_sound_resources,
            )

    def _ensure_compressed_message_sound_player(
        self,
    ) -> tuple[QMediaPlayer, QAudioOutput]:
        ensure_qt_multimedia_loaded()
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
        elif (
            state == QMediaPlayer.PlaybackState.StoppedState
            and self._tray_ui_suspended
        ):
            QTimer.singleShot(
                0,
                self._release_message_sound_resources,
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

    def _release_message_sound_resources(self) -> None:
        self._stop_message_sound()
        for effect in set(self.message_sound_effects.values()):
            effect.stop()
            effect.setSource(QUrl())
            effect.deleteLater()
        self.message_sound_effects.clear()
        self.pending_message_sound_effect = None
        self.active_message_sound_effect = None

        player = self.compressed_message_sound_player
        audio_output = self.compressed_message_sound_audio_output
        self.compressed_message_sound_player = None
        self.compressed_message_sound_audio_output = None
        if player is not None:
            player.stop()
            player.setSource(QUrl())
            player.setAudioOutput(None)
            player.deleteLater()
        if audio_output is not None:
            audio_output.deleteLater()

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

    def _on_close(self) -> bool:
        if self._closing:
            return True
        if not self._force_quit and self._can_minimize_to_tray():
            if not self._save_settings():
                return False
            self._hide_to_tray()
            return False

        self._closing = True
        self._minimized_to_tray = False
        QToolTip.hideText()
        self.update_check_timer.stop()
        self.background_history_prune_timer.stop()
        self.viewport_media_timer.stop()
        self.message_sound_stop_timer.stop()
        self.connection_error_timer.stop()
        self._release_message_sound_resources()
        self.tray_icon.hide()

        try:
            self._copy_ui_to_config()
            self._apply_message_history_limit()
            save_config(self.config_data)
        except Exception:
            pass

        self.stop_event.set()
        self.network_wakeup_event.set()
        self.subscription_refresh_event.set()
        # Network workers are daemons. Do not block the GUI thread waiting on
        # a request or streaming response while the application is exiting.
        self.session.close()
        threading.Thread(
            target=self.subscription_session.close,
            name="SpriteLinkSubscriptionClose",
            daemon=True,
        ).start()
        for controller in self.animated_media_controllers.values():
            controller.stop()
        self.animated_media_controllers.clear()
        self.last_inline_animation_frame_at.clear()
        self.image_fetch_executor.shutdown(
            wait=False,
            cancel_futures=True,
        )
        return True

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
