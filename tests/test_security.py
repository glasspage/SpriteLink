from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
import copy
import sys
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "SpriteLink.pyw"
LOADER = SourceFileLoader("spritelink_security_test_module", str(MODULE_PATH))
SPEC = spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
SPRITELINK = module_from_spec(SPEC)
sys.modules[SPEC.name] = SPRITELINK
LOADER.exec_module(SPRITELINK)


class ImageTrustTests(unittest.TestCase):
    def test_requested_and_common_cdn_hosts_are_trusted(self) -> None:
        trusted_urls = (
            "https://cdn.discordapp.com/attachments/1/2/example.png",
            "https://upload.wikimedia.org/wikipedia/commons/a/a0/example.jpg",
            "https://cdn.donmai.us/original/example.webp",
            "https://cdn.donmai.moe/original/example.webp",
            "https://pbs.twimg.com/media/example?format=jpg&name=large",
            "https://i.imgur.com/example.png",
            "https://images.unsplash.com/photo-example",
            "https://cdn.bsky.app/img/feed_fullsize/plain/example",
            "https://media.tenor.com/example/tenor.gif",
            "https://static.wikia.nocookie.net/example/image.png",
            "https://raw.githubusercontent.com/owner/repo/main/example.png",
        )
        for url in trusted_urls:
            with self.subTest(url=url):
                self.assertTrue(SPRITELINK.is_direct_image_url(url))
                self.assertTrue(SPRITELINK.is_trusted_image_url(url))

    def test_trusted_hosts_require_https_and_exact_host_matches(self) -> None:
        untrusted_urls = (
            "http://cdn.discordapp.com/attachments/1/2/example.png",
            "https://cdn.discordapp.com.evil.example/example.png",
            "https://evilcdn.discordapp.com/example.png",
            "https://cdn.donmai.evil.example/example.png",
            "https://example.com/example.png",
        )
        for url in untrusted_urls:
            with self.subTest(url=url):
                self.assertFalse(SPRITELINK.is_trusted_image_url(url))

    def test_sender_trust_overrides_domain_trust(self) -> None:
        url = "https://example.com/example.png"
        self.assertFalse(
            SPRITELINK.is_image_url_trusted_for_sender(
                url,
                "sender",
                False,
                set(),
            )
        )
        self.assertTrue(
            SPRITELINK.is_image_url_trusted_for_sender(
                url,
                "sender",
                False,
                {"sender"},
            )
        )
        self.assertTrue(
            SPRITELINK.is_image_url_trusted_for_sender(
                url,
                "sender",
                True,
                set(),
            )
        )


class RoomKeyTests(unittest.TestCase):
    def test_generated_room_keys_are_exactly_32_url_safe_characters(self) -> None:
        keys = {SPRITELINK.generate_chatroom_key() for _ in range(16)}
        self.assertEqual(len(keys), 16)
        for key in keys:
            self.assertEqual(len(key), 32)
            self.assertRegex(key, r"^[A-Za-z0-9_-]{32}$")


class PacketLimitTests(unittest.TestCase):
    PASSPHRASE = "correct horse battery staple with extra entropy"

    @staticmethod
    def _message(text: str) -> dict[str, object]:
        return {
            "v": SPRITELINK.APP_VERSION,
            "i": "message-id",
            "c": "client-id",
            "u": "User",
            "k": "#123456",
            "t": 1_700_000_000,
            "m": text,
        }

    def test_normal_packet_round_trip(self) -> None:
        message = self._message("hello")
        packet = SPRITELINK.make_opaque_packet(message, self.PASSPHRASE)
        self.assertEqual(
            SPRITELINK.open_opaque_packet(packet, self.PASSPHRASE),
            message,
        )

    def test_oversized_encoded_packet_is_rejected_before_decryption(self) -> None:
        packet = "A" * (SPRITELINK.MAX_ENCRYPTED_PACKET_CHARS + 1)
        with self.assertRaisesRegex(ValueError, "too large"):
            SPRITELINK.open_opaque_packet(packet, self.PASSPHRASE)

    def test_over_32_kib_decompressed_packet_is_rejected(self) -> None:
        message = self._message(
            "A" * (SPRITELINK.MAX_UNCOMPRESSED_MESSAGE_BYTES + 1)
        )
        packet = SPRITELINK.make_opaque_packet(message, self.PASSPHRASE)
        self.assertLessEqual(
            len(packet),
            SPRITELINK.MAX_ENCRYPTED_PACKET_CHARS,
        )
        with self.assertRaisesRegex(ValueError, "too large"):
            SPRITELINK.open_opaque_packet(packet, self.PASSPHRASE)


