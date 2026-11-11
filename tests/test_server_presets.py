import copy
import unittest
from unittest import mock

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from test_security import SPRITELINK as S
import test_chatroom_preferences

LOAD_CONFIG = S.load_config


class ServerPresetConfigTests(unittest.TestCase):
    def test_saved_server_choices_migrate_without_losing_custom_urls(self):
        cases = (
            ("ntfy.sh (public)", "https://ntfy.sh", S.DEFAULT_SERVER_PRESET, "https://ntfy.sh"),
            ("Local ntfy server", "http://127.0.0.1:8080", "Custom ntfy server", "http://127.0.0.1:8080"),
            ("Local ntfy server", "http://192.168.1.5:8080", "Custom ntfy server", "http://192.168.1.5:8080"),
            ("Custom ntfy server", "https://example.org/ntfy", "Custom ntfy server", "https://example.org/ntfy"),
            ("ntfy.envs.net", "https://ntfy.sh", "ntfy.envs.net", "https://ntfy.envs.net"),
        )
        for preset, url, expected_preset, expected_url in cases:
            with self.subTest(preset=preset, url=url):
                config = S.default_config()
                config.update(server_preset=preset, server_url=url)
                path = mock.Mock()
                path.exists.return_value = True
                path.read_bytes.return_value = b"settings"
                with mock.patch.object(S, "CONFIG_PATH", path), \
                     mock.patch.object(S, "_load_dpapi_config", return_value=copy.deepcopy(config)):
                    loaded = LOAD_CONFIG()
                self.assertEqual(loaded["server_preset"], expected_preset)
                self.assertEqual(loaded["server_url"], expected_url)


class ServerPresetControlTests(unittest.TestCase):
    setUp = test_chatroom_preferences.ChatroomPreferenceTests.setUp
    tearDown = test_chatroom_preferences.ChatroomPreferenceTests.tearDown

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_presets_set_fixed_urls_and_custom_allows_local_urls(self):
        c = self.client
        c._ensure_popup("config")
        self.assertEqual([c.server_preset_combo.itemText(i) for i in range(c.server_preset_combo.count())], [
            "ntfy.sh (default)", "ntfy.envs.net", "ntfy.adminforge.de", "ntfy.hostux.net", "Custom ntfy server",
        ])
        for preset, url in S.SERVER_PRESETS.items():
            c.server_preset_combo.setCurrentText(preset)
            custom = preset == "Custom ntfy server"
            self.assertEqual(c.server_url_entry.isEnabled(), custom)
            self.assertEqual(c.server_url_entry.isReadOnly(), not custom)
            if not custom:
                self.assertEqual(c.server_url_entry.text(), url)
                self.assertEqual(c.server_url_var.get(), url)
        c.server_url_entry.setText("http://127.0.0.1:8080")
        self.assertEqual(c._validate_current_settings(), "http://127.0.0.1:8080")
        with mock.patch.object(S, "set_start_with_windows", return_value=True):
            self.assertTrue(c._save_settings())
        self.assertEqual(self.config["server_preset"], "Custom ntfy server")
        self.assertEqual(self.config["server_url"], "http://127.0.0.1:8080")

    def test_disabled_url_looks_different_in_each_theme_and_custom_is_editable(self):
        c = self.client
        c._ensure_popup("config")
        c.advanced_config_content.show()
        self.root.resize(850, 650)
        self.root.show()
        c._on_config_toggled(True)
        for theme in S.THEMES:
            with self.subTest(theme=theme):
                c._on_theme_changed(theme)
                c.server_preset_combo.setCurrentText(S.DEFAULT_SERVER_PRESET)
                QTest.qWait(10)
                disabled = c.server_url_entry.grab().toImage()
                c.server_preset_combo.setCurrentText("Custom ntfy server")
                QTest.qWait(10)
                enabled = c.server_url_entry.grab().toImage()
                self.assertNotEqual(disabled, enabled)
                c.server_url_entry.setFocus()
                c.server_url_entry.selectAll()
                QTest.keyClicks(c.server_url_entry, "http://localhost:8080")
                self.assertEqual(c.server_url_var.get(), "http://localhost:8080")


if __name__ == "__main__":
    unittest.main()
