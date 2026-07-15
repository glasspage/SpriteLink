from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
