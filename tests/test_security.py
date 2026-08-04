from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
import base64
import copy
import inspect
import sys
import tempfile
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "SpriteLink.pyw"
LOADER = SourceFileLoader("spritelink_security_test_module", str(MODULE_PATH))
SPEC = spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
SPRITELINK = module_from_spec(SPEC)
sys.modules[SPEC.name] = SPRITELINK
LOADER.exec_module(SPRITELINK)


class LazyViewportMediaTests(unittest.TestCase):
    def test_config_dropdowns_ignore_mouse_wheel_changes(self) -> None:
        source = inspect.getsource(SPRITELINK.ThemeComboBox.wheelEvent)
        self.assertIn("event.ignore()", source)
        self.assertNotIn("setCurrentIndex", source)

    def test_viewport_range_preloads_one_screen_each_direction(self) -> None:
        self.assertEqual(SPRITELINK.VIEWPORT_MEDIA_PRELOAD_SCREENS, 1)
        self.assertTrue(
            SPRITELINK.vertical_range_is_near_viewport(-500, -450, 500)
        )
        self.assertTrue(
            SPRITELINK.vertical_range_is_near_viewport(950, 1000, 500)
        )
        self.assertFalse(
            SPRITELINK.vertical_range_is_near_viewport(-700, -501, 500)
        )
        self.assertFalse(
            SPRITELINK.vertical_range_is_near_viewport(1001, 1050, 500)
        )

    def test_only_nearby_media_is_fetched_and_embedded(self) -> None:
        viewport_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._update_viewport_media
        )
        self.assertIn("rendered_image_candidates", viewport_source)
        self.assertIn("cursorRect", viewport_source)
        self.assertIn(
            "vertical_range_is_near_viewport",
            viewport_source,
        )
        self.assertIn("_schedule_image_preview_fetch(url)", viewport_source)

        insert_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._insert_message_item
        )
        self.assertIn("displayed_image_urls", insert_source)
        self.assertIn("displayed_image_urls = list(image_urls)", insert_source)
        self.assertNotIn(
            "image_url in self.viewport_embedded_image_urls",
            insert_source,
        )
        self.assertNotIn("_schedule_image_preview_fetch", insert_source)

    def test_fresh_images_reserve_an_inline_object_before_visibility(self) -> None:
        insert_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._insert_message_item
        )
        preview_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._insert_embedded_image_preview
        )

        self.assertIn("displayed_image_urls = list(image_urls)", insert_source)
        self.assertIn(
            "for image_url in displayed_image_urls:",
            insert_source,
        )
        self.assertIn(
            "self.rendered_image_positions.setdefault(url, []).append",
            preview_source,
        )
        self.assertIn(
            "preview = self._unloaded_image_placeholder(*preview_size)",
            preview_source,
        )
        self.assertIn(
            "preview_size = self.embedded_image_preview_sizes.get(url)",
            insert_source,
        )
        self.assertIn(
            "self.embedded_image_preview_sizes[url] = preview_size",
            insert_source,
        )
        self.assertNotIn(
            "preview_height = self.embedded_image_preview_sizes[",
            insert_source,
        )

    def test_scroll_resize_and_render_refresh_lazy_media(self) -> None:
        build_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._build_chat_tab
        )
        self.assertIn("verticalScrollBar().valueChanged.connect", build_source)
        event_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient.eventFilter
        )
        self.assertIn("QEvent.Type.Resize", event_source)
        render_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._continue_message_log_render
        )
        self.assertIn("viewport_media_timer.start", render_source)

    def test_unloaded_images_keep_their_rendered_pixel_size(self) -> None:
        insert_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._insert_embedded_image_preview
        )
        placeholder_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._unloaded_image_placeholder
        )
        self.assertIn(
            "self.embedded_image_preview_sizes[url] =",
            insert_source,
        )
        self.assertIn(
            "self.embedded_image_preview_sizes.get(url)",
            insert_source,
        )
        self.assertIn(
            "preview = self._unloaded_image_placeholder(*preview_size)",
            insert_source,
        )
        self.assertEqual(SPRITELINK.EMBEDDED_IMAGE_PLACEHOLDER_SIZE, 48)
        self.assertIn("EMBEDDED_IMAGE_PLACEHOLDER_SIZE", insert_source)
        self.assertNotIn("preview_size is None:\n                return False", insert_source)
        self.assertIn('placeholder.fill(QColor("#d0d0d0"))', placeholder_source)
        self.assertIn("max(1, int(width))", placeholder_source)
        self.assertIn("max(1, int(height))", placeholder_source)

    def test_image_resources_swap_in_place_with_a_stable_view_anchor(
        self,
    ) -> None:
        viewport_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._update_viewport_media
        )
        refresh_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient
            ._refresh_viewport_image_resources
        )
        resource_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._set_rendered_inline_image
        )
        capture_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._capture_chat_view_anchor
        )
        finish_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient
            ._finish_preserving_scroll_rerender
        )
        restore_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient
            ._restore_preserving_scroll_rerender
        )
        action_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient
            ._on_chat_history_scroll_action
        )

        self.assertIn("_refresh_viewport_image_resources(", viewport_source)
        self.assertNotIn("_rerender_preserving_scroll()", viewport_source)
        self.assertIn("_capture_chat_view_anchor()", refresh_source)
        self.assertIn(
            "_set_history_render_updates_suppressed(True)",
            refresh_source,
        )
        self.assertIn("document.addResource(", resource_source)
        self.assertIn("cursor.setCharFormat(image_format)", resource_source)
        self.assertIn("document.markContentsDirty(position, 1)", resource_source)
        self.assertIn("geometry_changed = False", resource_source)
        self.assertIn("if size_changed:", resource_source)
        self.assertIn(
            "document.documentLayout().documentSize()",
            resource_source,
        )
        self.assertIn("changed_image_blocks", resource_source)
        self.assertIn(
            "_realign_inline_image_block_text(",
            resource_source,
        )
        self.assertIn("self.chat_display.viewport().update()", resource_source)
        self.assertNotIn("self.chat_display.clear()", refresh_source)
        self.assertIn("cursorForPosition(QPoint(0, 0))", capture_source)
        self.assertIn("rendered_message_blocks", capture_source)
        self.assertIn("blockBoundingRect(block)", capture_source)
        self.assertIn("QTimer.singleShot(", finish_source)
        self.assertIn("if bool(anchor.get(\"at_bottom\"))", restore_source)
        self.assertIn("block_top - float(anchor.get", restore_source)
        self.assertIn(
            "self._media_rerender_in_progress = False",
            restore_source,
        )
        self.assertIn(
            "or self._history_render_updates_suppressed",
            action_source,
        )
        self.assertIn(
            "or self._media_rerender_in_progress",
            action_source,
        )
        self.assertIn(
            "or scrollbar.value() > scrollbar.minimum()",
            action_source,
        )

    def test_loaded_image_geometry_realigns_its_existing_username(self) -> None:
        source = inspect.getsource(
            SPRITELINK.EncryptedChatClient
            ._realign_inline_image_block_text
        )

        self.assertIn("document.findBlock(block_position)", source)
        self.assertIn("formatting.isImageFormat()", source)
        self.assertIn(
            '"spritelink-chat-image-resource:"',
            source,
        )
        self.assertIn("preview_height = max(", source)
        self.assertIn("QFontMetrics(formatting.font()).height()", source)
        self.assertIn("formatting.setBaselineOffset(", source)
        self.assertIn("cursor.setCharFormat(formatting)", source)
        self.assertIn("document.markContentsDirty", source)

    def test_far_animations_are_stopped_and_removed(self) -> None:
        viewport_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._update_viewport_media
        )
        self.assertIn(
            "urls_to_unload = currently_rendered_urls - desired_urls",
            viewport_source,
        )
        refresh_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient
            ._refresh_viewport_image_resources
        )
        self.assertIn("controller.stop()", refresh_source)
        clear_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._clear_visible_room
        )
        self.assertIn("viewport_embedded_image_urls.clear()", clear_source)
        self.assertIn("controller.stop()", clear_source)


class BehaviorSettingsTests(unittest.TestCase):
    @staticmethod
    def _history_storage_patches(directory: Path) -> tuple[object, ...]:
        return (
            mock.patch.object(
                SPRITELINK,
                "CHATROOM_HISTORY_DIRECTORY",
                directory,
            ),
            mock.patch.object(
                SPRITELINK,
                "dpapi_encrypt",
                side_effect=lambda data, _entropy: data,
            ),
            mock.patch.object(
                SPRITELINK,
                "dpapi_decrypt",
                side_effect=lambda data, _entropy: data,
            ),
            mock.patch.object(
                SPRITELINK,
                "room_scope_id",
                side_effect=lambda _server, key: {
                    "active-key": "a" * 64,
                    "background-key": "b" * 64,
                    "other-key": "c" * 64,
                }[key],
            ),
        )

    def test_behavior_defaults_and_history_options(self) -> None:
        config = SPRITELINK.default_config()
        self.assertEqual(
            SPRITELINK.CHATROOM_HISTORY_OPTIONS,
            (100, 500, 1000, 10000),
        )
        self.assertEqual(config["chatroom_history_limit"], 1000)
        self.assertEqual(SPRITELINK.DEFAULT_CHATROOM_HISTORY_LIMIT, 1000)
        self.assertNotIn("history", config)
        self.assertTrue(config["minimize_to_tray"])
        self.assertFalse(config["start_with_windows"])

    def test_each_chatroom_history_uses_a_separate_encrypted_file(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            patches = self._history_storage_patches(directory)
            with patches[0], patches[1], patches[2], patches[3]:
                active_entries = [{"index": 1}]
                background_entries = [{"index": 2}]
                SPRITELINK.save_chatroom_history(
                    "https://ntfy.sh",
                    "active-key",
                    active_entries,
                )
                SPRITELINK.save_chatroom_history(
                    "https://ntfy.sh",
                    "background-key",
                    background_entries,
                )

                active_path = SPRITELINK.chatroom_history_path(
                    "https://ntfy.sh",
                    "active-key",
                )
                background_path = SPRITELINK.chatroom_history_path(
                    "https://ntfy.sh",
                    "background-key",
                )
                self.assertNotEqual(active_path, background_path)
                self.assertEqual(active_path.parent, directory)
                self.assertTrue(active_path.exists())
                self.assertTrue(background_path.exists())
                self.assertEqual(
                    SPRITELINK.load_chatroom_history(
                        "https://ntfy.sh",
                        "active-key",
                    ),
                    active_entries,
                )
                self.assertEqual(
                    SPRITELINK.load_chatroom_history(
                        "https://ntfy.sh",
                        "background-key",
                    ),
                    background_entries,
                )

    def test_history_pruning_is_per_chatroom(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            patches = self._history_storage_patches(directory)
            with patches[0], patches[1], patches[2], patches[3]:
                active_entries = [
                    {"index": index}
                    for index in range(1200)
                ]
                background_entries = [
                    {"index": index}
                    for index in range(700)
                ]
                SPRITELINK.save_chatroom_history(
                    "https://ntfy.sh",
                    "active-key",
                    active_entries,
                )
                SPRITELINK.save_chatroom_history(
                    "https://ntfy.sh",
                    "background-key",
                    background_entries,
                )

                self.assertTrue(SPRITELINK.prune_chatroom_history(
                    "https://ntfy.sh",
                    "background-key",
                    500,
                ))
                self.assertEqual(
                    SPRITELINK.load_chatroom_history(
                        "https://ntfy.sh",
                        "active-key",
                    ),
                    active_entries,
                )
                self.assertEqual(
                    SPRITELINK.load_chatroom_history(
                        "https://ntfy.sh",
                        "background-key",
                    ),
                    background_entries[-500:],
                )

    def test_history_limit_is_normalized_to_presets(self) -> None:
        for value in SPRITELINK.CHATROOM_HISTORY_OPTIONS:
            with self.subTest(value=value):
                self.assertEqual(
                    SPRITELINK.normalize_chatroom_history_limit(str(value)),
                    value,
                )
        self.assertEqual(
            SPRITELINK.normalize_chatroom_history_limit(999),
            SPRITELINK.DEFAULT_CHATROOM_HISTORY_LIMIT,
        )

    def test_behavior_controls_are_present_in_config(self) -> None:
        source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._build_config_tab
        )
        self.assertIn('self._heading("Behavior")', source)
        self.assertNotIn("Notifications and rendering", source)
        self.assertIn('QLabel("Chatroom History")', source)
        self.assertNotIn('QLabel("Message History")', source)
        self.assertIn('QCheckBox("Minimize to Tray")', source)
        self.assertIn('QCheckBox("Start with Windows")', source)
        self.assertLess(
            source.index('QCheckBox("Minimize to Tray")'),
            source.index('QCheckBox("Start with Windows")'),
        )
        self.assertIn("CHATROOM_HISTORY_OPTIONS", source)

    def test_start_with_windows_updates_the_current_user_run_key(self) -> None:
        registry_key = mock.MagicMock()
        winreg = mock.MagicMock()
        winreg.HKEY_CURRENT_USER = object()
        winreg.REG_SZ = 1
        winreg.CreateKey.return_value.__enter__.return_value = registry_key

        with (
            mock.patch.object(SPRITELINK.os, "name", "nt"),
            mock.patch.dict(sys.modules, {"winreg": winreg}),
            mock.patch.object(
                SPRITELINK,
                "windows_startup_command",
                return_value='"C:\\SpriteLink\\SpriteLink.exe"',
            ),
        ):
            self.assertTrue(SPRITELINK.set_start_with_windows(True))
            self.assertTrue(SPRITELINK.set_start_with_windows(False))

        winreg.CreateKey.assert_called_with(
            winreg.HKEY_CURRENT_USER,
            SPRITELINK.WINDOWS_STARTUP_REGISTRY_PATH,
        )
        winreg.SetValueEx.assert_called_once_with(
            registry_key,
            SPRITELINK.WINDOWS_STARTUP_VALUE_NAME,
            0,
            winreg.REG_SZ,
            '"C:\\SpriteLink\\SpriteLink.exe"',
        )
        winreg.DeleteValue.assert_called_once_with(
            registry_key,
            SPRITELINK.WINDOWS_STARTUP_VALUE_NAME,
        )

        installer = (
            Path(__file__).resolve().parents[1]
            / "packaging/windows/SpriteLink.iss"
        ).read_text(encoding="utf-8")
        self.assertIn("uninsdeletevalue", installer)
        self.assertIn('ValueName: "SpriteLink"', installer)

    def test_window_size_is_saved_within_supported_bounds(self) -> None:
        self.assertEqual(
            SPRITELINK.normalize_window_size(700, 550),
            (700, 550),
        )
        self.assertEqual(
            SPRITELINK.normalize_window_size(2000, 1200),
            (
                SPRITELINK.DEFAULT_WINDOW_WIDTH,
                SPRITELINK.DEFAULT_WINDOW_HEIGHT,
            ),
        )
        self.assertEqual(
            SPRITELINK.normalize_window_size(100, 100),
            (
                SPRITELINK.MINIMUM_WINDOW_WIDTH,
                SPRITELINK.MINIMUM_WINDOW_HEIGHT,
            ),
        )
        config = SPRITELINK.default_config()
        self.assertEqual(
            (config["window_width"], config["window_height"]),
            (
                SPRITELINK.DEFAULT_WINDOW_WIDTH,
                SPRITELINK.DEFAULT_WINDOW_HEIGHT,
            ),
        )

        init_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient.__init__
        )
        self.assertIn('self.config_data["window_width"]', init_source)
        self.assertIn('self.config_data["window_height"]', init_source)
        copy_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._copy_ui_to_config
        )
        self.assertIn("normalize_window_size(", copy_source)
        self.assertIn('self.config_data["window_width"]', copy_source)
        self.assertIn('self.config_data["window_height"]', copy_source)

    def test_background_history_pruning_runs_hourly(self) -> None:
        self.assertEqual(
            SPRITELINK.BACKGROUND_HISTORY_PRUNE_INTERVAL_MS,
            60 * 60 * 1000,
        )
        init_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient.__init__
        )
        self.assertIn("background_history_prune_timer.start()", init_source)
        prune_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._prune_background_local_histories
        )
        self.assertIn(
            'if room["id"] == self.active_chatroom_id',
            prune_source,
        )
        self.assertIn("prune_chatroom_history(", prune_source)
        background_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._accept_background_messages
        )
        self.assertIn("save_chatroom_history(", background_source)
        self.assertNotIn("history[-1000:]", background_source)

    def test_history_persistence_uses_selected_limit(self) -> None:
        load_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._load_saved_history_for_current_room
        )
        worker_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._load_saved_history_worker
        )
        persist_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._persist_local_history
        )
        add_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._add_message_to_log
        )
        self.assertIn("history_load_executor.submit(", load_source)
        self.assertIn("load_chatroom_history(", worker_source)
        self.assertIn("save_chatroom_history(", persist_source)
        self.assertNotIn("save_config(", persist_source)
        self.assertIn("_chatroom_history_limit()", load_source)
        self.assertIn("_chatroom_history_limit()", persist_source)
        self.assertIn("_chatroom_history_limit()", add_source)

    def test_long_chatroom_history_loading_is_threaded_and_staggered(
        self,
    ) -> None:
        self.assertEqual(SPRITELINK.INITIAL_HISTORY_RENDER_MESSAGES, 40)
        self.assertEqual(SPRITELINK.HISTORY_RENDER_PAGE_MESSAGES, 40)
        self.assertEqual(SPRITELINK.MESSAGE_RENDER_STEP_DELAY_MS, 1)
        self.assertEqual(SPRITELINK.UI_EVENT_BATCH_LIMIT, 8)

        load_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._load_saved_history_for_current_room
        )
        worker_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._load_saved_history_worker
        )
        queue_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._process_ui_queue
        )
        self.assertIn("history_load_executor.submit(", load_source)
        self.assertNotIn("load_chatroom_history(", load_source)
        self.assertIn("load_chatroom_history(", worker_source)
        self.assertIn(
            "chunk_end - INITIAL_HISTORY_RENDER_MESSAGES",
            worker_source,
        )
        self.assertIn('"history_chunk_loaded"', worker_source)
        self.assertIn('"history_chunk_loaded"', queue_source)
        self.assertIn("range(UI_EVENT_BATCH_LIMIT)", queue_source)
        self.assertIn("self.message_log[0:0] = accepted_items", queue_source)

        render_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._render_message_log
        )
        continue_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._continue_message_log_render
        )
        self.assertIn(
            "visible_message_items = "
            "self.message_log[-visible_message_limit:]",
            render_source,
        )
        self.assertIn("previous_message_backgrounds", render_source)
        self.assertIn("stripe_shift", render_source)
        self.assertIn(
            "previous_index - planned_index",
            render_source,
        )
        self.assertNotIn("while index <", continue_source)
        self.assertIn('"index": (', render_source)
        self.assertIn("len(render_steps) - 1", render_source)
        self.assertIn("if scroll_to_bottom", render_source)
        self.assertIn("else 0", render_source)
        self.assertIn('step = render_steps[index]', continue_source)
        self.assertIn("index -= 1", continue_source)
        self.assertIn(
            "cursor.movePosition(QTextCursor.MoveOperation.Start)",
            continue_source,
        )
        self.assertIn("if old_message_blocks:", continue_source)
        self.assertIn("cursor.insertBlock()", continue_source)
        self.assertIn(
            "QTextCursor.MoveOperation.PreviousBlock",
            continue_source,
        )
        self.assertLess(
            continue_source.index("cursor.insertBlock()"),
            continue_source.index("self._insert_message_item("),
        )
        bottom_align_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient
            ._bottom_align_short_message_log
        )
        self.assertIn("frame_format.setTopMargin(0.0)", bottom_align_source)
        self.assertIn("viewport().height()", bottom_align_source)
        self.assertIn("frame_format.setTopMargin(top_margin)", bottom_align_source)
        self.assertIn(
            "self._bottom_align_short_message_log()",
            continue_source,
        )
        self.assertIn(
            "_insert_log_separator_before_newer_content",
            continue_source,
        )
        forward_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient
            ._continue_message_log_render_forward
        )
        self.assertIn(
            "group = display_groups[index]",
            forward_source,
        )
        self.assertIn("index += 1", forward_source)
        self.assertIn(
            "self._insert_log_separator(",
            forward_source,
        )
        self.assertIn("stripe_shift = int(job[\"stripe_shift\"])", forward_source)
        self.assertIn("stripe_index + stripe_shift", forward_source)
        self.assertNotIn(
            "_insert_log_separator_before_newer_content",
            forward_source,
        )
        self.assertIn(
            "if scroll_to_bottom:",
            render_source,
        )
        self.assertIn(
            "self._continue_message_log_render_forward(generation)",
            render_source,
        )
        self.assertIn("block_number + block_delta", continue_source)
        self.assertIn("position + character_delta", continue_source)
        self.assertLess(
            continue_source.index('step = render_steps[index]'),
            continue_source.index("index -= 1"),
        )
        self.assertIn(
            "QTimer.singleShot(\n"
            "                MESSAGE_RENDER_STEP_DELAY_MS,",
            continue_source,
        )
        self.assertIn(
            "scrollbar.setValue(scrollbar.maximum())",
            continue_source,
        )

        scroll_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._on_chat_history_scrolled
        )
        self.assertNotIn("_schedule_older_history_page()", scroll_source)
        scroll_request = (
            SPRITELINK.EncryptedChatClient
            ._request_older_history_page_from_user_scroll
        )
        action_source = inspect.getsource(scroll_request)
        self.assertIn("_schedule_older_history_page()", action_source)
        scroll_action_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient
            ._on_chat_history_scroll_action
        )
        self.assertIn(
            "if self._older_history_user_request is not None",
            scroll_action_source,
        )
        self.assertIn(
            "self._older_history_user_request = request",
            scroll_action_source,
        )
        event_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient.eventFilter
        )
        self.assertIn(
            "self._on_chat_history_scroll_action(0)",
            event_source,
        )
        self.assertNotIn(
            "self._request_older_history_page_from_user_scroll,",
            event_source,
        )
        self.assertIn(
            "if self._older_history_user_request != request",
            action_source,
        )
        self.assertIn(
            "self._older_history_user_request = None",
            action_source,
        )
        page_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._load_older_history_page
        )
        finish_page_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._finish_older_history_page
        )
        restore_page_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._restore_older_history_page_view
        )
        self.assertIn("HISTORY_RENDER_PAGE_MESSAGES", page_source)
        self.assertIn("on_finished=", page_source)
        self.assertIn(
            "QTimer.singleShot(",
            finish_page_source,
        )
        self.assertIn(
            "new_maximum - max(0, previous_distance_from_bottom)",
            restore_page_source,
        )
        self.assertNotIn(
            "_schedule_older_history_page()",
            finish_page_source,
        )
        self.assertNotIn(
            "_schedule_older_history_page()",
            restore_page_source,
        )
        self.assertIn(
            "_set_history_render_updates_suppressed(True)",
            page_source,
        )
        queue_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._process_ui_queue
        )
        finish_initial_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient
            ._finalize_initial_history_render
        )
        self.assertNotIn(
            "_maybe_prefetch_initial_history_page",
            queue_source,
        )
        self.assertNotIn(
            "_schedule_older_history_page()",
            finish_initial_source,
        )
        reset_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient
            ._reset_history_render_window
        )
        self.assertIn(
            "self._older_history_user_request = None",
            reset_source,
        )
        build_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._build_chat_tab
        )
        self.assertIn("actionTriggered.connect", build_source)
        switch_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._activate_chatroom
        )
        self.assertNotIn("_persist_local_history()", switch_source)

        add_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._add_message_to_log
        )
        self.assertIn(
            "self.rendered_history_message_limit + 1",
            add_source,
        )
        self.assertIn("min(\n            len(self.message_log)", add_source)

    def test_settings_never_embed_chatroom_history(self) -> None:
        config = SPRITELINK.default_config()
        config["history"] = {"scope": [{"message": {"m": "secret"}}]}
        with mock.patch.object(
            SPRITELINK,
            "_write_dpapi_json",
        ) as write_json:
            SPRITELINK.save_config(config)
        stored_config = write_json.call_args.args[1]
        self.assertNotIn("history", stored_config)

    def test_previous_settings_formats_are_rejected_not_converted(
        self,
    ) -> None:
        legacy_config = {
            "config_version": SPRITELINK.CONFIG_FORMAT_VERSION - 1,
            "server_url": "https://legacy.invalid",
            "username": "Legacy User",
            "identities": [{"username": "Legacy User"}],
            "chime_enabled": False,
        }
        config_path = mock.Mock()
        config_path.exists.return_value = True
        config_path.read_bytes.return_value = b"legacy settings"
        with (
            mock.patch.object(SPRITELINK, "CONFIG_PATH", config_path),
            mock.patch.object(
                SPRITELINK,
                "_load_dpapi_config",
                return_value=legacy_config,
            ),
        ):
            config = SPRITELINK.load_config()

        self.assertEqual(
            config["server_url"],
            SPRITELINK.DEFAULT_SERVER_URL,
        )
        self.assertEqual(config["chatrooms"], [])
        self.assertNotIn("username", config)
        self.assertNotIn("identities", config)

        load_source = inspect.getsource(SPRITELINK.load_config)
        module_source = inspect.getsource(SPRITELINK)
        self.assertIn(
            'loaded.get("config_version") != CONFIG_FORMAT_VERSION',
            load_source,
        )
        for removed_upgrade_token in (
            "previous_config_version",
            "legacy_username",
            "chime_enabled",
            "server_cursors",
            "last_server_id",
            "EncryptedChatClient-v2-config",
            "EncryptedChatClient-v1-config",
            "identity_signature",
            "LEGACY_BASIC_THEME",
        ):
            self.assertNotIn(removed_upgrade_token, module_source)

    def test_minimize_to_tray_is_enabled_for_new_and_existing_users(
        self,
    ) -> None:
        self.assertTrue(SPRITELINK.default_config()["minimize_to_tray"])

        existing_config = SPRITELINK.default_config()
        existing_config["minimize_to_tray"] = False
        existing_config.pop(
            SPRITELINK.MINIMIZE_TO_TRAY_1_2_MIGRATION_KEY,
            None,
        )
        config_path = mock.Mock()
        config_path.exists.return_value = True
        config_path.read_bytes.return_value = b"settings"
        with (
            mock.patch.object(SPRITELINK, "CONFIG_PATH", config_path),
            mock.patch.object(
                SPRITELINK,
                "_load_dpapi_config",
                return_value=existing_config,
            ),
        ):
            migrated = SPRITELINK.load_config()

        self.assertTrue(migrated["minimize_to_tray"])
        self.assertTrue(
            migrated[SPRITELINK.MINIMIZE_TO_TRAY_1_2_MIGRATION_KEY]
        )

        migrated["minimize_to_tray"] = False
        with (
            mock.patch.object(SPRITELINK, "CONFIG_PATH", config_path),
            mock.patch.object(
                SPRITELINK,
                "_load_dpapi_config",
                return_value=migrated,
            ),
        ):
            user_disabled = SPRITELINK.load_config()
        self.assertFalse(user_disabled["minimize_to_tray"])

    def test_first_tray_close_notice_has_requested_actions(self) -> None:
        notice_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._show_minimize_to_tray_notice
        )
        self.assertIn(
            "SpriteLink will keep running in the system tray so it can ",
            notice_source,
        )
        self.assertIn("receive messages in the background.", notice_source)
        self.assertIn('"Disable and Close"', notice_source)
        self.assertIn('"OK"', notice_source)


