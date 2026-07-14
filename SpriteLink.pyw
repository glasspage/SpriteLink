# Encrypted Chat Client v9
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
import math
import sys
import uuid
import zlib
try:
    from PySide6.QtCore import QEvent, QObject, QTimer, Qt, Signal
    from PySide6.QtGui import (
        QColor,
        QFont,
        QFontMetrics,
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
        QFrame,
        QGridLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMenu,
        QMessageBox,
        QPlainTextEdit,
        QProgressBar,
        QPushButton,
        QSizePolicy,
        QTabWidget,
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


APP_NAME = "EncryptedChatClient"
APP_VERSION = 1
CONFIG_FORMAT_VERSION = 9

DEFAULT_SERVER_PRESET = "ntfy.sh (public)"
DEFAULT_SERVER_URL = "https://ntfy.sh"

SERVER_PRESETS: dict[str, str] = {
    DEFAULT_SERVER_PRESET: DEFAULT_SERVER_URL,
    "Local ntfy server": "http://127.0.0.1:8080",
    "Custom ntfy server": "",
}

POLL_INTERVAL_SECONDS = 5.25
REQUEST_TIMEOUT_SECONDS = 10
AUTO_HISTORY_SECONDS = 48 * 60 * 60
GAP_SEPARATOR_SECONDS = 6 * 60 * 60
MAX_MESSAGE_CHARS = 4000
NTFY_MAX_BODY_BYTES = 4096
PACKET_PADDING_BLOCK = 128
MESSAGE_SIZE_DEBOUNCE_MS = 1500
MESSAGE_ENTRY_MIN_LINES = 3
MESSAGE_ENTRY_MAX_LINES = 7

# Dark, moderately saturated colors that remain readable against the standard
# light Tkinter text background. A color is selected only when a new local
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
    return {
        "config_version": CONFIG_FORMAT_VERSION,
        "server_preset": DEFAULT_SERVER_PRESET,
        "server_url": DEFAULT_SERVER_URL,
        "username": "User",
        "username_color": secrets.choice(SAFE_USERNAME_COLORS),
        "encryption_key": "",
        "chime_enabled": True,
        "client_id": secrets.token_hex(32),
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

    # Migrate the exact default from v1 to the public ntfy server.
    old_preset = str(config.get("server_preset", ""))
    old_url = str(config.get("server_url", ""))
    if (
        old_preset == "Localhost"
        and old_url.rstrip("/") == "http://127.0.0.1:8000"
    ):
        config["server_preset"] = DEFAULT_SERVER_PRESET
        config["server_url"] = DEFAULT_SERVER_URL

    previous_config_version = int(config.get("config_version", 0) or 0)
    if previous_config_version < 3:
        if config.get("username") == "Anonymous":
            config["username"] = "User"
        if config.get("username_color") == "#4ea1ff":
            config["username_color"] = secrets.choice(SAFE_USERNAME_COLORS)

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
        self.root.setWindowTitle("Encrypted Chat Client")
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
        self.initial_history_pending = True
        self.draft_message_id = uuid.uuid4().hex
        self.current_estimated_packet_size = 0
        self.message_size_check_pending = False
        self.rendered_message_items: dict[str, dict[str, Any]] = {}
        self.rendered_tooltips: dict[str, str] = {}
        self._hovered_message_id: str | None = None
        self._closing = False

        self.server_preset_var = ValueModel(
            self.config_data["server_preset"]
        )
        self.server_url_var = ValueModel(
            self.config_data["server_url"]
        )
        self.username_var = ValueModel(
            self.config_data["username"]
        )
        self.color_var = ValueModel(
            self.config_data["username_color"]
        )
        self.encryption_key_var = ValueModel(
            self.config_data["encryption_key"]
        )
        self.chime_var = ValueModel(
            bool(self.config_data["chime_enabled"])
        )
        self.show_key_var = ValueModel(False)
        self.status_var = ValueModel("Connecting")
        self.status_detail_var = ValueModel(
            "Waiting for a room key."
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

        self._build_ui()
        self._apply_server_preset_state()
        self._update_color_preview()
        self._load_saved_history_for_current_room()

        self.root.close_callback = self._on_close
        self._start_network_thread()

    @staticmethod
    def _heading(text: str) -> QLabel:
        label = QLabel(text)
        font = QFont("Segoe UI", 10)
        font.setBold(True)
        label.setFont(font)
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
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)

        notebook = QTabWidget()
        central_layout.addWidget(notebook)
        self.root.setCentralWidget(central)

        self.chat_tab = QWidget()
        self.config_tab = QWidget()
        notebook.addTab(self.chat_tab, "Chatroom")
        notebook.addTab(self.config_tab, "Config")

        self._build_chat_tab()
        self._build_config_tab()

    def _build_chat_tab(self) -> None:
        layout = QVBoxLayout(self.chat_tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(7)

        status_layout = QHBoxLayout()
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.addWidget(QLabel("Status:"))

        self.status_label = QLabel()
        status_font = QFont("Segoe UI", 9)
        status_font.setBold(True)
        self.status_label.setFont(status_font)
        self.status_var.bind(self.status_label.setText)
        status_layout.addWidget(self.status_label)
        status_layout.addStretch(1)

        self.status_detail_label = QLabel()
        self.status_detail_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.status_detail_var.bind(self.status_detail_label.setText)
        status_layout.addWidget(self.status_detail_label)
        layout.addLayout(status_layout)

        self.chat_display = QTextBrowser()
        self.chat_display.setReadOnly(True)
        self.chat_display.setOpenLinks(False)
        self.chat_display.setOpenExternalLinks(False)
        self.chat_display.setUndoRedoEnabled(False)
        self.chat_display.setFont(QFont("Segoe UI", 10))
        self.chat_display.setViewportMargins(6, 6, 6, 6)
        self.chat_display.document().setDocumentMargin(4)
        text_option = self.chat_display.document().defaultTextOption()
        text_option.setWrapMode(QTextOption.WrapMode.WrapAnywhere)
        self.chat_display.document().setDefaultTextOption(text_option)
        self.chat_display.viewport().setMouseTracking(True)
        self.chat_display.viewport().installEventFilter(self)
        layout.addWidget(self.chat_display, 1)

        compose_layout = QGridLayout()
        compose_layout.setContentsMargins(0, 1, 0, 0)
        compose_layout.setHorizontalSpacing(8)
        compose_layout.setVerticalSpacing(5)

        self.message_entry = ComposeTextEdit()
        self.message_entry.setFont(QFont("Segoe UI", 10))
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
        layout.addLayout(compose_layout)

        self._resize_message_entry()
        self._run_message_size_check()

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
        layout.addWidget(self._heading("Identity and appearance"), row, 0, 1, 3)
        row += 1

        layout.addWidget(QLabel("Username"), row, 0)
        self.username_entry = QLineEdit()
        self.username_entry.setText(str(self.username_var.get()))
        self.username_entry.textChanged.connect(self.username_var.set)
        self.username_var.bind(self.username_entry.setText)
        layout.addWidget(self.username_entry, row, 1, 1, 2)
        row += 1

        layout.addWidget(QLabel("Username color"), row, 0)
        color_layout = QHBoxLayout()
        self.color_preview = QLabel()
        self.color_preview.setFixedSize(28, 22)
        self.color_preview.setFrameShape(QFrame.Shape.Panel)
        self.color_preview.setFrameShadow(QFrame.Shadow.Sunken)
        color_layout.addWidget(self.color_preview)

        choose_color_button = QPushButton("Choose color...")
        choose_color_button.clicked.connect(self._choose_color)
        color_layout.addWidget(choose_color_button)
        color_layout.addStretch(1)
        layout.addLayout(color_layout, row, 1, 1, 2)
        row += 1

        layout.addWidget(self._separator(), row, 0, 1, 3)
        row += 1
        layout.addWidget(self._heading("Encryption"), row, 0, 1, 3)
        row += 1

        layout.addWidget(QLabel("Chatroom key"), row, 0)
        self.key_entry = QLineEdit()
        self.key_entry.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_entry.setText(str(self.encryption_key_var.get()))
        self.key_entry.textChanged.connect(self.encryption_key_var.set)
        self.encryption_key_var.bind(self.key_entry.setText)
        layout.addWidget(self.key_entry, row, 1)

        self.show_key_checkbox = QCheckBox("Show")
        self.show_key_checkbox.toggled.connect(self.show_key_var.set)
        self.show_key_checkbox.toggled.connect(self._toggle_key_visibility)
        layout.addWidget(self.show_key_checkbox, row, 2)
        row += 1

        layout.addWidget(
            self._description(
                "The key determines both the encryption key and an opaque ntfy topic. "
                "Everyone in the same room must enter exactly the same value."
            ),
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

        layout.addWidget(self._separator(), row, 0, 1, 3)
        row += 1

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        test_button = QPushButton("Test connection")
        test_button.clicked.connect(self._test_connection)
        button_layout.addWidget(test_button)
        save_button = QPushButton("Save and reconnect")
        save_button.clicked.connect(self._save_and_reconnect)
        button_layout.addWidget(save_button)
        layout.addLayout(button_layout, row, 0, 1, 3)
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

    def _choose_color(self) -> None:
        initial = QColor(str(self.color_var.get()))
        selected = QColorDialog.getColor(
            initial if initial.isValid() else QColor("#4ea1ff"),
            self.root,
            "Choose username color",
        )

        if selected.isValid():
            self.color_var.set(selected.name())
            self._update_color_preview()

    def _update_color_preview(self) -> None:
        color = QColor(str(self.color_var.get()).strip() or "#4ea1ff")
        if not color.isValid():
            color = QColor("#4ea1ff")
            self.color_var.set(color.name())

        self.color_preview.setStyleSheet(
            f"background-color: {color.name()};"
        )

    def _toggle_key_visibility(self, _checked: Any = None) -> None:
        self.key_entry.setEchoMode(
            QLineEdit.EchoMode.Normal
            if bool(self.show_key_var.get())
            else QLineEdit.EchoMode.Password
        )

    def _validate_current_settings(self) -> tuple[str, str, str, str]:
        server_url = normalize_server_url(str(self.server_url_var.get()))
        username = str(self.username_var.get()).strip()
        color = str(self.color_var.get()).strip()
        encryption_key = str(self.encryption_key_var.get())

        if not server_url.startswith(("http://", "https://")):
            raise ValueError(
                "The server URL must begin with http:// or https://."
            )

        if not username:
            raise ValueError("The username cannot be empty.")

        if len(username) > 32:
            raise ValueError("The username must be 32 characters or fewer.")

        if not encryption_key:
            raise ValueError("The chatroom encryption key cannot be empty.")

        if not QColor(color).isValid():
            raise ValueError("The username color is invalid.")

        derive_ntfy_topic(encryption_key)
        return server_url, username, color, encryption_key

    def _copy_ui_to_config(self) -> None:
        server_url, username, color, encryption_key = (
            self._validate_current_settings()
        )

        self.config_data["server_preset"] = self.server_preset_var.get()
        self.config_data["server_url"] = server_url
        self.config_data["username"] = username
        self.config_data["username_color"] = color
        self.config_data["encryption_key"] = encryption_key
        self.config_data["chime_enabled"] = bool(self.chime_var.get())

    def _save_and_reconnect(self) -> None:
        try:
            self._copy_ui_to_config()
            save_config(self.config_data)
        except Exception as exc:
            messagebox.showerror(
                "Could not save settings",
                str(exc),
                parent=self.root,
            )
            return

        self._clear_visible_room()
        self._load_saved_history_for_current_room()
        self.initial_history_pending = True
        self.status_var.set("Reconnecting")
        self.status_detail_var.set("Applying the saved room configuration...")
        self.reconnect_requested.set()
        self._append_system_message("Configuration saved. Reconnecting.")

    def _test_connection(self) -> None:
        try:
            server_url, _, _, _ = self._validate_current_settings()
        except Exception as exc:
            messagebox.showerror(
                "Invalid configuration",
                str(exc),
                parent=self.root,
            )
            return

        self.status_detail_var.set("Testing ntfy server...")
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
        username = str(self.username_var.get()).strip() or "User"
        color = str(self.color_var.get()).strip() or "#315f8c"

        return {
            "v": APP_VERSION,
            "i": self.draft_message_id,
            "c": self.config_data["client_id"],
            "u": username[:32],
            "k": color,
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
        display_lines = max(
            1,
            int(math.ceil(document.size().height() / line_height)),
        )
        visible_lines = max(
            MESSAGE_ENTRY_MIN_LINES,
            min(MESSAGE_ENTRY_MAX_LINES, display_lines),
        )
        margins = (
            self.message_entry.frameWidth() * 2
            + int(document.documentMargin() * 2)
            + 4
        )
        self.message_entry.setFixedHeight(
            visible_lines * line_height + margins
        )

        if display_lines > MESSAGE_ENTRY_MAX_LINES:
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
            "QProgressBar { text-align: center; } "
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
            server_url, username, color, encryption_key = (
                self._validate_current_settings()
            )
        except Exception as exc:
            messagebox.showerror(
                "Cannot send message",
                str(exc),
                parent=self.root,
            )
            return

        if not self.connected:
            messagebox.showwarning(
                "Not connected",
                "The client is not currently connected to the selected server.",
                parent=self.root,
            )
            return

        message = self._build_draft_message(text)
        message["u"] = username
        message["k"] = color
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
            name="EncryptedChatNetwork",
            daemon=True,
        )
        self.network_thread.start()

    def _network_loop(self) -> None:
        next_poll = 0.0

        while not self.stop_event.is_set():
            if self.reconnect_requested.is_set():
                self.reconnect_requested.clear()
                self.connected = False
                self.initial_history_pending = True
                next_poll = 0.0
                self.ui_queue.put((
                    "status",
                    ("Connecting", "Applying saved configuration..."),
                ))

            while True:
                try:
                    outbound = self.send_queue.get_nowait()
                except queue.Empty:
                    break
                self._network_send(outbound)


            now = time.monotonic()
            if now >= next_poll:
                self._network_poll()
                next_poll = now + POLL_INTERVAL_SECONDS

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
                },
            ))

    def _current_poll_since(
        self,
        state: dict[str, Any],
    ) -> str:
        if self.initial_history_pending:
            return f"{AUTO_HISTORY_SECONDS}s"

        newest_id = state.get("newest_ntfy_id")
        if isinstance(newest_id, str) and newest_id:
            return newest_id

        return f"{AUTO_HISTORY_SECONDS}s"

    def _network_poll(self) -> None:
        server_url = normalize_server_url(
            str(self.config_data.get("server_url", ""))
        )
        encryption_key = str(
            self.config_data.get("encryption_key", "")
        )

        if not server_url or not encryption_key:
            if self.connected:
                self.connected = False

            self.ui_queue.put((
                "status",
                ("Disconnected", "Enter and save a chatroom key."),
            ))
            return

        try:
            topic = derive_ntfy_topic(encryption_key)
            scope_id = room_scope_id(server_url, encryption_key)
            state = self.config_data.setdefault("room_state", {}).setdefault(
                scope_id,
                {},
            )
            was_initial_history_scan = self.initial_history_pending
            since_value = self._current_poll_since(state)

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
            self.initial_history_pending = False

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

            if not self.connected:
                self.connected = True
                self.ui_queue.put((
                    "status",
                    ("Connected", server_url),
                ))

            if decoded_messages or was_initial_history_scan:
                self.ui_queue.put((
                    "messages",
                    {
                        "items": decoded_messages,
                        "history_scan": was_initial_history_scan,
                    },
                ))

            if failed_decryptions:
                self.ui_queue.put((
                    "decrypt_failures",
                    failed_decryptions,
                ))

        except Exception as exc:
            self.connected = False
            self.ui_queue.put((
                "status",
                ("Disconnected", str(exc)),
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

    def _process_ui_queue(self) -> None:
        try:
            while True:
                event_type, payload = self.ui_queue.get_nowait()

                if event_type == "status":
                    status, detail = payload
                    self.status_var.set(status)
                    self.status_detail_var.set(detail)

                elif event_type == "messages":
                    items = payload.get("items", [])
                    history_scan = bool(payload.get("history_scan", False))
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

                    if history_scan:
                        if added:
                            self.status_detail_var.set(
                                f"Loaded {added} message"
                                f"{'s' if added != 1 else ''} from available "
                                "history within the last 48 hours."
                            )
                        else:
                            self.status_detail_var.set(
                                "Connected. No additional readable messages were "
                                "available from the last 48 hours."
                            )

                elif event_type == "send_failed":
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
                    count = int(payload)
                    self.status_detail_var.set(
                        f"Connected, but skipped {count} unreadable packet"
                        f"{'s' if count != 1 else ''}."
                    )

                elif event_type == "test_ok":
                    self.status_detail_var.set(
                        f"ntfy server responded: {payload}"
                    )
                    messagebox.showinfo(
                        "Connection successful",
                        "The ntfy health endpoint responded successfully.",
                        parent=self.root,
                    )

                elif event_type == "test_failed":
                    self.status_detail_var.set("Server test failed.")
                    messagebox.showerror(
                        "Connection failed",
                        payload,
                        parent=self.root,
                    )

        except queue.Empty:
            pass

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
            and not self._is_user_muted(str(message["c"]))
        ):
            self._play_chime()

        return True

    def _observe_identity(self, message: dict[str, Any]) -> str | None:
        server_url = str(self.config_data.get("server_url", ""))
        encryption_key = str(self.config_data.get("encryption_key", ""))

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
        encryption_key = str(self.config_data.get("encryption_key", ""))

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
        encryption_key = str(self.config_data.get("encryption_key", ""))

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
        encryption_key = str(
            self.config_data.get("encryption_key", "")
        )

        if not server_url or not encryption_key:
            return None

        try:
            return room_scope_id(server_url, encryption_key)
        except Exception:
            return None

    def _room_preference_ids(self, category: str) -> set[str]:
        scope_id = self._current_room_scope_id()
        if scope_id is None:
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
        if watched is self.chat_display.viewport():
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
    ) -> str:
        normalized = " ".join(text.split())
        ending = " [...]"

        if not normalized:
            return "[...]"

        body_metrics = QFontMetrics(self.chat_display.font())
        username_font = QFont("Segoe UI", 10)
        username_font.setBold(True)
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

    @staticmethod
    def _text_format(
        color: str,
        *,
        bold: bool = False,
        anchor: str | None = None,
    ) -> QTextCharFormat:
        formatting = QTextCharFormat()
        formatting.setForeground(QColor(color))
        formatting.setFontFamilies(["Segoe UI"])
        formatting.setFontPointSize(10)
        formatting.setFontWeight(
            QFont.Weight.Bold if bold else QFont.Weight.Normal
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

        body_color = (
            self._blend_toward_chat_background("#202020")
            if is_muted
            else "#202020"
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
            ),
        )
        if status_suffix:
            cursor.insertText(
                status_suffix,
                self._text_format(suffix_color),
            )
        cursor.insertText(": ", self._text_format(body_color))

        display_text = (
            self._collapsed_message_preview(
                username,
                status_suffix,
                text,
            )
            if is_collapsed
            else text
        )
        cursor.insertText(display_text, self._text_format(body_color))

        if item.get("warning") and not is_collapsed:
            cursor.insertBlock()
            cursor.insertText(
                str(item["warning"]),
                self._text_format("#b00020"),
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
    app.setApplicationName("Encrypted Chat Client")
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
            "Encrypted Chat Client error",
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
            "Encrypted Chat Client startup error",
            f"The client could not start.{location}\n\n{error_text[-2200:]}",
            parent=root,
        )
        return

    root.show()
    app.exec()


if __name__ == "__main__":
    main()

