from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
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
        self.assertIn("active_image_urls", insert_source)
        self.assertIn("viewport_embedded_image_urls", insert_source)
        self.assertNotIn("_schedule_image_preview_fetch", insert_source)

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
            SPRITELINK.EncryptedChatClient._render_message_log
        )
        self.assertIn("viewport_media_timer.start", render_source)

    def test_far_animations_are_stopped_and_removed(self) -> None:
        viewport_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._update_viewport_media
        )
        self.assertIn(
            "cached_desired_urls != currently_rendered_urls",
            viewport_source,
        )
        clear_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._clear_visible_room
        )
        self.assertIn("viewport_embedded_image_urls.clear()", clear_source)
        self.assertIn("controller.stop()", clear_source)


class BehaviorSettingsTests(unittest.TestCase):
    def test_behavior_defaults_and_history_options(self) -> None:
        config = SPRITELINK.default_config()
        self.assertEqual(
            SPRITELINK.MESSAGE_HISTORY_OPTIONS,
            (100, 500, 1000, 10000),
        )
        self.assertEqual(config["message_history_limit"], 1000)
        self.assertFalse(config["minimize_to_tray"])

    def test_history_pruning_is_per_room_and_can_skip_active_room(self) -> None:
        histories = {
            "active": list(range(1200)),
            "background": list(range(700)),
        }
        changed = SPRITELINK.prune_local_history_map(
            histories,
            500,
            excluded_scope_id="active",
        )
        self.assertTrue(changed)
        self.assertEqual(len(histories["active"]), 1200)
        self.assertEqual(histories["background"], list(range(200, 700)))

        SPRITELINK.prune_local_history_map(histories, 500)
        self.assertEqual(histories["active"], list(range(700, 1200)))

    def test_history_limit_is_normalized_to_presets(self) -> None:
        for value in SPRITELINK.MESSAGE_HISTORY_OPTIONS:
            with self.subTest(value=value):
                self.assertEqual(
                    SPRITELINK.normalize_message_history_limit(str(value)),
                    value,
                )
        self.assertEqual(
            SPRITELINK.normalize_message_history_limit(999),
            SPRITELINK.DEFAULT_MESSAGE_HISTORY_LIMIT,
        )

    def test_behavior_controls_are_present_in_config(self) -> None:
        source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._build_config_tab
        )
        self.assertIn('self._heading("Behavior")', source)
        self.assertNotIn("Notifications and rendering", source)
        self.assertIn('QLabel("Message History")', source)
        self.assertIn('QCheckBox("Minimize to Tray")', source)
        self.assertIn("MESSAGE_HISTORY_OPTIONS", source)

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
        self.assertIn("excluded_scope_id=active_scope_id", prune_source)
        background_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._accept_background_messages
        )
        self.assertNotIn("history[-1000:]", background_source)

    def test_history_persistence_uses_selected_limit(self) -> None:
        load_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._load_saved_history_for_current_room
        )
        persist_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._persist_local_history
        )
        add_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._add_message_to_log
        )
        self.assertIn("_message_history_limit()", load_source)
        self.assertIn("_message_history_limit()", persist_source)
        self.assertIn("_message_history_limit()", add_source)
        self.assertNotIn("1000", persist_source)

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
        self.assertIn("_clear_tray_notification", restore_source)


