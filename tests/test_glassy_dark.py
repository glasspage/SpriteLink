import copy
import unittest
from unittest import mock

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QCheckBox, QDialog, QPushButton, QStyle, QStyleOptionButton, QWidget
from test_security import SPRITELINK as S
import test_chatroom_preferences
import spritelink_video as V

LOAD_CONFIG = S.load_config


class GlassyDarkTests(unittest.TestCase):
    setUp = test_chatroom_preferences.ChatroomPreferenceTests.setUp
    tearDown = test_chatroom_preferences.ChatroomPreferenceTests.tearDown

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _dark(self):
        self.client._ensure_popup("config")
        self.client.theme_combo.setCurrentText("Glassy Dark")
        self.root.resize(850, 650)
        self.root.show()
        QTest.qWait(20)

    def test_selection_persists_with_separate_global_and_room_colors(self):
        c = self.client
        self._dark()
        self.assertTrue(c._is_glassy_theme())
        self.assertTrue(self.app.property("spritelinkGlassy"))
        self.assertTrue(self.app.property("spritelinkGlassyDark"))
        self.assertFalse(self.app.property("spritelinkModern"))
        c.theme_color_slider.setValue(70)
        c._finish_theme_color_change()
        self.config["room_settings"] = {"room-a": {"theme_colors": {"Glassy Dark": 190}}}
        c._activate_chatroom("room-a")
        self.assertEqual(c._applied_theme_color, ("Glassy Dark", 190))
        self.assertEqual(c.theme_color_slider.value(), 70)
        c.theme_combo.setCurrentText("Glassy")
        self.assertEqual(c._applied_theme_color, ("Glassy", 0))
        c.theme_combo.setCurrentText("Glassy Dark")
        self.assertEqual(c._applied_theme_color, ("Glassy Dark", 190))
        path = mock.Mock()
        path.exists.return_value = True
        path.read_bytes.return_value = b"settings"
        with mock.patch.object(S, "CONFIG_PATH", path), \
             mock.patch.object(S, "_load_dpapi_config", return_value=copy.deepcopy(self.config)):
            loaded = LOAD_CONFIG()
        self.assertEqual(loaded["theme"], "Glassy Dark")
        self.assertEqual(loaded["theme_colors"]["Glassy Dark"], 70)
        self.assertEqual(loaded["room_settings"]["room-a"]["theme_colors"]["Glassy Dark"], 190)

    def test_palette_and_painted_controls_are_dark_with_muted_accents(self):
        c = self.client
        light_accent = self.app.palette().color(QPalette.ColorRole.Highlight)
        self._dark()
        palette = self.app.palette()
        self.assertLess(palette.color(QPalette.ColorRole.Highlight).hslSaturationF(), light_accent.hslSaturationF())
        self.assertGreater(palette.color(QPalette.ColorRole.Text).lightnessF(), 0.75)
        self.assertLess(palette.color(QPalette.ColorRole.Base).lightnessF(), 0.2)
        self.assertEqual(c.theme_color_slider.maximum(), 359)
        self.assertIn("Glassy Dark", S.THEMES)
        for widget in (c.message_entry, c.theme_combo, c.config_toggle):
            widget.ensurePolished()
            self.assertGreater(widget.palette().color(QPalette.ColorRole.Text if widget is c.message_entry
                                                      else QPalette.ColorRole.ButtonText).lightnessF(), 0.6)
        for color in S.message_row_backgrounds():
            self.assertLess(QColor(color).lightnessF(), 0.25)
            self.assertLess(QColor(color).alpha(), 255)

    def test_message_and_identity_colors_are_readable_without_modifying_profiles(self):
        c = self.client
        self._dark()
        original = copy.deepcopy(self.config["room_profiles"])
        self.assertIn("#ffffff", c.message_entry.styleSheet())
        self.assertGreater(c._text_format("#202020").foreground().color().lightnessF(), 0.75)
        self.assertGreater(c._text_format("#000000").foreground().color().lightnessF(), 0.7)
        self.assertEqual(S.message_display_color("#ffff80"), "#ffff80")
        bright = QColor(S.message_display_color("#2f6bff"))
        muted = QColor(c._blend_toward_chat_background("#2f6bff"))
        self.assertLess(muted.lightnessF(), bright.lightnessF())
        self.assertEqual(c._text_format(muted.name(), ui_font=True).foreground().color(), muted)
        self.assertEqual(self.config["room_profiles"], original)
        c.theme_combo.setCurrentText("Glassy")
        self.assertFalse(self.app.property("spritelinkGlassyDark"))
        self.assertIn("#202020", c.message_entry.styleSheet())
        self.assertEqual(c._text_format("#202020").foreground().color(), QColor("#202020"))
        self.assertEqual(self.config["room_profiles"], original)

    def test_glassy_geometry_and_ui_size_are_retained(self):
        c = self.client
        c._ensure_popup("config")
        self.root.resize(850, 650)
        self.root.show()
        QTest.qWait(20)
        for size in S.UI_SIZE_PRESETS:
            c._on_ui_size_changed(size)
            c._on_theme_changed("Glassy")
            c._on_config_toggled(True)
            self.root.show()
            QTest.qWait(20)
            light = [(widget.sizeHint(), widget.font().pointSize())
                     for widget in (c.config_toggle, c.theme_combo, c.message_sound_combo)]
            c._on_theme_changed("Glassy Dark")
            QTest.qWait(20)
            dark = [(widget.sizeHint(), widget.font().pointSize())
                    for widget in (c.config_toggle, c.theme_combo, c.message_sound_combo)]
            self.assertEqual(light, dark, size)
            c._dismiss_config_popup()

    def test_frosted_popup_capture_is_opaque_and_dark(self):
        c = self.client
        self._dark()
        c._on_config_toggled(True)
        QTest.qWait(40)
        overlay = c.config_overlay
        self.assertTrue(overlay._glassy)
        image = overlay._blurred_background.toImage()
        for x, y in ((0, 0), (image.width() - 1, image.height() - 1)):
            color = image.pixelColor(x, y)
            self.assertEqual(color.alpha(), 255)
            self.assertLess(color.lightnessF(), 0.4)
        self.assertTrue(overlay.isVisible())

    def test_glass_layers_leave_the_backdrop_visible(self):
        self._dark()
        panel = QWidget()
        panel.setObjectName("glassRoot")
        panel.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        panel.resize(200, 100)
        panel.show()
        QTest.qWait(20)
        try:
            image = panel.grab().toImage()
            ratio = image.devicePixelRatio()
            for x, y in ((20, 20), (100, 50), (180, 80)):
                self.assertLess(image.pixelColor(round(x * ratio), round(y * ratio)).alpha(), 140)
            # Keep the combined glass layers translucent.
            base_alpha = self.app.palette().color(QPalette.ColorRole.Base).alphaF()
            for stripe in S.message_row_backgrounds():
                combined = 1 - (1 - 128 / 255) * (1 - base_alpha) * (1 - QColor(stripe).alphaF())
                self.assertLess(combined, 0.75)
        finally:
            panel.close()
            panel.deleteLater()

    def test_button_dropdown_and_checkbox_outlines_stay_black_in_every_state(self):
        self._dark()
        button = QPushButton("Test", self.root)
        combo = S.ThemeComboBox(self.root)
        combo.addItems(["One", "Two"])
        check = QCheckBox("Test", self.root)
        for i, widget in enumerate((button, combo, check)):
            widget.setGeometry(20, 20 + i * 50, 180, 32)
            widget.show()
        try:
            for hue in (0, 120, 240):
                self.client.theme_color_slider.setValue(hue)
                self.client._finish_theme_color_change()
                for state in ("normal", "hover", "focus", "pressed", "disabled"):
                    for widget in (button, combo, check):
                        widget.setEnabled(state != "disabled")
                        widget.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, state == "hover")
                        if state == "focus":
                            widget.setFocus()
                        else:
                            widget.clearFocus()
                    button.setDown(state == "pressed")
                    check.setChecked(state == "pressed")
                    QTest.qWait(5)
                    for widget in (button, combo, check):
                        image = widget.grab().toImage()
                        ratio = image.devicePixelRatio()
                        x, y = widget.width() // 2, 0
                        if widget is check:
                            option = QStyleOptionButton()
                            check.initStyleOption(option)
                            rect = check.style().subElementRect(QStyle.SubElement.SE_CheckBoxIndicator, option, check)
                            x, y = rect.center().x(), rect.top()
                        color = image.pixelColor(round(x * ratio), round(y * ratio))
                        self.assertEqual(color, QColor("#000000"), (hue, state, type(widget).__name__, color.name()))
        finally:
            for widget in (button, combo, check):
                widget.close()
                widget.deleteLater()

    def test_video_controls_follow_dark_glass_in_main_mini_and_fullscreen(self):
        self._dark()
        player = V.VideoPlayer(self.root, use_process=False)
        player.play_button.setEnabled(True)
        player.play_button.ensurePolished()
        for mode in ("main", "mini"):
            player._mode = mode
            player.set_theme("Glassy Dark")
            self.assertIn("rgba(32,43,55,240)", player.controls.styleSheet())
            self.assertGreater(player.play_button.palette().color(QPalette.ColorRole.ButtonText).lightnessF(), 0.6)
        fullscreen = QDialog(self.root)
        fullscreen.setProperty("spritelinkVideoFullscreen", True)
        player._fullscreen = fullscreen
        player.set_theme("Glassy Dark")
        self.assertIn("border-radius:0px", player.controls.styleSheet())
        player._fullscreen = None
        fullscreen.deleteLater()


if __name__ == "__main__":
    unittest.main()