class MessageGroupingTests(unittest.TestCase):
    @staticmethod
    def _item(
        client_id: str,
        text: str,
        index: int,
        *,
        is_local: bool = False,
    ) -> dict[str, object]:
        return {
            "message": {
                "c": client_id,
                "m": text,
                "i": f"message-{index}",
                "t": 1000 + index,
            },
            "is_local": is_local,
            "warning": None,
            "ntfy_time": 1000 + index,
        }

    def test_muted_user_messages_combine_until_another_user_speaks(
        self,
    ) -> None:
        items = [
            self._item("muted", "one", 1),
            self._item("muted", "two", 2),
            self._item("other", "break", 3),
            self._item("muted", "three", 4),
        ]
        groups = SPRITELINK.group_messages_for_display(items, {"muted"})
        self.assertEqual([len(group) for group in groups], [2, 1, 1])

    def test_remote_duplicate_text_groups_but_local_text_does_not(
        self,
    ) -> None:
        items = [
            self._item("remote", "hello", 1),
            self._item("remote", "hello", 2),
            self._item("local", "hello", 3, is_local=True),
            self._item("local", "hello", 4, is_local=True),
        ]
        groups = SPRITELINK.group_messages_for_display(items, set())
        self.assertEqual([len(group) for group in groups], [2, 1, 1])
        self.assertEqual(
            SPRITELINK.EncryptedChatClient._repeat_prefixed_message_text(
                "hello",
                2,
            ),
            "[2x] hello",
        )
        display_client = mock.Mock()
        display_client._repeat_prefixed_message_text = (
            SPRITELINK.EncryptedChatClient._repeat_prefixed_message_text
        )
        display_item = (
            SPRITELINK.EncryptedChatClient._display_item_for_group(
                display_client,
                groups[0],
                set(),
            )
        )
        self.assertEqual(display_item["message"]["m"], "[2x] hello")

    def test_muted_combined_row_keeps_distinct_text_and_repeat_counts(
        self,
    ) -> None:
        items = [
            self._item("muted", "one", 1),
            self._item("muted", "two", 2),
            self._item("muted", "two", 3),
        ]
        group = SPRITELINK.group_messages_for_display(
            items,
            {"muted"},
        )[0]
        display_client = mock.Mock()
        display_client._repeat_prefixed_message_text = (
            SPRITELINK.EncryptedChatClient._repeat_prefixed_message_text
        )
        display_item = (
            SPRITELINK.EncryptedChatClient._display_item_for_group(
                display_client,
                group,
                {"muted"},
            )
        )
        self.assertEqual(
            display_item["message"]["m"],
            "one | [2x] two",
        )
        self.assertEqual(
            display_item["source_message_ids"],
            ["message-1", "message-2", "message-3"],
        )

    def test_muted_rows_replace_spoiler_characters_with_blocks(self) -> None:
        items = [
            self._item(
                "muted",
                "visible <sp>secret text</sp> end",
                1,
            ),
            self._item(
                "muted",
                "<sp><b>nested</b></sp>",
                2,
            ),
        ]
        group = SPRITELINK.group_messages_for_display(
            items,
            {"muted"},
        )[0]
        display_client = mock.Mock()
        display_client._repeat_prefixed_message_text = (
            SPRITELINK.EncryptedChatClient._repeat_prefixed_message_text
        )
        display_item = (
            SPRITELINK.EncryptedChatClient._display_item_for_group(
                display_client,
                group,
                {"muted"},
            )
        )
        self.assertEqual(
            display_item["message"]["m"],
            "visible ███████████ end | ██████",
        )
        self.assertNotIn("<sp>", display_item["message"]["m"])

    def test_muted_spoiler_redaction_cannot_bleed_between_messages(
        self,
    ) -> None:
        self.assertEqual(
            SPRITELINK.message_plain_text_with_spoilers_redacted(
                "<sp>unfinished"
            ),
            "██████████",
        )
        self.assertEqual(
            SPRITELINK.message_plain_text_with_spoilers_redacted(
                "next message"
            ),
            "next message",
        )

    def test_duplicate_sound_ordinal_suppresses_only_third_and_later(
        self,
    ) -> None:
        first = self._item("remote", "hello", 1)
        second = self._item("remote", "hello", 2)
        third_message = self._item("remote", "hello", 3)["message"]
        self.assertEqual(
            SPRITELINK.consecutive_duplicate_message_ordinal(
                [],
                first["message"],
                ntfy_time=1001,
            ),
            1,
        )
        self.assertEqual(
            SPRITELINK.consecutive_duplicate_message_ordinal(
                [first],
                second["message"],
                ntfy_time=1002,
            ),
            2,
        )
        self.assertEqual(
            SPRITELINK.consecutive_duplicate_message_ordinal(
                [first, second],
                third_message,
                ntfy_time=1003,
            ),
            3,
        )

        future = self._item("remote", "hello", 4)
        self.assertEqual(
            SPRITELINK.consecutive_duplicate_message_ordinal(
                [first, future],
                second["message"],
                ntfy_time=1002,
            ),
            2,
        )

        active_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._accept_network_message
        )
        background_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._accept_background_messages
        )
        self.assertIn("duplicate_ordinal <= 2", active_source)
        self.assertIn("duplicate_ordinal <= 2", background_source)
        queue_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._process_ui_queue
        )
        self.assertIn(
            "sorted(items, key=self._message_sort_key)",
            queue_source,
        )

    def test_muted_rows_use_ui_font_and_hide_log_icon(self) -> None:
        insert_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._insert_message_item
        )
        self.assertIn('"" if is_muted else profile_icon', insert_source)
        self.assertIn("ui_font=is_muted", insert_source)
        tooltip_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._tooltip_for_message_item
        )
        self.assertIn("profile_icon_tooltip_data_uri", tooltip_source)


