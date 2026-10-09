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
        self.app.setProperty("spritelinkGlassy", True)
        for color in SPRITELINK.message_row_backgrounds():
            self.assertLess(SPRITELINK.QColor(color).alpha(), 255)
        self.app.setProperty("spritelinkGlassy", False)
        self.assertEqual(SPRITELINK.message_row_backgrounds(), SPRITELINK.MESSAGE_ROW_BACKGROUNDS)

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

    def test_pressed_buttons_inset_bevel_without_changing_rounded_bounds(self):
        from PySide6.QtCore import QPoint
        from PySide6.QtGui import QColor
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QPushButton
        self.window.setObjectName("buttonTestBackground")
        self.window.setStyleSheet("QWidget#buttonTestBackground { background: #ff00ff; }")
        background = QColor("#ff00ff")
        button = QPushButton("", self.window)
        button.setCheckable(True)
        for width, height in ((120, 34), (80, 24), (30, 22)):
            button.setDown(False)
            button.setChecked(False)
            button.setGeometry(20, 20, width, height)
            button.show()
            QTest.mouseMove(self.window, QPoint(290, 150))
            self.app.processEvents()
            normal = button.grab().toImage()
            ratio = normal.devicePixelRatio()
            x = normal.width() // 2
            top, bottom = normal.height() // 4, 3 * normal.height() // 4
            self.assertGreater(normal.pixelColor(x, top).lightnessF(),
                               normal.pixelColor(x, bottom).lightnessF())
            edge = max(1, round(ratio))
            self.assertGreater(normal.pixelColor(x, edge).lightnessF(),
                               normal.pixelColor(x, normal.height() - 1 - edge).lightnessF())
            geometry, hint = button.geometry(), button.sizeHint()
            for checked in (False, True):
                with self.subTest(size=(width, height), checked=checked):
                    button.setChecked(checked)
                    button.setDown(not checked)
                    self.app.processEvents()
                    pressed = button.grab().toImage()
                    # The face retains overhead lighting; only the narrow
                    # bevel reverses into a shadow above and highlight below.
                    self.assertGreater(pressed.pixelColor(x, top).lightnessF(),
                                       pressed.pixelColor(x, bottom).lightnessF())
                    self.assertLess(pressed.pixelColor(x, edge).lightnessF(),
                                    pressed.pixelColor(x, pressed.height() - 1 - edge).lightnessF())
                    self.assertEqual(button.geometry(), geometry)
                    self.assertEqual(button.sizeHint(), hint)
                    # A single rounded outline keeps all four corners inside
                    # the normal silhouette, including at fractional scaling.
                    for y in range(normal.height()):
                        for px in range(normal.width()):
                            if normal.pixelColor(px, y) == background:
                                self.assertEqual(pressed.pixelColor(px, y), background)
                    inset = max(0, round(ratio / 2) - 1)
                    self.assertEqual(pressed.pixelColor(inset, pressed.height() // 2),
                                     pressed.pixelColor(x, pressed.height() - 1 - inset))
            button.setDown(False)
            button.setChecked(False)

    def test_separator_background_matches_message_stripe_including_padding(self):
        for path in ("_insert_log_separator", "_insert_log_separator_before_newer_content"):
            with self.subTest(path=path):
                self._check_separator_background(path)

    def _check_separator_background(self, path):
        from PySide6.QtGui import QColor, QTextBlockFormat, QTextCharFormat, QTextCursor
        browser = SPRITELINK.MessageLogBrowser(self.window)
        browser.setObjectName("chatViewport")
        browser.setGeometry(0, 0, 280, 150)
        browser.document().setDocumentMargin(0)
        client = SimpleNamespace(chat_display=browser, _text_format=lambda color: QTextCharFormat())
        previous_hue = self.app.property("spritelinkThemeHue")
        try:
            for hue in (0, 65, 240):
                self.app.setProperty("spritelinkThemeHue", hue)
                for shadows in (False, True):
                    self.app.setProperty("spritelinkTextShadows", shadows)
                    for stripe in (0, 1):
                        browser.clear()
                        browser.row_background_blocks.clear()
                        browser.row_background_padding_blocks.clear()
                        colors = SPRITELINK.message_row_backgrounds()
                        cursor = QTextCursor(browser.document())
                        cursor.insertText("Older message")
                        separator = getattr(SPRITELINK.EncryptedChatClient, path)(
                            client, cursor, "Nov 15, 2023", colors[stripe], [],
                        )
                        if path == "_insert_log_separator_before_newer_content":
                            cursor.insertBlock(QTextBlockFormat())
                        cursor.insertText("Newer message")
                        browser.row_background_blocks.update({
                            0: QColor(colors[1 - stripe]), separator: QColor(colors[stripe]),
                            cursor.blockNumber(): QColor(colors[stripe]),
                        })
                        browser.show()
                        self.app.processEvents()
                        image = browser.viewport().grab().toImage()
                        ratio = image.devicePixelRatio()
                        block = browser.document().findBlockByNumber(separator)
                        top, bottom = browser._block_content_vertical_bounds(block)
                        x = image.width() - 10
                        body = image.pixelColor(x, round((top + 4) * ratio))
                        message_top, _ = browser._block_content_vertical_bounds(cursor.block())
                        self.assertEqual(body, image.pixelColor(x, round((message_top + 4) * ratio)),
                                         (hue, shadows, stripe, "message stripe"))
                        for y in range(top - 7, top):
                            self.assertEqual(image.pixelColor(x, round(y * ratio)), body,
                                             (hue, shadows, stripe, y))
                        for y in range(bottom, bottom + 7):
                            self.assertEqual(image.pixelColor(x, round(y * ratio)), body,
                                             (hue, shadows, stripe, y))
        finally:
            self.app.setProperty("spritelinkThemeHue", previous_hue)

    def test_checkbox_outline_and_checked_glyph_survive_without_text_shadows(self):
        from PySide6.QtWidgets import QCheckBox, QStyle, QStyleOptionButton
        checkbox = QCheckBox("Check", self.window)
        checkbox.setGeometry(20, 20, 120, 30)
        checkbox.show()
        option = QStyleOptionButton()
        checkbox.initStyleOption(option)
        rect = checkbox.style().subElementRect(QStyle.SubElement.SE_CheckBoxIndicator, option, checkbox)
        images = []
        for checked in (False, True):
            checkbox.setChecked(checked)
            self.app.processEvents()
            images.append(checkbox.grab().toImage().copy(rect))
        self.assertNotEqual(images[0], images[1])
        for image in images:
            # Indicator frames must be visible against a pale glass surface.
            dark = sum(image.pixelColor(x, y).red() < 100
                       for y in range(image.height()) for x in range(image.width()))
            self.assertGreater(dark, 25)

    def test_checkbox_indicator_has_square_outlined_corners(self):
        from PySide6.QtWidgets import QCheckBox, QStyle, QStyleOptionButton
        checkbox = QCheckBox("Check", self.window)
        checkbox.setGeometry(20, 20, 120, 30)
        checkbox.show()
        for checked in (False, True):
            checkbox.setChecked(checked)
            self.app.processEvents()
            option = QStyleOptionButton()
            checkbox.initStyleOption(option)
            rect = checkbox.style().subElementRect(QStyle.SubElement.SE_CheckBoxIndicator, option, checkbox)
            image = checkbox.grab().toImage().copy(rect)
            for x, y in ((0, 0), (0, image.height() - 1),
                         (image.width() - 1, 0), (image.width() - 1, image.height() - 1)):
                self.assertLess(image.pixelColor(x, y).red(), 120)
                self.assertEqual(image.pixelColor(x, y).alpha(), 255)

    def test_compact_slider_keeps_top_and_bottom_handle_outlines(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QSlider, QStyle, QStyleOptionSlider, QSizePolicy, QVBoxLayout
        layout = QVBoxLayout(self.window)
        slider = SPRITELINK.ThemeSlider(Qt.Orientation.Horizontal)
        slider.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(slider)
        layout.addStretch()
        slider.setValue(50)
        self.app.processEvents()
        option = QStyleOptionSlider()
        slider.initStyleOption(option)
        rect = slider.style().subControlRect(QStyle.ComplexControl.CC_Slider, option,
                                             QStyle.SubControl.SC_SliderHandle, slider)
        self.assertEqual(rect.width(), 15)
        self.assertEqual(rect.height(), 15)
        self.assertGreaterEqual(rect.top(), 1)
        self.assertLessEqual(rect.bottom(), slider.height() - 2)
        image = slider.grab().toImage()
        for y in (rect.top(), rect.bottom()):
            color = image.pixelColor(rect.center().x(), y)
            self.assertLess(color.red(), 120)
            self.assertEqual(color.alpha(), 255)

    def test_slider_handle_has_a_visible_dark_outline(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QSlider, QStyle, QStyleOptionSlider
        slider = SPRITELINK.ThemeSlider(Qt.Orientation.Horizontal, self.window)
        slider.setGeometry(20, 20, 220, 30)
        slider.setValue(50)
        slider.show()
        self.app.processEvents()
        option = QStyleOptionSlider()
        slider.initStyleOption(option)
        rect = slider.style().subControlRect(QStyle.ComplexControl.CC_Slider, option,
                                             QStyle.SubControl.SC_SliderHandle, slider)
        image = slider.grab().toImage().copy(rect)
        dark = sum(image.pixelColor(x, y).red() < 100
                   for y in range(image.height()) for x in range(image.width()))
        self.assertGreater(dark, 25)

    def test_rounded_slider_handle_has_symmetric_corner_coverage(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QStyle, QStyleOptionSlider
        slider = SPRITELINK.ThemeSlider(Qt.Orientation.Horizontal, self.window)
        slider.setGeometry(20, 20, 220, 30)
        slider.setValue(50)
        slider.show()
        self.app.processEvents()
        option = QStyleOptionSlider()
        slider.initStyleOption(option)
        rect = slider.style().subControlRect(QStyle.ComplexControl.CC_Slider, option,
                                             QStyle.SubControl.SC_SliderHandle, slider)
        image = slider.grab().toImage().copy(rect)
        self.assertLess(image.pixelColor(0, 0).alpha(), 50)
        for y in range(4):
            for x in range(4):
                alpha = image.pixelColor(x, y).alpha()
                self.assertLessEqual(abs(alpha - image.pixelColor(image.width() - 1 - x, y).alpha()), 3)
                self.assertLessEqual(abs(alpha - image.pixelColor(x, image.height() - 1 - y).alpha()), 3)

    def test_slider_track_has_a_subtle_vertical_gradient(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QSlider, QStyle, QStyleOptionSlider
        slider = SPRITELINK.ThemeSlider(Qt.Orientation.Horizontal, self.window)
        slider.setGeometry(20, 20, 220, 30)
        slider.setValue(50)
        slider.show()
        self.app.processEvents()
        option = QStyleOptionSlider()
        slider.initStyleOption(option)
        rect = slider.style().subControlRect(QStyle.ComplexControl.CC_Slider, option,
                                             QStyle.SubControl.SC_SliderGroove, slider)
        image = slider.grab().toImage()
        x = rect.left() + 20
        top = image.pixelColor(x, rect.top() + 1)
        bottom = image.pixelColor(x, rect.bottom() - 1)
        self.assertGreater(bottom.red(), top.red() + 10)
        self.assertLess(bottom.red() - top.red(), 60)

    def test_popup_has_no_dark_padding_above_or_below_options(self):
        combo = SPRITELINK.ThemeComboBox(self.window)
        combo.setGeometry(20, 20, 200, 30)
        combo.addItems(list(SPRITELINK.THEMES))
        combo.show()
        for index in range(combo.count()):
            combo.setCurrentIndex(index)
            combo.showPopup()
            self.app.processEvents()
            popup = combo.view().window()
            image = popup.grab().toImage()
            # Away from text, every interior row is either the light popup
            # surface or the blue selection, with no black top/bottom bands.
            for y in range(1, image.height() - 1):
                color = image.pixelColor(image.width() - 8, y)
                self.assertEqual(color.alpha(), 255)
                self.assertGreater(color.blue(), 150)
            combo.hidePopup()

    def test_popup_frame_cannot_inherit_translucent_panel_padding(self):
        from PySide6.QtWidgets import QFrame
        combo = SPRITELINK.ThemeComboBox(self.window)
        combo.setGeometry(20, 20, 200, 30)
        combo.addItems(list(SPRITELINK.THEMES))
        combo.show()
        combo.showPopup()
        self.app.processEvents()
        popup = combo.view().window()
        # Reproduce the styled-panel frame used by some platform popup styles,
        # and include real space outside the item view rather than only rows.
        popup.setFrameShape(QFrame.Shape.StyledPanel)
        popup.layout().setContentsMargins(0, 6, 0, 6)
        popup.resize(popup.width(), popup.height() + 12)
        popup.layout().activate()
        self.app.processEvents()
        image = popup.grab().toImage()
        for y in (3, image.height() - 4):
            color = image.pixelColor(image.width() // 2, y)
            self.assertEqual(color.alpha(), 255)
            self.assertGreater(color.red(), 230)
            self.assertGreater(color.blue(), 230)
        combo.hidePopup()


if __name__ == "__main__":
    unittest.main()
