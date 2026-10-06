import ctypes
import os
import unittest
from types import MethodType, SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QLabel, QLineEdit, QStyle, QStyleFactory,
    QStyleOptionButton, QStyleOptionSlider, QTextBrowser, QWidget,
)
from test_security import SPRITELINK as S


class ThemeColorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.previous_palette = QPalette(self.app.palette())
        self.previous_font = QFont(self.app.font())
        self.previous_sheet = self.app.styleSheet()
        self.previous_properties = {name: self.app.property(name) for name in (
            "spritelinkGlassy", "spritelinkModern", "spritelinkWindowsClassic",
            "spritelinkTextShadows", "spritelinkThemeHue", "spritelinkClassicColor",
        )}
        self.window = QWidget()
        self.window.resize(340, 220)
        self.client = SimpleNamespace(
            root=self.window, theme_var=S.ValueModel("Modern"),
            config_data={"theme": "Modern", "theme_colors": S.normalize_theme_colors(None)},
            text_shadows_var=S.ValueModel(False),
            _basic_palette=QPalette(self.previous_palette), _basic_style_name="Fusion",
            _apply_titlebar_theme=mock.Mock(), _apply_application_font_strategy=mock.Mock(),
            _apply_active_composer_style=mock.Mock(), _rerender_preserving_scroll=mock.Mock(),
        )
        self.client._is_glassy_theme = lambda: self.client.theme_var.get() == "Glassy"
        self.client._is_windows_classic_theme = lambda: self.client.theme_var.get() == "Classic"
        for name in (
            "_apply_theme", "_windows_classic_palette", "_glassy_palette", "_modern_palette",
            "_config_panel_stylesheet", "_sync_theme_color_slider", "_on_theme_color_changed",
            "_apply_theme_colors", "_save_theme_colors", "_finish_theme_color_change", "_on_theme_changed",
        ):
            setattr(self.client, name, MethodType(getattr(S.EncryptedChatClient, name), self.client))
        self.client.theme_color_slider = S.ThemeSlider(Qt.Orientation.Horizontal, self.window)
        self.client.theme_color_slider.setGeometry(10, 10, 112, 24)
        self.client.theme_color_save_timer = QTimer(self.window)
        self.client.theme_color_save_timer.setSingleShot(True)
        self.client.theme_color_save_timer.setInterval(400)
        self.client.theme_color_save_timer.timeout.connect(self.client._save_theme_colors)
        self.client.theme_color_slider.valueChanged.connect(self.client._on_theme_color_changed)
        self.client.theme_color_slider.sliderReleased.connect(lambda: self.client._finish_theme_color_change())
        self.original_save_config = S.save_config
        self.save_patch = mock.patch.object(S, "save_config")
        self.saved = self.save_patch.start()
        self.client._apply_theme()
        self.client._saved_theme_colors = dict(self.client.config_data["theme_colors"])

    def tearDown(self):
        self.client.theme_color_save_timer.stop()
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.save_patch.stop()
        for name, value in self.previous_properties.items():
            self.app.setProperty(name, value)
        self.app.setStyle(QStyleFactory.create("Fusion"))
        self.app.setPalette(self.previous_palette)
        self.app.setStyleSheet(self.previous_sheet)
        self.app.setFont(self.previous_font)

    def test_old_and_malformed_settings_normalize_safely(self):
        self.assertEqual(S.normalize_theme_colors(None), {"Classic": 0, "Glassy": 0, "Modern": 0})
        self.assertEqual(S.normalize_theme_colors({"Classic": 100, "Glassy": -5, "Modern": "120"}),
                         {"Classic": 5, "Glassy": 0, "Modern": 120})
        for bad in (None, [], {}, True, "bad", float("inf"), float("nan")):
            self.assertEqual(S.normalize_theme_colors({"Modern": bad})["Modern"], 0)
        self.assertEqual(S.normalize_theme_colors({"Glassy+": 57})["Glassy"], 57)

    def test_settings_save_and_reload_per_theme(self):
        saved_config = S.default_config()
        saved_config["theme_colors"] = {"Classic": 4, "Glassy": 31, "Modern": 289}
        with mock.patch.object(S, "_write_dpapi_json") as write:
            self.original_save_config(saved_config)
        self.assertEqual(write.call_args.args[1]["theme_colors"], saved_config["theme_colors"])
        self.client.config_data = saved_config
        self.client._save_theme_colors()
        self.assertEqual(self.saved.call_args.args[0]["theme_colors"], saved_config["theme_colors"])
        config_path = mock.Mock()
        config_path.exists.return_value = True
        config_path.read_bytes.return_value = b"settings"
        with mock.patch.object(S, "CONFIG_PATH", config_path), \
             mock.patch.object(S, "_load_dpapi_config", return_value=saved_config):
            self.assertEqual(S.load_config()["theme_colors"], saved_config["theme_colors"])

    def test_theme_switch_restores_positions_without_value_signals(self):
        self.client.config_data["theme_colors"] = {"Classic": 3, "Glassy": 94, "Modern": 218}
        spy = QSignalSpy(self.client.theme_color_slider.valueChanged)
        for theme, value, maximum in (("Classic", 3, 5), ("Glassy", 94, 359),
                                      ("Modern", 218, 359), ("Classic", 3, 5)):
            self.client._on_theme_changed(theme)
            slider = self.client.theme_color_slider
            self.assertEqual((slider.value(), slider.maximum()), (value, maximum))
            self.assertEqual(slider.pageStep(), 1 if theme == "Classic" else 15)
            self.assertEqual(slider.tickPosition(), S.QSlider.TickPosition.TicksBelow
                             if theme == "Classic" else S.QSlider.TickPosition.NoTicks)
        self.assertEqual(spy.count(), 0)
        self.assertEqual(self.client.config_data["theme_colors"], {"Classic": 3, "Glassy": 94, "Modern": 218})

    def test_drag_updates_only_the_thumb_and_commits_once_on_release(self):
        self.client.chat_display = QTextBrowser(self.window)
        self.client.theme_color_slider.setSliderDown(True)
        sheet = self.app.styleSheet()
        for position in (15, 90, 240):
            self.client.theme_color_slider.setValue(position)
        self.saved.assert_not_called()
        # Pausing while holding the thumb must not trigger an expensive commit.
        QTest.qWait(500)
        self.assertEqual(self.app.property("spritelinkThemeHue"), 0)
        self.assertEqual(self.app.styleSheet(), sheet)
        self.assertEqual(self.client.theme_color_slider.property("spritelinkPreviewHue"), 240)
        self.client._rerender_preserving_scroll.assert_not_called()
        self.saved.assert_not_called()
        self.client.theme_color_slider.setSliderDown(False)
        self.assertEqual(self.app.property("spritelinkThemeHue"), 240)
        self.saved.assert_called_once()
        self.client._rerender_preserving_scroll.assert_called_once()
        self.assertEqual(self.saved.call_args.args[0]["theme_colors"]["Modern"], 240)
        self.assertFalse(self.client.theme_color_save_timer.isActive())
        self.client._apply_application_font_strategy.assert_not_called()

    def test_keyboard_adjustment_saves_after_pause(self):
        self.client.theme_color_slider.setValue(119)
        QTest.qWait(500)
        self.saved.assert_called_once()
        self.assertEqual(self.app.property("spritelinkThemeHue"), 119)

    def test_returning_to_original_color_skips_repolish_log_and_save(self):
        self.client.chat_display = QTextBrowser(self.window)
        slider = self.client.theme_color_slider
        slider.setSliderDown(True)
        slider.setValue(120)
        slider.setValue(0)
        with mock.patch.object(self.app, "setStyleSheet") as repolish, \
             mock.patch.object(self.app, "setPalette") as palette:
            slider.setSliderDown(False)
            self.client._finish_theme_color_change()
        repolish.assert_not_called()
        palette.assert_not_called()
        self.saved.assert_not_called()
        self.client._rerender_preserving_scroll.assert_not_called()
        self.assertIsNone(slider.property("spritelinkPreviewHue"))

    def test_hue_rotation_preserves_alpha_saturation_and_lightness(self):
        color = QColor(153, 201, 230, 72)
        shifted = S.shift_theme_color(color, 143)
        self.assertEqual(color.alpha(), shifted.alpha())
        self.assertAlmostEqual(color.hslSaturationF(), shifted.hslSaturationF(), places=3)
        self.assertAlmostEqual(color.lightnessF(), shifted.lightnessF(), places=3)
        self.assertEqual(S.shift_theme_color(color, 0), color)
        self.assertEqual(S.shift_theme_color("#eeeeee", 143), QColor("#eeeeee"))

    def test_warning_colors_and_visited_links_do_not_rotate(self):
        sheet = "color:#0067c0; background:#ffb9b9; border-color:#d97706; color:#5e4b9e; background:#ffffff;"
        shifted = S.hue_theme_stylesheet(sheet, 120)
        self.assertNotIn("#0067c0", shifted)
        for color in ("#ffb9b9", "#d97706", "#5e4b9e", "#ffffff"):
            self.assertIn(color, shifted)

    def test_classic_schemes_change_surfaces_and_preserve_chat_row_contrast(self):
        browser = QTextBrowser(self.window)
        browser.setObjectName("chatViewport")
        browser.setGeometry(10, 60, 200, 120)
        self.window.show()
        for position, scheme in enumerate(S.CLASSIC_COLOR_SCHEMES):
            self.client.config_data["theme_colors"]["Classic"] = position
            self.client._on_theme_changed("Classic")
            self.app.processEvents()
            self.assertEqual(self.app.palette().color(QPalette.ColorRole.Window), QColor(scheme["face"]))
            self.assertEqual(self.app.palette().color(QPalette.ColorRole.Highlight), QColor(scheme["highlight"]))
            backgrounds = scheme.get("chat_backgrounds", S.MESSAGE_ROW_BACKGROUNDS)
            self.assertEqual(S.message_row_backgrounds(), backgrounds)
            image = browser.viewport().grab().toImage()
            self.assertEqual(image.pixelColor(image.width() // 2, image.height() // 2), QColor(backgrounds[0]))

    def test_classic_thumb_previews_each_scheme_without_refreshing_window(self):
        self.client._on_theme_changed("Classic")
        self.saved.reset_mock()
        self.client.chat_display = QTextBrowser(self.window)
        slider = self.client.theme_color_slider
        other_slider = S.ThemeSlider(Qt.Orientation.Horizontal, self.window)
        other_slider.setGeometry(10, 60, 112, 24)
        self.window.show()
        self.app.processEvents()
        palette = QPalette(self.app.palette())
        sheet = self.app.styleSheet()
        other_image = other_slider.grab().toImage()
        slider.setSliderDown(True)
        for position, scheme in enumerate(S.CLASSIC_COLOR_SCHEMES):
            slider.setValue(position)
            self.app.processEvents()
            option = QStyleOptionSlider()
            slider.initStyleOption(option)
            handle = slider.style().subControlRect(QStyle.ComplexControl.CC_Slider, option,
                                                   QStyle.SubControl.SC_SliderHandle, slider)
            image = slider.grab().toImage()
            ratio = image.devicePixelRatio()
            self.assertEqual(image.pixelColor(round(handle.center().x() * ratio),
                                              round(handle.center().y() * ratio)), QColor(scheme["face"]))
            self.assertEqual(self.app.palette(), palette)
            self.assertEqual(self.app.styleSheet(), sheet)
            self.assertEqual(other_slider.grab().toImage(), other_image)
        QTest.qWait(500)
        self.saved.assert_not_called()
        self.client._rerender_preserving_scroll.assert_not_called()
        slider.setSliderDown(False)
        self.assertEqual(self.app.palette().color(QPalette.ColorRole.Window), QColor(scheme["face"]))
        self.saved.assert_called_once()
        self.client._rerender_preserving_scroll.assert_called_once()
        self.client._on_theme_changed("Modern")
        self.assertIsNone(slider.property("spritelinkPreviewClassicColor"))

    def test_modern_checkbox_slider_and_focus_edge_share_shifted_accent(self):
        checkbox = QCheckBox("Accent", self.window)
        checkbox.setChecked(True)
        checkbox.setGeometry(10, 50, 200, 32)
        field = QLineEdit(self.window)
        field.setGeometry(10, 90, 200, 30)
        slider = S.ThemeSlider(Qt.Orientation.Horizontal, self.window)
        slider.setGeometry(10, 140, 200, 24)
        slider.setValue(50)
        self.window.show()
        self.client.theme_color_slider.setValue(240)
        self.client._apply_theme_colors()
        field.setFocus()
        self.app.processEvents()
        expected = QColor(S.shift_theme_color("#0067c0", 240).name())
        option = QStyleOptionButton()
        checkbox.initStyleOption(option)
        rect = checkbox.style().subElementRect(QStyle.SubElement.SE_CheckBoxIndicator, option, checkbox)
        image = checkbox.grab().toImage()
        ratio = image.devicePixelRatio()
        self.assertEqual(image.pixelColor(round((rect.x() + 4) * ratio), round((rect.y() + 4) * ratio)), expected)
        option = QStyleOptionSlider()
        slider.initStyleOption(option)
        handle = slider.style().subControlRect(QStyle.ComplexControl.CC_Slider, option,
                                               QStyle.SubControl.SC_SliderHandle, slider)
        self.assertEqual((handle.width(), handle.height()), (20, 20))
        image = slider.grab().toImage()
        self.assertEqual(image.pixelColor(round(handle.center().x() * ratio), round(handle.center().y() * ratio)), expected)
        image = field.grab().toImage()
        self.assertEqual(image.pixelColor(image.width() // 2, image.height() - 1), expected)

    def test_modern_chat_backdrop_and_heading_weight_stay_unchanged(self):
        heading = QLabel("Heading", self.window)
        font = QFont(self.app.font())
        font.setBold(True)
        heading.setFont(font)
        base = self.app.palette().color(QPalette.ColorRole.Base)
        window = self.app.palette().color(QPalette.ColorRole.Window)
        for position in (70, 145, 240, 0):
            self.client.theme_color_slider.setValue(position)
            self.client._apply_theme_colors()
            self.assertTrue(heading.font().bold())
            self.assertEqual(self.app.palette().color(QPalette.ColorRole.Base), base)
            self.assertEqual(self.app.palette().color(QPalette.ColorRole.Window), window)
            self.assertEqual(S.message_row_backgrounds(), S.MESSAGE_ROW_BACKGROUNDS)

    def test_classic_color_commit_preserves_tahoma_and_aliased_heading_fonts(self):
        self.client._on_theme_changed("Classic")
        font = QFont("Tahoma", 10)
        font.setStyleStrategy(QFont.StyleStrategy.NoAntialias)
        self.app.setFont(font)
        font.setBold(True)
        headings = [QLabel(title, self.window) for title in ("Chatrooms", "Global")]
        for heading in headings:
            heading.setFont(font)
        for position in range(len(S.CLASSIC_COLOR_SCHEMES)):
            self.client.theme_color_slider.setValue(position)
            self.client._finish_theme_color_change()
            self.app.processEvents()
            self.assertEqual(self.app.font().family(), "Tahoma")
            self.assertEqual(self.app.font().styleStrategy(), QFont.StyleStrategy.NoAntialias)
            for heading in headings:
                self.assertEqual(heading.font().family(), "Tahoma")
                self.assertTrue(heading.font().bold())
                self.assertEqual(heading.font().styleStrategy(), QFont.StyleStrategy.NoAntialias)

    def test_glassy_tint_and_row_brushes_change_with_alpha_preserved(self):
        self.client._on_theme_changed("Glassy")
        before = S.message_row_backgrounds()
        self.client.chat_display = QTextBrowser(self.window)
        self.client.theme_color_slider.setSliderDown(True)
        self.client.theme_color_slider.setValue(130)
        self.client.theme_color_slider.setSliderDown(False)
        after = S.message_row_backgrounds()
        self.assertNotEqual(before[1], after[1])
        for original, shifted in zip(before, after):
            self.assertEqual(QColor(original).alpha(), QColor(shifted).alpha())
        self.client._rerender_preserving_scroll.assert_called_once()

    def test_native_classic_titlebar_uses_selected_scheme(self):
        calls = []
        def set_attribute(hwnd, attribute, data, size):
            calls.append((attribute.value, ctypes.cast(data, ctypes.POINTER(ctypes.c_uint)).contents.value))
            return 0
        dwm = SimpleNamespace(DwmSetWindowAttribute=mock.Mock(side_effect=set_attribute),
                              DwmExtendFrameIntoClientArea=mock.Mock(return_value=0))
        self.client._windows_colorref = S.EncryptedChatClient._windows_colorref
        self.client._set_windows_legacy_blur = mock.Mock(return_value=True)
        self.client.theme_var.set("Classic")
        for position in (0, 2, 5):
            calls.clear()
            self.client.config_data["theme_colors"]["Classic"] = position
            with mock.patch.object(S.os, "name", "nt"), \
                 mock.patch.object(S.ctypes, "windll", SimpleNamespace(dwmapi=dwm), create=True):
                S.EncryptedChatClient._apply_window_titlebar_theme(self.client, self.window)
            self.assertIn((35, self.client._windows_colorref(S.CLASSIC_COLOR_SCHEMES[position]["title"])), calls)
            self.assertIn((38, 1), calls)


if __name__ == "__main__":
    unittest.main()