class RuntimeOptimizationTests(unittest.TestCase):
    def test_chatroom_unread_rows_are_never_top_level_windows(self) -> None:
        row_source = inspect.getsource(SPRITELINK.ChatroomListRow.__init__)
        refresh_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._refresh_chatroom_list
        )
        self.assertIn("parent: QWidget", row_source)
        self.assertIn("super().__init__(parent)", row_source)
        self.assertIn("self.chatrooms_list,", refresh_source)

    def test_visible_window_defers_tray_icon_replacement(self) -> None:
        mark_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._mark_tray_notification
        )
        sync_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._sync_tray_notification_icon
        )
        event_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient.eventFilter
        )
        self.assertIn("_tray_notification_pending = True", mark_source)
        self.assertIn("self.root.isVisible()", sync_source)
        self.assertIn("not self.root.isMinimized()", sync_source)
        self.assertIn("self._tray_ui_suspended", sync_source)
        self.assertIn("_tray_icon_is_notification = True", sync_source)
        self.assertLess(
            mark_source.index("self.root.isVisible()"),
            mark_source.index("self.tray_icon.isVisible()"),
        )
        self.assertLess(
            sync_source.index("self.root.isVisible()"),
            sync_source.index("self.tray_icon.isVisible()"),
        )
        self.assertIn("_sync_tray_notification_icon", event_source)

    def test_network_worker_waits_until_real_work_is_due(self) -> None:
        self.assertEqual(
            SPRITELINK.polling_interval_seconds(
                window_focused=True,
                tray_suspended=False,
            ),
            6.0,
        )
        self.assertEqual(
            SPRITELINK.polling_interval_seconds(
                window_focused=False,
                tray_suspended=False,
            ),
            10.0,
        )
        self.assertEqual(
            SPRITELINK.polling_interval_seconds(
                window_focused=False,
                tray_suspended=True,
            ),
            20.0,
        )
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
            SPRITELINK.next_poll_room_id(
                room_ids=["active", "older", "urgent"],
                active_room_id="active",
                urgent_room_ids={"urgent"},
                last_poll_times={
                    "active": 90.0,
                    "older": 10.0,
                    "urgent": 99.0,
                },
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
            ),
            "older",
        )

        loop_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._network_loop
        )
        self.assertIn("network_idle_wait_seconds", loop_source)
        self.assertIn("network_wakeup_event.wait", loop_source)
        self.assertIn("last_global_poll_at", loop_source)
        self.assertIn("next_poll_room_id", loop_source)
        self.assertIn("subscription_room_queue", loop_source)
        self.assertNotIn("last_background_poll_at", loop_source)
        self.assertNotIn("wait(0.08)", loop_source)

    def test_network_wakes_immediately_for_send_config_and_focus(self) -> None:
        refresh_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._request_network_refresh
        )
        send_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._send_current_message
        )
        event_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient.eventFilter
        )
        self.assertIn("network_wakeup_event.set()", refresh_source)
        self.assertIn("network_wakeup_event.set()", send_source)
        self.assertIn("network_wakeup_event.set()", event_source)

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

    def test_subscription_snapshot_groups_all_room_topics(self) -> None:
        client = mock.Mock()
        client.config_data = {"server_url": "https://ntfy.sh"}
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
                "topic-a": ("room-a", "room-c"),
                "topic-b": ("room-b",),
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
        self.assertIn('params={"since": "latest"}', stream_source)
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
            SPRITELINK.EncryptedChatClient._add_chatroom,
            SPRITELINK.EncryptedChatClient._edit_chatroom,
            SPRITELINK.EncryptedChatClient._remove_chatroom,
            SPRITELINK.EncryptedChatClient._save_and_reconnect,
        ):
            self.assertIn(
                "_request_subscription_refresh",
                inspect.getsource(method),
            )

    def test_tray_state_selects_the_thirty_second_interval(self) -> None:
        suspend_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._suspend_for_tray
        )
        resume_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._resume_from_tray
        )
        loop_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._network_loop
        )
        self.assertIn("tray_mode_event.set()", suspend_source)
        self.assertIn("tray_mode_event.clear()", resume_source)
        self.assertIn("tray_mode_event.is_set()", loop_source)



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

    def test_notifications_work_for_normal_minimize(self) -> None:
        mark_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._mark_tray_notification
        )
        event_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient.eventFilter
        )
        self.assertIn("window_focused_event.is_set()", mark_source)
        self.assertNotIn("_minimized_to_tray", mark_source)
        self.assertIn("_clear_tray_notification", event_source)
        self.assertIn("WindowStateChange", event_source)

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
        close_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._on_close
        )
        self.assertIn("QApplication.instance()", quit_source)
        self.assertIn("app.quit()", quit_source)
        self.assertIn("network_thread.join", close_source)
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
        for source in (open_source, context_source):
            self.assertIn('scheme().casefold() == "https"', source)
            self.assertNotIn('"http"', source)


class RoomKeyTests(unittest.TestCase):
    def test_generated_room_keys_are_exactly_32_url_safe_characters(self) -> None:
        keys = {SPRITELINK.generate_chatroom_key() for _ in range(16)}
        self.assertEqual(len(keys), 16)
        for key in keys:
            self.assertEqual(len(key), 32)
            self.assertRegex(key, r"^[A-Za-z0-9_-]{32}$")


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

    def test_update_checks_are_forced_at_startup_and_every_30_minutes(
        self,
    ) -> None:
        self.assertEqual(
            SPRITELINK.UPDATE_CHECK_INTERVAL_MS,
            30 * 60 * 1000,
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


if __name__ == "__main__":
    unittest.main()