class MessageOrderingAndRowBoundaryTests(unittest.TestCase):
    def test_rapid_message_ids_preserve_submission_order(self) -> None:
        first_id, first_order = SPRITELINK.ordered_message_id(
            0,
            now_ns=500,
        )
        second_id, second_order = SPRITELINK.ordered_message_id(
            first_order,
            now_ns=500,
        )
        third_id, third_order = SPRITELINK.ordered_message_id(
            second_order,
            now_ns=499,
        )
        self.assertEqual(
            (first_order, second_order, third_order),
            (500, 501, 502),
        )
        self.assertEqual(len(first_id), 32)
        self.assertLess(first_id, second_id)
        self.assertLess(second_id, third_id)

        items = [
            {
                "message": {"i": third_id, "t": 1000},
                "ntfy_time": 1000,
                "ntfy_id": "a",
            },
            {
                "message": {"i": first_id, "t": 1000},
                "ntfy_time": 1000,
                "ntfy_id": "z",
            },
            {
                "message": {"i": second_id, "t": 1000},
                "ntfy_time": 1000,
                "ntfy_id": "m",
            },
        ]
        ordered = sorted(items, key=SPRITELINK.message_item_sort_key)
        self.assertEqual(
            [item["message"]["i"] for item in ordered],
            [first_id, second_id, third_id],
        )

        send_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._send_current_message
        )
        network_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._network_loop
        )
        self.assertIn("ordered_message_id", send_source)
        self.assertLess(
            network_source.index("self.send_queue.get_nowait()"),
            network_source.index("self._network_send(outbound)"),
        )

    def test_new_local_message_stays_after_received_rows(self) -> None:
        client = mock.Mock()
        client.message_log = [{
            "message": {"i": "remote", "t": 1001},
            "is_local": False,
            "warning": None,
            "ntfy_id": "ntfy-remote",
            "ntfy_time": 1001,
        }]
        client._message_sort_key = SPRITELINK.message_item_sort_key
        client._chatroom_history_limit.return_value = 100

        SPRITELINK.EncryptedChatClient._add_message_to_log(
            client,
            {"i": "local", "t": 1000},
            is_local=True,
            persist=False,
            render=False,
        )

        self.assertEqual(
            [item["message"]["i"] for item in client.message_log],
            ["remote", "local"],
        )
        self.assertGreater(
            client.message_log[-1]["display_sort_time"],
            client.message_log[0]["ntfy_time"],
        )

    def test_every_rendered_message_enforces_a_new_row_boundary(self) -> None:
        source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._insert_message_item
        )
        boundary = source.index("if cursor.block().text():")
        insert_block = source.index("cursor.insertBlock()", boundary)
        message_start = source.index("message_start_position =", boundary)
        self.assertLess(boundary, insert_block)
        self.assertLess(insert_block, message_start)

    def test_hour_separator_is_omitted_beside_a_date_separator(self) -> None:
        same_day_start = int(
            SPRITELINK.datetime(2026, 7, 20, 8, 0).timestamp()
        )
        same_day_end = int(
            SPRITELINK.datetime(2026, 7, 20, 15, 0).timestamp()
        )
        self.assertEqual(
            SPRITELINK.message_log_separator_texts(
                same_day_start,
                same_day_end,
            ),
            ("————— 7 hours later —————",),
        )

        previous_day = int(
            SPRITELINK.datetime(2026, 7, 20, 20, 0).timestamp()
        )
        next_day = int(
            SPRITELINK.datetime(2026, 7, 21, 3, 0).timestamp()
        )
        separators = SPRITELINK.message_log_separator_texts(
            previous_day,
            next_day,
        )
        self.assertEqual(len(separators), 1)
        self.assertIn("Jul 21, 2026", separators[0])
        self.assertNotIn("hours later", separators[0])

        insert_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._insert_log_separator
        )
        self.assertIn("if cursor.block().text():", insert_source)
        self.assertIn("setTopMargin(7)", insert_source)
        self.assertIn("setBottomMargin(7)", insert_source)
        self.assertIn("setBackground", insert_source)
        self.assertIn("row_background_padding_blocks", insert_source)

        padding_paint_source = inspect.getsource(
            SPRITELINK.MessageLogBrowser._paint_row_background_padding
        )
        self.assertEqual(padding_paint_source.count("painter.fillRect("), 2)
        paint_event_source = inspect.getsource(
            SPRITELINK.MessageLogBrowser.paintEvent
        )
        self.assertGreater(
            paint_event_source.index("_paint_row_background_padding"),
            paint_event_source.index("super().paintEvent(event)"),
        )

    def test_composer_menus_keep_a_bottomed_log_at_the_bottom(self) -> None:
        restore_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient
            ._restore_chat_bottom_after_layout_change
        )
        self.assertIn("if not keep_at_bottom", restore_source)
        self.assertIn("self._scroll_chat_to_bottom()", restore_source)
        self.assertIn("QTimer.singleShot(0", restore_source)

        for handler in (
            SPRITELINK.EncryptedChatClient._on_identity_menu_toggled,
            SPRITELINK.EncryptedChatClient._on_font_menu_toggled,
            SPRITELINK.EncryptedChatClient._on_formatting_menu_toggled,
        ):
            source = inspect.getsource(handler)
            self.assertIn("_chat_is_scrolled_to_bottom()", source)
            self.assertIn(
                "_restore_chat_bottom_after_layout_change",
                source,
            )

        visibility_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient
            ._set_composer_menu_visibility
        )
        self.assertIn("setUpdatesEnabled(False)", visibility_source)
        self.assertIn("setUpdatesEnabled(True)", visibility_source)
        self.assertLess(
            visibility_source.index("menu.setVisible(should_show)"),
            visibility_source.index("content_layout.activate()"),
        )

        sidebar_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._on_chatrooms_toggled
        )
        self.assertIn("chatrooms_panel.setVisible(expanded)", sidebar_source)
        self.assertIn("chatrooms_toggle.setText", sidebar_source)
        self.assertIn("_chat_is_scrolled_to_bottom()", sidebar_source)
        self.assertIn(
            "_restore_chat_bottom_after_layout_change",
            sidebar_source,
        )
        self.assertNotIn("self.root.setGeometry(", sidebar_source)
        self.assertNotIn("self.root.setMinimumWidth(", sidebar_source)
        self.assertNotIn("setUpdatesEnabled", sidebar_source)
        self.assertFalse(hasattr(
            SPRITELINK.EncryptedChatClient,
            "_set_window_redraw_enabled",
        ))


class TrayBehaviorTests(unittest.TestCase):
    def test_close_can_hide_to_tray_or_exit(self) -> None:
        close_event_source = inspect.getsource(
            SPRITELINK.MainWindow.closeEvent
        )
        self.assertIn("event.ignore()", close_event_source)
        close_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._on_close
        )
        self.assertIn("_can_minimize_to_tray()", close_source)
        self.assertIn("_hide_to_tray()", close_source)
        self.assertIn("return False", close_source)
        tray_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._build_tray_icon
        )
        self.assertIn('"Show SpriteLink"', tray_source)
        self.assertIn('"Exit"', tray_source)

    def test_tray_notification_uses_orange_outline(self) -> None:
        self.assertEqual(
            SPRITELINK.TRAY_NOTIFICATION_OUTLINE_COLOR,
            "#ff7a00",
        )
        icon_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._build_tray_notification_icon
        )
        self.assertIn("offset_x", icon_source)
        self.assertIn("offset_y", icon_source)
        self.assertIn("setPixelColor", icon_source)
        mark_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._mark_tray_notification
        )
        self.assertIn("_tray_notification_icon", mark_source)
        restore_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._restore_from_tray
        )
        self.assertIn(
            "_clear_tray_notification_if_no_unread",
            restore_source,
        )


class RichTextFormattingTests(unittest.TestCase):
    def test_parser_supports_safe_formatting_and_spoiler_tags(self) -> None:
        markup = (
            "plain <b>bold <i>both</i></b> "
            "<u>underlined</u> <sp>hidden</sp> <script>literal</script>"
        )
        plain, runs = SPRITELINK.parse_message_rich_text(markup)
        self.assertEqual(
            plain,
            "plain bold both underlined hidden <script>literal</script>",
        )
        styled_text = {
            plain[run.start:run.end]: (
                run.bold,
                run.italic,
                run.underline,
                run.spoiler is not None,
            )
            for run in runs
        }
        self.assertEqual(styled_text["bold "], (True, False, False, False))
        self.assertEqual(styled_text["both"], (True, True, False, False))
        self.assertEqual(styled_text["underlined"], (False, False, True, False))
        self.assertEqual(styled_text["hidden"], (False, False, False, True))

    def test_unmatched_closing_tags_remain_visible(self) -> None:
        plain, _runs = SPRITELINK.parse_message_rich_text(
            "text</b></i></u></sp>"
        )
        self.assertEqual(plain, "text</b></i></u></sp>")

    def test_unclosed_tags_do_not_affect_the_next_message(self) -> None:
        first_plain, first_runs = SPRITELINK.parse_message_rich_text(
            "<b><sp>unfinished"
        )
        second_plain, second_runs = SPRITELINK.parse_message_rich_text(
            "next message"
        )
        self.assertEqual(first_plain, "unfinished")
        self.assertTrue(all(run.bold for run in first_runs))
        self.assertTrue(all(run.spoiler is not None for run in first_runs))
        self.assertEqual(second_plain, "next message")
        self.assertTrue(all(not run.bold for run in second_runs))
        self.assertTrue(all(run.spoiler is None for run in second_runs))

    def test_spoiler_identity_survives_nested_formatting(self) -> None:
        plain, runs = SPRITELINK.parse_message_rich_text(
            "<sp>hidden <b>and bold</b> again</sp>"
        )
        self.assertEqual(plain, "hidden and bold again")
        self.assertEqual({run.spoiler for run in runs}, {1})

    def test_composer_segments_serialize_to_balanced_tags(self) -> None:
        markup = SPRITELINK.serialize_message_rich_text([
            ("bold", True, False, False, False),
            (" plain ", False, False, False, False),
            ("all", True, True, True, True),
            (" italic", False, True, False, False),
        ])
        self.assertEqual(
            markup,
            (
                "<b>bold</b> plain "
                "<sp><b><i><u>all</u></i></b></sp>"
                "<i> italic</i>"
            ),
        )
        self.assertEqual(
            SPRITELINK.message_plain_text(markup),
            "bold plain all italic",
        )

    def test_formatting_menu_and_tagged_send_path_are_wired(self) -> None:
        ui_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._build_chat_tab
        )
        self.assertIn('QPushButton("Formatting")', ui_source)
        self.assertIn('QPushButton("Bold")', ui_source)
        self.assertIn('QPushButton("Italic")', ui_source)
        self.assertIn('QPushButton("Underline")', ui_source)
        self.assertIn('SpoilerFormatButton("Spoiler")', ui_source)
        self.assertIn("bold_button_font.setBold(True)", ui_source)
        self.assertIn("italic_button_font.setItalic(True)", ui_source)
        self.assertIn("underline_button_font.setUnderline(True)", ui_source)

        composer_source = inspect.getsource(
            SPRITELINK.ComposeTextEdit.to_message_text
        )
        self.assertIn("serialize_message_rich_text", composer_source)
        toggle_source = inspect.getsource(
            SPRITELINK.ComposeTextEdit.apply_formatting
        )
        self.assertIn("cursor.hasSelection()", toggle_source)
        self.assertIn("cursor.mergeCharFormat", toggle_source)
        self.assertIn("mergeCurrentCharFormat", toggle_source)
        self.assertIn("COMPOSER_SPOILER_PROPERTY", toggle_source)
        self.assertIn("REVEALED_SPOILER_BLOCK_ALPHA", toggle_source)
        self.assertIn("formatting.setBackground", toggle_source)
        send_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._send_current_message
        )
        size_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._run_message_size_check
        )
        self.assertIn("to_message_text()", send_source)
        self.assertIn("to_message_text()", size_source)

        render_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._insert_message_item
        )
        self.assertIn(
            "cursor.setCharFormat(QTextCharFormat())",
            render_source,
        )
        username_format_start = render_source.index(
            "cursor.insertText(\n            username,"
        )
        username_format_end = render_source.index(
            "        if status_suffix:",
            username_format_start,
        )
        username_format = render_source[
            username_format_start:username_format_end
        ]
        self.assertIn("bold=False", username_format)
        self.assertIn("italic=False", username_format)
        self.assertIn("underline=False", username_format)
        self.assertNotIn("bold=True", username_format)

    def test_rendered_spoilers_use_clickable_format_properties(self) -> None:
        insert_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._insert_message_text_with_links
        )
        self.assertIn("RENDERED_SPOILER_ID_PROPERTY", insert_source)
        self.assertIn(
            "formatting.setBackground(QColor(SPOILER_BLOCK_COLOR))",
            insert_source,
        )
        self.assertEqual(SPRITELINK.SPOILER_BLOCK_COLOR, "#404040")
        self.assertEqual(SPRITELINK.REVEALED_SPOILER_BLOCK_ALPHA, 26)
        self.assertEqual(SPRITELINK.SPOILER_DISPLAY_HEIGHT_PX, 22)
        paint_source = inspect.getsource(
            SPRITELINK.MessageLogBrowser._paint_spoilers
        )
        self.assertIn("line.height() - spoiler_height", paint_source)
        self.assertIn("SPOILER_DISPLAY_HEIGHT_PX", paint_source)
        toggle_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._toggle_rendered_spoiler
        )
        self.assertIn("REVEALED_SPOILER_BLOCK_ALPHA", toggle_source)
        self.assertIn("formatting.setForeground", toggle_source)
        self.assertIn("self.chat_display.viewport().update()", toggle_source)
        self.assertIn("insert_padding(inside=False)", insert_source)
        self.assertIn("insert_padding(inside=True)", insert_source)
        self.assertIn("SPOILER_HORIZONTAL_PADDING_PX", insert_source)

    def test_spoilers_paint_when_text_shadows_start_disabled(self) -> None:
        toggle_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._on_text_shadows_toggled
        )
        self.assertIn("self._rerender_preserving_scroll()", toggle_source)

        shadow_source = inspect.getsource(
            SPRITELINK.MessageLogBrowser._paint_text_shadows
        )
        self.assertIn("RENDERED_SPOILER_ID_PROPERTY", shadow_source)
        self.assertIn("shadow_ranges", shadow_source)

        spoiler_paint_source = inspect.getsource(
            SPRITELINK.MessageLogBrowser._paint_spoilers
        )
        self.assertNotIn(
            "self.textCursor().hasSelection()",
            spoiler_paint_source,
        )
        self.assertNotIn("spritelinkTextShadows", spoiler_paint_source)
        self.assertIn(
            "RENDERED_SPOILER_ID_PROPERTY",
            spoiler_paint_source,
        )
        self.assertIn(
            "layout.draw(painter, layout_origin)",
            spoiler_paint_source,
        )
        paint_source = inspect.getsource(
            SPRITELINK.MessageLogBrowser.paintEvent
        )
        self.assertIn("_paint_spoilers(event)", paint_source)

        button_source = inspect.getsource(
            SPRITELINK.SpoilerFormatButton.paintEvent
        )
        self.assertIn("REVEALED_SPOILER_BLOCK_ALPHA", button_source)
        self.assertIn("painter.fillRect", button_source)


