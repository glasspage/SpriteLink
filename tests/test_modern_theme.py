import os
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QLabel, QLineEdit, QListWidgetItem,
    QStyle, QStyleFactory, QStyleOptionButton, QTextBrowser, QWidget,
)
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
        checkbox = QCheckBox("Theme change", self.window)
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
        option = QStyleOptionButton()
        checkbox.initStyleOption(option)
        rect = checkbox.style().subElementRect(QStyle.SubElement.SE_CheckBoxIndicator, option, checkbox)
        self.assertEqual((rect.width(), rect.height()), (20, 20))

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

    def test_segoe_font_prefers_static_faces_and_falls_back_without_it(self):
        family = SPRITELINK.EncryptedChatClient._ui_font_family
        for available, expected in (
            (["Segoe UI", "Segoe UI Variable"], "Segoe UI"),
            (["Segoe UI"], "Segoe UI"),
            (["Segoe UI Variable"], "Segoe UI Variable"),
            ([], "Fallback"),
        ):
            with mock.patch.object(SPRITELINK.QFontDatabase, "families", return_value=available):
                self.assertEqual(family(self.client), expected)
        self.theme = "Glassy"
        self.assertEqual(family(self.client), "Fallback")
        self.theme = "Classic"
        self.assertEqual(family(self.client), "Tahoma")

    def test_ui_fonts_keep_their_family_and_message_fonts_stay_validated(self):
        client = SimpleNamespace(
            _font_style_strategy=lambda: QFont.StyleStrategy.PreferDefault,
            _resolved_font_family=lambda name: name if name in SPRITELINK.SUPPORTED_MESSAGE_FONTS
                else SPRITELINK.DEFAULT_MESSAGE_FONT,
            _is_windows_classic_theme=lambda: False,
            _message_font_cache={},
        )
        client._make_font = lambda *args, **kwargs: SPRITELINK.EncryptedChatClient._make_font(
            client, *args, **kwargs
        )
        for family in ("Segoe UI", "Segoe UI Variable", "Tahoma"):
            for bold in (False, True):
                font = client._make_font(family, 10, bold=bold)
                self.assertEqual(font.family(), family)
                self.assertEqual(font.bold(), bold)
        message_font = SPRITELINK.EncryptedChatClient._make_message_font(client, "Invalid font")
        self.assertEqual(message_font.family(), SPRITELINK.DEFAULT_MESSAGE_FONT)

    def test_checkbox_is_twenty_pixels_and_preserves_all_glyph_states(self):
        self.apply()
        checkbox = QCheckBox("Notifications", self.window)
        checkbox.setGeometry(10, 10, 200, 32)
        checkbox.setTristate(True)
        self.window.resize(240, 60)
        self.window.show()
        self.app.processEvents()
        for enabled in (True, False):
            checkbox.setEnabled(enabled)
            images = []
            for state in (Qt.CheckState.Unchecked, Qt.CheckState.Checked,
                          Qt.CheckState.PartiallyChecked):
                checkbox.setCheckState(state)
                self.app.processEvents()
                option = QStyleOptionButton()
                checkbox.initStyleOption(option)
                rect = checkbox.style().subElementRect(
                    QStyle.SubElement.SE_CheckBoxIndicator, option, checkbox
                )
                self.assertEqual((rect.width(), rect.height()), (20, 20))
                self.assertGreaterEqual(checkbox.height(), rect.height())
                images.append(checkbox.grab().toImage())
            self.assertNotEqual(images[0], images[1])
            self.assertNotEqual(images[1], images[2])

    def test_disabled_checkbox_text_has_no_etching_or_extra_shadow(self):
        self.apply()
        checkbox = QCheckBox("Disabled notifications", self.window)
        checkbox.setEnabled(False)
        checkbox.setGeometry(10, 10, 220, 32)
        self.window.resize(250, 60)
        self.window.show()
        self.app.processEvents()
        for hint in (QStyle.StyleHint.SH_EtchDisabledText, QStyle.StyleHint.SH_DitherDisabledText):
            self.assertEqual(checkbox.style().styleHint(hint, None, checkbox), 0)
        images = []
        for shadows in (False, True):
            self.app.setProperty("spritelinkTextShadows", shadows)
            checkbox.update()
            self.app.processEvents()
            images.append(checkbox.grab().toImage())
        self.assertEqual(images[0], images[1])

    def test_custom_chatroom_rows_fit_descenders_after_font_changes(self):
        self.apply()
        rooms = SPRITELINK.ChatroomListWidget(self.window)
        rooms.setGeometry(0, 0, 175, 180)
        rows = []
        for name, unread in (("gypsy pq_j", 0), ("Project glyphs", 12)):
            item = QListWidgetItem(rooms)
            row = SPRITELINK.ChatroomListRow(name, unread, False, rooms)
            row.ensurePolished()
            item.setSizeHint(row.sizeHint())
            rooms.setItemWidget(item, row)
            rows.append(row)
        self.window.resize(175, 180)
        self.window.show()
        for size in (9, 13, 10):
            rooms.setFont(QFont(self.app.font().family(), size))
            self.app.processEvents()
            rooms.refresh_row_sizes()
            self.app.processEvents()
            for row in rows:
                self.assertGreaterEqual(row.height(), row.sizeHint().height())
                for label in row.findChildren(QLabel):
                    if label.isVisible():
                        self.assertGreaterEqual(label.height(), label.sizeHint().height())


if __name__ == "__main__":
    unittest.main()
