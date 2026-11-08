"""Portable, authenticated SpriteLink data archives.

A .sldata file is portable across computers. Password-protected archives
are encrypted with AES-GCM and Scrypt rather than local Windows DPAPI.
Password-free archives are explicitly unencrypted. Category names remain
visible so the import picker can disable absent categories.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import zlib
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt


ARCHIVE_VERSION = 1
ARCHIVE_MAGIC = "SpriteLink Data"
ARCHIVE_CATEGORIES = ("Chatrooms", "Config", "Emoji", "Identity")
CONFIG_EXPORT_FIELDS = (
    "server_preset", "server_url", "theme", "theme_colors", "text_shadows",
    "ui_size", "message_sound", "message_sound_volume",
    "custom_message_sound_path", "desktop_notifications",
    "chatroom_history_limit", "minimize_to_tray", "start_with_windows",
    "window_width", "window_height",
)
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_DECOMPRESSED_BYTES = 1024 * 1024 * 1024


def config_for_export(config: dict[str, Any]) -> dict[str, Any]:
    """Only global settings: never include keys, identity, or room overrides."""
    return {key: config[key] for key in CONFIG_EXPORT_FIELDS if key in config}


def _encode_bytes(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _decode_bytes(value: Any) -> bytes:
    if not isinstance(value, str):
        raise ValueError("Invalid .sldata encoding.")
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("Invalid .sldata encoding.") from exc


def _header_data(categories: list[str], salt: str, nonce: str) -> bytes:
    return json.dumps(
        {"version": ARCHIVE_VERSION, "categories": categories,
         "salt": salt, "nonce": nonce},
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def _derive_key(password: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(
        password.encode("utf-8")
    )


def inspect_archive(blob: bytes) -> tuple[dict[str, Any], tuple[str, ...]]:
    if len(blob) > MAX_ARCHIVE_BYTES:
        raise ValueError("The .sldata file is too large.")
    try:
        envelope = json.loads(blob)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("Not a valid .sldata file.") from exc
    if not isinstance(envelope, dict) or envelope.get("magic") != ARCHIVE_MAGIC:
        raise ValueError("Not a SpriteLink data file.")
    if envelope.get("version") != ARCHIVE_VERSION:
        raise ValueError("Unsupported .sldata format version.")
    cipher = envelope.get("cipher")
    if cipher not in ("AES-256-GCM/Scrypt", "none"):
        raise ValueError("Unsupported .sldata encryption.")
    categories = envelope.get("categories")
    if (
        not isinstance(categories, list) or not categories
        or any(category not in ARCHIVE_CATEGORIES for category in categories)
        or len(set(categories)) != len(categories)
    ):
        raise ValueError("Invalid .sldata contents.")
    raw_data = _decode_bytes(envelope.get("data"))
    if cipher == "AES-256-GCM/Scrypt":
        if (
            len(_decode_bytes(envelope.get("salt"))) != 16
            or len(_decode_bytes(envelope.get("nonce"))) != 12
            or len(raw_data) < 16
        ):
            raise ValueError("Invalid .sldata encryption data.")
    else:
        if (
            envelope.get("salt", "") != ""
            or envelope.get("nonce", "") != ""
            or not raw_data
            or not isinstance(envelope.get("checksum"), str)
            or len(envelope["checksum"]) != 64
        ):
            raise ValueError("Invalid password-free .sldata data.")
    return envelope, tuple(categories)


def create_archive(data: dict[str, Any], password: str = "") -> bytes:
    categories = [category for category in ARCHIVE_CATEGORIES if category in data]
    if not categories:
        raise ValueError("Select at least one data type.")
    if any(key not in ARCHIVE_CATEGORIES for key in data):
        raise ValueError("Invalid data type.")
    raw = json.dumps(
        {category: data[category] for category in categories},
        ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")
    if len(raw) > MAX_DECOMPRESSED_BYTES:
        raise ValueError("The selected data is too large to export.")
    compressed = zlib.compress(raw, level=6)
    if password:
        salt, nonce = os.urandom(16), os.urandom(12)
        salt_text, nonce_text = _encode_bytes(salt), _encode_bytes(nonce)
        encrypted = AESGCM(_derive_key(password, salt)).encrypt(
            nonce, compressed, _header_data(categories, salt_text, nonce_text),
        )
        archive = {
            "magic": ARCHIVE_MAGIC, "version": ARCHIVE_VERSION,
            "cipher": "AES-256-GCM/Scrypt", "categories": categories,
            "salt": salt_text, "nonce": nonce_text,
            "data": _encode_bytes(encrypted),
        }
    else:
        # Do not disguise a passwordless archive as cryptographically secure.
        # This checksum detects accidental corruption, not malicious edits.
        archive = {
            "magic": ARCHIVE_MAGIC, "version": ARCHIVE_VERSION,
            "cipher": "none", "categories": categories,
            "salt": "", "nonce": "",
            "data": _encode_bytes(compressed),
            "checksum": hashlib.sha256(
                _header_data(categories, "", "") + compressed
            ).hexdigest(),
        }
    packed = json.dumps(archive, separators=(",", ":")).encode("utf-8")
    if len(packed) > MAX_ARCHIVE_BYTES:
        raise ValueError("The selected data is too large to export.")
    return packed


def open_archive(blob: bytes, password: str = "") -> dict[str, Any]:
    envelope, categories = inspect_archive(blob)
    salt_text, nonce_text = envelope["salt"], envelope["nonce"]
    if envelope["cipher"] == "none":
        compressed = _decode_bytes(envelope["data"])
        expected = hashlib.sha256(
            _header_data(list(categories), "", "") + compressed
        ).hexdigest()
        if not hmac.compare_digest(expected, envelope["checksum"]):
            raise ValueError("The .sldata file is damaged.")
    else:
        salt, nonce = _decode_bytes(salt_text), _decode_bytes(nonce_text)
        try:
            compressed = AESGCM(_derive_key(password, salt)).decrypt(
                nonce, _decode_bytes(envelope["data"]),
                _header_data(list(categories), salt_text, nonce_text),
            )
        except InvalidTag as exc:
            raise ValueError("Incorrect password or damaged .sldata file.") from exc
    decompressor = zlib.decompressobj()
    raw = decompressor.decompress(compressed, MAX_DECOMPRESSED_BYTES + 1)
    if (
        len(raw) > MAX_DECOMPRESSED_BYTES
        or not decompressor.eof
        or decompressor.unused_data
    ):
        raise ValueError("The .sldata file is damaged or too large.")
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("The .sldata file contains invalid data.") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != set(categories)
    ):
        raise ValueError("The .sldata file contains invalid data.")
    return payload
