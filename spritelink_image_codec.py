"""Wire format for SpriteLink drawings."""
import base64
import binascii
import zlib

IMAGE_SIZES = ((90, 60), (60, 60), (60, 90))
IMAGE_PALETTES = (
    (
        "#000000", "#FFFFFF", "#E85154", "#F4CB4E",
        "#55B265", "#438FE0", "#975BCC", "#85878C",
    ),
    (
        "#000000", "#FFFFFF", "#242424", "#494949",
        "#6D6D6D", "#929292", "#B6B6B6", "#DBDBDB",
    ),
)


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
    # The low nibble stores palette, compression flag, and canvas size.
    header = 0x10 | (palette << 3) | size
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
    if header & 0xF0 != 0x10 or size >= len(IMAGE_SIZES):
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
    return size, bytes(pixels), (header >> 3) & 1
