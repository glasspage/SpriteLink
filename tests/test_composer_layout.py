import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QRect, QTimer, Qt
from PySide6.QtGui import QContextMenuEvent, QFont, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyleFactory
from test_security import SPRITELINK as S


class ComposerLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.palette = QPalette(self.app.palette())
        self.font = QFont(self.app.font())
        self.sheet = self.app.styleSheet()
        self.properties = {name: self.app.property(name) for name in (
            "spritelinkGlassy", "spritelinkModern", "spritelinkWindowsClassic",
            "spritelinkTextShadows", "spritelinkThemeHue", "spritelinkClassicColor",
        )}
        config = S.default_config()
        config["theme"] = "Glassy"
        self.patches = [
            mock.patch.object(S, "load_config", return_value=config),
            mock.patch.object(S, "save_config"),
            mock.patch.object(S.EncryptedChatClient, "_start_network_thread"),
            mock.patch.object(S.EncryptedChatClient, "_check_for_updates"),
            mock.patch.object(S.EncryptedChatClient, "_load_saved_history_for_current_room"),
        ]
        for patch in self.patches:
            patch.start()
        self.root = S.MainWindow()
        self.client = S.EncryptedChatClient(self.root)
        self.root.show()
        QTest.qWait(20)
        self.edit = self.client.message_entry

    def tearDown(self):
        for timer in self.root.findChildren(QTimer):
            timer.stop()
        self.client.stop_event.set()
        self.client.history_load_executor.shutdown(wait=False, cancel_futures=True)
        self.client.image_fetch_executor.shutdown(wait=False, cancel_futures=True)
        self.root.close_callback = None
        self.root.close()
        self.root.deleteLater()
        self.app.processEvents()
        for patch in reversed(self.patches):
            patch.stop()
        for name, value in self.properties.items():
            self.app.setProperty(name, value)
        self.app.setStyle(QStyleFactory.create("Fusion"))
        self.app.setPalette(self.palette)
        self.app.setStyleSheet(self.sheet)
        self.app.setFont(self.font)

    def test_empty_and_typed_single_line_keep_the_same_height_in_both_themes(self):
        initial_height = self.edit.height()
        for theme in ("Glassy", "Modern", "Glassy"):
            self.client._on_theme_changed(theme)
            self.edit.clear()
            QTest.qWait(20)
            self.assertEqual(self.edit.height(), initial_height)
            self.assertFalse(self.edit.verticalScrollBar().isVisible())
            QTest.keyClicks(self.edit, "Hello")
            QTest.qWait(20)
            self.assertEqual(self.edit.height(), initial_height)
            self.assertFalse(self.edit.verticalScrollBar().isVisible())
            self.assertEqual(self.edit.verticalScrollBar().maximum(), 0)

    def test_extra_lines_grow_until_six_then_scroll_and_shrink_when_cleared(self):
        for theme in ("Glassy", "Modern"):
            self.client._on_theme_changed(theme)
            self.edit.clear()
            QTest.qWait(20)
            initial_height = self.edit.height()
            previous_height = 0
            cap = None
            for count in range(1, 9):
                self.edit.setPlainText("\n".join("Line" for _ in range(count)))
                QTest.qWait(20)
                if count <= S.MESSAGE_ENTRY_MAX_LINES:
                    self.assertGreater(self.edit.height(), previous_height)
                    self.assertFalse(self.edit.verticalScrollBar().isVisible())
                    self.assertEqual(self.edit.verticalScrollBar().maximum(), 0)
                    self.assertEqual(self.edit.verticalScrollBarPolicy(), Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                    cap = self.edit.height()
                else:
                    self.assertEqual(self.edit.height(), cap)
                    self.assertTrue(self.edit.verticalScrollBar().isVisible())
                    self.assertGreater(self.edit.verticalScrollBar().maximum(), 0)
                previous_height = self.edit.height()
            self.edit.clear()
            QTest.qWait(20)
            self.assertEqual(self.edit.height(), initial_height)
            self.assertFalse(self.edit.verticalScrollBar().isVisible())

    def test_wrapping_updates_height_after_window_width_changes(self):
        self.root.resize(1100, 650)
        # Stay below the six-line cap to measure wrapping.
        self.edit.setPlainText("A message with several words to wrap. " * 6)
        QTest.qWait(30)
        wide_height = self.edit.height()
        self.root.resize(560, 650)
        QTest.qWait(30)
        self.assertGreater(self.edit.height(), wide_height)
        self.root.resize(1100, 650)
        QTest.qWait(30)
        self.assertEqual(self.edit.height(), wide_height)

    def test_emoji_canvas_action_and_composer_draft_round_trip(self):
        client = self.client
        client.image_menu_button.setChecked(True)
        self.assertEqual(client.send_button.text(), "Send")
        self.assertEqual(client.image_composer_label.text(), "Image message")
        client.image_menu.size_combo.setCurrentIndex(S.EMOJI_SIZE)
        self.assertEqual(client.image_menu.size_combo.currentText(), "Emoji (16x16)")
        self.assertEqual(client.send_button.text(), "Save to Emojis")
        self.assertFalse(client.send_button.isEnabled())

        pixels = bytes([2]) * 256
        client.image_menu.canvas.replace(S.EMOJI_SIZE, pixels)
        encoded = S.encode_drawing(S.EMOJI_SIZE, pixels)
        with mock.patch.object(client, "_save_emoji_dialog", return_value=True) as save:
            client.send_button.click()
            self.assertEqual(save.call_args.args[0], encoded)
        self.assertTrue(client._store_saved_emojis({"sleepy cat": encoded}))

        client.emojis_menu_button.setChecked(True)
        self.assertEqual(client.send_button.text(), "Send")
        self.edit.setPlainText("🐈 says ")
        self.edit.moveCursor(S.QTextCursor.MoveOperation.End)
        client._insert_saved_emoji(client.emojis_menu.item(0))
        QTest.keyClicks(self.edit, " hi")
        markup = f"🐈 says {S.emoji_markup('sleepy cat', encoded)} hi"
        self.assertEqual(self.edit.toPlainText(), "🐈 says \ufffc hi")
        self.assertEqual(self.edit.to_message_text(), markup)
        client._restore_active_chatroom_draft()
        self.assertEqual(self.edit.to_message_text(), markup)

        self.edit.selectAll()
        mime = self.edit.createMimeDataFromSelection()
        self.assertEqual(mime.text(), "🐈 says :sleepy cat: hi")
        self.edit.clear()
        self.edit.insertFromMimeData(mime)
        self.assertEqual(self.edit.to_message_text(), markup)
        self.edit.moveCursor(S.QTextCursor.MoveOperation.Left)
        self.edit.moveCursor(S.QTextCursor.MoveOperation.Left)
        self.edit.moveCursor(S.QTextCursor.MoveOperation.Left)
        QTest.keyClick(self.edit, Qt.Key.Key_Backspace)
        self.assertEqual(self.edit.to_message_text(), "🐈 says  hi")

    def test_tiny_inline_emoji_fits_text_and_spoilers_hide_the_pixels(self):
        client = self.client
        encoded = S.encode_drawing(S.EMOJI_SIZE, bytes([2]) * 256)
        markup = S.emoji_markup("sleepy cat", encoded)
        for ui_size in ("Small", "Tiny"):
            with self.subTest(ui_size=ui_size):
                client.config_data["ui_size"] = ui_size
                document = client.chat_display.document()
                document.clear()
                document.setTextWidth(400)
                client._insert_message_text_with_links(
                    S.QTextCursor(document), f"before <sp>{markup}</sp> after", "#000000",
                    S.DEFAULT_MESSAGE_FONT, "user", "message",
                    muted=False, ui_font=False, embedded_image_urls=set(),
                    align_top=False, top_align_height=0, render_emojis=True,
                )

                def image_format():
                    iterator = document.begin().begin()
                    while not iterator.atEnd():
                        formatting = iterator.fragment().charFormat()
                        if formatting.isImageFormat():
                            return formatting.toImageFormat()
                        iterator += 1
                    self.fail("No inline image")
                formatting = image_format()
                self.assertFalse(formatting.isAnchor())
                self.assertEqual(formatting.height(), 16)
                if ui_size == "Small":
                    self.assertEqual(
                        formatting.verticalAlignment(), S.QTextCharFormat.VerticalAlignment.AlignMiddle,
                    )
                client._toggle_rendered_spoiler("message:1")
                self.assertTrue(image_format().isAnchor())
                self.assertEqual(image_format().toolTip(), "sleepy cat")
                client._toggle_rendered_spoiler("message:1")
                self.assertFalse(image_format().isAnchor())

    def test_standalone_and_inline_emoji_use_same_chat_size(self):
        client = self.client
        client.config_data["ui_size"] = "Tiny"
        client._apply_ui_size()
        encoded = S.encode_drawing(S.EMOJI_SIZE, bytes([2]) * 256)
        self.edit.insert_emoji("sleepy cat", encoded)
        composer_image = self.edit.document().begin().begin().fragment().charFormat().toImageFormat()
        self.assertEqual(composer_image.height(), 16)
        markup = self.edit.to_message_text()
        self.edit.set_message_text(markup)
        restored_image = self.edit.document().begin().begin().fragment().charFormat().toImageFormat()
        self.assertEqual(restored_image.height(), 16)

        cases = (
            ("Small", markup, 16), ("Small", f"hi {markup}", 16),
            ("Tiny", markup, 16), ("Tiny", f"hi {markup}", 16),
            ("Tiny", f"{markup} {markup}", 16),
        )
        for ui_size, text, expected_size in cases:
            client.config_data["ui_size"] = ui_size
            with self.subTest(text=text):
                message = client._build_draft_message(text)
                item = {"message": message, "is_local": True, "ntfy_time": message["t"]}
                document = client.chat_display.document()
                document.clear()
                client._insert_message_item(
                    S.QTextCursor(document), item, muted_ids=set(), collapsed_ids=set(),
                    trusted_user_ids=set(), background_color="#ffffff", row_selections=[],
                )
                iterator = document.begin().begin()
                sizes = []
                while not iterator.atEnd():
                    formatting = iterator.fragment().charFormat()
                    if formatting.isImageFormat():
                        sizes.append(formatting.toImageFormat().height())
                    iterator += 1
                self.assertTrue(sizes)
                self.assertEqual(set(sizes), {expected_size})
                self.assertEqual(
                    document.begin().blockFormat().lineHeight(),
                    client._chat_line_height(S.DEFAULT_MESSAGE_FONT),
                )

    def test_full_size_emoji_pixels_fit_between_tiny_chat_rows(self):
        client = self.client
        client.config_data["ui_size"] = "Tiny"
        client._apply_ui_size()
        encoded = S.encode_drawing(S.EMOJI_SIZE, bytes([2]) * 256)
        emoji = S.emoji_markup("cat", encoded)
        color = S.emoji_image(encoded).pixel(0, 0)
        for font in (S.DEFAULT_MESSAGE_FONT, "Segoe UI"):
            with self.subTest(font=font):
                document = client.chat_display.document()
                document.clear()
                document.setTextWidth(400)
                cursor = S.QTextCursor(document)
                for text in ("previous", f"before {emoji} after", "next"):
                    message = client._build_draft_message(text)
                    message["f"] = font
                    item = {"message": message, "is_local": True, "ntfy_time": message["t"]}
                    client._insert_message_item(
                        cursor, item, muted_ids=set(), collapsed_ids=set(),
                        trusted_user_ids=set(), background_color="#ffffff", row_selections=[],
                    )
                layout = document.documentLayout()
                emoji_block = document.begin().next()
                top = layout.blockBoundingRect(emoji_block).top()
                bottom = layout.blockBoundingRect(emoji_block.next()).top()
                image = S.QImage(400, 100, S.QImage.Format.Format_RGB32)
                image.fill(S.QColor("white"))
                painter = S.QPainter(image)
                document.drawContents(painter)
                painter.end()
                colored_rows = [
                    y for y in range(image.height())
                    if any(image.pixel(x, y) == color for x in range(image.width()))
                ]
                self.assertEqual(len(colored_rows), 16)
                self.assertGreaterEqual(colored_rows[0], top)
                self.assertLess(colored_rows[-1], bottom)

    def test_save_emoji_turns_spaces_into_underscores(self):
        client = self.client
        encoded = S.encode_drawing(S.EMOJI_SIZE, bytes([2]) * 256)

        def edit_dialog(dialog):
            entry = dialog.findChild(S.QLineEdit)
            self.assertEqual(entry.text(), "sleepy_cat")
            self.assertNotIn("One or two words.", [label.text() for label in dialog.findChildren(S.QLabel)])
            entry.setText("happy")
            entry.setCursorPosition(5)
            QTest.keyClick(entry, Qt.Key.Key_Space)
            QTest.keyClicks(entry, "cat")
            self.assertEqual(entry.text(), "happy_cat")
            buttons = dialog.findChild(S.QDialogButtonBox)
            buttons.button(S.QDialogButtonBox.StandardButton.Save).click()
            return dialog.result()

        with mock.patch.object(S.QDialog, "exec", edit_dialog):
            self.assertTrue(client._save_emoji_dialog(encoded, "sleepy cat"))
        self.assertEqual(client.config_data["saved_emojis"], {"happy_cat": encoded})
        self.assertFalse(S.valid_emoji_name("happy cat"))

    def test_edit_emoji_saved_under_new_name_keeps_original(self):
        client = self.client
        original = S.encode_drawing(S.EMOJI_SIZE, bytes([2]) * 256)
        edited = S.encode_drawing(S.EMOJI_SIZE, bytes([3]) * 256)
        self.assertTrue(client._store_saved_emojis({"original": original}))

        def save_new_name(dialog):
            entry = dialog.findChild(S.QLineEdit)
            buttons = dialog.findChild(S.QDialogButtonBox)
            self.assertEqual(entry.text(), "original")
            entry.setText("edited_copy")
            self.assertTrue(buttons.button(S.QDialogButtonBox.StandardButton.Save).isEnabled())
            buttons.button(S.QDialogButtonBox.StandardButton.Save).click()
            return dialog.result()

        with mock.patch.object(S.QDialog, "exec", save_new_name):
            self.assertTrue(client._save_emoji_dialog(
                edited, "original", replace_name="original",
            ))
        self.assertEqual(
            client.config_data["saved_emojis"],
            {"original": original, "edited_copy": edited},
        )

        def overwrite_same_name(dialog):
            buttons = dialog.findChild(S.QDialogButtonBox)
            buttons.button(S.QDialogButtonBox.StandardButton.Save).click()
            return dialog.result()

        with mock.patch.object(S.QDialog, "exec", overwrite_same_name):
            self.assertTrue(client._save_emoji_dialog(
                edited, "original", replace_name="original",
            ))
        self.assertEqual(client.config_data["saved_emojis"]["original"], edited)
        self.assertIn("edited_copy", client.config_data["saved_emojis"])

    def test_chat_emoji_save_requires_unique_name_even_if_image_is_identical(self):
        client = self.client
        encoded = S.encode_drawing(S.EMOJI_SIZE, bytes([2]) * 256)
        self.assertTrue(client._store_saved_emojis({"Existing": encoded}))

        def save_from_chat(dialog):
            entry = dialog.findChild(S.QLineEdit)
            buttons = dialog.findChild(S.QDialogButtonBox)
            save = buttons.button(S.QDialogButtonBox.StandardButton.Save)
            hint = next(
                label for label in dialog.findChildren(S.QLabel)
                if label.text() == "Name already used."
            )
            self.assertEqual(entry.text(), "Existing")
            self.assertFalse(save.isEnabled())
            self.assertTrue(hint.isVisibleTo(dialog))
            entry.setText("existing")
            self.assertFalse(save.isEnabled())
            entry.setText("NewEmoji")
            self.assertTrue(save.isEnabled())
            self.assertFalse(hint.isVisibleTo(dialog))
            save.click()
            return dialog.result()

        with mock.patch.object(S.QDialog, "exec", save_from_chat):
            self.assertTrue(client._save_emoji_dialog(encoded, "Existing"))
        self.assertEqual(
            client.config_data["saved_emojis"],
            {"Existing": encoded, "NewEmoji": encoded},
        )

    def test_explicit_rename_emoji_changes_original_name(self):
        client = self.client
        encoded = S.encode_drawing(S.EMOJI_SIZE, bytes([4]) * 256)
        self.assertTrue(client._store_saved_emojis({"old": encoded}))

        def rename(dialog):
            dialog.findChild(S.QLineEdit).setText("new")
            dialog.findChild(S.QDialogButtonBox).button(
                S.QDialogButtonBox.StandardButton.Save
            ).click()
            return dialog.result()

        with mock.patch.object(S.QDialog, "exec", rename):
            self.assertTrue(client._save_emoji_dialog(
                encoded, "old", replace_name="old",
                title="Rename Emoji", rename_original=True,
            ))
        self.assertEqual(client.config_data["saved_emojis"], {"new": encoded})

    def test_emoji_tooltip_uses_chat_hover_delay_and_profile_preview_size(self):
        client = self.client
        encoded = S.encode_drawing(S.EMOJI_SIZE, bytes([2]) * 256)
        anchor = f"spritelink-emoji:{S.emoji_markup('happy_cat', encoded)}"
        with mock.patch.object(S.QToolTip, "showText") as show:
            client._schedule_chat_tooltip(anchor, S.QPoint(10, 20))
            self.assertEqual(client.chat_tooltip_timer.interval(), S.CHAT_TOOLTIP_HOVER_DELAY_MS)
            show.assert_not_called()
            client._show_pending_chat_tooltip()
            tooltip = show.call_args.args[1]
            self.assertIn('width="64" height="64"', tooltip)
            self.assertIn("data:image/png;base64,", tooltip)
            self.assertIn("happy_cat", tooltip)

    def test_make_emoji_link_stays_visible_and_opens_emoji_canvas(self):
        client = self.client
        client.emojis_menu_button.setChecked(True)
        self.assertTrue(client.make_emoji_link.isVisible())
        self.assertFalse(client.emojis_menu.isVisible())
        self.assertEqual(
            client.make_emoji_link.text(),
            '<a href="image">Make an Emoji</a>',
        )
        client.make_emoji_link.linkActivated.emit("image")
        self.assertTrue(client.image_menu_button.isChecked())
        self.assertFalse(client.emojis_menu_button.isChecked())
        self.assertEqual(client.image_menu.size_combo.currentIndex(), S.EMOJI_SIZE)
        self.assertEqual(client.send_button.text(), "Save to Emojis")
        encoded = S.encode_drawing(S.EMOJI_SIZE, bytes(256))
        self.assertTrue(client._store_saved_emojis({"hello": encoded}))
        client.emojis_menu_button.setChecked(True)
        self.assertTrue(client.emojis_menu.isVisible())
        self.assertTrue(client.make_emoji_link.isVisible())
        self.assertEqual(client.emojis_menu.iconSize(), S.QSize(32, 32))
        self.assertEqual(client.emojis_menu.item(0).icon().actualSize(S.QSize(32, 32)), S.QSize(32, 32))
        self.assertEqual(client.emojis_menu.selectionMode(), S.QAbstractItemView.SelectionMode.NoSelection)
        self.assertIsInstance(client.emojis_menu.itemDelegate(), S.NoFocusRectItemDelegate)
        viewport = client.emojis_menu.viewport()
        position = client.emojis_menu.visualItemRect(client.emojis_menu.item(0)).center()
        QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, pos=position)
        self.assertEqual(self.edit.toPlainText(), "\ufffc")
        QTest.mouseDClick(viewport, Qt.MouseButton.LeftButton, pos=position)
        QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=position)
        self.assertEqual(self.edit.toPlainText(), "\ufffc\ufffc")
        self.assertFalse(client.emojis_menu.selectedItems())
        client.make_emoji_link.linkActivated.emit("image")
        self.assertEqual(client.image_composer_label.text(), "Creating Emoji")
        client.image_menu.size_combo.setCurrentIndex(0)
        self.assertEqual(client.image_composer_label.text(), "Image message")

    def test_canvas_selection_outline_only_appears_for_select_or_move(self):
        client = self.client
        client.image_menu_button.setChecked(True)
        canvas = client.image_menu.canvas
        canvas.selection = QRect(3, 3, 12, 12)
        self.app.processEvents()

        buttons = {
            button.text(): button
            for button in client.image_menu.findChildren(S.QPushButton)
            if button.text() in ("Pencil", "Line", "Bucket", "Select", "Move")
        }
        self.assertEqual(set(buttons), {"Pencil", "Line", "Bucket", "Select", "Move"})

        buttons["Select"].click()
        selected = canvas.grab().toImage()
        for tool in ("Pencil", "Line", "Bucket"):
            with self.subTest(tool=tool):
                buttons[tool].click()
                self.assertEqual(canvas.tool, tool)
                self.assertEqual(canvas.selection, QRect(3, 3, 12, 12))
                no_outline = canvas.grab().toImage()
                self.assertNotEqual(selected, no_outline)
                canvas.selection = QRect()
                self.assertEqual(canvas.grab().toImage(), no_outline)
                canvas.selection = QRect(3, 3, 12, 12)

        buttons["Move"].click()
        self.assertEqual(canvas.grab().toImage(), selected)
        buttons["Pencil"].click()
        self.assertNotEqual(canvas.grab().toImage(), selected)

    def test_native_image_context_menu_separates_image_and_message_actions(self):
        client = self.client
        drawing = S.encode_drawing(1, bytes([2]) * 3600)
        message = client._build_draft_message("", drawing)
        item = {"message": message, "is_local": True, "ntfy_time": message["t"]}
        chat = client.chat_display
        viewport = chat.viewport()
        message_id = message["i"]

        def render():
            chat.document().clear()
            client._insert_message_item(
                S.QTextCursor(chat.document()), item, muted_ids=set(),
                collapsed_ids=client._room_preference_ids("collapsed_messages"),
                trusted_user_ids=set(), background_color="#ffffff", row_selections=[],
            )
            chat.document().size()

        def menu_at(position, trigger=None):
            labels = []

            class InspectableMenu(S.QMenu):
                def exec(self, _position):
                    labels.extend(action.text() for action in self.actions())
                    if trigger:
                        next(action for action in self.actions() if action.text() == trigger).trigger()

            event = QContextMenuEvent(
                QContextMenuEvent.Reason.Mouse, position, viewport.mapToGlobal(position),
            )
            with mock.patch.object(S, "QMenu", InspectableMenu):
                QApplication.sendEvent(viewport, event)
            return labels

        render()
        image_point = next(
            S.QPoint(x, y)
            for y in range(5, min(120, viewport.height()), 5)
            for x in range(10, min(300, viewport.width()), 5)
            if chat.anchorAt(S.QPoint(x, y)) == f"spritelink-drawing:{message_id}"
        )
        beside_image = S.QPoint(viewport.width() - 15, image_point.y())
        self.assertEqual(menu_at(image_point), ["Copy Image", "Import Image"])
        with mock.patch.object(client, "_rerender_preserving_scroll", side_effect=render):
            self.assertEqual(
                menu_at(beside_image, "Collapse Message"), ["Copy Message", "Collapse Message"],
            )
            self.assertIn("[Image]", chat.toPlainText())
            self.assertNotIn("\ufffc", chat.toPlainText())
            self.assertEqual(
                menu_at(S.QPoint(beside_image.x(), 10), "Expand Message"),
                ["Copy Message", "Expand Message"],
            )
        self.assertEqual(chat.anchorAt(image_point), f"spritelink-drawing:{message_id}")


if __name__ == "__main__":
    unittest.main()