class SignedClientIdentityTests(unittest.TestCase):
    ROOM_ONE = "first room encryption key"
    ROOM_TWO = "second room encryption key"

    def setUp(self) -> None:
        self.private_key = SPRITELINK.generate_identity_private_key()

    @staticmethod
    def _message(text: str = "hello") -> dict[str, object]:
        return {
            "v": SPRITELINK.APP_VERSION,
            "i": "0123456789abcdef0123456789abcdef",
            "u": "User",
            "k": "#123456",
            "f": SPRITELINK.DEFAULT_MESSAGE_FONT,
            "o": SPRITELINK.DEFAULT_MESSAGE_TEXT_COLOR,
            "t": 1_700_000_000,
            "m": text,
        }

    def _signed_message(
        self,
        room_key: str | None = None,
    ) -> dict[str, object]:
        return SPRITELINK.sign_message_identity(
            self._message(),
            room_key or self.ROOM_ONE,
            self.private_key,
        )

    def test_one_installation_keeps_the_same_id_across_rooms(self) -> None:
        first_room_message = self._signed_message(self.ROOM_ONE)
        second_room_message = self._signed_message(self.ROOM_TWO)

        self.assertEqual(
            first_room_message["c"],
            second_room_message["c"],
        )
        self.assertRegex(first_room_message["c"], r"^[0-9a-f]{64}$")
        SPRITELINK.verify_message_identity(
            first_room_message,
            self.ROOM_ONE,
        )
        SPRITELINK.verify_message_identity(
            second_room_message,
            self.ROOM_TWO,
        )

    def test_visible_user_id_is_eight_uppercase_characters(self) -> None:
        message = self._signed_message()
        visible_id = SPRITELINK.visible_user_id(str(message["c"]))
        self.assertEqual(len(visible_id), 8)
        self.assertRegex(visible_id, r"^[0-9A-F]{8}$")

    def test_message_round_trip_requires_a_valid_signature(self) -> None:
        message = self._signed_message()
        packet = SPRITELINK.make_opaque_packet(message, self.ROOM_ONE)
        opened = SPRITELINK.open_opaque_packet(packet, self.ROOM_ONE)
        SPRITELINK.EncryptedChatClient._validate_decrypted_message(
            opened,
            self.ROOM_ONE,
        )
        self.assertEqual(opened, message)

    def test_changing_signed_message_content_is_rejected(self) -> None:
        message = self._signed_message()
        forged = copy.deepcopy(message)
        forged["m"] = "forged text"

        with self.assertRaises(Exception):
            SPRITELINK.verify_message_identity(forged, self.ROOM_ONE)

    def test_copying_a_client_id_to_another_key_is_rejected(self) -> None:
        message = self._signed_message()
        other_private_key = SPRITELINK.generate_identity_private_key()
        forged = SPRITELINK.sign_message_identity(
            self._message("forged text"),
            self.ROOM_ONE,
            other_private_key,
        )
        forged["c"] = message["c"]

        with self.assertRaisesRegex(ValueError, "does not match"):
            SPRITELINK.verify_message_identity(forged, self.ROOM_ONE)

    def test_signature_is_bound_to_its_chatroom(self) -> None:
        message = self._signed_message(self.ROOM_ONE)

        with self.assertRaises(Exception):
            SPRITELINK.verify_message_identity(message, self.ROOM_TWO)

    def test_unsigned_messages_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            SPRITELINK.EncryptedChatClient._validate_decrypted_message(
                self._message(),
                self.ROOM_ONE,
            )

    def test_generated_private_keys_are_unique_and_well_formed(self) -> None:
        keys = {
            SPRITELINK.generate_identity_private_key()
            for _ in range(16)
        }
        self.assertEqual(len(keys), 16)
        for key in keys:
            normalized = SPRITELINK.normalize_identity_private_key(key)
            self.assertEqual(normalized, key)
            self.assertEqual(
                len(SPRITELINK.identity_public_key_bytes(key)),
                SPRITELINK.ED25519_PUBLIC_KEY_BYTES,
            )


if __name__ == "__main__":
    unittest.main()