class RuntimeOptimizationTests(unittest.TestCase):
    def test_wrapped_messages_align_to_row_edge_and_keep_background(self) -> None:
        source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._insert_message_item
        )
        self.assertNotIn("message_continuation_indent", source)
        self.assertNotIn("horizontalAdvance(username)", source)
        self.assertIn("setLeftMargin(10)", source)
        self.assertIn("setTextIndent(0)", source)
        self.assertNotIn("block_format.setBackground(", source)
        self.assertIn(
            "block_format.clearProperty(",
            source,
        )
        self.assertIn(
            "QTextFormat.Property.BackgroundBrush",
            source,
        )
        self.assertIn("setTopMargin(0.0)", source)
        self.assertIn("setBottomMargin(0.0)", source)
        self.assertIn("LineHeightTypes.FixedHeight", source)

        render_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._continue_message_log_render
        )
        self.assertIn("setExtraSelections([])", render_source)
        self.assertNotIn(
            "setExtraSelections(row_selections)",
            render_source,
        )

        content_bounds_source = inspect.getsource(
            SPRITELINK.MessageLogBrowser._block_content_vertical_bounds
        )
        self.assertIn("layout.boundingRect()", content_bounds_source)
        self.assertIn(
            "content_rect.toAlignedRect()",
            content_bounds_source,
        )
        self.assertNotIn("round(", content_bounds_source)

        row_bounds_source = inspect.getsource(
            SPRITELINK.MessageLogBrowser._block_row_vertical_bounds
        )
        self.assertIn("blockBoundingRect(block)", row_bounds_source)
        self.assertIn("next_block = block.next()", row_bounds_source)
        self.assertIn("blockBoundingRect(", row_bounds_source)
        self.assertIn("_nearest_pixel_edge", row_bounds_source)
        self.assertNotIn(
            "row_background_padding_blocks",
            row_bounds_source,
        )
        self.assertNotIn("layout.boundingRect()", row_bounds_source)
        self.assertNotIn("toAlignedRect()", row_bounds_source)
        self.assertNotIn("round(", row_bounds_source)

        browser = mock.Mock()
        block = mock.Mock()
        next_block = mock.Mock()
        block.next.return_value = next_block
        next_block.isValid.return_value = True
        document_layout = (
            browser.document.return_value.documentLayout.return_value
        )
        document_layout.blockBoundingRect.side_effect = (
            SPRITELINK.QRectF(0.0, 10.25, 100.0, 22.5),
            SPRITELINK.QRectF(0.0, 32.75, 100.0, 22.5),
        )
        browser.verticalScrollBar.return_value.value.return_value = 3
        browser._nearest_pixel_edge = (
            SPRITELINK.MessageLogBrowser._nearest_pixel_edge
        )
        top, bottom = (
            SPRITELINK.MessageLogBrowser._block_row_vertical_bounds(
                browser,
                block,
            )
        )
        self.assertEqual((top, bottom), (7, 30))

        background_source = inspect.getsource(
            SPRITELINK.MessageLogBrowser._paint_row_backgrounds
        )
        self.assertIn("_block_row_vertical_bounds(block)", background_source)
        self.assertIn("bottom - top", background_source)
        self.assertNotIn("height + 1", background_source)
        paint_source = inspect.getsource(
            SPRITELINK.MessageLogBrowser.paintEvent
        )
        self.assertLess(
            paint_source.index("_paint_row_backgrounds(event)"),
            paint_source.index("super().paintEvent(event)"),
        )

    def test_status_line_uses_chatroom_name_and_delayed_error(self) -> None:
        self.assertEqual(
            SPRITELINK.chatroom_connection_label(
                "Global",
                connection_error=False,
            ),
            "Global",
        )
        self.assertEqual(
            SPRITELINK.chatroom_connection_label(
                "Global",
                connection_error=True,
            ),
            "Global - Connection error",
        )
        self.assertEqual(SPRITELINK.CONNECTION_ERROR_DELAY_MS, 15_000)

        title_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._update_window_title
        )
        ui_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._build_chat_tab
        )
        timer_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._sync_connection_error_timer
        )
        condition_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient
            ._connection_error_timer_should_run
        )
        restart_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._restart_connection_error_delay
        )
        timeout_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient
            ._show_connection_error_if_still_disconnected
        )

        self.assertIn("setWindowTitle(APP_NAME)", title_source)
        self.assertNotIn("nickname", title_source)
        self.assertNotIn('QLabel("Status:")', ui_source)
        self.assertIn("_on_connection_status_changed", ui_source)
        self.assertIn("connection_error_timer.isActive()", timer_source)
        self.assertIn('status_var.get()) == "Connected"', timer_source)
        self.assertIn("window_focused_event.is_set()", condition_source)
        self.assertIn('!= "Connected"', condition_source)
        self.assertNotIn("_connection_error_visible = False", restart_source)
        self.assertNotIn("_connection_error_visible = False", timeout_source)

    def test_chatroom_unread_rows_are_never_top_level_windows(self) -> None:
        row_source = inspect.getsource(SPRITELINK.ChatroomListRow.__init__)
        refresh_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._refresh_chatroom_list
        )
        self.assertIn("parent: QWidget", row_source)
        self.assertIn("super().__init__(parent)", row_source)
        self.assertIn("self.chatrooms_list,", refresh_source)
        self.assertIn("QLabel(nickname, self)", row_source)
        self.assertIn(
            'QLabel(f"({unread_count})", self)',
            row_source,
        )
        self.assertLess(
            row_source.index("layout.addWidget(unread_label)"),
            row_source.index("unread_label.setVisible"),
        )

    def test_network_worker_waits_until_real_work_is_due(self) -> None:
        self.assertEqual(
            SPRITELINK.polling_interval_seconds(
                window_on_screen=True,
            ),
            6.0,
        )
        self.assertEqual(
            SPRITELINK.polling_interval_seconds(
                window_on_screen=False,
            ),
            9.0,
        )
        self.assertEqual(
            SPRITELINK.muted_inactive_polling_interval_seconds(
                window_on_screen=True,
            ),
            5 * 60.0,
        )
        self.assertEqual(
            SPRITELINK.muted_inactive_polling_interval_seconds(
                window_on_screen=False,
            ),
            7.5 * 60.0,
        )

        activity_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._sync_window_activity
        )
        network_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._network_loop
        )
        self.assertIn("not self._tray_ui_suspended", activity_source)
        self.assertIn("self.root.isVisible()", activity_source)
        self.assertIn("not self.root.isMinimized()", activity_source)
        self.assertIn("self.window_on_screen_event", activity_source)
        self.assertNotIn("window_focused_event", network_source)
        self.assertNotIn("tray_mode_event", network_source)
        self.assertEqual(
            SPRITELINK.network_idle_wait_seconds(
                now=100.0,
                last_global_poll_at=95.0,
                poll_interval=6.0,
            ),
            1.0,
        )
        self.assertEqual(
            SPRITELINK.network_idle_wait_seconds(
                now=100.0,
                last_global_poll_at=None,
                poll_interval=6.0,
            ),
            0.0,
        )
        self.assertEqual(
            SPRITELINK.network_idle_wait_seconds(
                now=100.0,
                last_global_poll_at=100.0,
                poll_interval=6.0,
            ),
            6.0,
        )
        self.assertEqual(
            SPRITELINK.immediate_poll_borrowed_seconds(
                now=101.0,
                last_global_poll_at=100.0,
                poll_interval=6.0,
            ),
            5.0,
        )
        self.assertEqual(
            SPRITELINK.immediate_poll_borrowed_seconds(
                now=101.0,
                last_global_poll_at=None,
                poll_interval=6.0,
            ),
            0.0,
        )
        self.assertEqual(
            SPRITELINK.background_repayment_delay_seconds(
                borrowed_seconds=6.0,
                checks_remaining=6,
            ),
            1.0,
        )
        self.assertEqual(
            SPRITELINK.CHATROOM_SWITCH_REPAYMENT_BACKGROUND_POLLS,
            6,
        )

        self.assertEqual(
            SPRITELINK.next_poll_room_id(
                room_ids=["active", "older", "urgent"],
                active_room_id="active",
                urgent_room_ids={"urgent"},
                last_poll_times={
                    "active": 90.0,
                    "older": 80.0,
                    "urgent": 99.0,
                },
                now=100.0,
                poll_interval=10.0,
            ),
            "urgent",
        )
        self.assertEqual(
            SPRITELINK.next_poll_room_id(
                room_ids=["active", "older", "newer"],
                active_room_id="active",
                urgent_room_ids=set(),
                last_poll_times={
                    "active": 90.0,
                    "older": 10.0,
                    "newer": 80.0,
                },
                now=100.0,
                poll_interval=10.0,
            ),
            "older",
        )
        self.assertEqual(
            SPRITELINK.next_poll_room_id(
                room_ids=["active", "overdue", "urgent"],
                active_room_id="active",
                urgent_room_ids={"active", "urgent"},
                last_poll_times={
                    "active": 95.0,
                    "overdue": 60.0,
                    "urgent": 99.0,
                },
                now=100.0,
                poll_interval=10.0,
            ),
            "active",
        )
        self.assertEqual(
            SPRITELINK.next_poll_room_id(
                room_ids=["active", "overdue", "urgent"],
                active_room_id="active",
                urgent_room_ids={"active", "urgent"},
                last_poll_times={
                    "active": 100.0,
                    "overdue": 60.0,
                    "urgent": 99.0,
                },
                now=110.0,
                poll_interval=10.0,
            ),
            "overdue",
        )
        self.assertEqual(
            SPRITELINK.next_poll_room_id(
                room_ids=["active", "unchecked", "urgent"],
                active_room_id="active",
                urgent_room_ids={"active", "urgent"},
                last_poll_times={
                    "active": 100.0,
                    "urgent": 99.0,
                },
                now=106.0,
                poll_interval=10.0,
            ),
            "unchecked",
        )
        self.assertEqual(
            SPRITELINK.next_poll_room_id(
                room_ids=["active", "background"],
                active_room_id="active",
                urgent_room_ids=set(),
                last_poll_times={
                    "active": 88.0,
                    "background": 99.0,
                },
                now=100.0,
                poll_interval=6.0,
            ),
            "active",
        )

        # A stream signal cannot make a muted inactive room urgent.
        self.assertEqual(
            SPRITELINK.next_poll_room_id(
                room_ids=["active", "ordinary", "muted"],
                active_room_id="active",
                urgent_room_ids={"muted"},
                last_poll_times={
                    "active": 199.0,
                    "ordinary": 198.0,
                    "muted": 100.0,
                },
                now=200.0,
                poll_interval=6.0,
                muted_inactive_room_ids={"muted"},
                muted_poll_interval=300.0,
            ),
            "ordinary",
        )
        # Once its own timer expires, the muted room gets a request slot
        # without needing a stream signal.
        self.assertEqual(
            SPRITELINK.next_poll_room_id(
                room_ids=["active", "ordinary", "muted"],
                active_room_id="active",
                urgent_room_ids=set(),
                last_poll_times={
                    "active": 199.0,
                    "ordinary": 198.0,
                    "muted": -100.0,
                },
                now=200.0,
                poll_interval=6.0,
                muted_inactive_room_ids={"muted"},
                muted_poll_interval=300.0,
            ),
            "muted",
        )

        self.assertFalse(
            SPRITELINK.poll_message_should_notify(
                {"ntfy_time": 99},
                history_scan=True,
                notification_started_at=100,
            )
        )
        self.assertTrue(
            SPRITELINK.poll_message_should_notify(
                {"ntfy_time": 100},
                history_scan=True,
                notification_started_at=100,
            )
        )
        self.assertTrue(
            SPRITELINK.poll_message_should_notify(
                {"ntfy_time": 1},
                history_scan=False,
                notification_started_at=100,
            )
        )

        loop_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._network_loop
        )
        self.assertIn("network_idle_wait_seconds", loop_source)
        self.assertIn("network_wakeup_event.wait", loop_source)
        self.assertIn("last_global_poll_at", loop_source)
        self.assertIn("next_poll_room_id", loop_source)
        self.assertIn("subscription_room_queue", loop_source)
        self.assertIn("forced_active_poll_room_id", loop_source)
        self.assertIn("background_delay_debt", loop_source)
        self.assertIn("background_repayment_delay_seconds", loop_source)
        self.assertIn("muted_inactive_room_ids", loop_source)
        self.assertIn(
            "urgent_room_ids.difference_update",
            loop_source,
        )
        self.assertIn(
            "last_poll_times.setdefault(room_id, now)",
            loop_source,
        )
        self.assertNotIn("last_background_poll_at", loop_source)
        self.assertNotIn("wait(0.08)", loop_source)

        subscription_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._subscription_loop
        )
        self.assertIn(
            "SUBSCRIPTION_RECONNECT_BACKFILL_SECONDS",
            subscription_source,
        )
        self.assertNotIn('"since": "latest"', subscription_source)

        background_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._accept_background_messages
        )
        self.assertIn("poll_message_should_notify", background_source)

    def test_network_wakes_for_work_and_window_visibility_changes(self) -> None:
        refresh_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._request_network_refresh
        )
        send_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._send_current_message
        )
        activity_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._sync_window_activity
        )
        event_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient.eventFilter
        )
        self.assertIn("network_wakeup_event.set()", refresh_source)
        self.assertIn("network_wakeup_event.set()", send_source)
        self.assertIn("network_wakeup_event.set()", activity_source)
        self.assertNotIn("network_wakeup_event.set()", event_source)

    def test_send_reconnect_bypasses_poll_rate_limits(self) -> None:
        client = mock.Mock()
        client.active_chatroom_id = "room-a"
        SPRITELINK.EncryptedChatClient._request_network_refresh(
            client,
            poll_immediately=True,
            bypass_rate_limits=True,
        )
        client.network_control_queue.put.assert_called_once_with({
            "room_id": "room-a",
            "poll_immediately": True,
            "bypass_rate_limits": True,
        })
        client.network_wakeup_event.set.assert_called_once_with()

        send_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._send_current_message
        )
        loop_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._network_loop
        )
        self.assertNotIn('"Not connected"', send_source)
        self.assertIn("if not self.connected:", send_source)
        self.assertIn("bypass_rate_limits=True", send_source)
        self.assertIn("pending_control.get(\"bypass_rate_limits\")", loop_source)
        self.assertIn("if not bypass_rate_limits:", loop_source)

    def test_ui_queue_is_signal_driven_instead_of_polled(self) -> None:
        init_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient.__init__
        )
        queue_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._queue_ui_event
        )
        self.assertIn(
            "ui_event_available.connect(self._process_ui_queue)",
            init_source,
        )
        self.assertNotIn("ui_queue_timer", init_source)
        self.assertIn("ui_queue.put(event)", queue_source)
        self.assertIn("ui_event_available.emit()", queue_source)

    def test_multimedia_and_sounds_are_loaded_on_demand(self) -> None:
        init_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient.__init__
        )
        wav_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._message_sound_effect_for_path
        )
        video_source = inspect.getsource(
            SPRITELINK.AnimatedMediaController._start_video
        )
        self.assertNotIn(
            "_message_sound_effect_for_path(",
            init_source,
        )
        self.assertIn("ensure_qt_multimedia_loaded()", wav_source)
        self.assertIn("ensure_qt_multimedia_loaded()", video_source)

    def test_repeated_message_url_parsing_is_cached(self) -> None:
        SPRITELINK._cached_message_url_spans.cache_clear()
        text = "hello https://example.com/picture.png"
        self.assertEqual(
            SPRITELINK.message_url_spans(text),
            [(6, len(text), "https://example.com/picture.png")],
        )
        SPRITELINK.message_url_spans(text)
        self.assertEqual(
            SPRITELINK._cached_message_url_spans.cache_info().hits,
            1,
        )

    def test_tooltips_are_created_only_when_hovered(self) -> None:
        insert_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._insert_message_item
        )
        show_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._show_pending_chat_tooltip
        )
        self.assertNotIn("profile_icon_tooltip_data_uri", insert_source)
        self.assertIn("_tooltip_for_message_item", show_source)


    def test_unread_divider_tracks_the_next_row_background_edge(
        self,
    ) -> None:
        browser = mock.Mock()
        block = mock.Mock()
        browser._block_row_vertical_bounds.return_value = (42, 65)

        divider_y = SPRITELINK.MessageLogBrowser._unread_divider_y(
            browser,
            block,
        )

        self.assertEqual(divider_y, 64)
        browser._block_row_vertical_bounds.assert_called_once_with(block)
        paint_source = inspect.getsource(
            SPRITELINK.MessageLogBrowser._paint_unread_divider
        )
        fade_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._update_unread_divider_fade
        )
        self.assertIn("_unread_divider_y(block)", paint_source)
        self.assertIn("_unread_divider_y(block)", fade_source)

    def test_unread_divider_moves_below_a_separator_before_unread(
        self,
    ) -> None:
        separator_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient
            ._insert_log_separator_before_newer_content
        )
        self.assertIn(") -> int:", separator_source)
        self.assertIn("return separator_block_number", separator_source)
        self.assertNotIn("cursor.insertBlock()\n        return", separator_source)

        render_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._continue_message_log_render
        )
        self.assertIn(
            "self._message_id_from_log_item(group[-1])",
            render_source,
        )
        self.assertIn(
            "== unread_boundary_id",
            render_source,
        )
        self.assertIn(
            "self.rendered_message_last_blocks[unread_boundary_id] =",
            render_source,
        )
        self.assertIn(
            "last_separator_block_number",
            render_source,
        )
        self.assertLess(
            render_source.index(
                "_insert_log_separator_before_newer_content"
            ),
            render_source.index(
                "self.rendered_message_last_blocks[unread_boundary_id] ="
            ),
        )


class QualityOfLifeUpdateTests(unittest.TestCase):
    def test_single_instance_mutex_rejects_a_second_process(self) -> None:
        kernel32 = mock.Mock()
        kernel32.CreateMutexW.return_value = 123
        kernel32.GetLastError.return_value = 183

        with mock.patch.object(
            SPRITELINK,
            "_SINGLE_INSTANCE_MUTEX_HANDLE",
            None,
        ):
            self.assertFalse(
                SPRITELINK.acquire_single_instance_lock(kernel32)
            )

        kernel32.CloseHandle.assert_called_once_with(123)
        main_source = inspect.getsource(SPRITELINK.main)
        self.assertLess(
            main_source.index("acquire_single_instance_lock()"),
            main_source.index("QApplication.instance()"),
        )

    def test_single_instance_mutex_is_retained_for_process_lifetime(
        self,
    ) -> None:
        kernel32 = mock.Mock()
        kernel32.CreateMutexW.return_value = 456
        kernel32.GetLastError.return_value = 0

        with mock.patch.object(
            SPRITELINK,
            "_SINGLE_INSTANCE_MUTEX_HANDLE",
            None,
        ):
            self.assertTrue(
                SPRITELINK.acquire_single_instance_lock(kernel32)
            )
            self.assertEqual(
                SPRITELINK._SINGLE_INSTANCE_MUTEX_HANDLE,
                456,
            )

        kernel32.CloseHandle.assert_not_called()

    def test_chatroom_context_menu_order_and_invite_copy(self) -> None:
        source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._show_chatroom_context_menu
        )
        edit_at = source.index('menu.addAction("Edit")')
        copy_at = source.index('menu.addAction("Copy Invite Code")')
        mute_at = source.index('mute_menu = menu.addMenu("Mute")')
        remove_at = source.index('menu.addAction("Remove")')
        forever_at = source.index('mute_menu.addAction("Forever")')
        one_hour_at = source.index('mute_menu.addAction("For 1 hour")')
        eight_hours_at = source.index('mute_menu.addAction("For 8 hours")')
        twenty_four_hours_at = source.index(
            'mute_menu.addAction("For 24 hours")'
        )
        self.assertLess(edit_at, copy_at)
        self.assertLess(copy_at, mute_at)
        self.assertLess(mute_at, remove_at)
        self.assertLess(forever_at, one_hour_at)
        self.assertLess(one_hour_at, eight_hours_at)
        self.assertLess(eight_hours_at, twenty_four_hours_at)
        self.assertIn("_copy_chatroom_invite_code(room_id)", source)
        self.assertIn("mute_menu.menuAction().triggered.connect", source)
        self.assertIn("duration_seconds=60 * 60", source)
        self.assertIn("duration_seconds=8 * 60 * 60", source)
        self.assertIn("duration_seconds=24 * 60 * 60", source)

    def test_user_and_message_context_menus_are_separated(self) -> None:
        user_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._show_username_context_menu
        )
        self.assertLess(
            user_source.index(
                'menu.addAction("Trust Links && Images from User")'
            ),
            user_source.index('"Unmute User" if is_muted else "Mute User"'),
        )
        self.assertNotIn("Trust Images from User", user_source)
        self.assertNotIn("Trust Links from User", user_source)
        self.assertNotIn("Collapse Message", user_source)
        self.assertNotIn("Expand Message", user_source)

        message_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._show_message_context_menu
        )
        self.assertLess(
            message_source.index('menu.addAction("Copy Message")'),
            message_source.index('"Collapse Message"'),
        )
        self.assertIn("message_plain_text", message_source)
        self.assertIn("Expand Message", message_source)

        event_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient.eventFilter
        )
        self.assertIn("_message_id_at_position", event_source)
        insert_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._insert_message_item
        )
        self.assertIn("rendered_message_blocks", insert_source)

    def test_remove_dialog_places_remove_button_on_the_right(self) -> None:
        source = inspect.getsource(SPRITELINK.RemoveChatroomDialog.__init__)
        self.assertLess(
            source.index('self.cancel_button = QPushButton("Cancel")'),
            source.index('self.remove_button = QPushButton("Remove!")'),
        )
        self.assertIn(
            '"This chatroom and its locally stored history will be removed "',
            source,
        )
        self.assertIn(
            '"from your computer. Existing messages will still be visible "',
            source,
        )
        self.assertIn('"for other participants."', source)

    def test_removing_chatroom_deletes_its_local_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            with (
                mock.patch.object(
                    SPRITELINK,
                    "CHATROOM_HISTORY_DIRECTORY",
                    directory,
                ),
                mock.patch.object(
                    SPRITELINK,
                    "dpapi_encrypt",
                    side_effect=lambda data, _entropy: data,
                ),
                mock.patch.object(
                    SPRITELINK,
                    "dpapi_decrypt",
                    side_effect=lambda data, _entropy: data,
                ),
                mock.patch.object(
                    SPRITELINK,
                    "room_scope_id",
                    side_effect=lambda _server, key: {
                        "room-key": "a" * 64,
                        "other-key": "b" * 64,
                    }[key],
                ),
            ):
                SPRITELINK.save_chatroom_history(
                    "https://ntfy.sh",
                    "room-key",
                    [{"message": {"m": "secret"}}],
                )
                SPRITELINK.save_chatroom_history(
                    "https://ntfy.sh",
                    "other-key",
                    [{"message": {"m": "keep"}}],
                )
                self.assertTrue(
                    SPRITELINK.delete_local_chatroom_history(
                        "https://ntfy.sh",
                        "room-key",
                    )
                )
                self.assertEqual(
                    SPRITELINK.load_chatroom_history(
                        "https://ntfy.sh",
                        "room-key",
                    ),
                    [],
                )
                self.assertEqual(
                    SPRITELINK.load_chatroom_history(
                        "https://ntfy.sh",
                        "other-key",
                    ),
                    [{"message": {"m": "keep"}}],
                )

        remove_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._remove_chatroom
        )
        self.assertIn("delete_local_chatroom_history(", remove_source)


