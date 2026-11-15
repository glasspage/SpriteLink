"""Wire format for SpriteLink drawings."""
import base64
import binascii
import re
import zlib

EMOJI_SIZE = 3
MAX_MESSAGE_EMOJIS = 128
IMAGE_SIZES = ((90, 60), (60, 60), (60, 90), (16, 16))
IMAGE_PALETTES = (
    (
        "#000000", "#FFFFFF", "#E85154", "#F4CB4E",
        "#55B265", "#438FE0", "#975BCC", "#85878C",
    ),
    (
        "#000000", "#FFFFFF", "#242424", "#494949",
        "#6D6D6D", "#929292", "#B6B6B6", "#DBDBDB",
    ),
    ("#F3E7C9", "#8FC7E8", "#4F8FC7", "#8EBB5A",
     "#4F7A3E", "#A77A4D", "#6B4A2E", "#2F3E2F"),
    (
        "#61586B", "#FFFFFF", "#F69AAB", "#FFE29A",
        "#A0D9AA", "#98C7F6", "#C8A8EF", "#BDBBC6",
    ),
    (
        "#FFE7A0", "#F8B65E", "#E77D57", "#D3698B",
        "#A17CB4", "#60749F", "#394A78", "#272D4C",
    ),
)
PALETTE_HEADERS = (0x10, 0x18, 0x20, 0x28, 0x30)


def encode_drawing(size: int, pixels: bytes, palette: int = 0) -> str:
    width, height = IMAGE_SIZES[size]
    if len(pixels) != width * height or any(pixel > 7 for pixel in pixels):
        raise ValueError("Invalid drawing pixels.")
    if not 0 <= palette < len(IMAGE_PALETTES):
        raise ValueError("Invalid drawing palette.")
    packed = bytearray()
    for start in range(0, len(pixels), 8):
        word = 0
        for pixel in pixels[start:start + 8]:
            word = (word << 3) | pixel
        packed.extend(word.to_bytes(3, "big"))
    compressed = zlib.compress(packed, level=9)
    # Keep the original Basic and Grayscale header values.
    header = PALETTE_HEADERS[palette] | size
    if len(compressed) < len(packed):
        data = bytes([header | 4]) + compressed
    else:
        data = bytes([header]) + packed
    return base64.b64encode(data).decode("ascii")


def decode_drawing(encoded: str) -> tuple[int, bytes, int]:
    if not isinstance(encoded, str) or not 4 <= len(encoded) <= 2704:
        raise ValueError("Invalid drawing length.")
    try:
        data = base64.b64decode(encoded, validate=True)
        header = data[0]
    except (binascii.Error, IndexError, ValueError) as exc:
        raise ValueError("Invalid drawing encoding.") from exc
    size = header & 3
    palette_header = header & 0xF8
    if palette_header not in PALETTE_HEADERS or size >= len(IMAGE_SIZES):
        raise ValueError("Unsupported drawing format.")
    width, height = IMAGE_SIZES[size]
    expected = width * height * 3 // 8
    packed = data[1:]
    if header & 4:
        inflater = zlib.decompressobj()
        try:
            packed = inflater.decompress(packed, expected + 1)
        except zlib.error as exc:
            raise ValueError("Invalid compressed drawing.") from exc
        if not inflater.eof or inflater.unused_data:
            raise ValueError("Invalid compressed drawing.")
    if len(packed) != expected:
        raise ValueError("Invalid drawing dimensions.")
    pixels = bytearray()
    for start in range(0, expected, 3):
        word = int.from_bytes(packed[start:start + 3], "big")
        pixels.extend((word >> shift) & 7 for shift in range(21, -1, -3))
    return size, bytes(pixels), PALETTE_HEADERS.index(palette_header)


LEGACY_EMOJI_NAME_PATTERN = r"[^\W_]+(?:['’_-][^\W_]+)*(?: [^\W_]+(?:['’_-][^\W_]+)*)?"
EMOJI_NAME_PATTERN = r"_*[^\W_]+(?:(?:['’-]|_+)[^\W_]+)*_*"
EMOJI_MARKUP = re.compile(r"<sl-emoji:([A-Za-z0-9+/=]{4,132}):([^<>:\r\n]{1,32})>")


def valid_emoji_name(name: str) -> bool:
    return isinstance(name, str) and len(name) <= 32 and bool(
        re.fullmatch(EMOJI_NAME_PATTERN, name)
    )


def valid_emoji(name: str, encoded: str) -> bool:
    legacy_name = isinstance(name, str) and len(name) <= 32 and re.fullmatch(
        LEGACY_EMOJI_NAME_PATTERN, name
    )
    if not (valid_emoji_name(name) or legacy_name) or not isinstance(encoded, str) or len(encoded) > 132:
        return False
    try:
        size, _, _ = decode_drawing(encoded)
    except ValueError:
        return False
    return size == EMOJI_SIZE


def emoji_markup(name: str, encoded: str) -> str:
    return f"<sl-emoji:{encoded}:{name}>"


def pack_message_emojis(markup: str) -> tuple[str, list[list]]:
    """Use offsets into the signed message text, which carries readable names."""
    parts = []
    entries = []
    end = length = 0
    for match in EMOJI_MARKUP.finditer(markup):
        encoded, name = match.groups()
        if not valid_emoji(name, encoded):
            continue
        parts.append(markup[end:match.start()])
        length += match.start() - end
        entries.append([length, name, encoded])
        parts.append(f":{name}:")
        length += len(name) + 2
        end = match.end()
    parts.append(markup[end:])
    return "".join(parts), entries


def unpack_message_emojis(text: str, entries: list) -> str:
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_MESSAGE_EMOJIS:
        raise ValueError("Invalid message emojis.")
    parts = []
    end = 0
    for entry in entries:
        if not isinstance(entry, list) or len(entry) != 3:
            raise ValueError("Invalid message emoji.")
        start, name, encoded = entry
        if type(start) is not int or start < end or not valid_emoji(name, encoded):
            raise ValueError("Invalid message emoji.")
        if not text.startswith(f":{name}:", start):
            raise ValueError("Emoji name does not match message text.")
        parts.extend((text[end:start], emoji_markup(name, encoded)))
        end = start + len(name) + 2
    parts.append(text[end:])
    return "".join(parts)
