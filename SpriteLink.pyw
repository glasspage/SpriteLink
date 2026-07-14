# SpriteLink v11
# Windows + Python 3.10+
#
# Required packages:
#   pip install PySide6 requests cryptography
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
from functools import lru_cache
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import queue
import secrets
import threading
import time
import traceback
import sys
import uuid
import zlib
try:
    from PySide6.QtCore import QEvent, QObject, QTimer, Qt, Signal
    from PySide6.QtGui import (
        QColor,
        QFont,
        QFontMetrics,
        QPalette,
        QTextBlockFormat,
        QTextCharFormat,
        QTextCursor,
        QTextOption,
    )
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QColorDialog,
        QComboBox,
        QDialog,
        QFrame,
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
        QSizePolicy,
        QTextBrowser,
        QToolTip,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: PySide6\n\nInstall it with:\n"
        "pip install PySide6 requests cryptography"
    ) from exc
from typing import Any

try:
    import requests
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: requests\n\nInstall it with:\n"
        "pip install PySide6 requests cryptography"
    ) from exc

try:
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: cryptography\n\nInstall it with:\n"
        "pip install PySide6 requests cryptography"
    ) from exc

try:
    import winsound
except ImportError:
    winsound = None


APP_NAME = "SpriteLink"
APP_VERSION = 1
CONFIG_FORMAT_VERSION = 11

DEFAULT_SERVER_PRESET = "ntfy.sh (public)"
DEFAULT_SERVER_URL = "https://ntfy.sh"
GLOBAL_CHATROOM_ID = "global"
GLOBAL_CHATROOM_NICKNAME = "Global"
GLOBAL_CHATROOM_KEY = "Xpkri=AKDzpyRjwi^g6+*GJZ=7CUH-QjdbJA%q"
CHATROOM_SIDEBAR_WIDTH = 240

SERVER_PRESETS: dict[str, str] = {
    DEFAULT_SERVER_PRESET: DEFAULT_SERVER_URL,
    "Local ntfy server": "http://127.0.0.1:8080",
    "Custom ntfy server": "",
}

POLL_INTERVAL_SECONDS = 6.0
MIN_POLL_REQUEST_SPACING_SECONDS = 5.0
BACKGROUND_POLL_INTERVAL_SECONDS = 60.0
REQUEST_TIMEOUT_SECONDS = 10
AUTO_HISTORY_SECONDS = 48 * 60 * 60
GAP_SEPARATOR_SECONDS = 6 * 60 * 60
MAX_MESSAGE_CHARS = 4000
NTFY_MAX_BODY_BYTES = 4096
PACKET_PADDING_BLOCK = 128
MESSAGE_SIZE_DEBOUNCE_MS = 1500
MESSAGE_ENTRY_MIN_LINES = 1
MESSAGE_ENTRY_MAX_LINES = 6
DEFAULT_MESSAGE_FONT = "Segoe UI"
DEFAULT_MESSAGE_TEXT_COLOR = "#202020"
SELECTABLE_MESSAGE_FONTS = (
    "Corbel",
    "Tahoma",
    "Times New Roman",
    "Segoe UI",
)
MESSAGE_FONT_POINT_SIZES = {
    "Corbel": 16,
    "Tahoma": 14,
    "Times New Roman": 16,
    "Segoe UI": 14,
}
# Preserve receive compatibility with messages created by an earlier v11
# draft, where "System" resolved to the application UI font rather than the
# legacy Windows bitmap face.
SUPPORTED_MESSAGE_FONTS = SELECTABLE_MESSAGE_FONTS + ("System",)

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


