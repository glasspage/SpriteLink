import unittest

from spritelink_update import (
    UpdateError,
    parse_semantic_version,
    parse_sha256_checksum,
    release_from_github_payload,
    release_is_newer,
)


class VersionTests(unittest.TestCase):
    def test_parses_release_versions(self) -> None:
        self.assertEqual(parse_semantic_version("v1.2.3"), (1, 2, 3))
        self.assertEqual(parse_semantic_version("1.2.3"), (1, 2, 3))
        self.assertIsNone(parse_semantic_version("Development"))
        self.assertIsNone(parse_semantic_version("1.2"))
        self.assertIsNone(parse_semantic_version("01.2.3"))

    def test_compares_versions_numerically(self) -> None:
        self.assertTrue(release_is_newer("0.9.9", "0.10.0"))
        self.assertFalse(release_is_newer("1.0.0", "1.0.0"))
        self.assertFalse(release_is_newer("Development", "1.0.0"))


class ReleaseTests(unittest.TestCase):
    def test_selects_exact_versioned_assets(self) -> None:
        payload = {
            "tag_name": "v0.2.0",
            "html_url": "https://github.com/example/SpriteLink/releases/tag/v0.2.0",
            "body": "Release notes",
            "assets": [
                {
                    "name": "SpriteLink-Setup-0.2.0.exe",
                    "browser_download_url": "https://github.com/example/SpriteLink/releases/download/v0.2.0/SpriteLink-Setup-0.2.0.exe",
                },
                {
                    "name": "SpriteLink-Setup-0.2.0.exe.sha256",
                    "browser_download_url": "https://github.com/example/SpriteLink/releases/download/v0.2.0/SpriteLink-Setup-0.2.0.exe.sha256",
                },
            ],
        }
        release = release_from_github_payload(payload)
        self.assertEqual(release.version, "0.2.0")
        self.assertEqual(release.notes, "Release notes")

    def test_rejects_release_missing_checksum(self) -> None:
        payload = {
            "tag_name": "v0.2.0",
            "html_url": "https://github.com/example/SpriteLink/releases/tag/v0.2.0",
            "assets": [
                {
                    "name": "SpriteLink-Setup-0.2.0.exe",
                    "browser_download_url": "https://github.com/example/SpriteLink/releases/download/v0.2.0/SpriteLink-Setup-0.2.0.exe",
                },
            ],
        }
        with self.assertRaises(UpdateError):
            release_from_github_payload(payload)

    def test_checksum_must_match_expected_filename(self) -> None:
        checksum = "a" * 64
        text = f"{checksum}  SpriteLink-Setup-0.2.0.exe"
        self.assertEqual(
            parse_sha256_checksum(text, "SpriteLink-Setup-0.2.0.exe"),
            checksum,
        )
        with self.assertRaises(UpdateError):
            parse_sha256_checksum(text, "SpriteLink-Setup-0.3.0.exe")


if __name__ == "__main__":
    unittest.main()
