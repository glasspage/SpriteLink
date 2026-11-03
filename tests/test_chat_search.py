from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from PySide6.QtCore import QUrl
from PySide6.QtGui import QColor, QImage, QTextDocument
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QPushButton
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

    def test_text_and_media_filters(self):
        def find(text, query="", *filters):
            return S.message_text_for_search({"message": {"m": text}}, query, set(filters))

        self.assertEqual(find("A <b>Blue</b> Moon", "blue moon"), "A Blue Moon")
        self.assertIsNone(find("<sp>secret</sp>", "secret"))
        self.assertIsNotNone(find("https://example.org", "", "Links"))
        self.assertIsNotNone(find("https://i.imgur.com/example.png", "", "Images"))
        self.assertIsNotNone(find("https://example.org/example.mp4", "", "Videos"))
        self.assertIsNotNone(find("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "", "Videos"))
        self.assertIsNone(find("https://example.org/example.mp4", "", "Images"))
        self.assertIsNone(find("https://i.imgur.com/example.png", "unrelated", "Images"))
        self.assertIsNotNone(find("https://example.org/example.mp4", "", "Images", "Videos"))

    def test_search_message_uses_chat_formatting_and_lighter_background(self):
        client = self.client
        message = client._build_draft_message("<b>Bold</b> <i>Italic</i> <u>Under</u> <sp>secret</sp>")
        body = client._search_result_message(message)
        document = QTextDocument()
        document.setHtml(body.text())
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
        document.setHtml(body.text())
        image_format = document.begin().begin().fragment().charFormat().toImageFormat()
        self.assertEqual(image_format.width(), S.PROFILE_ICON_SIZE)
        image = document.resource(QTextDocument.ResourceType.ImageResource,
                                  QUrl(image_format.name()))
        self.assertFalse(image.isNull())
        self.client.config_data["ui_size"] = "Tiny"
        self.assertNotIn("<img", self.client._search_result_message(message).text())

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
        body = frame.findChild(QLabel, "searchResultMessage")
        before = body.text()
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
        after = body.text()
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

    def test_saved_history_search_pagination_and_jump(self):
        client = self.client
        self.root.resize(800, 600)
        self.root.show()
        server, key = client.config_data["server_url"], client._active_chatroom()["key"]
        entries = []
        for index in range(100):
            text = "https://example.org/example" if index < 30 else f"Message {index}"
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
            config_width = client.config_panel.width()
            client.config_toggle.click()
            client.search_toggle.click()
            self.app.processEvents()
            self.assertEqual(client.search_overlay.panel.width(), config_width)
            client.search_filters["Links"].click()
            self.wait_for(lambda: client.search_results_layout.count() == 25)
            client.search_more_button.click()
            self.wait_for(lambda: client.search_results_layout.count() == 30)
            frame = client.search_results_layout.itemAt(29).widget()
            frame.findChild(QPushButton).click()
            self.wait_for(lambda: not client._rendering_message_log)
            self.assertTrue(client.search_overlay.isHidden())
            self.assertIn("item-000", client.rendered_message_last_blocks)
            self.assertNotIn("item-099", client.rendered_message_last_blocks)
            self.assertTrue(client.latest_history_button.isVisible())
            client.latest_history_button.click()
            self.wait_for(lambda: not client._rendering_message_log)
            self.assertIn("item-099", client.rendered_message_last_blocks)
            self.assertTrue(client.latest_history_button.isHidden())
