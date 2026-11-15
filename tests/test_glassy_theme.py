import ctypes
import os
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QWidget
from test_security import SPRITELINK


class GlassyThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.theme = "Glassy"
        self.client = SimpleNamespace(
            _is_glassy_theme=lambda: self.theme == "Glassy",
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
        self.app.setProperty("spritelinkGlassy", False)
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def apply(self, **kwargs):
        with mock.patch.object(SPRITELINK.os, "name", "nt"), mock.patch.object(
            SPRITELINK.ctypes, "windll", SimpleNamespace(dwmapi=self.dwm), create=True
        ):
            SPRITELINK.EncryptedChatClient._apply_window_titlebar_theme(
                self.client, self.window, **kwargs
            )

    def test_color_change_keeps_native_and_legacy_acrylic_active(self):
        for legacy in (False, True):
            self.fail_backdrop = legacy
            self.apply()
            self.assertTrue(self.window.property("spritelinkDesktopBlur"))
            self.calls.clear()
            self.dwm.DwmExtendFrameIntoClientArea.reset_mock()
            self.client._set_windows_legacy_blur.reset_mock()
            self.apply(color_only=True)
            self.assertEqual({attribute for attribute, _ in self.calls}, {34, 35, 36})
            self.dwm.DwmExtendFrameIntoClientArea.assert_not_called()
            self.client._set_windows_legacy_blur.assert_not_called()
            self.assertTrue(self.window.property("spritelinkDesktopBlur"))
            self.assertEqual(bool(self.window.property("spritelinkLegacyBlur")), legacy)

    def test_native_acrylic_enables_live_backdrop(self):
        self.apply()
        self.assertIn((38, 3), self.calls)
        self.assertTrue(self.window.property("spritelinkDesktopBlur"))
        self.client._set_windows_legacy_blur.assert_not_called()

    def test_dark_glass_retains_acrylic_with_a_dark_caption(self):
        self.client.theme_var = SPRITELINK.ValueModel("Glassy Dark")
        self.apply()
        self.assertIn((20, 1), self.calls)
        self.assertIn((35, self.client._windows_colorref("#252a30")), self.calls)
        self.assertIn((34, 0), self.calls)
        self.assertIn((38, 3), self.calls)
        self.assertIn((33, 2), self.calls)
        self.assertTrue(self.window.property("spritelinkDesktopBlur"))
        self.calls.clear()
        self.apply(color_only=True)
        self.assertIn((35, self.client._windows_colorref("#252a30")), self.calls)
        self.assertEqual({attribute for attribute, _ in self.calls}, {34, 35, 36})
        self.client.theme_var.set("Glassy")
        self.calls.clear()
        self.apply()
        self.assertIn((20, 0), self.calls)
        self.assertIn((35, 0xFFFFFFFF), self.calls)

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

    def test_fullscreen_video_disables_rounding_and_backdrop_for_every_theme(self):
        self.window.setProperty("spritelinkVideoWindow", True)
        self.window.setProperty("spritelinkVideoFullscreen", True)
        for theme in ("Glassy", "Modern", "Classic"):
            self.theme = theme
            self.calls.clear()
            self.apply()
            self.assertIn((33, 1), self.calls)
            self.assertIn((38, 1), self.calls)
            self.assertFalse(self.window.property("spritelinkDesktopBlur"))
        self.window.setProperty("spritelinkVideoFullscreen", False)
        self.theme = "Glassy"
        self.calls.clear()
        self.apply()
        self.assertIn((33, 2), self.calls)
        self.assertTrue(self.window.property("spritelinkDesktopBlur"))

    def test_message_stripes_are_translucent_only_for_glassy(self):
        # Other tests may have left Classic style enabled on the shared app.
        old_classic = self.app.property("spritelinkWindowsClassic")
        self.app.setProperty("spritelinkWindowsClassic", False)
        try:
            self.app.setProperty("spritelinkGlassy", True)
            for color in SPRITELINK.message_row_backgrounds():
                self.assertLess(SPRITELINK.QColor(color).alpha(), 255)
            self.app.setProperty("spritelinkGlassy", False)
            self.assertEqual(SPRITELINK.message_row_backgrounds(), SPRITELINK.MESSAGE_ROW_BACKGROUNDS)
        finally:
            self.app.setProperty("spritelinkWindowsClassic", old_classic)

    def test_windows_fullscreen_marker_calls_shell_and_balances_com_lifetime(self):
        from ctypes import wintypes
        marks, releases = [], []
        init = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p)(lambda pointer: 0)
        release = ctypes.CFUNCTYPE(ctypes.c_uint32, ctypes.c_void_p)(lambda pointer: releases.append(pointer) or 0)
        mark = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, wintypes.HWND, wintypes.BOOL)(
            lambda pointer, hwnd, fullscreen: marks.append((hwnd, bool(fullscreen))) or 0)
        table = (ctypes.c_void_p * 9)()
        table[2] = ctypes.cast(release, ctypes.c_void_p)
        table[3] = ctypes.cast(init, ctypes.c_void_p)
        table[8] = ctypes.cast(mark, ctypes.c_void_p)
        interface = (ctypes.c_void_p * 1)(ctypes.addressof(table))

        def create(clsid, outer, context, iid, result):
            self.assertEqual(ctypes.string_at(clsid, 16), SPRITELINK.uuid.UUID(
                "56fdf344-fd6d-11d0-958a-006097c9a090").bytes_le)
            self.assertEqual(ctypes.string_at(iid, 16), SPRITELINK.uuid.UUID(
                "602d4995-b13a-429b-a66e-1935e44f4317").bytes_le)
            ctypes.cast(result, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.addressof(interface)
            return 0

        ole32 = SimpleNamespace(CoInitializeEx=mock.Mock(return_value=0),
                                CoUninitialize=mock.Mock(), CoCreateInstance=mock.Mock(side_effect=create))
        with mock.patch.object(SPRITELINK.os, "name", "nt"), \
             mock.patch.object(SPRITELINK.ctypes, "windll", SimpleNamespace(ole32=ole32), create=True), \
             mock.patch.object(SPRITELINK.ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE, create=True):
            self.assertTrue(SPRITELINK.set_windows_fullscreen_window(1234, True))
            self.assertTrue(SPRITELINK.set_windows_fullscreen_window(1234, False))
            ole32.CoCreateInstance.side_effect = None
            ole32.CoCreateInstance.return_value = -1
            self.assertFalse(SPRITELINK.set_windows_fullscreen_window(1234, True))
        self.assertEqual(marks, [(1234, True), (1234, False)])
        self.assertEqual(len(releases), 2)
        self.assertEqual(ole32.CoUninitialize.call_count, 3)


class GlassyControlRenderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from PySide6.QtGui import QPalette
        from PySide6.QtWidgets import QStyleFactory
        self.previous_palette = QPalette(self.app.palette())
        self.previous_sheet = self.app.styleSheet()
        self.previous_glassy = self.app.property("spritelinkGlassy")
        self.previous_shadows = self.app.property("spritelinkTextShadows")
        self.app.setProperty("spritelinkGlassy", True)
        self.app.setProperty("spritelinkTextShadows", False)
        client = SimpleNamespace(_basic_palette=self.previous_palette)
        self.app.setPalette(SPRITELINK.EncryptedChatClient._glassy_palette(client))
        self.app.setStyle(SPRITELINK.TextShadowProxyStyle(QStyleFactory.create("Fusion")))
        self.app.setStyleSheet(SPRITELINK.GLASSY_STYLESHEET)
        self.window = QWidget()
        self.window.resize(300, 160)
        self.window.show()

    def tearDown(self):
        from PySide6.QtWidgets import QStyleFactory
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.app.setProperty("spritelinkGlassy", self.previous_glassy)
        self.app.setProperty("spritelinkTextShadows", self.previous_shadows)
        self.app.setStyle(QStyleFactory.create("Fusion"))
        self.app.setPalette(self.previous_palette)
        self.app.setStyleSheet(self.previous_sheet)

if __name__ == "__main__":
    unittest.main()