class MultiTopicSubscriptionTests(unittest.TestCase):
    def test_subscription_reconnects_leave_request_budget_headroom(
        self,
    ) -> None:
        self.assertGreaterEqual(
            SPRITELINK.SUBSCRIPTION_RECONNECT_DELAY_SECONDS,
            30.0,
        )

    def test_message_records_route_by_opaque_topic(self) -> None:
        topic_rooms = {
            "topic-a": ("room-a",),
            "topic-b": ("room-b", "room-c"),
        }
        self.assertEqual(
            SPRITELINK.subscription_room_ids(
                {
                    "event": "message",
                    "topic": "topic-b",
                    "message": "opaque",
                },
                topic_rooms,
            ),
            ("room-b", "room-c"),
        )
        self.assertEqual(
            SPRITELINK.subscription_room_ids(
                {"event": "keepalive", "topic": "topic-b"},
                topic_rooms,
            ),
            (),
        )
        self.assertEqual(
            SPRITELINK.subscription_room_ids(
                {"event": "message", "topic": "unknown"},
                topic_rooms,
            ),
            (),
        )

    def test_subscription_omits_muted_inactive_room_topics(self) -> None:
        client = mock.Mock()
        client.config_data = {"server_url": "https://ntfy.sh"}
        client.active_chatroom_id = "room-a"
        client._muted_chatroom_ids.return_value = {
            "room-a",
            "room-b",
        }
        client._chatroom_definitions.return_value = [
            {"id": "room-a", "key": "key-a"},
            {"id": "room-b", "key": "key-b"},
            {"id": "room-c", "key": "key-a"},
        ]

        with mock.patch.object(
            SPRITELINK,
            "derive_ntfy_topic",
            side_effect=lambda key: f"topic-{key[-1]}",
        ):
            server_url, topic_rooms = (
                SPRITELINK.EncryptedChatClient._subscription_snapshot(
                    client
                )
            )

        self.assertEqual(server_url, "https://ntfy.sh")
        self.assertEqual(
            topic_rooms,
            {
                # The muted active room remains responsive. The muted
                # inactive room-b is absent from the subscription.
                "topic-a": ("room-a", "room-c"),
            },
        )

    def test_one_stream_covers_all_topics_and_wakes_secure_polls(self) -> None:
        stream_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._subscription_loop
        )
        network_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._network_loop
        )
        start_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._start_network_thread
        )

        self.assertIn('topics = ",".join(topic_rooms)', stream_source)
        self.assertIn(
            "SUBSCRIPTION_RECONNECT_BACKFILL_SECONDS",
            stream_source,
        )
        self.assertNotIn('"since": "latest"', stream_source)
        self.assertIn("stream=True", stream_source)
        self.assertIn("response.iter_lines()", stream_source)
        self.assertIn("subscription_room_ids(", stream_source)
        self.assertIn("subscription_room_queue.put_nowait", stream_source)
        self.assertNotIn("open_opaque_packet", stream_source)
        self.assertIn("subscription_room_queue.get_nowait", network_source)
        self.assertIn("self._network_poll(", network_source)
        self.assertIn('"SpriteLinkSubscription"', start_source)

    def test_subscription_restarts_when_topics_or_server_change(self) -> None:
        for method in (
            SPRITELINK.EncryptedChatClient._store_new_chatroom,
            SPRITELINK.EncryptedChatClient._edit_chatroom,
            SPRITELINK.EncryptedChatClient._remove_chatroom,
            SPRITELINK.EncryptedChatClient._set_chatroom_muted,
            SPRITELINK.EncryptedChatClient._switch_active_chatroom,
            SPRITELINK.EncryptedChatClient._save_and_reconnect,
        ):
            self.assertIn(
                "_request_subscription_refresh",
                inspect.getsource(method),
            )

    def test_subscription_refresh_does_not_wait_for_stream_close(
        self,
    ) -> None:
        close_started = SPRITELINK.threading.Event()
        allow_close = SPRITELINK.threading.Event()

        class BlockingResponse:
            def close(self) -> None:
                close_started.set()
                allow_close.wait(2.0)

        client = mock.Mock()
        client.subscription_refresh_event = mock.Mock()
        client.subscription_response_lock = SPRITELINK.threading.Lock()
        client.subscription_response = BlockingResponse()

        started_at = SPRITELINK.time.monotonic()
        try:
            SPRITELINK.EncryptedChatClient._request_subscription_refresh(
                client
            )
            elapsed = SPRITELINK.time.monotonic() - started_at
            self.assertLess(elapsed, 0.5)
            client.subscription_refresh_event.set.assert_called_once_with()
            self.assertTrue(close_started.wait(0.5))
        finally:
            allow_close.set()

        refresh_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._request_subscription_refresh
        )
        self.assertIn('"SpriteLinkSubscriptionRefreshClose"', refresh_source)
        self.assertIn("daemon=True", refresh_source)

    def test_minimized_and_tray_windows_share_off_screen_polling(self) -> None:
        suspend_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._suspend_for_tray
        )
        resume_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._resume_from_tray
        )
        event_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient.eventFilter
        )
        loop_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._network_loop
        )
        self.assertIn("_sync_window_activity()", suspend_source)
        self.assertIn("_sync_window_activity()", resume_source)
        self.assertIn("WindowStateChange", event_source)
        self.assertIn("_sync_window_activity", event_source)
        self.assertIn("window_on_screen_event.is_set()", loop_source)
        self.assertNotIn("tray_mode_event", loop_source)



class TrayLifecycleOptimizationTests(unittest.TestCase):
    def test_tray_icon_is_always_visible_while_running(self) -> None:
        build_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._build_tray_icon
        )
        self.assertIn("self.tray_icon.show()", build_source)
        self.assertLess(
            build_source.index('"Show SpriteLink"'),
            build_source.index('"Minimize to Tray"'),
        )
        self.assertIn("setCheckable(True)", build_source)
        self.assertIn("_on_tray_minimize_toggled", build_source)

        restore_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._restore_from_tray
        )
        self.assertNotIn("tray_icon.hide()", restore_source)

    def test_setting_only_controls_close_button_behavior(self) -> None:
        close_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._on_close
        )
        can_hide_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._can_minimize_to_tray
        )
        build_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._build_tray_icon
        )
        self.assertIn("_can_minimize_to_tray()", close_source)
        self.assertIn("minimize_to_tray_var.get()", can_hide_source)
        self.assertNotIn("minimize_to_tray_var.get()", build_source)

    def test_notifications_work_while_open_or_minimized(self) -> None:
        mark_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._mark_tray_notification
        )
        event_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient.eventFilter
        )
        self.assertNotIn("window_focused_event.is_set()", mark_source)
        self.assertIn("tray_icon.isVisible()", mark_source)
        self.assertNotIn("_minimized_to_tray", mark_source)
        self.assertIn(
            "_mark_chatroom_read(self.active_chatroom_id)",
            event_source,
        )
        self.assertNotIn(
            "self._clear_tray_notification()",
            event_source,
        )
        self.assertIn("WindowStateChange", event_source)

    def test_tray_outline_is_kept_until_all_unread_are_cleared(self) -> None:
        client = mock.Mock()
        unread_counts: dict[str, int] = {"background": 2}
        client._unread_counts.side_effect = lambda: unread_counts
        client._has_unread_messages.side_effect = lambda: (
            SPRITELINK.EncryptedChatClient._has_unread_messages(client)
        )

        SPRITELINK.EncryptedChatClient._clear_tray_notification_if_no_unread(
            client
        )
        client._clear_tray_notification.assert_not_called()

        unread_counts.clear()
        SPRITELINK.EncryptedChatClient._clear_tray_notification_if_no_unread(
            client
        )
        client._clear_tray_notification.assert_called_once_with()

    def test_active_room_outline_requires_an_unfocused_window(self) -> None:
        self.assertFalse(
            SPRITELINK.notification_outline_required(
                "active",
                "active",
                window_focused=True,
            )
        )
        self.assertTrue(
            SPRITELINK.notification_outline_required(
                "active",
                "active",
                window_focused=False,
            )
        )
        self.assertTrue(
            SPRITELINK.notification_outline_required(
                "background",
                "active",
                window_focused=True,
            )
        )
        active_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._accept_network_message
        )
        background_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._accept_background_messages
        )
        self.assertIn("notification_outline_required", active_source)
        self.assertIn("notification_outline_required", background_source)

    def test_existing_unread_is_shown_when_tray_icon_starts(self) -> None:
        build_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._build_tray_icon
        )
        self.assertIn("self._has_unread_messages()", build_source)
        self.assertIn("self._mark_tray_notification()", build_source)
        self.assertLess(
            build_source.index("self.tray_icon.show()"),
            build_source.index("self._has_unread_messages()"),
        )

    def test_hidden_tray_mode_releases_heavy_render_state(self) -> None:
        suspend_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._suspend_for_tray
        )
        resume_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._resume_from_tray
        )
        render_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._render_message_log
        )
        queue_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._process_ui_queue
        )
        self.assertIn("_pause_animated_media()", suspend_source)
        self.assertIn(
            "_release_message_sound_resources()",
            suspend_source,
        )
        self.assertIn("image_preview_cache.clear()", suspend_source)
        self.assertIn("_reset_chat_document()", suspend_source)
        self.assertIn("QPixmapCache.clear()", suspend_source)
        self.assertIn("setUpdatesEnabled(False)", suspend_source)
        self.assertIn("setUpdatesEnabled(True)", resume_source)
        self.assertIn("_render_message_log", resume_source)
        self.assertIn("_tray_ui_suspended", render_source)
        self.assertIn("_tray_ui_suspended", queue_source)

    def test_tray_mode_fully_detaches_multimedia_backends(self) -> None:
        controller_source = inspect.getsource(
            SPRITELINK.AnimatedMediaController.stop
        )
        audio_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient
            ._release_message_sound_resources
        )
        self.assertIn("player.setVideoSink(None)", controller_source)
        self.assertIn("player.setSource(QUrl())", controller_source)
        self.assertIn("video_sink.deleteLater()", controller_source)
        self.assertIn("_buffer.setData(QByteArray())", controller_source)
        self.assertIn("effect.setSource(QUrl())", audio_source)
        self.assertIn("player.setAudioOutput(None)", audio_source)
        self.assertIn("message_sound_effects.clear()", audio_source)

    def test_tray_audio_is_released_after_notifications(self) -> None:
        wav_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient
            ._on_message_sound_effect_playing_changed
        )
        compressed_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient
            ._on_compressed_message_sound_state_changed
        )
        self.assertIn("_tray_ui_suspended", wav_source)
        self.assertIn("_release_message_sound_resources", wav_source)
        self.assertIn("_tray_ui_suspended", compressed_source)
        self.assertIn(
            "_release_message_sound_resources",
            compressed_source,
        )

    def test_tray_exit_explicitly_stops_the_application(self) -> None:
        quit_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._quit_from_tray
        )
        finish_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._finish_quit_from_tray
        )
        close_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._on_close
        )
        self.assertIn("QTimer.singleShot", quit_source)
        self.assertNotIn("self.root.close()", quit_source)
        self.assertIn("self.root.close()", finish_source)
        self.assertIn("QApplication.instance()", finish_source)
        self.assertIn("app.quit()", finish_source)
        self.assertNotIn(".join(", close_source)
        self.assertIn("self.subscription_refresh_event.set()", close_source)
        self.assertIn("daemon=True", close_source)
        self.assertIn("self.session.close()", close_source)
        self.assertIn(
            "_release_message_sound_resources()",
            close_source,
        )

    def test_image_workers_cannot_keep_python_running(self) -> None:
        worker_pool = SPRITELINK.DaemonTaskPool(
            max_workers=2,
            thread_name_prefix="SecurityTestWorker",
        )
        try:
            self.assertTrue(worker_pool._threads)
            self.assertTrue(
                all(worker.daemon for worker in worker_pool._threads)
            )
        finally:
            worker_pool.shutdown(wait=True, cancel_futures=True)


class ProfileIconTests(unittest.TestCase):
    def test_jpeg_profile_icons_are_optimized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "profile.jpg"
            SPRITELINK.Image.new(
                "RGB",
                (48, 32),
                (20, 100, 180),
            ).save(source_path, format="JPEG")
            gif_data = SPRITELINK.optimize_profile_icon(str(source_path))

        self.assertIn(gif_data[:6], (b"GIF87a", b"GIF89a"))
        SPRITELINK.decode_profile_icon(
            SPRITELINK.encode_profile_icon(gif_data)
        )

    def test_profile_icon_picker_uses_native_thumbnail_dialog(self) -> None:
        source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._choose_profile_icon
        )
        self.assertNotIn("DontUseNativeDialog", source)
        self.assertIn("*.png *.jpg *.jpeg", source)
        self.assertIn("_exec_themed_file_dialog", source)

    def test_custom_chime_picker_uses_native_themed_dialog(self) -> None:
        source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._on_message_sound_selected
        )
        self.assertNotIn("DontUseNativeDialog", source)
        self.assertIn("_exec_themed_file_dialog", source)
        themed_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._exec_themed_file_dialog
        )
        self.assertIn("_apply_window_titlebar_theme", themed_source)
        self.assertIn("QTimer.singleShot", themed_source)

    def test_all_non_file_dialogs_receive_active_window_theme(self) -> None:
        init_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient.__init__
        )
        self.assertIn("app.installEventFilter(self)", init_source)

        event_filter_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient.eventFilter
        )
        self.assertIn("isinstance(watched, QDialog)", event_filter_source)
        self.assertIn(
            "not isinstance(watched, QFileDialog)",
            event_filter_source,
        )
        self.assertIn("QEvent.Type.Show", event_filter_source)
        self.assertIn("QEvent.Type.WindowActivate", event_filter_source)
        self.assertIn(
            "self._apply_dialog_window_theme(watched)",
            event_filter_source,
        )

        helper_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._apply_dialog_window_theme
        )
        self.assertIn("_apply_window_titlebar_theme", helper_source)
        self.assertIn("QTimer.singleShot", helper_source)


class ImageTrustTests(unittest.TestCase):
    def test_requested_and_common_cdn_hosts_are_trusted(self) -> None:
        trusted_urls = (
            "https://cdn.discordapp.com/attachments/1/2/example.png",
            "https://upload.wikimedia.org/wikipedia/commons/a/a0/example.jpg",
            "https://cdn.donmai.us/original/example.webp",
            "https://cdn.donmai.moe/original/example.webp",
            "https://pbs.twimg.com/media/example?format=jpg&name=large",
            "https://i.imgur.com/example.png",
            "https://images.unsplash.com/photo-example",
            "https://cdn.bsky.app/img/feed_fullsize/plain/example",
            "https://media.tenor.com/example/tenor.gif",
            "https://media2.giphy.com/media/example/giphy.gif",
            "https://cdn.klipy.com/example.gif",
            "https://res.cloudinary.com/example/image/upload/sample",
            "https://images.ctfassets.net/example/image.jpg",
            "https://cdn.sanity.io/images/example/image.webp",
            "https://live.staticflickr.com/example/image.jpg",
            "https://cdn.pixabay.com/photo/example.jpg",
            "https://static1.e621.net/data/example.png",
            "https://img3.gelbooru.com/images/example.jpg",
            "https://i.nhentai.net/galleries/example/1.jpg",
            "https://thumbs2.redgifs.com/example.jpg",
            "https://wimg.rule34.xxx/images/example.jpeg",
            "https://static.wikia.nocookie.net/example/image.png",
            "https://raw.githubusercontent.com/owner/repo/main/example.png",
        )
        for url in trusted_urls:
            with self.subTest(url=url):
                self.assertTrue(SPRITELINK.is_direct_image_url(url))
                self.assertTrue(SPRITELINK.is_trusted_image_url(url))

    def test_trusted_hosts_require_https_and_exact_host_matches(self) -> None:
        untrusted_urls = (
            "http://cdn.discordapp.com/attachments/1/2/example.png",
            "https://cdn.discordapp.com.evil.example/example.png",
            "https://evilcdn.discordapp.com/example.png",
            "https://cdn.donmai.evil.example/example.png",
            "https://static1.e621.net.evil.example/example.png",
            "https://cdn.klipy.com.evil.example/example.gif",
            "https://example.com/example.png",
        )
        for url in untrusted_urls:
            with self.subTest(url=url):
                self.assertFalse(SPRITELINK.is_trusted_image_url(url))

    def test_sender_trust_overrides_domain_trust(self) -> None:
        url = "https://example.com/example.png"
        self.assertFalse(
            SPRITELINK.is_image_url_trusted_for_sender(
                url,
                "sender",
                False,
                set(),
            )
        )
        self.assertTrue(
            SPRITELINK.is_image_url_trusted_for_sender(
                url,
                "sender",
                False,
                {"sender"},
            )
        )
        self.assertTrue(
            SPRITELINK.is_image_url_trusted_for_sender(
                url,
                "sender",
                True,
                set(),
            )
        )


