import base64
import random
import unittest
import zlib
from cryptography.exceptions import InvalidSignature

from spritelink_image_codec import IMAGE_SIZES, decode_drawing, encode_drawing
from test_security import SPRITELINK as S


class DrawingMessageTests(unittest.TestCase):
    def test_packed_and_compressed_images_round_trip(self):
        rng = random.Random(56)
        for size, (width, height) in enumerate(IMAGE_SIZES):
            for compressed in (False, True):
                with self.subTest(size=size, compressed=compressed):
                    count = width * height
                    pixels = bytes([2]) * count if compressed else bytes(
                        rng.randrange(8) for _ in range(count)
                    )
                    encoded = encode_drawing(size, pixels)
                    self.assertEqual(bool(base64.b64decode(encoded)[0] & 4), compressed)
                    self.assertEqual(decode_drawing(encoded), (size, pixels, 0))
                    for palette in (1, 2, 3):
                        colored = encode_drawing(size, pixels, palette)
                        self.assertEqual(len(colored), len(encoded))
                        self.assertEqual(decode_drawing(colored), (size, pixels, palette))

    def test_invalid_packed_or_compressed_images_are_rejected(self):
        compressed = zlib.compress(bytes(2025))
        for data in (
            b"\x10" + bytes(2024),
            b"\x13" + bytes(2025),
            b"\x14" + compressed[:-1],
            b"\x14" + compressed + b"extra",
            b"\x14" + zlib.compress(bytes(100_000)),
        ):
            with self.subTest(header=data[0], size=len(data)):
                with self.assertRaises(ValueError):
                    decode_drawing(base64.b64encode(data).decode("ascii"))

    def test_drawing_is_authenticated_and_cannot_include_text(self):
        private_key = S.generate_identity_private_key()
        room_key = "drawing test room"
        drawing = encode_drawing(0, bytes(5400))
        message = {
            "v": S.APP_VERSION, "i": "test-id", "u": "Artist",
            "k": "#ffffff", "t": 1000, "m": "", "drawing": drawing,
        }
        S.sign_message_identity(message, room_key, private_key)
        packet = S.make_opaque_packet(message, room_key)
        self.assertEqual(len(packet), S.estimate_opaque_packet_size(message))
        received = S.open_opaque_packet(packet, room_key)
        S.EncryptedChatClient._validate_decrypted_message(received, room_key)
        self.assertEqual(received["drawing"], drawing)

        received["drawing"] = encode_drawing(0, bytes([1]) * 5400)
        with self.assertRaises(InvalidSignature):
            S.EncryptedChatClient._validate_decrypted_message(received, room_key)

        received["m"] = "caption"
        S.sign_message_identity(received, room_key, private_key)
        with self.assertRaisesRegex(ValueError, "cannot contain message text"):
            S.EncryptedChatClient._validate_decrypted_message(received, room_key)
