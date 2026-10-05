import ctypes
import os
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QWidget
from test_security import SPRITELINK


class GlassyPlusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.theme = "Glassy+"
        self.client = SimpleNamespace(
            _is_glassy_plus_theme=lambda: self.theme == "Glassy+",
            _is_glassy_theme=lambda: self.theme in ("Glassy", "Glassy+"),
            _is_windows_classic_theme=lambda: self.theme == "Classic",
            _windows_colorref=SPRITELINK.EncryptedChatClient._windows_colorref,
            _set_windows_legacy_blur=mock.Mock(return_value=True),
        )
        self.window = QWidget()
        self.calls = []

        def set_attribute(hwnd, attribute, data, size):
            value = ctypes.cast(data, ctypes.POINTER(ctypes.c_uint)).contents.value
            self.calls.append((attribute.value, value))
            return -1 if attribute.value == 38 and self.fail_backdrop else 0

        self.fail_backdrop = False
        self.dwm = SimpleNamespace(
            DwmSetWindowAttribute=mock.Mock(side_effect=set_attribute),
            DwmExtendFrameIntoClientArea=mock.Mock(return_value=0),
        )

    def tearDown(self):
        self.app.setProperty("spritelinkGlassyPlus", False)
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def apply(self):
        with mock.patch.object(SPRITELINK.os, "name", "nt"), mock.patch.object(
            SPRITELINK.ctypes, "windll", SimpleNamespace(dwmapi=self.dwm), create=True
        ):
            SPRITELINK.EncryptedChatClient._apply_window_titlebar_theme(
                self.client, self.window
            )

    def test_native_acrylic_enables_live_backdrop(self):
        self.apply()
        self.assertIn((38, 3), self.calls)
        self.assertTrue(self.window.property("spritelinkDesktopBlur"))
        self.client._set_windows_legacy_blur.assert_not_called()

    def test_older_windows_uses_legacy_blur_and_clears_it_on_theme_change(self):
        self.fail_backdrop = True
        self.apply()
        self.assertTrue(self.window.property("spritelinkDesktopBlur"))
        self.assertTrue(self.window.property("spritelinkLegacyBlur"))
        self.theme = "Classic"
        self.apply()
        self.assertFalse(self.window.property("spritelinkDesktopBlur"))
        self.assertFalse(self.window.property("spritelinkLegacyBlur"))
        self.assertFalse(self.client._set_windows_legacy_blur.call_args.args[1])
        self.assertIn((38, 1), self.calls)

    def test_failed_backends_keep_opaque_fallback(self):
        self.fail_backdrop = True
        self.client._set_windows_legacy_blur.return_value = False
        self.apply()
        self.assertFalse(self.window.property("spritelinkDesktopBlur"))

    def test_frame_failure_does_not_enable_blur(self):
        self.dwm.DwmExtendFrameIntoClientArea.return_value = -1
        self.apply()
        self.assertFalse(self.window.property("spritelinkDesktopBlur"))
        self.client._set_windows_legacy_blur.assert_not_called()

    def test_message_stripes_are_translucent_only_for_glassy_plus(self):
        self.app.setProperty("spritelinkGlassyPlus", True)
        for color in SPRITELINK.message_row_backgrounds():
            self.assertLess(SPRITELINK.QColor(color).alpha(), 255)
        self.app.setProperty("spritelinkGlassyPlus", False)
        self.assertEqual(SPRITELINK.message_row_backgrounds(), SPRITELINK.MESSAGE_ROW_BACKGROUNDS)


if __name__ == "__main__":
    unittest.main()