class LinkSafetyTests(unittest.TestCase):
    def test_link_trust_policy_distinguishes_local_and_remote_users(
        self,
    ) -> None:
        trusted_user_ids = {"trusted-sender"}
        ordinary = SPRITELINK.analyze_link_url("https://example.com/")
        suspicious = SPRITELINK.analyze_link_url(
            "https://google.com@evilsite.net/"
        )
        lookalike = SPRITELINK.analyze_link_url(
            "https://gооgle.com/"
        )

        self.assertFalse(SPRITELINK.link_requires_warning(
            ordinary,
            "trusted-sender",
            trusted_user_ids,
        ))
        self.assertTrue(SPRITELINK.link_requires_warning(
            ordinary,
            "untrusted-sender",
            trusted_user_ids,
        ))
        self.assertTrue(SPRITELINK.link_requires_warning(
            suspicious,
            "trusted-sender",
            trusted_user_ids,
        ))
        self.assertTrue(SPRITELINK.link_requires_warning(
            lookalike,
            "trusted-sender",
            trusted_user_ids,
        ))
        self.assertFalse(SPRITELINK.link_requires_warning(
            suspicious,
            "local-sender",
            trusted_user_ids,
            is_local=True,
        ))
        self.assertFalse(SPRITELINK.link_requires_warning(
            lookalike,
            "local-sender",
            trusted_user_ids,
            is_local=True,
        ))

        open_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._open_url_in_browser
        )
        self.assertIn("self._authenticated_client_id()", open_source)
        self.assertIn("is_local=", open_source)

    def test_user_trust_is_disabled_by_default_and_persisted(self) -> None:
        config = SPRITELINK.default_config()
        self.assertEqual(config["trusted_link_and_image_users"], {})
        source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._set_user_links_and_images_trusted
        )
        self.assertIn('"trusted_link_and_image_users"', source)
        self.assertIn("_write_room_preference_ids", source)
        menu_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._show_username_context_menu
        )
        self.assertIn("setCheckable(True)", menu_source)
        self.assertIn(
            "trust_action.setChecked(is_local or trusts_links_and_images)",
            menu_source,
        )
        self.assertIn(
            "trust_action.setEnabled(not is_local)",
            menu_source,
        )

    def test_separate_legacy_trust_settings_are_merged(self) -> None:
        existing_config = SPRITELINK.default_config()
        existing_config.pop("trusted_link_and_image_users")
        existing_config["trusted_image_users"] = {
            "room-a": ["image-user", "both-user"],
        }
        existing_config["trusted_link_users"] = {
            "room-a": ["link-user", "both-user"],
            "room-b": ["other-user"],
        }
        config_path = mock.Mock()
        config_path.exists.return_value = True
        config_path.read_bytes.return_value = b"settings"

        with (
            mock.patch.object(SPRITELINK, "CONFIG_PATH", config_path),
            mock.patch.object(
                SPRITELINK,
                "_load_dpapi_config",
                return_value=existing_config,
            ),
        ):
            migrated = SPRITELINK.load_config()

        self.assertEqual(
            migrated["trusted_link_and_image_users"],
            {
                "room-a": ["both-user", "image-user", "link-user"],
                "room-b": ["other-user"],
            },
        )
        self.assertNotIn("trusted_image_users", migrated)
        self.assertNotIn("trusted_link_users", migrated)

    def test_rendered_links_carry_sender_identity_to_warning_policy(self) -> None:
        insert_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._insert_message_text_with_links
        )
        click_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient.eventFilter
        )
        image_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._insert_embedded_image_preview
        )

        self.assertIn(
            "_register_rendered_link(url, client_id)",
            insert_source,
        )
        self.assertIn("anchored_client_id", click_source)
        self.assertIn("rendered_link_senders.get(anchor)", click_source)
        self.assertIn(
            "rendered_link_senders[anchor] = client_id",
            image_source,
        )

    def test_popular_and_sfw_media_domains_open_without_warning(self) -> None:
        trusted_urls = (
            "https://google.com/search?q=spritelink",
            "https://www.youtube.com/watch?v=example",
            "https://github.com/glasspage/SpriteLink",
            "https://discord.com/channels/example",
            "https://en.wikipedia.org/wiki/Instant_messaging",
            "https://cdn.discordapp.com/attachments/1/2/example.png",
            "https://upload.wikimedia.org/example.png",
            "https://media2.giphy.com/media/example/giphy.gif",
            "https://cdn.klipy.com/example.gif",
            "https://images.unsplash.com/example",
            "https://raw.githubusercontent.com/owner/repo/main/image.png",
            "https://adriansblinkiecollection.neocities.org/blinkies.html",
        )
        for url in trusted_urls:
            with self.subTest(url=url):
                self.assertTrue(SPRITELINK.analyze_link_url(url).trusted)

    def test_domain_matching_does_not_trust_suffix_spoofing(self) -> None:
        for url in (
            "https://github.com.evil.example/login",
            "https://notgithub.com/login",
            "https://discord.com.evil.example/invite",
            "https://adriansblinkiecollection.neocities.org.evil.example/",
            "https://sub.adriansblinkiecollection.neocities.org/",
            "https://example.com/",
        ):
            with self.subTest(url=url):
                analysis = SPRITELINK.analyze_link_url(url)
                self.assertFalse(analysis.trusted)
                self.assertFalse(analysis.suspicious)

    def test_unicode_lookalikes_are_underlined_and_suspicious(self) -> None:
        url = "https://gооgle.com/account"
        analysis = SPRITELINK.analyze_link_url(url)
        self.assertFalse(analysis.trusted)
        self.assertTrue(analysis.has_lookalike_characters)
        self.assertFalse(analysis.has_userinfo)
        html = SPRITELINK.link_warning_url_html(
            url,
            analysis.underlined_indices,
        )
        self.assertIn(
            '<span style="color: #c00000;"><u>оо</u></span>',
            html,
        )
        self.assertNotIn("<u>gle", html)

    def test_punycode_domains_are_marked_as_lookalikes(self) -> None:
        url = "https://xn--80ak6aa92e.com/"
        analysis = SPRITELINK.analyze_link_url(url)
        self.assertTrue(analysis.has_lookalike_characters)
        self.assertIn(
            (
                '<span style="color: #c00000;"><u>'
                "xn--80ak6aa92e</u></span>"
            ),
            SPRITELINK.link_warning_url_html(
                url,
                analysis.underlined_indices,
            ),
        )

    def test_non_confusable_international_domain_is_not_mislabeled(self) -> None:
        analysis = SPRITELINK.analyze_link_url("https://münich.example/")
        self.assertFalse(analysis.trusted)
        self.assertFalse(analysis.has_lookalike_characters)

    def test_userinfo_spoofing_underlines_the_real_hostname(self) -> None:
        url = "https://google.com@evilsite.net/private"
        analysis = SPRITELINK.analyze_link_url(url)
        self.assertFalse(analysis.trusted)
        self.assertTrue(analysis.has_userinfo)
        self.assertFalse(analysis.has_lookalike_characters)
        self.assertIn(
            (
                'google.com@<span style="color: #c00000;"><u>'
                "evilsite.net</u></span>/private"
            ),
            SPRITELINK.link_warning_url_html(
                url,
                analysis.underlined_indices,
            ),
        )

    def test_trusted_real_hostname_still_warns_when_userinfo_exists(self) -> None:
        analysis = SPRITELINK.analyze_link_url(
            "https://attacker@github.com/"
        )
        self.assertFalse(analysis.trusted)
        self.assertTrue(analysis.suspicious)

    def test_link_warning_popup_contains_requested_copy_and_actions(self) -> None:
        build_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._build_link_warning_popup
        )
        show_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._show_link_warning_popup
        )
        self.assertIn('self._heading("Link Warning")', build_source)
        self.assertIn(
            "Make sure you trust this website before continuing.",
            build_source,
        )
        self.assertIn('QPushButton("Go to URL")', build_source)
        self.assertIn('QPushButton("Cancel")', build_source)
        self.assertIn("TextSelectableByMouse", build_source)
        self.assertIn("look-alike characters", show_source)
        self.assertIn("username section", show_source)
        self.assertIn("before the domain name.", show_source)
        self.assertIn('"color: #c00000;"', show_source)

    def test_link_warning_monospace_font_survives_style_refreshes(self) -> None:
        font_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._make_link_warning_url_font
        )
        refresh_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._apply_application_font_strategy
        )
        build_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._build_link_warning_popup
        )
        self.assertIn('"lucida console"', font_source)
        self.assertIn('"consolas"', font_source)
        self.assertIn("setFixedPitch(True)", font_source)
        self.assertIn("_refresh_special_widget_fonts()", refresh_source)
        self.assertIn("_make_link_warning_url_font()", build_source)


class ImageEmbeddingTests(unittest.TestCase):
    def test_extensionless_provider_pages_are_embeddable(self) -> None:
        urls = (
            "https://tenor.com/view/shower-head-shower-random-dacing-taking-shower-gif-3237625789977090490",
            "https://klipy.com/gifs/christian-bale-me-when-the",
            "https://giphy.com/gifs/example-slug",
            "https://imgur.com/example",
            "https://redgifs.com/watch/example",
        )
        for url in urls:
            with self.subTest(url=url):
                self.assertTrue(
                    SPRITELINK.is_supported_media_page_url(url)
                )
                self.assertTrue(
                    SPRITELINK.is_embeddable_media_url(url)
                )
                self.assertTrue(SPRITELINK.is_trusted_image_url(url))
                self.assertEqual(
                    SPRITELINK.direct_image_urls_in_message(url),
                    [url],
                )

    def test_provider_metadata_prefers_looping_video(self) -> None:
        page_url = "https://tenor.com/view/example"
        html = """
        <html><head>
        <meta property="og:image"
              content="https://media.tenor.com/example/tenor.gif">
        <meta property="og:video:secure_url"
              content="https://media.tenor.com/example/tenor.mp4">
        </head></html>
        """
        self.assertEqual(
            SPRITELINK.resolve_media_url_from_page(page_url, html),
            (
                "https://media.tenor.com/example/tenor.mp4",
                "video",
            ),
        )

    def test_klipy_metadata_accepts_extensionless_cdn_media(self) -> None:
        page_url = "https://klipy.com/gifs/example"
        html = """
        <meta property="og:image"
              content="//cdn.klipy.com/media/example">
        """
        self.assertEqual(
            SPRITELINK.resolve_media_url_from_page(page_url, html),
            ("https://cdn.klipy.com/media/example", "image"),
        )
        fetch_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._fetch_remote_image_preview
        )
        self.assertIn("resolve_media_url_from_page", fetch_source)

    def test_remote_media_download_limit_is_50_mb(self) -> None:
        self.assertEqual(
            SPRITELINK.MAX_REMOTE_IMAGE_BYTES,
            50 * 1024 * 1024,
        )

    def test_nsfw_hosts_are_trusted_but_remain_masked(self) -> None:
        urls = (
            "https://static1.e621.net/data/example.png",
            "https://img3.gelbooru.com/images/example.jpg",
            "https://i.nhentai.net/galleries/example/1.jpg",
            "https://thumbs2.redgifs.com/example.jpg",
            "https://rule34-data-001.paheal.net/example.jpg",
            "https://wimg.rule34.xxx/images/example.jpeg",
            "https://img.xbooru.com/images/example.png",
            "https://files.yande.re/image/example.jpg",
        )
        for url in urls:
            with self.subTest(url=url):
                self.assertTrue(SPRITELINK.is_trusted_image_url(url))
                self.assertTrue(SPRITELINK.is_likely_nsfw_image_url(url))

    def test_tenor_videos_are_embeddable_looping_media(self) -> None:
        urls = (
            "https://media.tenor.com/example/tenor.mp4",
            "https://c.tenor.com/example/tenor.webm",
            "https://media.tenor.com/example/tenor?format=mp4",
            "https://cdn.klipy.com/example.mp4",
            "https://media2.giphy.com/example.webm",
        )
        for url in urls:
            with self.subTest(url=url):
                if "tenor.com" in url:
                    self.assertTrue(SPRITELINK.is_tenor_video_url(url))
                self.assertTrue(
                    SPRITELINK.is_trusted_looping_video_url(url)
                )
                self.assertTrue(SPRITELINK.is_embeddable_media_url(url))
                self.assertTrue(SPRITELINK.is_trusted_image_url(url))
        self.assertFalse(
            SPRITELINK.is_tenor_video_url(
                "https://example.com/not-tenor.mp4"
            )
        )
        controller_source = inspect.getsource(
            SPRITELINK.AnimatedMediaController
        )
        self.assertIn("QVideoSink", controller_source)
        self.assertIn("QMediaPlayer.Loops.Infinite", controller_source)
        self.assertIn("player.play()", controller_source)

    def test_gifs_use_the_animated_media_controller(self) -> None:
        controller_source = inspect.getsource(
            SPRITELINK.AnimatedMediaController
        )
        self.assertIn("QMovie", controller_source)
        self.assertIn("movie.frameChanged.connect", controller_source)
        preview_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._on_animated_media_frame
        )
        self.assertIn("_set_large_image_preview_frame", preview_source)
        self.assertIn("document.addResource", preview_source)

    def test_animated_frames_keep_native_playback_timing(self) -> None:
        controller_source = inspect.getsource(
            SPRITELINK.AnimatedMediaController._emit_frame
        )
        self.assertIn("emitted_frame = frame.copy()", controller_source)
        self.assertNotIn("IMAGE_PREVIEW_MAX_WIDTH", controller_source)
        inline_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._on_animated_media_frame
        )
        self.assertNotIn("MIN_INLINE_ANIMATION_FRAME_MS", inline_source)
        self.assertNotIn("time.monotonic()", inline_source)

    def test_small_inline_media_is_not_upscaled(self) -> None:
        frame = SPRITELINK.QImage(
            48,
            32,
            SPRITELINK.QImage.Format.Format_ARGB32,
        )
        media = SPRITELINK.RemoteMediaPreview(
            source_url="https://example.com/small.gif",
            data=b"",
            kind="animated_gif",
            frame=frame,
        )
        preview = SPRITELINK.EncryptedChatClient._scaled_inline_media_frame(
            media,
            frame,
        )
        self.assertEqual((preview.width(), preview.height()), (48, 32))

        video_media = SPRITELINK.RemoteMediaPreview(
            source_url="https://example.com/small.mp4",
            data=b"",
            kind="looping_video",
            frame=frame,
        )
        video_preview = (
            SPRITELINK.EncryptedChatClient._scaled_inline_media_frame(
                video_media,
                frame,
            )
        )
        self.assertEqual(
            (video_preview.width(), video_preview.height()),
            (48, 32),
        )

    def test_large_preview_contains_media_within_visible_overlay(self) -> None:
        source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._set_large_image_preview_frame
        )
        self.assertIn("self.image_preview_overlay.height()", source)
        self.assertIn("self.image_preview_button_row.sizeHint()", source)
        self.assertIn("available_width", source)
        self.assertIn("available_height", source)
        self.assertIn("Qt.AspectRatioMode.KeepAspectRatio", source)
        self.assertNotIn(
            "EMBEDDED_IMAGE_MAX_EDGE",
            source,
        )

    def test_untrusted_image_link_is_omitted_from_display_text(self) -> None:
        url = "https://example.com/private-image.png"
        display_text = (
            SPRITELINK.message_text_with_untrusted_images_hidden(
                f"look at {url} please",
                {url},
            )
        )
        self.assertEqual(
            display_text,
            "[untrusted image] look at please",
        )
        self.assertNotIn(url, display_text)
        render_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._insert_message_item
        )
        self.assertIn(
            "message_text_with_untrusted_images_hidden",
            render_source,
        )
        self.assertEqual(
            SPRITELINK.message_text_with_untrusted_images_hidden(
                url,
                {url},
            ),
            "[untrusted image]",
        )


class HttpsOnlyMediaAndLinkTests(unittest.TestCase):
    def test_only_https_urls_are_recognized_as_links(self) -> None:
        insecure = "http://cdn.discordapp.com/example.png"
        secure = "https://cdn.discordapp.com/example.png"
        spans = SPRITELINK.message_url_spans(
            f"insecure {insecure} secure {secure}"
        )
        self.assertEqual(
            [url for _start, _end, url in spans],
            [secure],
        )
        self.assertFalse(SPRITELINK.is_direct_image_url(insecure))
        self.assertTrue(SPRITELINK.is_direct_image_url(secure))
        self.assertFalse(
            SPRITELINK.is_tenor_video_url(
                "http://media.tenor.com/example.mp4"
            )
        )

    def test_browser_and_context_link_guards_require_https(self) -> None:
        open_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._open_url_in_browser
        )
        context_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._link_url_from_anchor
        )
        self.assertIn(
            'scheme().casefold() != "https"',
            open_source,
        )
        self.assertIn(
            'scheme().casefold() == "https"',
            context_source,
        )
        for source in (open_source, context_source):
            self.assertNotIn('"http"', source)


class RoomKeyTests(unittest.TestCase):
    def test_generated_room_keys_are_exactly_32_url_safe_characters(self) -> None:
        keys = {SPRITELINK.generate_chatroom_key() for _ in range(16)}
        self.assertEqual(len(keys), 16)
        for key in keys:
            self.assertEqual(len(key), 32)
            self.assertRegex(key, r"^[A-Za-z0-9_-]{32}$")


class ChatroomInviteCodeTests(unittest.TestCase):
    def test_invite_code_round_trip_packs_name_and_key(self) -> None:
        code = SPRITELINK.make_chatroom_invite_code(
            "Café Friends",
            "key with spaces/+ symbols",
        )
        self.assertTrue(code.startswith("SL-"))
        self.assertNotIn("=", code)
        encoded = code.removeprefix("SL-")
        payload = base64.urlsafe_b64decode(
            encoded + ("=" * (-len(encoded) % 4))
        )
        self.assertEqual(
            SPRITELINK.json.loads(payload.decode("utf-8")),
            {
                "name": "Café Friends",
                "key": "key with spaces/+ symbols",
            },
        )
        self.assertEqual(
            SPRITELINK.parse_chatroom_invite_code(f"  {code}\n"),
            ("Café Friends", "key with spaces/+ symbols"),
        )

    def test_invalid_invite_codes_are_rejected(self) -> None:
        invalid_payloads = (
            "",
            "not-an-invite",
            "SL-",
            "SL-***",
            "SL-e30",
            "SL-W10",
            "SL-eyJuYW1lIjoiUm9vbSJ9",
        )
        for code in invalid_payloads:
            with self.subTest(code=code):
                with self.assertRaisesRegex(ValueError, "valid"):
                    SPRITELINK.parse_chatroom_invite_code(code)

        overlong_name_code = SPRITELINK.INVITE_CODE_PREFIX + (
            base64.urlsafe_b64encode(
                SPRITELINK.json.dumps({
                    "name": "N" * 65,
                    "key": "key",
                }).encode("utf-8")
            ).decode("ascii").rstrip("=")
        )
        with self.assertRaisesRegex(ValueError, "valid"):
            SPRITELINK.parse_chatroom_invite_code(overlong_name_code)

    def test_add_chatroom_dialogs_use_create_and_join_terminology(self) -> None:
        choice_source = inspect.getsource(
            SPRITELINK.AddChatroomChoiceDialog
        )
        details_source = inspect.getsource(
            SPRITELINK.ChatroomDetailsDialog
        )
        join_source = inspect.getsource(SPRITELINK.JoinChatroomDialog)
        created_source = inspect.getsource(
            SPRITELINK.ChatroomCreatedDialog
        )
        self.assertIn('QPushButton("Join Chatroom")', choice_source)
        self.assertIn('QPushButton("Create Chatroom")', choice_source)
        self.assertIn('QLabel("Name")', details_source)
        self.assertNotIn("Nickname", details_source)
        self.assertNotIn("Only share this key", details_source)
        self.assertIn('QLabel("Invite Code")', join_source)
        self.assertIn('QPushButton("Join Chatroom")', join_source)
        self.assertIn('self.setWindowTitle("Chatroom created!")', created_source)
        self.assertIn(
            'QPushButton("Copy Invite Code")',
            created_source,
        )
        self.assertIn('QPushButton("OK")', created_source)

    def test_add_chatroom_routes_to_selected_flow(self) -> None:
        add_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._add_chatroom
        )
        create_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._create_chatroom
        )
        join_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._join_chatroom
        )
        self.assertIn('selected_flow == "create"', add_source)
        self.assertIn("self._create_chatroom()", add_source)
        self.assertIn('selected_flow == "join"', add_source)
        self.assertIn("self._join_chatroom()", add_source)
        self.assertIn("ChatroomCreatedDialog", create_source)
        self.assertIn("make_chatroom_invite_code", create_source)
        self.assertIn("parse_chatroom_invite_code", join_source)
        self.assertIn("_store_new_chatroom", join_source)

    def test_new_chatroom_stores_invited_name_and_key(self) -> None:
        client = mock.Mock()
        client.root = mock.Mock()
        client.config_data = {"chatrooms": [], "room_profiles": {}}
        client.initial_history_pending_rooms = set()
        client._chatroom_definitions.return_value = [{"key": "global-key"}]
        client._room_profile.return_value = {"username": "User"}

        with mock.patch.object(SPRITELINK, "save_config"):
            room_id = SPRITELINK.EncryptedChatClient._store_new_chatroom(
                client,
                "Friends",
                "shared-key",
                error_title="Cannot join chatroom",
            )

        self.assertIsInstance(room_id, str)
        self.assertEqual(len(room_id), 32)
        self.assertEqual(
            client.config_data["chatrooms"],
            [{
                "id": room_id,
                "nickname": "Friends",
                "key": "shared-key",
            }],
        )
        client._request_subscription_refresh.assert_called_once_with()
        client._refresh_chatroom_list.assert_called_once_with()
        client._activate_chatroom.assert_called_once_with(room_id)