def default_room_profile() -> dict[str, str]:
    return {
        "username": "User",
        "username_color": secrets.choice(SAFE_USERNAME_COLORS),
        "font": DEFAULT_MESSAGE_FONT,
        "text_color": DEFAULT_MESSAGE_TEXT_COLOR,
    }


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

    return {
        "username": username[:32],
        "username_color": username_color.name(),
        "font": font,
        "text_color": text_color.name(),
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


def default_config() -> dict[str, Any]:
    global_profile = default_room_profile()
    return {
        "config_version": CONFIG_FORMAT_VERSION,
        "server_preset": DEFAULT_SERVER_PRESET,
        "server_url": DEFAULT_SERVER_URL,
        "chime_enabled": True,
        "anti_aliased_text": True,
        "client_id": secrets.token_hex(32),
        "chatrooms": [],
        "active_chatroom_id": GLOBAL_CHATROOM_ID,
        "room_profiles": {
            GLOBAL_CHATROOM_ID: global_profile,
        },
        "muted_chatrooms": [],
        "unread_counts": {},
        "room_state": {},
        "identities": {},
        "history": {},
        "muted_users": {},
        "collapsed_messages": {},
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

    if not isinstance(config.get("client_id"), str) or len(config["client_id"]) < 32:
        config["client_id"] = secrets.token_hex(32)

    if not isinstance(config.get("room_state"), dict):
        config["room_state"] = {}

    if not isinstance(config.get("identities"), dict):
        config["identities"] = {}

    if not isinstance(config.get("history"), dict):
        config["history"] = {}

    if not isinstance(config.get("muted_users"), dict):
        config["muted_users"] = {}

    if not isinstance(config.get("collapsed_messages"), dict):
        config["collapsed_messages"] = {}

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
    config["room_profiles"] = cleaned_profiles
    config.pop("username", None)
    config.pop("username_color", None)

    config["anti_aliased_text"] = bool(
        config.get("anti_aliased_text", True)
    )

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
    padding = "=" * ((4 - len(packet_text) % 4) % 4)
    packet = base64.urlsafe_b64decode(packet_text + padding)

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
    raw_json = zlib.decompress(compressed)
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
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Chatroom")
        self.setModal(True)
        self.setMinimumWidth(430)

        layout = QVBoxLayout(self)
        form = QGridLayout()
        form.setColumnStretch(1, 1)

        form.addWidget(QLabel("Nickname"), 0, 0)
        self.nickname_entry = QLineEdit()
        self.nickname_entry.setMaxLength(64)
        form.addWidget(self.nickname_entry, 0, 1, 1, 2)

        form.addWidget(QLabel("Key"), 1, 0)
        self.key_entry = QLineEdit()
        self.key_entry.setEchoMode(QLineEdit.EchoMode.Password)
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
        add_button = QPushButton("Add Chatroom")
        add_button.setDefault(True)
        add_button.clicked.connect(self.accept)
        buttons.addWidget(add_button)
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

        self.config_data = load_config()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": f"{APP_NAME}/{CONFIG_FORMAT_VERSION}",
            "Accept": "application/json, application/x-ndjson",
        })

        self.stop_event = threading.Event()
        self.reconnect_requested = threading.Event()
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
        self._hovered_message_id: str | None = None
        self._config_snapshot_at_open: tuple[Any, ...] | None = None
        self._loading_profile_controls = False
        self.active_chatroom_id = str(
            self.config_data.get("active_chatroom_id", GLOBAL_CHATROOM_ID)
        )
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

        self.server_preset_var = ValueModel(
            self.config_data["server_preset"]
        )
        self.server_url_var = ValueModel(
            self.config_data["server_url"]
        )
        self.chime_var = ValueModel(
            bool(self.config_data["chime_enabled"])
        )
        self.anti_alias_var = ValueModel(
            bool(self.config_data.get("anti_aliased_text", True))
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

        self.chat_render_timer = QTimer(self)
        self.chat_render_timer.setSingleShot(True)
        self.chat_render_timer.timeout.connect(self._finish_chat_resize_render)

        self.ui_queue_timer = QTimer(self)
        self.ui_queue_timer.timeout.connect(self._process_ui_queue)
        self.ui_queue_timer.start(100)

        self._apply_application_font_strategy()
        self._build_ui()
        self._apply_server_preset_state()
        self._load_saved_history_for_current_room()

        self.root.close_callback = self._on_close
        self._start_network_thread()

    def _font_style_strategy(self) -> QFont.StyleStrategy:
        return (
            QFont.StyleStrategy.PreferDefault
            if self.anti_alias_var.get()
            else QFont.StyleStrategy.NoAntialias
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

    def _build_chatroom_sidebar(self, root_layout: QHBoxLayout) -> None:
        self.chatrooms_panel = QWidget()
        self.chatrooms_panel.setFixedWidth(CHATROOM_SIDEBAR_WIDTH)
        panel_layout = QVBoxLayout(self.chatrooms_panel)
        panel_layout.setContentsMargins(6, 10, 8, 10)
        panel_layout.setSpacing(7)
        panel_layout.addWidget(self._heading("Chatrooms"))

        self.chatrooms_list = QListWidget()
        self.chatrooms_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.chatrooms_list.itemClicked.connect(
            lambda item: self._activate_chatroom(
                str(item.data(Qt.ItemDataRole.UserRole))
            )
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

    def _room_profile(self, room_id: str) -> dict[str, str]:
        profiles = self.config_data.setdefault("room_profiles", {})
        if not isinstance(profiles, dict):
            profiles = {}
            self.config_data["room_profiles"] = profiles

        global_profile = normalize_room_profile(
            profiles.get(GLOBAL_CHATROOM_ID)
        )
        profiles[GLOBAL_CHATROOM_ID] = global_profile
        if room_id == GLOBAL_CHATROOM_ID:
            return global_profile

        profile = normalize_room_profile(
            profiles.get(room_id),
            global_profile,
        )
        profiles[room_id] = profile
        return profile

    def _active_room_profile(self) -> dict[str, str]:
        return self._room_profile(self.active_chatroom_id)

    def _persist_profile_changes(self) -> None:
        try:
            save_config(self.config_data)
        except Exception:
            pass

    def _schedule_profile_save(self) -> None:
        self.profile_save_timer.start(250)
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
                "QPushButton {"
                " background-color: #f8d8ad;"
                " color: #8a4b08;"
                " border: 1px solid #dca15d;"
                " border-radius: 3px;"
                "}"
                "QPushButton:hover { background-color: #f3c78d; }"
                "QPushButton:pressed { background-color: #efb968; }"
            )
        else:
            self.chatrooms_toggle.setStyleSheet("")

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
        self._persist_local_history()
        self.active_chatroom_id = room_id
        self.config_data["active_chatroom_id"] = room_id
        self._switch_active_chatroom()

    def _switch_active_chatroom(self) -> None:
        self._unread_counts().pop(self.active_chatroom_id, None)
        try:
            save_config(self.config_data)
        except Exception:
            pass
        self._clear_visible_room()
        self._load_active_room_profile_into_controls()
        self._load_saved_history_for_current_room()
        self.connected = False
        self.status_var.set("Connecting")
        self.reconnect_requested.set()
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
        status_layout.addWidget(self.config_toggle)
        layout.addLayout(status_layout)

        self.chat_content = QWidget()
        content_layout = QVBoxLayout(self.chat_content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(7)
        layout.addWidget(self.chat_content, 1)

        self.chat_display = QTextBrowser()
        self.chat_display.setReadOnly(True)
        self.chat_display.setOpenLinks(False)
        self.chat_display.setOpenExternalLinks(False)
        self.chat_display.setUndoRedoEnabled(False)
        self.chat_display.setFont(self._make_font("Segoe UI", 10))
        self.chat_display.setViewportMargins(6, 6, 6, 6)
        self.chat_display.document().setDocumentMargin(4)
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
        identity_layout.addWidget(QLabel("Username"))
        self.identity_username_entry = QLineEdit()
        self.identity_username_entry.setMaxLength(32)
        self.identity_username_entry.textChanged.connect(
            self._on_identity_username_changed
        )
        self.identity_username_entry.editingFinished.connect(
            self._normalize_identity_username_entry
        )
        identity_layout.addWidget(self.identity_username_entry, 1)
        identity_layout.addWidget(QLabel("Username color"))
        self.identity_color_preview = QLabel()
        self.identity_color_preview.setFixedSize(26, 22)
        self.identity_color_preview.setFrameShape(QFrame.Shape.Panel)
        self.identity_color_preview.setFrameShadow(QFrame.Shadow.Sunken)
        identity_layout.addWidget(self.identity_color_preview)
        identity_color_button = QPushButton("Choose...")
        identity_color_button.clicked.connect(self._choose_identity_color)
        identity_layout.addWidget(identity_color_button)
        self.identity_menu.hide()
        content_layout.addWidget(self.identity_menu)

        self.font_menu = QFrame()
        self.font_menu.setFrameShape(QFrame.Shape.StyledPanel)
        font_layout = QHBoxLayout(self.font_menu)
        font_layout.setContentsMargins(8, 5, 8, 5)
        font_layout.setSpacing(7)
        font_layout.addWidget(QLabel("Font"))
        self.message_font_combo = QComboBox()
        self.message_font_combo.addItems(list(SELECTABLE_MESSAGE_FONTS))
        for index, font_name in enumerate(SELECTABLE_MESSAGE_FONTS):
            self.message_font_combo.setItemData(
                index,
                self._make_message_font(font_name),
                Qt.ItemDataRole.FontRole,
            )
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

        self.message_size_bar = QProgressBar()
        self.message_size_bar.setRange(0, NTFY_MAX_BODY_BYTES)
        self.message_size_bar.setTextVisible(True)
        self.message_size_bar.setFixedHeight(18)
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

    def _load_active_room_profile_into_controls(self) -> None:
        if not hasattr(self, "identity_username_entry"):
            return

        profile = self._active_room_profile()
        self._loading_profile_controls = True
        try:
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
        self._active_room_profile()["username"] = (
            value.strip()[:32] or "User"
        )
        self._schedule_profile_save()

    def _normalize_identity_username_entry(self) -> None:
        profile = self._active_room_profile()
        normalized = profile["username"]
        if self.identity_username_entry.text() != normalized:
            self._loading_profile_controls = True
            try:
                self.identity_username_entry.setText(normalized)
            finally:
                self._loading_profile_controls = False

    def _choose_identity_color(self) -> None:
        profile = self._active_room_profile()
        selected = QColorDialog.getColor(
            QColor(profile["username_color"]),
            self.root,
            "Choose username color",
        )
        if not selected.isValid():
            return
        profile["username_color"] = selected.name()
        self._set_color_preview(
            self.identity_color_preview,
            selected.name(),
        )
        self._schedule_profile_save()

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
        selected = QColorDialog.getColor(
            QColor(profile["text_color"]),
            self.root,
            "Choose message text color",
        )
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
        self.config_panel.setMaximumWidth(720)
        self.config_panel.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.config_panel.setStyleSheet(
            "QFrame#configPanel { background: palette(window); "
            "border: 1px solid palette(mid); border-radius: 3px; }"
        )
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

    def _build_config_tab(self) -> None:
        layout = QGridLayout(self.config_tab)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(5)
        layout.setColumnStretch(1, 1)
        row = 0

        layout.addWidget(self._heading("Server"), row, 0, 1, 3)
        row += 1

        layout.addWidget(QLabel("Preset"), row, 0)
        self.server_preset_combo = QComboBox()
        self.server_preset_combo.addItems(list(SERVER_PRESETS.keys()))
        self.server_preset_combo.setCurrentText(str(self.server_preset_var.get()))
        self.server_preset_combo.currentTextChanged.connect(
            self.server_preset_var.set
        )
        self.server_preset_combo.currentTextChanged.connect(
            self._on_server_preset_changed
        )
        self.server_preset_var.bind(self.server_preset_combo.setCurrentText)
        layout.addWidget(self.server_preset_combo, row, 1, 1, 2)
        row += 1

        layout.addWidget(QLabel("Server URL"), row, 0)
        self.server_url_entry = QLineEdit()
        self.server_url_entry.setText(str(self.server_url_var.get()))
        self.server_url_entry.textChanged.connect(self.server_url_var.set)
        self.server_url_var.bind(self.server_url_entry.setText)
        layout.addWidget(self.server_url_entry, row, 1, 1, 2)
        row += 1

        layout.addWidget(
            self._description(
                "ntfy.sh is selected by default. On startup, the client requests "
                "up to 48 hours of cached encrypted history, subject to the server's "
                "actual retention period."
            ),
            row,
            0,
            1,
            3,
        )
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

        self.chime_checkbox = QCheckBox(
            "Play a chime when another user sends a message"
        )
        self.chime_checkbox.setChecked(bool(self.chime_var.get()))
        self.chime_checkbox.toggled.connect(self.chime_var.set)
        self.chime_var.bind(self.chime_checkbox.setChecked)
        layout.addWidget(self.chime_checkbox, row, 0, 1, 3)
        row += 1

        self.anti_alias_checkbox = QCheckBox("Anti-aliased Text")
        self.anti_alias_checkbox.setChecked(bool(self.anti_alias_var.get()))
        self.anti_alias_checkbox.toggled.connect(self.anti_alias_var.set)
        self.anti_alias_var.bind(self.anti_alias_checkbox.setChecked)
        layout.addWidget(self.anti_alias_checkbox, row, 0, 1, 3)
        row += 1

        layout.addWidget(self._separator(), row, 0, 1, 3)
        row += 1

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        test_button = QPushButton("Test connection")
        test_button.clicked.connect(self._test_connection)
        button_layout.addWidget(test_button)
        layout.addLayout(button_layout, row, 0, 1, 3)
        row += 1

        apply_note = self._description(
            "Changes are saved and applied when the Config panel closes."
        )
        layout.addWidget(apply_note, row, 0, 1, 3)
        row += 1

        layout.addWidget(
            self._description(
                "Settings, server message cursors, local history, and the hidden "
                "client identity are saved in a Windows DPAPI-encrypted file tied "
                "to the current Windows user."
            ),
            row,
            0,
            1,
            3,
        )
        row += 1
        layout.setRowStretch(row, 1)

    def _sync_config_overlay_geometry(self) -> None:
        self.config_overlay.setGeometry(self.chat_content.rect())

    def _config_ui_snapshot(self) -> tuple[Any, ...]:
        return (
            str(self.server_preset_var.get()),
            str(self.server_url_var.get()),
            bool(self.chime_var.get()),
            bool(self.anti_alias_var.get()),
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
            self.server_preset_combo.setFocus()
            return

        self._dismiss_config_popup()

    def _dismiss_config_popup(self) -> bool:
        if not self.config_overlay.isVisible():
            self._set_config_toggle_checked(False)
            return True

        changed = (
            self._config_snapshot_at_open is not None
            and self._config_ui_snapshot() != self._config_snapshot_at_open
        )
        if changed and not self._save_and_reconnect():
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
        self.config_data["chime_enabled"] = bool(self.chime_var.get())
        self.config_data["anti_aliased_text"] = bool(
            self.anti_alias_var.get()
        )

    def _save_and_reconnect(self) -> bool:
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

        self._clear_visible_room()
        self._apply_application_font_strategy()
        self._apply_active_composer_style()
        self._load_saved_history_for_current_room()
        self.initial_history_pending_rooms.update(
            room["id"] for room in self._chatroom_definitions()
        )
        self.status_var.set("Reconnecting")
        self.reconnect_requested.set()
        self._append_system_message("Configuration saved. Reconnecting.")
        return True

    def _test_connection(self) -> None:
        try:
            server_url = self._validate_current_settings()
        except Exception as exc:
            messagebox.showerror(
                "Invalid configuration",
                str(exc),
                parent=self.root,
            )
            return

        threading.Thread(
            target=self._test_connection_worker,
            args=(server_url,),
            daemon=True,
        ).start()

    def _test_connection_worker(self, server_url: str) -> None:
        try:
            response = requests.get(
                f"{server_url}/v1/health",
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            payload = response.json()

            if not isinstance(payload, dict):
                raise ValueError("Health endpoint returned invalid JSON.")
        except Exception as exc:
            self.ui_queue.put(("test_failed", str(exc)))
        else:
            self.ui_queue.put(("test_ok", server_url))

    def _build_draft_message(self, text: str) -> dict[str, Any]:
        profile = self._active_room_profile()

        return {
            "v": APP_VERSION,
            "i": self.draft_message_id,
            "c": self.config_data["client_id"],
            "u": profile["username"],
            "k": profile["username_color"],
            "f": profile["font"],
            "o": profile["text_color"],
            "t": int(time.time()),
            "m": text,
        }

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

    def _draw_message_size_bar(self) -> None:
        packet_size = max(0, int(self.current_estimated_packet_size))
        at_or_over_limit = packet_size >= NTFY_MAX_BODY_BYTES

        self.message_size_bar.setValue(
            min(packet_size, NTFY_MAX_BODY_BYTES)
        )
        self.message_size_bar.setStyleSheet(
            "QProgressBar { background-color: #eeeeee; "
            "border: 1px solid #a8a8a8; color: "
            + ("#ffffff" if at_or_over_limit else "#202020")
            + "; text-align: center; } "
            "QProgressBar::chunk { background: "
            + ("#303030" if at_or_over_limit else "#b8b8b8")
            + "; }"
        )

        if self.message_size_check_pending:
            self.message_size_bar.setFormat("Calculating...")
        else:
            self.message_size_bar.setFormat(
                f"{packet_size / 1024.0:.1f} KB / "
                f"{NTFY_MAX_BODY_BYTES / 1024.0:.1f} KB"
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
        next_poll_allowed = 0.0
        force_active_poll = True

        while not self.stop_event.is_set():
            if self.reconnect_requested.is_set():
                self.reconnect_requested.clear()
                self.connected = False
                force_active_poll = True
                self.ui_queue.put((
                    "status",
                    (
                        "Connecting",
                        "Applying saved configuration...",
                        self.active_chatroom_id,
                    ),
                ))

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
            valid_room_ids = {room["id"] for room in rooms}
            for room_id in tuple(last_poll_times):
                if room_id not in valid_room_ids:
                    last_poll_times.pop(room_id, None)

            if now >= next_poll_allowed:
                active_room_id = active_room["id"]
                active_poll_due = (
                    force_active_poll
                    or now - last_poll_times.get(active_room_id, 0.0)
                    >= POLL_INTERVAL_SECONDS
                )
                room_to_poll: dict[str, str] | None = None
                poll_is_active = False

                if active_poll_due:
                    room_to_poll = active_room
                    poll_is_active = True
                else:
                    room_to_poll = next((
                        room
                        for room in rooms
                        if room["id"] != active_room_id
                        and (
                            room["id"] not in last_poll_times
                            or now - last_poll_times[room["id"]]
                            >= BACKGROUND_POLL_INTERVAL_SECONDS
                        )
                    ), None)

                if room_to_poll is not None:
                    self._network_poll(
                        room_to_poll,
                        is_active=poll_is_active,
                    )
                    completed_at = time.monotonic()
                    last_poll_times[room_to_poll["id"]] = completed_at
                    next_poll_allowed = (
                        completed_at + MIN_POLL_REQUEST_SPACING_SECONDS
                    )
                    if poll_is_active:
                        force_active_poll = False

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
                    self._validate_decrypted_message(message)
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
    def _validate_decrypted_message(message: dict[str, Any]) -> None:
        required = {
            "i": str,
            "c": str,
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

        if len(message["i"]) > 128:
            raise ValueError("Message ID is too long.")
        if len(message["c"]) > 256:
            raise ValueError("Client ID is too long.")
        if len(message["u"]) > 32:
            raise ValueError("Username is too long.")
        if len(message["m"]) > MAX_MESSAGE_CHARS:
            raise ValueError("Message text is too long.")

        font_name = message.get("f", DEFAULT_MESSAGE_FONT)
        if (
            not isinstance(font_name, str)
            or font_name not in SUPPORTED_MESSAGE_FONTS
        ):
            raise ValueError("Message font is unsupported.")

        text_color = message.get("o", DEFAULT_MESSAGE_TEXT_COLOR)
        if not isinstance(text_color, str) or not QColor(text_color).isValid():
            raise ValueError("Message text color is invalid.")

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

                elif event_type == "decrypt_failures":
                    pass

                elif event_type == "test_ok":
                    messagebox.showinfo(
                        "Connection successful",
                        "The ntfy health endpoint responded successfully.",
                        parent=self.root,
                    )

                elif event_type == "test_failed":
                    messagebox.showerror(
                        "Connection failed",
                        payload,
                        parent=self.root,
                    )

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
            warning = self._observe_identity(message, encryption_key)
            is_local = message.get("c") == self.config_data["client_id"]
            history.append({
                "message": message,
                "warning": warning,
                "ntfy_id": ntfy_id,
                "ntfy_time": int(
                    item.get("ntfy_time", message.get("t", 0)) or 0
                ),
            })
            added += 1

            if not history_scan and not is_local:
                unread_added += 1
                if (
                    self.chime_var.get()
                    and not self._is_chatroom_muted(room_id)
                    and str(message.get("c", "")) not in muted_user_ids
                ):
                    self._play_chime()

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

        warning = self._observe_identity(message)
        is_local = message["c"] == self.config_data["client_id"]

        self._add_message_to_log(
            message,
            is_local=is_local,
            warning=warning,
            ntfy_id=ntfy_id,
            ntfy_time=item["ntfy_time"],
            persist=persist,
            render=render,
        )

        if (
            play_chime
            and not is_local
            and self.chime_var.get()
            and not self._is_chatroom_muted(self.active_chatroom_id)
            and not self._is_user_muted(str(message["c"]))
        ):
            self._play_chime()

        return True

    def _observe_identity(
        self,
        message: dict[str, Any],
        encryption_key: str | None = None,
    ) -> str | None:
        server_url = str(self.config_data.get("server_url", ""))
        if encryption_key is None:
            encryption_key = self._active_chatroom()["key"]

        if not server_url or not encryption_key:
            return None

        scope_id = room_scope_id(server_url, encryption_key)
        all_identities = self.config_data.setdefault("identities", {})
        identities = all_identities.setdefault(scope_id, {})

        username = message["u"]
        client_id = message["c"]
        known = identities.get(username)
        warning = None

        if isinstance(known, str) and known != client_id:
            warning = (
                f'Identity warning: "{username}" is using a different hidden '
                "client ID than previously observed."
            )
        elif known is None:
            identities[username] = client_id
            try:
                save_config(self.config_data)
            except Exception:
                pass

        return warning

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
                self._validate_decrypted_message(message)
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
                "is_local": message["c"] == self.config_data["client_id"],
                "warning": item.get("warning") if isinstance(item.get("warning"), str) else None,
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

    def _on_chat_display_configure(self) -> None:
        muted_ids = self._room_preference_ids("muted_users")
        collapsed_ids = self._room_preference_ids("collapsed_messages")
        if muted_ids or collapsed_ids:
            self.chat_render_timer.start(120)

    def _finish_chat_resize_render(self) -> None:
        self._rerender_preserving_scroll()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (
            watched is getattr(self, "chat_content", None)
            and event.type() == QEvent.Type.Resize
            and hasattr(self, "config_overlay")
        ):
            self._sync_config_overlay_geometry()

        if (
            hasattr(self, "chat_display")
            and watched is self.chat_display.viewport()
        ):
            if event.type() == QEvent.Type.Resize:
                self._on_chat_display_configure()

            elif event.type() == QEvent.Type.MouseMove:
                anchor = self.chat_display.anchorAt(event.position().toPoint())
                message_id = self._message_id_from_anchor(anchor)

                if message_id:
                    self.chat_display.viewport().setCursor(
                        Qt.CursorShape.PointingHandCursor
                    )
                    if message_id != self._hovered_message_id:
                        self._hovered_message_id = message_id
                        QToolTip.showText(
                            event.globalPosition().toPoint(),
                            self.rendered_tooltips.get(message_id, ""),
                            self.chat_display.viewport(),
                        )
                else:
                    self._hovered_message_id = None
                    self.chat_display.viewport().setCursor(
                        Qt.CursorShape.IBeamCursor
                    )
                    QToolTip.hideText()

            elif event.type() == QEvent.Type.Leave:
                self._hovered_message_id = None
                QToolTip.hideText()

            elif event.type() == QEvent.Type.ContextMenu:
                anchor = self.chat_display.anchorAt(event.pos())
                message_id = self._message_id_from_anchor(anchor)
                item = self.rendered_message_items.get(message_id or "")
                if item is not None:
                    self._show_username_context_menu(
                        item,
                        event.globalPos(),
                    )
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
        amount: float = 0.70,
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
        username: str,
        status_suffix: str,
        text: str,
        font_name: str,
    ) -> str:
        normalized = " ".join(text.split())
        ending = " [...]"

        if not normalized:
            return "[...]"

        body_metrics = QFontMetrics(self._make_message_font(font_name))
        username_font = self._make_message_font(font_name, bold=True)
        username_metrics = QFontMetrics(username_font)
        prefix_width = (
            username_metrics.horizontalAdvance(username)
            + body_metrics.horizontalAdvance(status_suffix + ": ")
            + 36
        )
        available_width = max(
            0,
            self.chat_display.viewport().width() - prefix_width,
        )

        if body_metrics.horizontalAdvance(normalized + ending) <= available_width:
            return normalized + ending

        if body_metrics.horizontalAdvance("[...]") > available_width:
            return "[...]"

        low = 0
        high = len(normalized)
        while low < high:
            midpoint = (low + high + 1) // 2
            candidate = normalized[:midpoint].rstrip() + ending
            if body_metrics.horizontalAdvance(candidate) <= available_width:
                low = midpoint
            else:
                high = midpoint - 1

        return (
            normalized[:low].rstrip() + ending
            if low > 0
            else "[...]"
        )

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
        is_muted = client_id in muted_ids
        is_manually_collapsed = message_id in collapsed_ids

        menu = QMenu(self.root)
        mute_action = menu.addAction(
            "Unmute User" if is_muted else "Mute User"
        )
        mute_action.setEnabled(not is_local)
        if not is_local:
            mute_action.triggered.connect(
                lambda: self._set_user_muted(client_id, not is_muted)
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
        return formatting

    def _render_message_log(self, *, scroll_to_bottom: bool) -> None:
        QToolTip.hideText()
        self.rendered_message_items.clear()
        self.rendered_tooltips.clear()
        self.chat_display.clear()

        cursor = self.chat_display.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        muted_ids = self._room_preference_ids("muted_users")
        collapsed_ids = self._room_preference_ids("collapsed_messages")
        previous_timestamp: int | None = None
        first_item = True

        for item in self.message_log:
            current_timestamp = self._display_timestamp_for_item(item)

            if (
                previous_timestamp is not None
                and current_timestamp - previous_timestamp
                >= GAP_SEPARATOR_SECONDS
            ):
                if not first_item:
                    cursor.insertBlock()
                gap_seconds = current_timestamp - previous_timestamp
                gap_hours = max(6, int((gap_seconds / 3600.0) + 0.5))
                gap_block = QTextBlockFormat()
                gap_block.setAlignment(Qt.AlignmentFlag.AlignCenter)
                gap_block.setTopMargin(7)
                gap_block.setBottomMargin(7)
                cursor.setBlockFormat(gap_block)
                cursor.insertText(
                    f"————— {gap_hours} hours later —————",
                    self._text_format("#777777"),
                )
                cursor.insertBlock()
                cursor.setBlockFormat(QTextBlockFormat())
            elif not first_item:
                cursor.insertBlock()

            self._insert_message_item(
                cursor,
                item,
                muted_ids=muted_ids,
                collapsed_ids=collapsed_ids,
            )
            first_item = False
            previous_timestamp = current_timestamp

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
    ) -> None:
        message = item["message"]
        timestamp = self._display_timestamp_for_item(item)
        username = str(message["u"])
        original_color = str(message["k"])
        font_name = str(message.get("f", DEFAULT_MESSAGE_FONT))
        original_text_color = str(
            message.get("o", DEFAULT_MESSAGE_TEXT_COLOR)
        )
        text = str(message["m"])
        message_id = str(message["i"])
        client_id = str(message["c"])
        unique_id_preview = client_id[:5]

        is_muted = client_id in muted_ids and not item["is_local"]
        is_collapsed = is_muted or message_id in collapsed_ids
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
        self.rendered_tooltips[message_id] = (
            f"{self._format_hover_timestamp(timestamp)}\n"
            f"Unique ID: {unique_id_preview}"
        )

        cursor.insertText(
            username,
            self._text_format(
                username_color,
                bold=True,
                anchor=f"spritelink:{message_id}",
                font_name=font_name,
            ),
        )
        if status_suffix:
            cursor.insertText(
                status_suffix,
                self._text_format(suffix_color, font_name=font_name),
            )
        cursor.insertText(
            ": ",
            self._text_format(body_color, font_name=font_name),
        )

        display_text = (
            self._collapsed_message_preview(
                username,
                status_suffix,
                text,
                font_name,
            )
            if is_collapsed
            else text
        )
        cursor.insertText(
            display_text,
            self._text_format(body_color, font_name=font_name),
        )

        if item.get("warning") and not is_collapsed:
            cursor.insertBlock()
            cursor.insertText(
                str(item["warning"]),
                self._text_format("#b00020", font_name=font_name),
            )

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
        QToolTip.hideText()
        self.seen_client_message_ids.clear()
        self.seen_ntfy_message_ids.clear()
        self.message_log.clear()
        self.rendered_message_items.clear()
        self.rendered_tooltips.clear()
        self.chat_display.clear()

    def _play_chime(self) -> None:
        if winsound is None:
            return

        try:
            winsound.MessageBeep(winsound.MB_OK)
        except Exception:
            pass

    def _on_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        QToolTip.hideText()

        try:
            self._copy_ui_to_config()
            save_config(self.config_data)
        except Exception:
            pass

        self.stop_event.set()
        self.ui_queue_timer.stop()

def _write_crash_log(error_text: str) -> Path | None:
    try:
        APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
        crash_path = APP_DATA_DIR / "crash.log"
        crash_path.write_text(error_text, encoding="utf-8")
        return crash_path
    except Exception:
        return None



def main() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("SpriteLink")
    root = MainWindow()

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
