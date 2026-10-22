import io
import unittest
from unittest import mock

from PIL import Image
from PySide6.QtCore import QUrl
from PySide6.QtGui import QTextCursor, QTextDocument
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from test_security import SPRITELINK as S
import test_chatroom_preferences


class ProgressiveImageTests(unittest.TestCase):
    setUp = test_chatroom_preferences.ChatroomPreferenceTests.setUp
    url = "https://i.imgur.com/early.png"

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        self.client._reset_history_render_window()
        test_chatroom_preferences.ChatroomPreferenceTests.tearDown(self)

    def _items(self, count=12, image_index=None, url=None):
        return [{
            "message": {"i": str(i), "t": 1730000000 + i * 60,
                        "u": "Alex", "c": "test-user", "k": "#007f82",
                        "m": (url or self.url) if i == (count - 1 if image_index is None else image_index)
                        else f"Message {i}"},
            "ntfy_time": 1730000000 + i * 60, "is_local": False,
        } for i in range(count)]

    def _start(self, items, *, initial=True):
        c = self.client
        self.root.resize(800, 600)
        self.root.show()
        if initial:
            c.ui_queue.put(("history_chunk_loaded", {
                "generation": c._history_render_generation,
                "room_id": c.active_chatroom_id,
                "initial": True, "items": items,
            }))
            c._process_ui_queue()
        else:
            c.message_log = items
            c._render_message_log(scroll_to_bottom=False)

    def _deliver(self, url=None):
        data = io.BytesIO()
        Image.new("RGB", (96, 64), "red").save(data, format="PNG")
        self.client.ui_queue.put(("image_preview_loaded", {
            "url": url or self.url, "source_url": url or self.url,
            "kind": "static_image", "data": data.getvalue(),
        }))
        self.client._process_ui_queue()

    def _wait_for(self, predicate, timeout=2000):
        for _ in range(timeout // 5):
            if predicate():
                return
            QTest.qWait(5)
        self.fail("Timed out waiting for progressive image update")

    def _assert_image(self, url=None):
        c = self.client
        url = url or self.url
        self.assertIn(url, c.rendered_loaded_image_urls)
        document = c.chat_display.document()
        for position in c.rendered_image_positions[url]:
            cursor = QTextCursor(document)
            cursor.setPosition(position)
            cursor.movePosition(QTextCursor.MoveOperation.NextCharacter, QTextCursor.MoveMode.KeepAnchor)
            formatting = cursor.charFormat().toImageFormat()
            resource = document.resource(QTextDocument.ResourceType.ImageResource, QUrl(formatting.name()))
            self.assertEqual((formatting.width(), formatting.height()), (96, 64))
            self.assertEqual(resource.pixelColor(10, 10).name(), "#ff0000")

    def test_first_visible_image_downloads_and_displays_before_initial_render_finishes(self):
        c = self.client
        with mock.patch.object(S, "MESSAGE_RENDER_STEP_DELAY_MS", 40), \
             mock.patch.object(c.image_fetch_executor, "submit") as submit:
            self._start(self._items())
            self.assertTrue(c._rendering_message_log)
            self._wait_for(lambda: submit.called)
            submit.assert_called_once_with(c._fetch_remote_image_preview, self.url)
            job = c._message_render_job
            self.assertTrue(c._rendering_message_log)
            self.assertFalse(c._history_initial_render_complete)
            old_position = c.rendered_image_positions[self.url][0]
            self._deliver()
            self._assert_image()
            self.assertIs(c._message_render_job, job)
            self.assertTrue(c._rendering_message_log)
            self.assertFalse(c._media_rerender_in_progress)
            self.assertFalse(c._history_render_updates_suppressed)
            self._wait_for(lambda: c._history_initial_render_complete)
            self._assert_image()
            self.assertGreater(c.rendered_image_positions[self.url][0], old_position)
            self.assertEqual(submit.call_count, 1)
            self.assertEqual(c.chat_display.verticalScrollBar().value(), c.chat_display.verticalScrollBar().maximum())

    def test_forward_rows_and_cached_images_load_during_render(self):
        c = self.client
        with mock.patch.object(S, "MESSAGE_RENDER_STEP_DELAY_MS", 40), \
             mock.patch.object(c.image_fetch_executor, "submit") as submit:
            self._deliver()
            self._start(self._items(image_index=0), initial=False)
            self._wait_for(lambda: self.url in c.rendered_loaded_image_urls)
            self.assertTrue(c._rendering_message_log)
            self._assert_image()
            submit.assert_not_called()
            self._wait_for(lambda: not c._rendering_message_log)

    def test_unrendered_collapsed_and_untrusted_images_are_not_requested(self):
        c = self.client
        with mock.patch.object(S, "MESSAGE_RENDER_STEP_DELAY_MS", 40), \
             mock.patch.object(c.image_fetch_executor, "submit") as submit:
            items = self._items(image_index=0)
            items[-1]["message"]["m"] = "https://untrusted.example/image.png"
            items[-2]["message"]["m"] = "https://i.imgur.com/collapsed.png"
            c._write_room_preference_ids("collapsed_messages", {str(len(items) - 2)})
            self._start(items)
            QTest.qWait(80)
            self.assertNotIn(self.url, c.rendered_image_candidates)
            submit.assert_not_called()
            self._wait_for(lambda: not c._rendering_message_log)
            self.assertNotIn("https://untrusted.example/image.png", c.rendered_image_candidates)
            self.assertNotIn("https://i.imgur.com/collapsed.png", c.rendered_image_candidates)

    def test_older_page_image_loads_while_preserving_visible_message_anchor(self):
        c = self.client
        items = self._items(36, image_index=5)
        self._start(items[6:])
        self._wait_for(lambda: c._history_initial_render_complete)
        c.chat_display.verticalScrollBar().setValue(0)
        anchor = c._capture_chat_view_anchor()

        def visible_offset():
            number = min(number for number, message_id in c.rendered_message_blocks.items()
                         if message_id == anchor["message_id"])
            block = c.chat_display.document().findBlockByNumber(number)
            return (c.chat_display.document().documentLayout().blockBoundingRect(block).top()
                    - c.chat_display.verticalScrollBar().value())

        offset = visible_offset()
        c.message_log = items
        c.rendered_history_message_limit = 36
        with mock.patch.object(S, "MESSAGE_RENDER_STEP_DELAY_MS", 40), \
             mock.patch.object(c.image_fetch_executor, "submit") as submit:
            c._loading_older_history_page = True
            c._prepend_older_history_page(c._history_render_generation, c.active_chatroom_id, 30, 36)
            self._wait_for(lambda: submit.called)
            self.assertTrue(c._loading_older_history_page)
            self._deliver()
            self._assert_image()
            self.assertTrue(c._rendering_message_log)
            self.assertAlmostEqual(visible_offset(), offset, delta=1)
            self._wait_for(lambda: not c._loading_older_history_page)
            self.assertAlmostEqual(visible_offset(), offset, delta=1)

    def test_room_switch_does_not_apply_old_image_result_to_new_history(self):
        c = self.client
        with mock.patch.object(S, "MESSAGE_RENDER_STEP_DELAY_MS", 40), \
             mock.patch.object(c.image_fetch_executor, "submit") as submit:
            self._start(self._items())
            self._wait_for(lambda: submit.called)
            c._activate_chatroom("room-a")
            document = c.chat_display.document()
            self._deliver()
            QTest.qWait(80)
            self.assertIs(c.chat_display.document(), document)
            self.assertTrue(document.isEmpty())
            self.assertFalse(c.rendered_loaded_image_urls)
            self.assertFalse(c._rendering_message_log)

    def test_suppressed_layout_can_prefetch_without_applying_images(self):
        c = self.client
        with mock.patch.object(S, "MESSAGE_RENDER_STEP_DELAY_MS", 40), \
             mock.patch.object(c.image_fetch_executor, "submit") as submit:
            self._start(self._items())
            c._set_history_render_updates_suppressed(True)
            self._wait_for(lambda: submit.called)
            self._deliver()
            self.assertNotIn(self.url, c.rendered_loaded_image_urls)
            self.assertTrue(c._history_render_updates_suppressed)
            c._set_history_render_updates_suppressed(False)
            c._update_viewport_media()
            self._assert_image()


if __name__ == "__main__":
    unittest.main()