class PacketLimitTests(unittest.TestCase):
    PASSPHRASE = "correct horse battery staple with extra entropy"

    @staticmethod
    def _message(text: str) -> dict[str, object]:
        return {
            "v": SPRITELINK.APP_VERSION,
            "i": "message-id",
            "c": "client-id",
            "u": "User",
            "k": "#123456",
            "t": 1_700_000_000,
            "m": text,
        }

    def test_normal_packet_round_trip(self) -> None:
        message = self._message("hello")
        packet = SPRITELINK.make_opaque_packet(message, self.PASSPHRASE)
        self.assertEqual(
            SPRITELINK.open_opaque_packet(packet, self.PASSPHRASE),
            message,
        )

    def test_oversized_encoded_packet_is_rejected_before_decryption(self) -> None:
        packet = "A" * (SPRITELINK.MAX_ENCRYPTED_PACKET_CHARS + 1)
        with self.assertRaisesRegex(ValueError, "too large"):
            SPRITELINK.open_opaque_packet(packet, self.PASSPHRASE)

    def test_over_32_kib_decompressed_packet_is_rejected(self) -> None:
        message = self._message(
            "A" * (SPRITELINK.MAX_UNCOMPRESSED_MESSAGE_BYTES + 1)
        )
        packet = SPRITELINK.make_opaque_packet(message, self.PASSPHRASE)
        self.assertLessEqual(
            len(packet),
            SPRITELINK.MAX_ENCRYPTED_PACKET_CHARS,
        )
        with self.assertRaisesRegex(ValueError, "too large"):
            SPRITELINK.open_opaque_packet(packet, self.PASSPHRASE)


class PollDecryptLimitTests(unittest.TestCase):
    def test_records_are_newest_first(self) -> None:
        records = [
            {"id": "old", "time": 100},
            {"id": "new", "time": 300},
            {"id": "middle", "time": 200},
        ]
        ordered = SPRITELINK.newest_first_ntfy_records(records)
        self.assertEqual(
            [record["id"] for record in ordered],
            ["new", "middle", "old"],
        )

    def test_each_poll_prioritizes_new_unpolled_records(self) -> None:
        self.assertEqual(SPRITELINK.MAX_DECRYPT_ATTEMPTS_PER_POLL, 25)
        first_records = [
            {
                "event": "message",
                "id": f"record-{index}",
                "time": 1_000 + index,
                "message": f"packet-{index}",
            }
            for index in range(30)
        ]
        second_records = first_records + [
            {
                "event": "message",
                "id": f"record-{index}",
                "time": 1_000 + index,
                "message": f"packet-{index}",
            }
            for index in (30, 31)
        ]
        room = {"id": "room", "key": "room key"}
        response = mock.Mock()
        client = mock.Mock()
        client.config_data = {
            "server_url": "https://ntfy.sh",
            "room_state": {},
        }
        client.pending_ntfy_poll_batches = {}
        client.initial_history_pending_rooms = set()
        client.session.get.return_value = response
        client._find_chatroom.return_value = room
        client._current_poll_since.return_value = "48h"
        opened_packets: list[str] = []

        def open_packet(packet: str, _key: str) -> dict[str, object]:
            opened_packets.append(packet)
            return {"packet": packet}

        with (
            mock.patch.object(
                SPRITELINK,
                "derive_ntfy_topic",
                return_value="topic",
            ),
            mock.patch.object(
                SPRITELINK,
                "room_scope_id",
                return_value="scope",
            ),
            mock.patch.object(
                SPRITELINK,
                "parse_ntfy_ndjson",
                side_effect=[first_records, second_records],
            ),
            mock.patch.object(
                SPRITELINK,
                "open_opaque_packet",
                side_effect=open_packet,
            ),
            mock.patch.object(SPRITELINK, "save_config") as save_config,
        ):
            SPRITELINK.EncryptedChatClient._network_poll(
                client,
                room,
                is_active=False,
            )

            self.assertEqual(len(opened_packets), 25)
            self.assertEqual(opened_packets[0], "packet-29")
            self.assertEqual(opened_packets[-1], "packet-5")
            pending_batch = client.pending_ntfy_poll_batches["scope"]
            self.assertEqual(len(pending_batch["records"]), 5)
            self.assertEqual(len(pending_batch["polled_ids"]), 25)
            self.assertNotIn(
                "newest_ntfy_id",
                client.config_data["room_state"]["scope"],
            )
            save_config.assert_not_called()

            SPRITELINK.EncryptedChatClient._network_poll(
                client,
                room,
                is_active=False,
            )

        self.assertEqual(
            opened_packets[25:],
            [
                "packet-31",
                "packet-30",
                "packet-4",
                "packet-3",
                "packet-2",
                "packet-1",
                "packet-0",
            ],
        )
        self.assertEqual(client.session.get.call_count, 2)
        self.assertNotIn("scope", client.pending_ntfy_poll_batches)
        self.assertEqual(
            client.config_data["room_state"]["scope"]["newest_ntfy_id"],
            "record-31",
        )
        self.assertEqual(
            client._validate_decrypted_message.call_count,
            32,
        )
        self.assertEqual(
            client._validate_decrypted_message.call_args_list[25].kwargs[
                "ntfy_time"
            ],
            1_031,
        )
        save_config.assert_called_once()


class SignedClientIdentityTests(unittest.TestCase):
    ROOM_ONE = "first room encryption key"
    ROOM_TWO = "second room encryption key"

    def setUp(self) -> None:
        self.private_key = SPRITELINK.generate_identity_private_key()

    @staticmethod
    def _message(text: str = "hello") -> dict[str, object]:
        return {
            "v": SPRITELINK.APP_VERSION,
            "i": "0123456789abcdef0123456789abcdef",
            "u": "User",
            "k": "#123456",
            "f": SPRITELINK.DEFAULT_MESSAGE_FONT,
            "o": SPRITELINK.DEFAULT_MESSAGE_TEXT_COLOR,
            "t": 1_700_000_000,
            "m": text,
        }

    def _signed_message(
        self,
        room_key: str | None = None,
    ) -> dict[str, object]:
        return SPRITELINK.sign_message_identity(
            self._message(),
            room_key or self.ROOM_ONE,
            self.private_key,
        )

    def test_one_installation_keeps_the_same_id_across_rooms(self) -> None:
        first_room_message = self._signed_message(self.ROOM_ONE)
        second_room_message = self._signed_message(self.ROOM_TWO)

        self.assertEqual(
            first_room_message["c"],
            second_room_message["c"],
        )
        self.assertRegex(first_room_message["c"], r"^[0-9a-f]{64}$")
        SPRITELINK.verify_message_identity(
            first_room_message,
            self.ROOM_ONE,
        )
        SPRITELINK.verify_message_identity(
            second_room_message,
            self.ROOM_TWO,
        )

    def test_visible_user_id_is_eight_base32_characters(self) -> None:
        message = self._signed_message()
        visible_id = SPRITELINK.visible_user_id(str(message["c"]))
        self.assertEqual(len(visible_id), 8)
        self.assertRegex(visible_id, r"^[A-Z2-7]{8}$")
        self.assertEqual(
            SPRITELINK.visible_user_id("00" * 32),
            "AAAAAAAA",
        )
        self.assertEqual(
            SPRITELINK.visible_user_id("ff" * 32),
            "77777777",
        )

    def test_message_round_trip_requires_a_valid_signature(self) -> None:
        message = self._signed_message()
        packet = SPRITELINK.make_opaque_packet(message, self.ROOM_ONE)
        opened = SPRITELINK.open_opaque_packet(packet, self.ROOM_ONE)
        SPRITELINK.EncryptedChatClient._validate_decrypted_message(
            opened,
            self.ROOM_ONE,
        )
        self.assertEqual(opened, message)

    def test_ntfy_time_accepts_clock_skew_within_tolerance(self) -> None:
        message = self._signed_message()
        message_time = int(message["t"])
        tolerance = SPRITELINK.MESSAGE_CLOCK_TOLERANCE_SECONDS
        for delta in (-tolerance, 0, tolerance):
            with self.subTest(delta=delta):
                SPRITELINK.EncryptedChatClient._validate_decrypted_message(
                    message,
                    self.ROOM_ONE,
                    ntfy_time=message_time + delta,
                )

    def test_ntfy_time_rejects_records_outside_clock_tolerance(self) -> None:
        message = self._signed_message()
        message_time = int(message["t"])
        outside = SPRITELINK.MESSAGE_CLOCK_TOLERANCE_SECONDS + 1
        for delta in (-outside, outside):
            with self.subTest(delta=delta):
                with self.assertRaisesRegex(ValueError, "Ntfy time"):
                    SPRITELINK.EncryptedChatClient._validate_decrypted_message(
                        message,
                        self.ROOM_ONE,
                        ntfy_time=message_time + delta,
                    )

    def test_username_sanitizer_removes_spoofing_characters(self) -> None:
        unsafe = (
            " Al\nice\u200b\u202e\u2066X\u2069\ufe0f "
        )
        self.assertEqual(
            SPRITELINK.sanitize_username(unsafe),
            "AliceX",
        )
        normalized = SPRITELINK.normalize_identity_preset({
            "username": unsafe,
            "username_color": "#123456",
        })
        self.assertEqual(normalized["username"], "AliceX")

    def test_signed_username_with_invisible_controls_is_rejected(self) -> None:
        unsafe_message = self._message()
        unsafe_message["u"] = "Alice\u200b\u202eBob"
        signed = SPRITELINK.sign_message_identity(
            unsafe_message,
            self.ROOM_ONE,
            self.private_key,
        )
        with self.assertRaisesRegex(ValueError, "unsupported characters"):
            SPRITELINK.EncryptedChatClient._validate_decrypted_message(
                signed,
                self.ROOM_ONE,
            )

    def test_changing_signed_message_content_is_rejected(self) -> None:
        message = self._signed_message()
        forged = copy.deepcopy(message)
        forged["m"] = "forged text"

        with self.assertRaises(Exception):
            SPRITELINK.verify_message_identity(forged, self.ROOM_ONE)

    def test_copying_a_client_id_to_another_key_is_rejected(self) -> None:
        message = self._signed_message()
        other_private_key = SPRITELINK.generate_identity_private_key()
        forged = SPRITELINK.sign_message_identity(
            self._message("forged text"),
            self.ROOM_ONE,
            other_private_key,
        )
        forged["c"] = message["c"]

        with self.assertRaisesRegex(ValueError, "does not match"):
            SPRITELINK.verify_message_identity(forged, self.ROOM_ONE)

    def test_signature_is_bound_to_its_chatroom(self) -> None:
        message = self._signed_message(self.ROOM_ONE)

        with self.assertRaises(Exception):
            SPRITELINK.verify_message_identity(message, self.ROOM_TWO)

    def test_unsigned_messages_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            SPRITELINK.EncryptedChatClient._validate_decrypted_message(
                self._message(),
                self.ROOM_ONE,
            )

    def test_generated_private_keys_are_unique_and_well_formed(self) -> None:
        keys = {
            SPRITELINK.generate_identity_private_key()
            for _ in range(16)
        }
        self.assertEqual(len(keys), 16)
        for key in keys:
            normalized = SPRITELINK.normalize_identity_private_key(key)
            self.assertEqual(normalized, key)
            self.assertEqual(
                len(SPRITELINK.identity_public_key_bytes(key)),
                SPRITELINK.ED25519_PUBLIC_KEY_BYTES,
            )


class _FakeUpdateWidget:
    def __init__(self) -> None:
        self.text = ""
        self.enabled = True
        self.tooltip = ""
        self.focused = False
        self.stylesheet = ""

    def setText(self, text: str) -> None:
        self.text = text

    def setEnabled(self, enabled: bool) -> None:
        self.enabled = enabled

    def setToolTip(self, tooltip: str) -> None:
        self.tooltip = tooltip

    def setFocus(self) -> None:
        self.focused = True

    def isEnabled(self) -> bool:
        return self.enabled

    def setStyleSheet(self, stylesheet: str) -> None:
        self.stylesheet = stylesheet


class _FakeConfigOverlay:
    def isVisible(self) -> bool:
        return False


class _FakeUpdateClient:
    def __init__(self) -> None:
        self.available_update = object()
        self._update_check_in_progress = True
        self.latest_version_label = _FakeUpdateWidget()
        self.update_button = _FakeUpdateWidget()
        self.config_overlay = _FakeConfigOverlay()
        self.config_style_updates = 0
        self.windows_classic = False

    def _is_windows_classic_theme(self) -> bool:
        return self.windows_classic

    def _update_config_toggle_update_style(self) -> None:
        self.config_style_updates += 1

    def _update_update_button_style(self) -> None:
        SPRITELINK.EncryptedChatClient._update_update_button_style(self)

    def _show_no_available_update(self) -> None:
        SPRITELINK.EncryptedChatClient._show_no_available_update(self)


class NotificationButtonThemeTests(unittest.TestCase):
    def test_classic_attention_style_preserves_beveled_buttons(self) -> None:
        classic = SPRITELINK.notification_button_stylesheet(True)
        modern = SPRITELINK.notification_button_stylesheet(False)

        self.assertEqual(
            classic,
            SPRITELINK.WINDOWS_CLASSIC_NOTIFICATION_BUTTON_STYLESHEET,
        )
        self.assertEqual(
            modern,
            SPRITELINK.NOTIFICATION_BUTTON_STYLESHEET,
        )
        self.assertIn("background-color: #f8d8ad", classic)
        self.assertIn("border-top: 2px solid #ffffff", classic)
        self.assertIn("border-left: 2px solid #ffffff", classic)
        self.assertIn("border-right: 2px solid #000000", classic)
        self.assertIn("border-bottom: 2px solid #000000", classic)
        self.assertIn("border-radius: 0px", classic)
        self.assertIn(
            "QPushButton:pressed, QPushButton:checked",
            classic,
        )
        self.assertNotIn("border-radius: 3px", classic)
        self.assertNotIn("border: 1px solid #dca15d", classic)

    def test_expand_config_and_update_buttons_use_theme_style(self) -> None:
        for method in (
            SPRITELINK.EncryptedChatClient
            ._update_chatrooms_toggle_unread_style,
            SPRITELINK.EncryptedChatClient
            ._update_config_toggle_update_style,
            SPRITELINK.EncryptedChatClient._update_update_button_style,
        ):
            source = inspect.getsource(method)
            self.assertIn("notification_button_stylesheet(", source)
            self.assertIn("_is_windows_classic_theme()", source)


