from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from PySide6.QtCore import QUrl
from PySide6.QtGui import QColor, QImage, QPainter, QTextCursor, QTextDocument
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton
from spritelink_video import VideoInfo
from test_startup import DeferredStartupTests
from test_security import SPRITELINK as S


class ChatSearchTests(unittest.TestCase):
    setUpClass = classmethod(DeferredStartupTests.setUpClass.__func__)
    setUp = DeferredStartupTests.setUp
    tearDown = DeferredStartupTests.tearDown

    def wait_for(self, condition):
        for _ in range(600):
            if condition():
                return
            QTest.qWait(5)
        self.fail("Timed out waiting for chat search")

    def test_live_messages_preserve_view_until_scrolled_into_view(self):
        client = self.client
        self.root.resize(800, 600)
        self.root.show()

        def receive(index: int, text: str, *, live: bool = True) -> None:
            timestamp = 1_730_000_000 + index
            message = client._build_draft_message(text)
            message.update(i=f"live-{index}", t=timestamp, c="remote-user")
            self.assertTrue(client._accept_network_message(
                {"message": message, "ntfy_id": f"ntfy-{index}", "ntfy_time": timestamp},
                persist=False, play_chime=live,
            ))

        display = client.chat_display
        scrollbar = display.verticalScrollBar()
        client.window_focused_event.set()
        with mock.patch.object(client, "_show_desktop_notification"), mock.patch.object(
            client, "_play_notification_sound",
        ):
            receive(0, "First message", live=False)
            original = client._capture_chat_view_anchor()
            receive(1, "New message")
            receive(2, "Another new message")
            self.assertEqual(client._capture_chat_view_anchor()["viewport_offset"], original["viewport_offset"])
            self.assertTrue(display.show_unread_bottom_edge)
            image = display.viewport().grab().toImage()
            for offset in (1, 2):
                self.assertEqual(image.pixelColor(5, image.height() - offset), QColor("#ff8a00"))
            scrollbar.setValue(scrollbar.maximum())
            self.wait_for(lambda: not display.show_unread_bottom_edge)

            for index in range(3, 25):
                receive(index, f"History message {index}", live=False)
            original = client._capture_chat_view_anchor()
            receive(25, "Newest message")
            receive(26, "Newest message")  # Repeated messages rebuild the shared row.
            self.wait_for(lambda: not client._media_rerender_in_progress)
            self.assertLess(scrollbar.value(), scrollbar.maximum())
            self.assertEqual(client._capture_chat_view_anchor()["message_id"], original["message_id"])
            self.assertEqual(client._capture_chat_view_anchor()["viewport_offset"], original["viewport_offset"])
            self.assertTrue(display.show_unread_bottom_edge)
            scrollbar.setValue(scrollbar.maximum())
            self.wait_for(lambda: not display.show_unread_bottom_edge)

    def test_text_and_media_filters(self):
        def find(text, query="", *filters):
            return S.message_text_for_search({"message": {"m": text}}, query, set(filters))

        self.assertEqual(find("A <b>Blue</b> Moon", "blue moon"), "A Blue Moon")
        self.assertIsNone(find("<sp>secret</sp>", "secret"))
        self.assertIsNotNone(find("https://example.org", "", "Links"))
        self.assertIsNone(find("https://i.imgur.com/example.png", "", "Links"))
        self.assertIsNone(find("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "", "Links"))
        self.assertIsNotNone(find("https://i.imgur.com/example.png https://example.org", "", "Links"))
        self.assertIsNotNone(find("https://i.imgur.com/example.png", "", "Links", "Embedded Images"))
        self.assertIsNotNone(find("https://i.imgur.com/example.png", "", "Embedded Images"))
        self.assertIsNone(find("https://i.imgur.com/example.png", "", "Images"))
        drawing = {"message": {"m": "", "drawing": S.encode_drawing(0, bytes(5400))}}
        self.assertEqual(S.message_text_for_search(drawing, "", {"Images"}), "")
        self.assertIsNone(S.message_text_for_search(drawing, "", {"Embedded Images", "Embedded Videos"}))
        self.assertIsNotNone(find("https://example.org/example.mp4", "", "Embedded Videos"))
        self.assertIsNotNone(find("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "", "Embedded Videos"))
        self.assertIsNone(find("https://example.org/example.mp4", "", "Embedded Images"))
        self.assertIsNone(find("https://i.imgur.com/example.png", "unrelated", "Embedded Images"))
        self.assertIsNotNone(find("https://example.org/example.mp4", "", "Embedded Images", "Embedded Videos"))

    def test_search_message_uses_chat_formatting_and_lighter_background(self):
        client = self.client
        message = client._build_draft_message("<b>Bold</b> <i>Italic</i> <u>Under</u> <sp>secret</sp>")
        body = client._search_result_message(message)
        body.resize(600, body.heightForWidth(600))
        body.show()
        self.app.processEvents()
        document = QTextDocument()
        document.setHtml(body.toHtml())
        bold = document.find("Bold").charFormat()
        self.assertEqual(bold.fontWeight(), 700)
        self.assertEqual(
            bold.fontPointSize(),
            client._make_message_font(message.get("f", S.DEFAULT_MESSAGE_FONT), bold=True,
                                      role="chat_log").pointSizeF(),
        )
        self.assertTrue(document.find("Italic").charFormat().fontItalic())
        self.assertTrue(document.find("Under").charFormat().fontUnderline())
        self.assertNotIn("secret", document.toPlainText())
        self.assertIn(max(S.message_row_backgrounds(), key=lambda color: QColor(color).lightness()),
                      body.styleSheet())

    def test_search_message_contains_a_loadable_inline_profile_icon(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "profile.png"
            S.Image.new("RGB", (16, 16), (20, 100, 180)).save(path)
            icon = S.encode_profile_icon(S.optimize_profile_icon(str(path)))
        message = self.client._build_draft_message("Hello")
        message["p"] = icon
        body = self.client._search_result_message(message)
        document = QTextDocument()
        document.setHtml(body.toHtml())
        image_format = document.begin().begin().fragment().charFormat().toImageFormat()
        self.assertEqual(image_format.width(), S.PROFILE_ICON_SIZE)
        self.assertEqual(image_format.height(), S.PROFILE_ICON_SIZE)
        image = document.resource(QTextDocument.ResourceType.ImageResource,
                                  QUrl(image_format.name()))
        self.assertFalse(image.isNull())
        self.assertEqual((image.width(), image.height()), (16, 16))
        self.client.config_data["ui_size"] = "Tiny"
        self.assertNotIn("<img", self.client._search_result_message(message).toHtml())

    def test_chat_profile_icons_remain_exactly_16_pixels_in_normal_and_large_ui(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "profile.png"
            S.Image.new("RGB", (16, 16), (20, 100, 180)).save(path)
            encoded_icon = S.encode_profile_icon(S.optimize_profile_icon(str(path)))

        for ui_size in ("Small (Default)", "Large"):
            with self.subTest(ui_size=ui_size):
                self.client.config_data["ui_size"] = ui_size
                self.assertTrue(self.client._chat_icons_visible())
                document = QTextDocument()
                cursor = QTextCursor(document)
                self.assertTrue(
                    self.client._insert_profile_icon(
                        cursor, encoded_icon, "profile-icon-test"
                    )
                )
                image_format = (
                    document.begin().begin().fragment().charFormat().toImageFormat()
                )
                self.assertEqual(
                    (image_format.width(), image_format.height()), (16.0, 16.0),
                )
                resource = document.resource(
                    QTextDocument.ResourceType.ImageResource,
                    QUrl(image_format.name()),
                )
                self.assertEqual(
                    (resource.width(), resource.height()), (16, 16),
                )
                self.assertEqual(resource.devicePixelRatio(), 1.0)

        self.client.config_data["ui_size"] = "Tiny"
        self.assertFalse(self.client._chat_icons_visible())

    def test_search_embeds_noninteractive_media_and_clears_query_on_room_change(self):
        client = self.client
        self.root.show()
        client.search_toggle.click()
        client.search_entry.setText("find me")
        client.search_timer.stop()
        image_url = "https://i.imgur.com/example.png"
        video_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        image = QImage(40, 30, QImage.Format.Format_RGB32)
        image.fill(QColor("blue"))
        link = S.video_link(video_url, lambda _: True)
        client.image_preview_cache[video_url] = S.RemoteMediaPreview(
            video_url, b"", "video", image, VideoInfo(link, "youtube.com"),
        )
        ordinary_url = "https://example.org"
        message = client._build_draft_message(f"find me {image_url} {video_url} {ordinary_url}")
        with mock.patch.object(client, "_schedule_image_preview_fetch") as fetch:
            client._show_chat_search_results(client._search_generation, [
                ((1, 1, message["i"]), message["i"], 1, message),
            ], None)
            fetch.assert_called_once_with(image_url)
        frame = client.search_results_layout.itemAt(0).widget()
        body = frame.findChild(S.SearchResultMessage, "searchResultMessage")
        before = body.toHtml()
        self.assertEqual(before.count("<img"), 2)
        self.assertNotIn(image_url, before)
        self.assertNotIn(video_url, before)
        self.assertIn(ordinary_url, before)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "preview.png"
            image.save(str(path))
            client.ui_queue.put(("image_preview_loaded", {
                "url": image_url, "kind": "static_image", "data": path.read_bytes(),
            }))
            client._process_ui_queue()
        after = body.toHtml()
        self.assertNotEqual(before, after)
        self.assertEqual(after.count("<img"), 2)
        self.assertNotIn(image_url, after)
        self.assertNotIn(video_url, after)
        self.assertIn(ordinary_url, after)
        client.config_data["chatrooms"] = [
            {"id": "room-b", "nickname": "Room B", "key": "b" * 32},
        ]
        client._activate_chatroom("room-b")
        self.assertTrue(client.search_overlay.isHidden())
        self.assertEqual(client.search_entry.text(), "")
        self.assertFalse(client._search_media_updates)

    def test_wrapped_result_measurement_does_not_reflow_the_visible_document(self):
        body = self.client._search_result_message(
            self.client._build_draft_message("Wrapped message " * 5)
        )
        body.resize(350, body.heightForWidth(350))
        body.show()
        self.app.processEvents()
        document = body.document()
        document.size()
        width = document.textWidth()
        lines = document.begin().layout().lineCount()
        self.assertEqual(lines, 1)
        self.assertTrue(document.toPlainText().endswith("[...]"))
        self.assertEqual(body.heightForWidth(100), body.heightForWidth(350))
        body.heightForWidth(100)
        self.assertEqual(document.textWidth(), width)
        self.assertEqual(document.begin().layout().lineCount(), lines)
        # A fixed 1200px width can still wrap on Windows with larger Qt
        # font metrics/DPI. Measure the actual full, unelided source instead.
        measure = QTextDocument()
        measure.setDefaultFont(document.defaultFont())
        measure.setDocumentMargin(document.documentMargin())
        measure.setHtml(body._source_html)
        measure.setTextWidth(10000)
        measure.size()
        natural_width = measure.begin().layout().lineAt(0).naturalTextWidth()
        wide_width = max(1200, int(natural_width) + 160)
        body.resize(wide_width, body.heightForWidth(wide_width))
        self.app.processEvents()
        self.assertNotIn(
            "[...]", document.toPlainText(),
            f"widget={body.width()} viewport={body.viewport().width()} "
            f"measured={body._preview_width} source_has_marker={('[...]' in body._source_html)} "
            f"same_doc={document is body.document()} "
            f"current_text={body.document().toPlainText()!r} "
            f"blocks={body.document().blockCount()} "
            f"lines={body.document().begin().layout().lineCount()}",
        )
        self.assertEqual(document.toPlainText().count("Wrapped message"), 5)
        # Ensure that a second and third resize rebuild from the original
        # rich text, rather than from a truncated copy of the first preview.
        original_html = body._source_html
        full_text = document.toPlainText()
        for _ in range(3):
            body.resize(250, body.heightForWidth(250))
            self.app.processEvents()
            self.assertIn("[...]", document.toPlainText())
            body.resize(wide_width, body.heightForWidth(wide_width))
            self.app.processEvents()
            self.assertEqual(document.toPlainText(), full_text)
            self.assertEqual(body._source_html, original_html)
        body.close()

    def test_saved_history_search_pagination_and_jump(self):
        client = self.client
        self.root.resize(800, 600)
        self.root.show()
        server, key = client.config_data["server_url"], client._active_chatroom()["key"]
        entries = []
        for index in range(100):
            text = "https://example.org/example" if index < 30 else f"Message {index}"
            if index == 29:
                text += " " + "wrapped result " * 6
            message = client._build_draft_message(text)
            message.update(i=f"item-{index:03}", t=1_730_000_000 + index)
            S.sign_message_identity(message, key, client._identity_private_key())
            entries.append({"message": message, "ntfy_time": message["t"]})
        with (
            TemporaryDirectory() as directory,
            mock.patch.object(S, "CHATROOM_HISTORY_DIRECTORY", Path(directory)),
            mock.patch.object(S, "dpapi_encrypt", side_effect=lambda data, _: data),
            mock.patch.object(S, "dpapi_decrypt", side_effect=lambda data, _: data),
        ):
            S.save_chatroom_history(server, key, entries)
            client.message_log = [dict(item, is_local=True) for item in entries]
            client.config_toggle.click()
            self.app.processEvents()
            client.config_toggle.click()
            client.search_toggle.click()
            self.app.processEvents()
            # Filters intentionally stay on one row. They can make Search a
            # little wider than Config at some Windows font/DPI settings.
            self.assertEqual(
                client.search_overlay.panel.maximumWidth(),
                client.config_panel.maximumWidth(),
            )
            self.assertLessEqual(
                client.search_overlay.panel.width(),
                client.search_overlay.panel.maximumWidth(),
            )
            self.assertEqual(
                len({checkbox.y() for checkbox in client.search_filters.values()}),
                1,
            )
            client.search_filters["Links"].click()
            client._start_chat_search()
            self.assertEqual(client.search_status.text(), "Searching...")
            self.wait_for(lambda: client.search_results_layout.count() > 0)
            self.assertLess(client.search_results_layout.count(), 25)
            self.assertEqual(client.search_status.text(), "Searching...")
            first_card = client.search_results_layout.itemAt(0).widget()
            self.app.processEvents()
            first_size = first_card.size()
            first_body = first_card.findChild(S.SearchResultMessage)
            document = first_body.document()
            document.size()
            first_lines = document.begin().layout().lineCount()
            self.assertEqual(first_lines, 1)
            self.assertTrue(document.toPlainText().endswith("[...]"))

            def assert_card_spacing(count: int) -> None:
                layout = client.search_results_layout
                for index in range(1, count):
                    previous = layout.itemAt(index - 1).widget()
                    card = layout.itemAt(index).widget()
                    gap = card.y() - previous.y() - previous.height()
                    self.assertEqual(gap, layout.spacing(), f"before card {index + 1}")

            for count in (2, 3, 5, 10, 15, 20, 25):
                self.wait_for(lambda: (
                    client.search_results_layout.count() >= count
                    and client.search_results_layout.itemAt(count - 1).widget().y() > first_card.y()
                ))
                self.assertEqual(first_card.size(), first_size, f"after {count} cards")
                assert_card_spacing(count)
                self.assertGreaterEqual(first_body.viewport().height(), document.size().height())
            self.wait_for(lambda: client.search_status.text() == "25+ results found")
            self.assertEqual(client.search_results_layout.count(), 25)
            self.assertTrue(client.search_more_button.isVisible())
            client.search_more_button.click()
            self.wait_for(lambda: client.search_status.text() == "30 results found.")
            self.assertTrue(client.search_more_button.isHidden())
            self.assertEqual(first_card.size(), first_size)
            self.assertEqual(document.begin().layout().lineCount(), first_lines)
            self.assertEqual(client.search_results_layout.count(), 30)
            assert_card_spacing(30)
            frame = client.search_results_layout.itemAt(24).widget()
            frame.findChild(QPushButton).click()
            self.wait_for(lambda: not client._rendering_message_log)
            self.assertTrue(client.search_overlay.isHidden())
            self.assertIn("item-000", client.rendered_message_last_blocks)
            display = client.chat_display
            document = display.document()
            block_number = next(
                number for number, message_id in client.rendered_message_blocks.items()
                if message_id == "item-005"
            )
            target_top = document.documentLayout().blockBoundingRect(
                document.findBlockByNumber(block_number)
            ).top()
            scrollbar = display.verticalScrollBar()
            self.assertGreater(round(target_top), scrollbar.minimum())
            self.assertLessEqual(round(target_top), scrollbar.maximum())
            self.assertEqual(scrollbar.value(), round(target_top))
            self.assertNotIn("item-099", client.rendered_message_last_blocks)
            self.assertTrue(client.latest_history_button.isVisible())
            client.latest_history_button.click()
            self.wait_for(lambda: not client._rendering_message_log)
            self.assertIn("item-099", client.rendered_message_last_blocks)
            self.assertTrue(client.latest_history_button.isHidden())
