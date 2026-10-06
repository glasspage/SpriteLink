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

    def test_message_stripes_are_translucent_only_for_glassy(self):
        self.app.setProperty("spritelinkGlassy", True)
        for color in SPRITELINK.message_row_backgrounds():
            self.assertLess(SPRITELINK.QColor(color).alpha(), 255)
        self.app.setProperty("spritelinkGlassy", False)
        self.assertEqual(SPRITELINK.message_row_backgrounds(), SPRITELINK.MESSAGE_ROW_BACKGROUNDS)


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

    def test_slider_handle_has_a_visible_dark_outline(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QSlider, QStyle, QStyleOptionSlider
        slider = QSlider(Qt.Orientation.Horizontal, self.window)
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