class UpdateConfigTests(unittest.TestCase):
    @staticmethod
    def _release(version: str) -> object:
        return SPRITELINK.ReleaseInfo(
            version=version,
            tag_name=f"v{version}",
            installer_url=f"https://example.com/SpriteLink-{version}.exe",
            checksum_url=f"https://example.com/SpriteLink-{version}.exe.sha256",
            page_url=f"https://example.com/releases/{version}",
            notes="Release notes",
        )

    def test_update_checks_are_forced_at_startup_and_every_5_minutes(
        self,
    ) -> None:
        self.assertEqual(
            SPRITELINK.UPDATE_CHECK_INTERVAL_MS,
            5 * 60 * 1000,
        )
        self.assertNotIn(
            "automatic_update_checks",
            SPRITELINK.default_config(),
        )
        init_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient.__init__
        )
        self.assertIn(
            "QTimer.singleShot(0, self._check_for_updates)",
            init_source,
        )
        self.assertIn("self.update_check_timer.start()", init_source)

    def test_version_controls_use_the_compact_left_aligned_format(
        self,
    ) -> None:
        ui_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._build_config_tab
        )
        self.assertIn("Current Version: {RUNNING_VERSION}", ui_source)
        self.assertIn('QLabel("Latest Version: ---")', ui_source)
        self.assertIn('QPushButton("Up-to-date")', ui_source)
        self.assertIn("Qt.AlignmentFlag.AlignLeft", ui_source)
        self.assertNotIn("Check for Updates", ui_source)
        self.assertNotIn("Check automatically", ui_source)

    def test_failed_check_uses_silent_unknown_up_to_date_state(self) -> None:
        client = _FakeUpdateClient()
        SPRITELINK.EncryptedChatClient._handle_update_check_result(
            client,
            {"error": True},
        )
        self.assertEqual(client.latest_version_label.text, "Latest Version: ---")
        self.assertEqual(client.latest_version_label.tooltip, "")
        self.assertEqual(client.update_button.text, "Up-to-date")
        self.assertFalse(client.update_button.enabled)
        self.assertEqual(client.update_button.tooltip, "")
        self.assertIsNone(client.available_update)
        self.assertFalse(client._update_check_in_progress)

    def test_available_update_enables_update_button(self) -> None:
        client = _FakeUpdateClient()
        release = self._release("2.0.0")
        with mock.patch.object(
            SPRITELINK,
            "release_is_newer",
            return_value=True,
        ):
            SPRITELINK.EncryptedChatClient._handle_update_check_result(
                client,
                {"release": release},
            )
        self.assertEqual(client.latest_version_label.text, "Latest Version: 2.0.0")
        self.assertEqual(client.update_button.text, "Update")
        self.assertTrue(client.update_button.enabled)
        self.assertEqual(
            client.update_button.stylesheet,
            SPRITELINK.NOTIFICATION_BUTTON_STYLESHEET,
        )
        self.assertIs(client.available_update, release)

    def test_available_update_uses_classic_attention_style(self) -> None:
        client = _FakeUpdateClient()
        client.windows_classic = True
        release = self._release("2.0.0")
        with mock.patch.object(
            SPRITELINK,
            "release_is_newer",
            return_value=True,
        ):
            SPRITELINK.EncryptedChatClient._handle_update_check_result(
                client,
                {"release": release},
            )
        self.assertEqual(
            client.update_button.stylesheet,
            SPRITELINK.WINDOWS_CLASSIC_NOTIFICATION_BUTTON_STYLESHEET,
        )

    def test_current_release_disables_up_to_date_button(self) -> None:
        client = _FakeUpdateClient()
        release = self._release("1.0.0")
        with mock.patch.object(
            SPRITELINK,
            "release_is_newer",
            return_value=False,
        ):
            SPRITELINK.EncryptedChatClient._handle_update_check_result(
                client,
                {"release": release},
            )
        self.assertEqual(client.latest_version_label.text, "Latest Version: 1.0.0")
        self.assertEqual(client.update_button.text, "Up-to-date")
        self.assertFalse(client.update_button.enabled)
        self.assertEqual(client.update_button.stylesheet, "")
        self.assertIsNone(client.available_update)


class Version120ReleaseTests(unittest.TestCase):
    def test_release_defaults_and_config_control_order(self) -> None:
        config = SPRITELINK.default_config()
        self.assertEqual(SPRITELINK.DEFAULT_MESSAGE_FONT, "Arial")
        self.assertTrue(config["text_shadows"])
        self.assertFalse(config["desktop_notifications"])
        self.assertEqual(SPRITELINK.DEFAULT_THEME, "Classic")
        self.assertEqual(SPRITELINK.THEMES, ("Classic", "Modern"))

        source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._build_config_tab
        )
        self.assertLess(
            source.index('QLabel("Themes")'),
            source.index('QCheckBox("Text Shadows")'),
        )
        self.assertLess(
            source.index('QLabel("Message Sound")'),
            source.index('"Desktop Notifications"'),
        )
        self.assertLess(
            source.index('"Desktop Notifications"'),
            source.index('QLabel("Chatroom History")'),
        )

    def test_legacy_theme_names_keep_their_equivalent_theme(self) -> None:
        for legacy_name, expected_name in (
            ("Windows Classic", "Classic"),
            ("Modern (Light)", "Modern"),
        ):
            with self.subTest(theme=legacy_name):
                existing_config = SPRITELINK.default_config()
                existing_config["theme"] = legacy_name
                config_path = mock.Mock()
                config_path.exists.return_value = True
                config_path.read_bytes.return_value = b"settings"
                with (
                    mock.patch.object(
                        SPRITELINK,
                        "CONFIG_PATH",
                        config_path,
                    ),
                    mock.patch.object(
                        SPRITELINK,
                        "_load_dpapi_config",
                        return_value=existing_config,
                    ),
                ):
                    loaded = SPRITELINK.load_config()
                self.assertEqual(loaded["theme"], expected_name)

    def test_font_dropdown_removes_only_its_item_focus_outline(self) -> None:
        build_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._build_chat_tab
        )
        self.assertIn(
            "NoFocusRectItemDelegate(self.message_font_combo)",
            build_source,
        )
        delegate_source = inspect.getsource(
            SPRITELINK.NoFocusRectItemDelegate.paint
        )
        self.assertIn("State_HasFocus", delegate_source)
        self.assertIn("super().paint", delegate_source)

    def test_volume_slider_can_compress_in_small_config_windows(self) -> None:
        source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._build_config_tab
        )
        self.assertIn(
            "message_sound_volume_slider.setMinimumWidth(40)",
            source,
        )
        self.assertIn("QSizePolicy.Policy.Expanding", source)

    def test_windows_classic_uses_tahoma_for_builtin_ui(self) -> None:
        family_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._ui_font_family
        )
        strategy_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._apply_application_font_strategy
        )
        self.assertIn('return "Tahoma"', family_source)
        self.assertIn(
            "application_font.setFamily(self._ui_font_family())",
            strategy_source,
        )
        self.assertIn(
            "widget_font.setFamily(self._ui_font_family())",
            strategy_source,
        )
        self.assertEqual(SPRITELINK.DEFAULT_MESSAGE_FONT, "Arial")

    def test_text_shadows_are_one_pixel_at_15_percent(self) -> None:
        source = inspect.getsource(
            SPRITELINK.TextShadowProxyStyle.drawItemText
        )
        self.assertIn("setAlphaF(0.15)", source)
        self.assertIn("rect.translated(1, 1)", source)
        chat_shadow_source = inspect.getsource(
            SPRITELINK.MessageLogBrowser._paint_text_shadows
        )
        self.assertNotIn("setAlphaF(0.15)", chat_shadow_source)
        self.assertIn("painter.translate(1, 1)", chat_shadow_source)
        self.assertIn("painter.setOpacity(0.15)", chat_shadow_source)
        self.assertLess(
            chat_shadow_source.index("painter.setOpacity(0.15)"),
            chat_shadow_source.index(
                "layout.draw(painter, layout_origin, shadow_ranges)"
            ),
        )
        self.assertIn("layout.draw", chat_shadow_source)
        self.assertIn(
            "-self.verticalScrollBar().value()",
            chat_shadow_source,
        )
        self.assertNotIn("cursorRect", chat_shadow_source)
        image_clip_source = inspect.getsource(
            SPRITELINK.MessageLogBrowser._text_shadow_clip_region
        )
        self.assertIn('"\\ufffc"', image_clip_source)
        self.assertIn("isImageFormat", image_clip_source)
        self.assertIn("clip_region.subtracted", image_clip_source)
        self.assertIn("_text_shadow_clip_region", chat_shadow_source)
        paint_source = inspect.getsource(
            SPRITELINK.MessageLogBrowser.paintEvent
        )
        self.assertIn("_paint_text_shadows(event)", paint_source)
        apply_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._apply_theme
        )
        self.assertIn("spritelinkTextShadows", apply_source)
        toggle_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._on_text_shadows_toggled
        )
        self.assertLess(
            toggle_source.index("_apply_theme()"),
            toggle_source.index("_apply_application_font_strategy()"),
        )

    def test_taskbar_and_tray_icons_share_unread_state(self) -> None:
        mark_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._mark_tray_notification
        )
        clear_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._clear_tray_notification
        )
        for source in (mark_source, clear_source):
            self.assertIn("root.setWindowIcon", source)
            self.assertIn("app.setWindowIcon", source)

    def test_silent_windows_toast_payload_and_message_format(self) -> None:
        command = SPRITELINK.silent_windows_notification_command(
            'Room "One"',
            "User: <hello> & goodbye",
            "room-tag",
            "global",
        )
        self.assertEqual(command[0], "powershell.exe")
        script = base64.b64decode(command[-1]).decode("utf-16-le")
        self.assertIn('<audio silent="true"/>', script)
        self.assertIn(SPRITELINK.WINDOWS_APP_USER_MODEL_ID, script)
        self.assertIn("CreateTextNode($title)", script)
        self.assertIn("CreateTextNode($body)", script)
        self.assertIn("$toast.Tag = $tag", script)
        self.assertIn("$toast.Group = 'SpriteLink.Chatrooms'", script)
        self.assertIn('activationType="protocol"', script)
        self.assertIn("SetAttribute('launch', $launchUri)", script)
        self.assertNotIn("<text>{0}</text>", script)
        self.assertEqual(
            SPRITELINK.notification_tag_for_chatroom("room-one"),
            SPRITELINK.notification_tag_for_chatroom("room-one"),
        )
        self.assertNotEqual(
            SPRITELINK.notification_tag_for_chatroom("room-one"),
            SPRITELINK.notification_tag_for_chatroom("room-two"),
        )

        client = mock.Mock()
        client.desktop_notifications_var.get.return_value = True
        client.window_focused_event.is_set.return_value = False
        client._find_chatroom.return_value = {"nickname": "Room One"}
        client.pending_desktop_notifications = {}
        client.desktop_notification_timer = mock.Mock()
        SPRITELINK.EncryptedChatClient._show_desktop_notification(
            client,
            "room-one",
            {"u": "User", "m": "<b>Newest</b>", "t": 2, "i": "b"},
            sort_key=(2, 2, "b"),
        )
        SPRITELINK.EncryptedChatClient._show_desktop_notification(
            client,
            "room-one",
            {"u": "User", "m": "Older", "t": 1, "i": "a"},
            sort_key=(1, 1, "a"),
        )
        with mock.patch.object(
            SPRITELINK,
            "show_silent_windows_notification",
        ) as show_notification:
            SPRITELINK.EncryptedChatClient._flush_desktop_notifications(
                client
            )
        show_notification.assert_called_once_with(
            "Room One",
            "User: Newest",
            SPRITELINK.notification_tag_for_chatroom("room-one"),
            "room-one",
        )
        self.assertEqual(client.pending_desktop_notifications, {})

    def test_reading_and_clicking_notifications_are_channel_specific(
        self,
    ) -> None:
        room_id = "0123456789abcdef0123456789abcdef"
        uri = SPRITELINK.notification_uri_for_chatroom(room_id)
        self.assertEqual(
            SPRITELINK.chatroom_id_from_notification_uri(uri),
            room_id,
        )
        self.assertEqual(
            SPRITELINK.notification_uri_from_arguments([
                "SpriteLink.exe",
                "--notification-uri",
                uri,
            ]),
            uri,
        )
        self.assertIsNone(
            SPRITELINK.chatroom_id_from_notification_uri(
                "spritelink://chatroom/not-a-room"
            )
        )

        clear_command = SPRITELINK.clear_windows_notification_command(
            "room-tag"
        )
        clear_script = base64.b64decode(clear_command[-1]).decode(
            "utf-16-le"
        )
        self.assertIn("::History.Remove($tag", clear_script)
        self.assertIn(SPRITELINK.WINDOWS_NOTIFICATION_GROUP, clear_script)
        self.assertIn(SPRITELINK.WINDOWS_APP_USER_MODEL_ID, clear_script)

        client = mock.Mock()
        client.pending_desktop_notifications = {room_id: object()}
        with mock.patch.object(
            SPRITELINK,
            "clear_windows_notification",
        ) as clear_notification:
            SPRITELINK.EncryptedChatClient._clear_desktop_notification(
                client,
                room_id,
            )
        self.assertEqual(client.pending_desktop_notifications, {})
        client.desktop_notification_timer.stop.assert_called_once_with()
        clear_notification.assert_called_once_with(
            SPRITELINK.notification_tag_for_chatroom(room_id)
        )

        open_client = mock.Mock()
        open_client._find_chatroom.return_value = {"id": room_id}
        SPRITELINK.EncryptedChatClient._open_notification_chatroom(
            open_client,
            room_id,
        )
        open_client._restore_from_tray.assert_called_once_with()
        open_client._activate_chatroom.assert_called_once_with(room_id)

        read_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._mark_chatroom_read
        )
        self.assertIn("_clear_desktop_notification(room_id)", read_source)
        main_source = inspect.getsource(SPRITELINK.main)
        self.assertIn("write_notification_activation", main_source)
        self.assertIn("_open_notification_chatroom", main_source)

    def test_notification_sounds_have_a_two_second_cooldown(self) -> None:
        client = mock.Mock()
        client.last_notification_sound_at = float("-inf")
        with mock.patch.object(
            SPRITELINK.time,
            "monotonic",
            side_effect=(10.0, 11.9, 12.0),
        ):
            for _ in range(3):
                SPRITELINK.EncryptedChatClient._play_notification_sound(
                    client
                )
        self.assertEqual(client._play_message_sound.call_count, 2)
        self.assertEqual(SPRITELINK.MESSAGE_SOUND_COOLDOWN_SECONDS, 2.0)

    def test_desktop_notifications_require_an_unfocused_window(self) -> None:
        client = mock.Mock()
        client.desktop_notifications_var.get.return_value = True
        client.window_focused_event.is_set.return_value = True
        with mock.patch.object(
            SPRITELINK,
            "show_silent_windows_notification",
        ) as show_notification:
            SPRITELINK.EncryptedChatClient._show_desktop_notification(
                client,
                "room-one",
                {"u": "User", "m": "Hello"},
            )
        show_notification.assert_not_called()
        client._find_chatroom.assert_not_called()

    def test_server_history_window_and_tooltip_status_match(self) -> None:
        retention = 12 * 60 * 60
        self.assertEqual(
            SPRITELINK.SERVER_HISTORY_RETENTION_SECONDS,
            retention,
        )
        self.assertEqual(
            SPRITELINK.message_storage_status(
                1_000_000 - retention,
                now=1_000_000,
            ),
            "On server (1h)",
        )
        self.assertEqual(
            SPRITELINK.message_storage_status(
                1_000_000,
                now=1_000_000,
            ),
            "On server (12h)",
        )
        self.assertEqual(
            SPRITELINK.message_storage_status(
                1_000_000 - 3601,
                now=1_000_000,
            ),
            "On server (11h)",
        )
        self.assertEqual(
            SPRITELINK.message_storage_status(
                1_000_000 - retention - 1,
                now=1_000_000,
            ),
            "Expired",
        )
        poll_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._current_poll_since
        )
        self.assertIn("SERVER_HISTORY_RETENTION_SECONDS", poll_source)
        config_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._build_config_tab
        )
        self.assertIn("requests up to 12 hours", config_source)

        tooltip_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._tooltip_for_message_item
        )
        self.assertLess(
            tooltip_source.index("User ID:"),
            tooltip_source.index("{hover_timestamp}</td>"),
        )
        self.assertEqual(tooltip_source.count('height="3"'), 4)
        self.assertIn('bgcolor="#888888" height="1"', tooltip_source)
        self.assertIn('width="1" height="1"', tooltip_source)
        self.assertNotIn("<br>", tooltip_source)
        self.assertIn("storage_status", tooltip_source)

        show_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._show_pending_chat_tooltip
        )
        self.assertIn("CHAT_TOOLTIP_DISPLAY_TIME_MS", show_source)
        self.assertIn("self.chat_display.viewport().rect()", show_source)
        self.assertEqual(
            SPRITELINK.CHAT_TOOLTIP_DISPLAY_TIME_MS,
            2_147_483_647,
        )

    def test_each_message_resets_its_non_breakable_block_format(self) -> None:
        source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._insert_message_item
        )
        self.assertIn("message_block_format = QTextBlockFormat()", source)
        self.assertIn(
            "message_block_format.setNonBreakableLines(is_collapsed)",
            source,
        )
        self.assertNotIn(
            "collapsed_block_format = cursor.blockFormat()",
            source,
        )

    def test_user_message_lines_are_fixed_at_24_pixels(self) -> None:
        self.assertEqual(SPRITELINK.MESSAGE_LINE_HEIGHT_PX, 24)
        source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._insert_message_item
        )
        self.assertIn("block_format.setLineHeight(", source)
        self.assertIn(
            "float(MESSAGE_LINE_HEIGHT_PX)",
            source,
        )
        self.assertIn(
            "int(QTextBlockFormat.LineHeightTypes.FixedHeight.value)",
            source,
        )
        self.assertIn("embedded_media_block_numbers", source)

        icon_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._insert_profile_icon
        )
        self.assertEqual(SPRITELINK.PROFILE_ICON_VERTICAL_OFFSET_PX, 2)
        self.assertIn(
            "else PROFILE_ICON_VERTICAL_OFFSET_PX",
            icon_source,
        )
        self.assertNotIn(
            "VerticalAlignment.AlignMiddle",
            icon_source,
        )
        self.assertIn(
            "VerticalAlignment.AlignTop",
            icon_source,
        )
        self.assertIn(
            "TOP_ALIGNED_PROFILE_ICON_PADDING",
            icon_source,
        )
        self.assertIn(
            "- PROFILE_ICON_VERTICAL_OFFSET_PX",
            icon_source,
        )

        separator_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._insert_log_separator
        )
        self.assertNotIn("setLineHeight", separator_source)

    def test_message_text_trims_after_its_last_visible_character(self) -> None:
        self.assertEqual(
            SPRITELINK.trim_message_text("Hello   \n\n"),
            "Hello",
        )
        self.assertEqual(
            SPRITELINK.trim_message_text(
                "<b>Hello <i>there   \n</i></b>"
            ),
            "<b>Hello <i>there</i></b>",
        )
        self.assertEqual(
            SPRITELINK.trim_message_text("  leading space"),
            "  leading space",
        )

        send_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._send_current_message
        )
        self.assertIn("text = trim_message_text", send_source)
        receive_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._validate_decrypted_message
        )
        self.assertLess(
            receive_source.index("verify_message_identity"),
            receive_source.index(
                'message["m"] = trim_message_text(message["m"])'
            ),
        )

    def test_windows_package_defaults_to_version_120(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        workflow = (
            project_root / ".github/workflows/windows-package.yml"
        ).read_text(encoding="utf-8")
        build_script = (
            project_root / "packaging/windows/build.ps1"
        ).read_text(encoding="utf-8")
        installer = (
            project_root / "packaging/windows/SpriteLink.iss"
        ).read_text(encoding="utf-8")
        self.assertIn('default: "1.2.0"', workflow)
        self.assertIn('$Version = "1.2.0"', build_script)
        self.assertIn('#define AppVersion "1.2.0"', installer)


if __name__ == "__main__":
    unittest.main()
