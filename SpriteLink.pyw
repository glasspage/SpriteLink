# Encrypted Chat Client v9
# Windows + Python 3.10+
#
# Required packages:
#   pip install requests cryptography
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
import uuid
import zlib
import tkinter as tk
from tkinter import colorchooser, font as tkfont, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Any

try:
    import requests
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: requests\n\nInstall it with:\n"
        "pip install requests cryptography"
    ) from exc

try:
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: cryptography\n\nInstall it with:\n"
        "pip install requests cryptography"
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


class EncryptedChatClient:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Encrypted Chat Client")
        self.root.geometry("840x650")
        self.root.minsize(670, 500)

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
        self.tooltip_window: tk.Toplevel | None = None
        self.draft_message_id = uuid.uuid4().hex
        self.current_estimated_packet_size = 0
        self.message_size_check_pending = False
        self.message_size_check_job: str | None = None
        self.message_resize_job: str | None = None
        self.chat_render_job: str | None = None
        self.username_context_menu: tk.Menu | None = None

        self.server_preset_var = tk.StringVar(
            value=self.config_data["server_preset"]
        )
        self.server_url_var = tk.StringVar(
            value=self.config_data["server_url"]
        )
        self.username_var = tk.StringVar(
            value=self.config_data["username"]
        )
        self.color_var = tk.StringVar(
            value=self.config_data["username_color"]
        )
        self.encryption_key_var = tk.StringVar(
            value=self.config_data["encryption_key"]
        )
        self.chime_var = tk.BooleanVar(
            value=bool(self.config_data["chime_enabled"])
        )
        self.show_key_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Connecting")
        self.status_detail_var = tk.StringVar(
            value="Waiting for a room key."
        )

        self._build_ui()
        self._apply_server_preset_state()
        self._update_color_preview()
        self._load_saved_history_for_current_room()

        self.root.after(100, self._process_ui_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._start_network_thread()

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        notebook = ttk.Notebook(self.root)
        notebook.grid(row=0, column=0, sticky="nsew")

        self.chat_tab = ttk.Frame(notebook, padding=10)
        self.config_tab = ttk.Frame(notebook, padding=14)

        notebook.add(self.chat_tab, text="Chatroom")
        notebook.add(self.config_tab, text="Config")

        self._build_chat_tab()
        self._build_config_tab()

    def _build_chat_tab(self) -> None:
        self.chat_tab.columnconfigure(0, weight=1)
        self.chat_tab.rowconfigure(1, weight=1)

        status_frame = ttk.Frame(self.chat_tab)
        status_frame.grid(row=0, column=0, sticky="ew", pady=(0, 7))
        status_frame.columnconfigure(1, weight=1)

        ttk.Label(status_frame, text="Status:").grid(
            row=0, column=0, sticky="w"
        )

        ttk.Label(
            status_frame,
            textvariable=self.status_var,
            font=("Segoe UI", 9, "bold"),
        ).grid(row=0, column=1, sticky="w", padx=(6, 0))

        ttk.Label(
            status_frame,
            textvariable=self.status_detail_var,
        ).grid(row=0, column=2, sticky="e")


        self.chat_display = ScrolledText(
            self.chat_tab,
            wrap="char",
            state="disabled",
            font=("Segoe UI", 10),
            padx=10,
            pady=10,
            undo=False,
        )
        self.chat_display.grid(row=1, column=0, sticky="nsew")
        self.chat_display.bind(
            "<Configure>",
            self._on_chat_display_configure,
            add="+",
        )
        self.chat_display.tag_configure("timestamp", foreground="#777777")
        self.chat_display.tag_configure("system", foreground="#a06000")
        self.chat_display.tag_configure("warning", foreground="#b00020")
        self.chat_display.tag_configure("message", foreground="#202020")
        self.chat_display.tag_configure(
            "gap",
            foreground="#777777",
            justify="center",
            spacing1=7,
            spacing3=7,
        )

        compose_frame = ttk.Frame(self.chat_tab)
        compose_frame.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        compose_frame.columnconfigure(0, weight=1)

        self.message_entry = tk.Text(
            compose_frame,
            height=MESSAGE_ENTRY_MIN_LINES,
            wrap="word",
            font=("Segoe UI", 10),
        )
        self.message_entry.grid(row=0, column=0, sticky="ew")
        self.message_entry.bind("<Return>", self._handle_enter)
        self.message_entry.bind("<Shift-Return>", lambda event: None)
        self.message_entry.bind(
            "<<Modified>>",
            self._on_message_entry_modified,
        )
        # Fallback for Tk builds where <<Modified>> is inconsistent.
        self.message_entry.bind(
            "<KeyRelease>",
            self._on_message_entry_activity,
            add="+",
        )
        self.message_entry.edit_modified(False)

        self.send_button = ttk.Button(
            compose_frame,
            text="Send",
            command=self._send_current_message,
        )
        self.send_button.grid(row=0, column=1, sticky="ns", padx=(8, 0))

        self.message_size_canvas = tk.Canvas(
            compose_frame,
            height=18,
            background="#eeeeee",
            highlightthickness=1,
            highlightbackground="#a8a8a8",
            borderwidth=0,
        )
        self.message_size_canvas.grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(5, 0),
        )
        self.message_size_fill = self.message_size_canvas.create_rectangle(
            0,
            0,
            0,
            18,
            fill="#b8b8b8",
            outline="",
        )
        self.message_size_label = self.message_size_canvas.create_text(
            0,
            9,
            text="0.0 KB / 4.0 KB",
            fill="#202020",
            font=("Segoe UI", 8),
            anchor="center",
        )
        self.message_size_canvas.bind(
            "<Configure>",
            lambda _event: self._draw_message_size_bar(),
        )
        self._run_message_size_check()

    def _build_config_tab(self) -> None:
        self.config_tab.columnconfigure(1, weight=1)
        row = 0

        ttk.Label(
            self.config_tab,
            text="Server",
            font=("Segoe UI", 10, "bold"),
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(0, 8))
        row += 1

        ttk.Label(self.config_tab, text="Preset").grid(
            row=row, column=0, sticky="w", padx=(0, 10), pady=5
        )

        self.server_preset_combo = ttk.Combobox(
            self.config_tab,
            textvariable=self.server_preset_var,
            values=list(SERVER_PRESETS.keys()),
            state="readonly",
        )
        self.server_preset_combo.grid(row=row, column=1, sticky="ew", pady=5)
        self.server_preset_combo.bind(
            "<<ComboboxSelected>>",
            self._on_server_preset_changed,
        )
        row += 1

        ttk.Label(self.config_tab, text="Server URL").grid(
            row=row, column=0, sticky="w", padx=(0, 10), pady=5
        )

        self.server_url_entry = ttk.Entry(
            self.config_tab,
            textvariable=self.server_url_var,
        )
        self.server_url_entry.grid(
            row=row,
            column=1,
            columnspan=2,
            sticky="ew",
            pady=5,
        )
        row += 1

        ttk.Label(
            self.config_tab,
            text=(
                "ntfy.sh is selected by default. On startup, the client requests "
                "up to 48 hours of cached encrypted history, subject to the server's "
                "actual retention period."
            ),
            wraplength=680,
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(2, 8))
        row += 1

        ttk.Separator(self.config_tab).grid(
            row=row, column=0, columnspan=3, sticky="ew", pady=12
        )
        row += 1

        ttk.Label(
            self.config_tab,
            text="Identity and appearance",
            font=("Segoe UI", 10, "bold"),
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(0, 8))
        row += 1

        ttk.Label(self.config_tab, text="Username").grid(
            row=row, column=0, sticky="w", padx=(0, 10), pady=5
        )

        ttk.Entry(
            self.config_tab,
            textvariable=self.username_var,
        ).grid(
            row=row,
            column=1,
            columnspan=2,
            sticky="ew",
            pady=5,
        )
        row += 1

        ttk.Label(self.config_tab, text="Username color").grid(
            row=row, column=0, sticky="w", padx=(0, 10), pady=5
        )

        self.color_preview = tk.Label(
            self.config_tab,
            text="   ",
            relief="sunken",
            borderwidth=1,
        )
        self.color_preview.grid(row=row, column=1, sticky="w", pady=5)

        ttk.Button(
            self.config_tab,
            text="Choose color...",
            command=self._choose_color,
        ).grid(row=row, column=2, sticky="w", padx=(8, 0), pady=5)
        row += 1

        ttk.Separator(self.config_tab).grid(
            row=row, column=0, columnspan=3, sticky="ew", pady=12
        )
        row += 1

        ttk.Label(
            self.config_tab,
            text="Encryption",
            font=("Segoe UI", 10, "bold"),
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(0, 8))
        row += 1

        ttk.Label(self.config_tab, text="Chatroom key").grid(
            row=row, column=0, sticky="w", padx=(0, 10), pady=5
        )

        self.key_entry = ttk.Entry(
            self.config_tab,
            textvariable=self.encryption_key_var,
            show="•",
        )
        self.key_entry.grid(row=row, column=1, sticky="ew", pady=5)

        ttk.Checkbutton(
            self.config_tab,
            text="Show",
            variable=self.show_key_var,
            command=self._toggle_key_visibility,
        ).grid(row=row, column=2, sticky="w", padx=(8, 0), pady=5)
        row += 1

        ttk.Label(
            self.config_tab,
            text=(
                "The key determines both the encryption key and an opaque ntfy topic. "
                "Everyone in the same room must enter exactly the same value."
            ),
            wraplength=680,
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(2, 8))
        row += 1

        ttk.Checkbutton(
            self.config_tab,
            text="Play a chime when another user sends a message",
            variable=self.chime_var,
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=5)
        row += 1

        ttk.Separator(self.config_tab).grid(
            row=row, column=0, columnspan=3, sticky="ew", pady=12
        )
        row += 1

        button_frame = ttk.Frame(self.config_tab)
        button_frame.grid(row=row, column=0, columnspan=3, sticky="ew")
        button_frame.columnconfigure(0, weight=1)

        ttk.Button(
            button_frame,
            text="Test connection",
            command=self._test_connection,
        ).grid(row=0, column=1, padx=(0, 8))

        ttk.Button(
            button_frame,
            text="Save and reconnect",
            command=self._save_and_reconnect,
        ).grid(row=0, column=2)
        row += 1

        ttk.Label(
            self.config_tab,
            text=(
                "Settings, server message cursors, local history, and the hidden "
                "client identity are saved in a Windows DPAPI-encrypted file tied "
                "to the current Windows user."
            ),
            wraplength=680,
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(14, 0))

    def _apply_server_preset_state(self) -> None:
        preset = self.server_preset_var.get()
        self.server_url_entry.configure(
            state="normal" if preset == "Custom ntfy server" else "readonly"
        )

    def _on_server_preset_changed(self, _event: Any = None) -> None:
        preset = self.server_preset_var.get()
        preset_url = SERVER_PRESETS.get(preset, "")

        if preset != "Custom ntfy server":
            self.server_url_var.set(preset_url)

        self._apply_server_preset_state()

    def _choose_color(self) -> None:
        selected = colorchooser.askcolor(
            color=self.color_var.get(),
            title="Choose username color",
            parent=self.root,
        )

        if selected and selected[1]:
            self.color_var.set(selected[1])
            self._update_color_preview()

    def _update_color_preview(self) -> None:
        color = self.color_var.get().strip() or "#4ea1ff"

        try:
            self.color_preview.configure(background=color)
        except tk.TclError:
            self.color_var.set("#4ea1ff")
            self.color_preview.configure(background="#4ea1ff")

    def _toggle_key_visibility(self) -> None:
        self.key_entry.configure(
            show="" if self.show_key_var.get() else "•"
        )

    def _validate_current_settings(self) -> tuple[str, str, str, str]:
        server_url = normalize_server_url(self.server_url_var.get())
        username = self.username_var.get().strip()
        color = self.color_var.get().strip()
        encryption_key = self.encryption_key_var.get()

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

        try:
            self.root.winfo_rgb(color)
        except tk.TclError as exc:
            raise ValueError("The username color is invalid.") from exc

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
        username = self.username_var.get().strip() or "User"
        color = self.color_var.get().strip() or "#315f8c"

        return {
            "v": APP_VERSION,
            "i": self.draft_message_id,
            "c": self.config_data["client_id"],
            "u": username[:32],
            "k": color,
            "t": int(time.time()),
            "m": text,
        }

    def _on_message_entry_modified(
        self,
        _event: tk.Event | None = None,
    ) -> None:
        if not self.message_entry.edit_modified():
            return

        self.message_entry.edit_modified(False)
        self._schedule_composer_update()

    def _on_message_entry_activity(
        self,
        _event: tk.Event | None = None,
    ) -> None:
        self._schedule_composer_update()

    def _schedule_composer_update(self) -> None:
        if self.message_resize_job is not None:
            try:
                self.root.after_cancel(self.message_resize_job)
            except tk.TclError:
                pass

        self.message_resize_job = self.root.after_idle(
            self._resize_message_entry
        )

        if self.message_size_check_job is not None:
            try:
                self.root.after_cancel(self.message_size_check_job)
            except tk.TclError:
                pass

        self.message_size_check_pending = True
        self.message_size_check_job = self.root.after(
            MESSAGE_SIZE_DEBOUNCE_MS,
            self._run_message_size_check,
        )

        # A stale over-limit result should not block a click after the user
        # shortens the message. Send performs its own immediate exact check.
        self.send_button.configure(state="normal")
        self._draw_message_size_bar()

    def _resize_message_entry(self) -> None:
        self.message_resize_job = None

        try:
            self.message_entry.update_idletasks()
            counted = self.message_entry.count(
                "1.0",
                "end",
                "displaylines",
            )
            display_lines = int(counted[0]) if counted else 1
        except (tk.TclError, TypeError, ValueError):
            text = self.message_entry.get("1.0", "end-1c")
            display_lines = max(1, text.count("\n") + 1)

        visible_lines = max(
            MESSAGE_ENTRY_MIN_LINES,
            min(MESSAGE_ENTRY_MAX_LINES, display_lines),
        )

        if int(self.message_entry.cget("height")) != visible_lines:
            self.message_entry.configure(height=visible_lines)
            self.message_entry.update_idletasks()

        if display_lines > MESSAGE_ENTRY_MAX_LINES:
            self.message_entry.see("end-1c")
            self.message_entry.yview_moveto(1.0)

    def _cancel_pending_message_size_check(self) -> None:
        if self.message_size_check_job is not None:
            try:
                self.root.after_cancel(self.message_size_check_job)
            except tk.TclError:
                pass

        self.message_size_check_job = None
        self.message_size_check_pending = False

    def _run_message_size_check(self) -> None:
        self.message_size_check_job = None
        self.message_size_check_pending = False
        text = self.message_entry.get("1.0", "end-1c")

        try:
            draft_message = self._build_draft_message(text)
            self.current_estimated_packet_size = estimate_opaque_packet_size(
                draft_message
            )
        except Exception:
            self.current_estimated_packet_size = NTFY_MAX_BODY_BYTES

        self._draw_message_size_bar()

    def _draw_message_size_bar(self) -> None:
        if not hasattr(self, "message_size_canvas"):
            return

        width = max(1, self.message_size_canvas.winfo_width())
        height = max(1, self.message_size_canvas.winfo_height())
        packet_size = max(0, int(self.current_estimated_packet_size))

        fraction = min(
            1.0,
            packet_size / float(NTFY_MAX_BODY_BYTES),
        )
        fill_width = int(width * fraction)
        at_or_over_limit = packet_size >= NTFY_MAX_BODY_BYTES

        self.message_size_canvas.coords(
            self.message_size_fill,
            0,
            0,
            fill_width,
            height,
        )
        self.message_size_canvas.itemconfigure(
            self.message_size_fill,
            fill="#303030" if at_or_over_limit else "#b8b8b8",
        )
        self.message_size_canvas.coords(
            self.message_size_label,
            width / 2,
            height / 2,
        )
        if self.message_size_check_pending:
            label_text = "Calculating..."
            label_color = "#202020"
        else:
            label_text = (
                f"{packet_size / 1024.0:.1f} KB / "
                f"{NTFY_MAX_BODY_BYTES / 1024.0:.1f} KB"
            )
            label_color = "#ffffff" if at_or_over_limit else "#202020"

        self.message_size_canvas.itemconfigure(
            self.message_size_label,
            text=label_text,
            fill=label_color,
        )

        if not self.message_size_check_pending and at_or_over_limit:
            self.send_button.configure(state="disabled")
        else:
            self.send_button.configure(state="normal")

    def _handle_enter(self, event: tk.Event) -> str | None:
        if event.state & 0x0001:
            return None

        self._send_current_message()
        return "break"

    def _send_current_message(self) -> None:
        text = self.message_entry.get("1.0", "end-1c")

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
            self.current_estimated_packet_size = estimated_size
            self._draw_message_size_bar()
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

        self.message_entry.delete("1.0", "end")
        self.message_entry.edit_modified(False)
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
        finally:
            if not self.stop_event.is_set():
                self.root.after(100, self._process_ui_queue)

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
        try:
            current_view = self.chat_display.yview()
            top_fraction = current_view[0] if current_view else 0.0
        except tk.TclError:
            top_fraction = 0.0

        self._render_message_log(scroll_to_bottom=False)

        try:
            self.chat_display.yview_moveto(top_fraction)
        except tk.TclError:
            pass

    def _on_chat_display_configure(
        self,
        _event: tk.Event | None = None,
    ) -> None:
        muted_ids = self._room_preference_ids("muted_users")
        collapsed_ids = self._room_preference_ids("collapsed_messages")

        if not muted_ids and not collapsed_ids:
            return

        if self.chat_render_job is not None:
            try:
                self.root.after_cancel(self.chat_render_job)
            except tk.TclError:
                pass

        self.chat_render_job = self.root.after(
            120,
            self._finish_chat_resize_render,
        )

    def _finish_chat_resize_render(self) -> None:
        self.chat_render_job = None
        self._rerender_preserving_scroll()

    def _blend_toward_chat_background(
        self,
        color: str,
        amount: float = 0.70,
    ) -> str:
        amount = max(0.0, min(1.0, amount))

        try:
            foreground_rgb = self.root.winfo_rgb(color)
            background_rgb = self.root.winfo_rgb(
                self.chat_display.cget("background")
            )
        except tk.TclError:
            return "#a0a0a0"

        blended = []

        for foreground, background in zip(
            foreground_rgb,
            background_rgb,
        ):
            value_16bit = round(
                foreground * (1.0 - amount)
                + background * amount
            )
            blended.append(max(0, min(255, value_16bit // 257)))

        return "#{:02x}{:02x}{:02x}".format(*blended)

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

        try:
            body_font = tkfont.Font(font=self.chat_display.cget("font"))
            username_font = tkfont.Font(
                family="Segoe UI",
                size=10,
                weight="bold",
            )

            widget_width = max(1, self.chat_display.winfo_width())
            prefix_width = (
                username_font.measure(username)
                + body_font.measure(status_suffix + ": ")
                + 52
            )
            available_width = max(0, widget_width - prefix_width)

            if body_font.measure(normalized + ending) <= available_width:
                return normalized + ending

            if body_font.measure("[...]") > available_width:
                return "[...]"

            low = 0
            high = len(normalized)

            while low < high:
                midpoint = (low + high + 1) // 2
                candidate = normalized[:midpoint].rstrip() + ending

                if body_font.measure(candidate) <= available_width:
                    low = midpoint
                else:
                    high = midpoint - 1

            if low <= 0:
                return "[...]"

            return normalized[:low].rstrip() + ending

        except tk.TclError:
            fallback = normalized[:80].rstrip()
            return (fallback + ending) if fallback else "[...]"

    def _show_username_context_menu(
        self,
        event: tk.Event,
        item: dict[str, Any],
    ) -> str:
        self._hide_username_tooltip()

        message = item["message"]
        client_id = str(message["c"])
        message_id = str(message["i"])
        is_local = bool(item.get("is_local", False))

        muted_ids = self._room_preference_ids("muted_users")
        collapsed_ids = self._room_preference_ids("collapsed_messages")

        is_muted = client_id in muted_ids
        is_manually_collapsed = message_id in collapsed_ids

        if self.username_context_menu is not None:
            try:
                self.username_context_menu.destroy()
            except tk.TclError:
                pass

        menu = tk.Menu(self.root, tearoff=False)
        self.username_context_menu = menu

        if is_local:
            menu.add_command(
                label="Mute User",
                state="disabled",
            )
        else:
            menu.add_command(
                label="Unmute User" if is_muted else "Mute User",
                command=lambda: self._set_user_muted(
                    client_id,
                    not is_muted,
                ),
            )

        if is_muted:
            menu.add_command(
                label="Expand Message",
                state="disabled",
            )
        else:
            menu.add_command(
                label=(
                    "Expand Message"
                    if is_manually_collapsed
                    else "Collapse Message"
                ),
                command=lambda: self._set_message_collapsed(
                    message_id,
                    not is_manually_collapsed,
                ),
            )

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                menu.grab_release()
            except tk.TclError:
                pass

        return "break"

    def _render_message_log(self, *, scroll_to_bottom: bool) -> None:
        self._hide_username_tooltip()
        self.chat_display.configure(state="normal")
        self.chat_display.delete("1.0", "end")

        muted_ids = self._room_preference_ids("muted_users")
        collapsed_ids = self._room_preference_ids("collapsed_messages")
        previous_timestamp: int | None = None

        for item in self.message_log:
            current_timestamp = self._display_timestamp_for_item(item)

            if (
                previous_timestamp is not None
                and current_timestamp - previous_timestamp
                >= GAP_SEPARATOR_SECONDS
            ):
                gap_seconds = current_timestamp - previous_timestamp
                gap_hours = max(6, int((gap_seconds / 3600.0) + 0.5))
                self.chat_display.insert(
                    "end",
                    f"————— {gap_hours} hours later —————\n",
                    "gap",
                )

            self._insert_message_item(
                item,
                muted_ids=muted_ids,
                collapsed_ids=collapsed_ids,
            )
            previous_timestamp = current_timestamp

        self.chat_display.configure(state="disabled")

        if scroll_to_bottom:
            self.chat_display.see("end")

    def _insert_message_item(
        self,
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

        user_tag = (
            "user_"
            + hashlib.sha1(
                (client_id + original_color + str(is_muted)).encode("utf-8")
            ).hexdigest()[:12]
        )
        hover_tag = (
            "hover_"
            + hashlib.sha1(
                message_id.encode("utf-8")
            ).hexdigest()[:16]
        )
        message_tag = (
            "msg_"
            + hashlib.sha1(
                message_id.encode("utf-8")
            ).hexdigest()[:16]
        )
        body_tag = "muted_message" if is_muted else "message"
        suffix_tag = "muted_suffix" if is_muted else "timestamp"

        username_color = (
            self._blend_toward_chat_background(original_color)
            if is_muted
            else original_color
        )

        try:
            self.chat_display.tag_configure(
                user_tag,
                foreground=username_color,
                font=("Segoe UI", 10, "bold"),
            )
        except tk.TclError:
            self.chat_display.tag_configure(
                user_tag,
                foreground=(
                    self._blend_toward_chat_background("#4ea1ff")
                    if is_muted
                    else "#4ea1ff"
                ),
                font=("Segoe UI", 10, "bold"),
            )

        self.chat_display.tag_configure(
            "muted_message",
            foreground=self._blend_toward_chat_background("#202020"),
        )
        self.chat_display.tag_configure(
            "muted_suffix",
            foreground=self._blend_toward_chat_background("#777777"),
        )

        tooltip_text = (
            f"{self._format_hover_timestamp(timestamp)}\n"
            f"Unique ID: {unique_id_preview}"
        )
        self.chat_display.tag_bind(
            hover_tag,
            "<Enter>",
            lambda event, value=tooltip_text: self._show_username_tooltip(
                event,
                value,
            ),
        )
        self.chat_display.tag_bind(
            hover_tag,
            "<Leave>",
            lambda _event: self._hide_username_tooltip(),
        )
        self.chat_display.tag_bind(
            hover_tag,
            "<Button-3>",
            lambda event, value=item: self._show_username_context_menu(
                event,
                value,
            ),
        )

        start_index = self.chat_display.index("end-1c")
        self.chat_display.insert(
            "end",
            username,
            (user_tag, hover_tag, message_tag),
        )

        status_suffix = ""

        if item["is_local"]:
            status_suffix = " (you)"
        elif is_muted:
            status_suffix = " (muted)"

        if status_suffix:
            self.chat_display.insert(
                "end",
                status_suffix,
                (suffix_tag, message_tag),
            )

        self.chat_display.insert(
            "end",
            ": ",
            (body_tag, message_tag),
        )

        display_text = (
            self._collapsed_message_preview(
                username,
                status_suffix,
                text,
            )
            if is_collapsed
            else text
        )

        self.chat_display.insert(
            "end",
            display_text + "\n",
            (body_tag, message_tag),
        )

        if item["warning"] and not is_collapsed:
            self.chat_display.insert(
                "end",
                item["warning"] + "\n",
                ("warning", message_tag),
            )

        end_index = self.chat_display.index("end-1c")
        self.chat_display.tag_add(message_tag, start_index, end_index)

    def _show_username_tooltip(
        self,
        event: tk.Event,
        text: str,
    ) -> None:
        self._hide_username_tooltip(reset_cursor=False)

        try:
            self.chat_display.configure(cursor="hand2")
        except tk.TclError:
            pass

        tooltip = tk.Toplevel(self.root)
        tooltip.wm_overrideredirect(True)
        tooltip.wm_attributes("-topmost", True)

        label = tk.Label(
            tooltip,
            text=text,
            justify="left",
            background="#fffbe8",
            foreground="#202020",
            relief="solid",
            borderwidth=1,
            padx=7,
            pady=5,
            font=("Segoe UI", 9),
        )
        label.pack()

        x = int(getattr(event, "x_root", self.root.winfo_pointerx())) + 12
        y = int(getattr(event, "y_root", self.root.winfo_pointery())) + 16
        tooltip.wm_geometry(f"+{x}+{y}")
        self.tooltip_window = tooltip

    def _hide_username_tooltip(self, reset_cursor: bool = True) -> None:
        if self.tooltip_window is not None:
            try:
                self.tooltip_window.destroy()
            except tk.TclError:
                pass
            self.tooltip_window = None

        if reset_cursor:
            try:
                self.chat_display.configure(cursor="xterm")
            except tk.TclError:
                pass

    def _append_system_message(
        self,
        text: str,
        warning: bool = False,
    ) -> None:
        tag = "warning" if warning else "system"
        display_time = time.strftime("%H:%M:%S")

        self.chat_display.configure(state="normal")
        self.chat_display.insert(
            "end",
            f"[{display_time}] ",
            "timestamp",
        )
        self.chat_display.insert("end", text + "\n", tag)
        self.chat_display.configure(state="disabled")
        self.chat_display.see("end")

    def _clear_visible_room(self) -> None:
        self._hide_username_tooltip()

        if self.username_context_menu is not None:
            try:
                self.username_context_menu.destroy()
            except tk.TclError:
                pass
            self.username_context_menu = None

        self.seen_client_message_ids.clear()
        self.seen_ntfy_message_ids.clear()
        self.message_log.clear()

        self.chat_display.configure(state="normal")
        self.chat_display.delete("1.0", "end")
        self.chat_display.configure(state="disabled")

    def _play_chime(self) -> None:
        if winsound is None:
            return

        try:
            winsound.MessageBeep(winsound.MB_OK)
        except Exception:
            pass

    def _on_close(self) -> None:
        self._hide_username_tooltip()

        if self.username_context_menu is not None:
            try:
                self.username_context_menu.destroy()
            except tk.TclError:
                pass
            self.username_context_menu = None

        try:
            self._copy_ui_to_config()
            save_config(self.config_data)
        except Exception:
            pass

        self.stop_event.set()
        self.root.destroy()


def _write_crash_log(error_text: str) -> Path | None:
    try:
        APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
        crash_path = APP_DATA_DIR / "crash.log"
        crash_path.write_text(error_text, encoding="utf-8")
        return crash_path
    except Exception:
        return None


def main() -> None:
    if os.name != "nt":
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Windows required",
            "This version uses Windows DPAPI and must be run on Windows.",
        )
        root.destroy()
        return

    root = tk.Tk()

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

    root.report_callback_exception = report_callback_exception

    try:
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except Exception:
        pass

    try:
        EncryptedChatClient(root)
    except Exception:
        error_text = traceback.format_exc()
        crash_path = _write_crash_log(error_text)
        root.withdraw()
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
        root.destroy()
        return

    root.mainloop()


if __name__ == "__main__":
    main()
