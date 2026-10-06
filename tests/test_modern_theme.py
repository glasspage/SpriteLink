import os
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication, QLineEdit, QStyleFactory, QTextBrowser, QWidget
from test_security import SPRITELINK


class ModernThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.previous_palette = QPalette(self.app.palette())
        self.previous_sheet = self.app.styleSheet()
        self.properties = {
            name: self.app.property(name) for name in (
                "spritelinkGlassy", "spritelinkModern",
                "spritelinkWindowsClassic", "spritelinkTextShadows",
            )
        }
        self.theme = "Modern"
        self.client = SimpleNamespace(
            _basic_palette=QPalette(self.previous_palette),
            _basic_style_name="Fusion",
            _basic_application_font=QFont("Fallback"),
            _is_glassy_theme=lambda: self.theme == "Glassy",
            _is_windows_classic_theme=lambda: self.theme == "Classic",
            text_shadows_var=SimpleNamespace(get=lambda: False),
            _apply_titlebar_theme=mock.Mock(),
        )
        for name in ("_modern_palette", "_glassy_palette", "_windows_classic_palette"):
            setattr(self.client, name, lambda name=name: getattr(
                SPRITELINK.EncryptedChatClient, name
            )(self.client))
        self.window = QWidget()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        for name, value in self.properties.items():
            self.app.setProperty(name, value)
        self.app.setStyle(QStyleFactory.create("Fusion"))
        self.app.setPalette(self.previous_palette)
        self.app.setStyleSheet(self.previous_sheet)

    def apply(self):
        SPRITELINK.EncryptedChatClient._apply_theme(self.client)
        self.app.processEvents()

    def test_chat_backdrop_palette_and_message_stripes_are_preserved(self):
        for group, color in (
            (QPalette.ColorGroup.Active, "#fafafa"),
            (QPalette.ColorGroup.Inactive, "#f8f8f8"),
            (QPalette.ColorGroup.Disabled, "#efefef"),
        ):
            for role in (QPalette.ColorRole.Base, QPalette.ColorRole.AlternateBase):
                self.client._basic_palette.setColor(group, role, QColor(color))
        self.apply()
        for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive,
                      QPalette.ColorGroup.Disabled):
            for role in (QPalette.ColorRole.Base, QPalette.ColorRole.AlternateBase):
                self.assertEqual(self.app.palette().color(group, role),
                                 self.client._basic_palette.color(group, role))
        self.assertEqual(SPRITELINK.message_row_backgrounds(),
                         SPRITELINK.MESSAGE_ROW_BACKGROUNDS)
        browser = QTextBrowser(self.window)
        browser.setObjectName("chatViewport")
        browser.setGeometry(0, 0, 200, 100)
        self.window.resize(200, 100)
        self.window.show()
        self.app.processEvents()
        rendered = browser.viewport().grab().toImage()
        self.assertEqual(rendered.pixelColor(80, 40),
                         browser.palette().color(QPalette.ColorRole.Base))

    def test_switching_themes_clears_modern_styling(self):
        self.apply()
        self.assertTrue(self.app.property("spritelinkModern"))
        self.assertEqual(self.app.styleSheet(), SPRITELINK.MODERN_STYLESHEET)
        for theme, sheet in (("Glassy", SPRITELINK.GLASSY_STYLESHEET),
                             ("Classic", SPRITELINK.WINDOWS_CLASSIC_STYLESHEET)):
            self.theme = theme
            self.apply()
            self.assertFalse(self.app.property("spritelinkModern"))
            self.assertEqual(self.app.styleSheet(), sheet)
        self.theme = "Modern"
        self.apply()
        self.assertFalse(self.app.property("spritelinkGlassy"))
        self.assertFalse(self.app.property("spritelinkWindowsClassic"))

    def test_input_has_a_thin_gray_edge_and_blue_focus_edge(self):
        self.apply()
        field = QLineEdit(self.window)
        field.setGeometry(10, 10, 180, 30)
        self.window.resize(200, 50)
        self.window.show()
        self.app.processEvents()
        field.clearFocus()
        self.app.processEvents()
        image = field.grab().toImage()
        x, y = image.width() // 2, image.height() - 1
        self.assertEqual(image.pixelColor(x, y), QColor("#858585"))
        self.assertEqual(image.pixelColor(x, y - 1), QColor("#ffffff"))
        field.setFocus()
        self.app.processEvents()
        image = field.grab().toImage()
        self.assertEqual(image.pixelColor(x, y), QColor("#0067c0"))
        self.assertEqual(image.pixelColor(x, y - 1), QColor("#ffffff"))

    def test_native_windows11_style_is_preferred_when_available(self):
        create_style = QStyleFactory.create
        with mock.patch.object(SPRITELINK.QStyleFactory, "keys",
                               return_value=["Fusion", "WindowsVista", "Windows11"]), \
             mock.patch.object(SPRITELINK.QStyleFactory, "create",
                               side_effect=lambda name: create_style("Fusion")) as create:
            self.apply()
        create.assert_called_once_with("Windows11")

    def test_segoe_font_prefers_variable_and_falls_back_without_it(self):
        family = SPRITELINK.EncryptedChatClient._ui_font_family
        for available, expected in (
            (["Segoe UI", "Segoe UI Variable"], "Segoe UI Variable"),
            (["Segoe UI"], "Segoe UI"),
            ([], "Fallback"),
        ):
            with mock.patch.object(SPRITELINK.QFontDatabase, "families", return_value=available):
                self.assertEqual(family(self.client), expected)
        self.theme = "Glassy"
        self.assertEqual(family(self.client), "Fallback")
        self.theme = "Classic"
        self.assertEqual(family(self.client), "Tahoma")


if __name__ == "__main__":
    unittest.main()
