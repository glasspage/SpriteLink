from contextlib import ExitStack
import os
import subprocess
import sys
import threading
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyleFactory
from test_security import SPRITELINK as S


class DeferredStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.palette = QPalette(self.app.palette())
        self.font = QFont(self.app.font())
        self.sheet = self.app.styleSheet()
        self.properties = {name: self.app.property(name) for name in (
            "spritelinkGlassy", "spritelinkGlassyDark", "spritelinkModern", "spritelinkWindowsClassic",
            "spritelinkTextShadows", "spritelinkThemeHue", "spritelinkClassicColor",
        )}
        self.stack = ExitStack()
        self.config = S.default_config()
        self.config["theme"] = "Glassy"
        self.stack.enter_context(mock.patch.object(S, "load_config", return_value=self.config))
        self.stack.enter_context(mock.patch.object(S, "save_config"))
        self.network = self.stack.enter_context(mock.patch.object(S.EncryptedChatClient, "_start_network_thread"))
        self.updates = self.stack.enter_context(mock.patch.object(S.EncryptedChatClient, "_check_for_updates"))
        self.history = self.stack.enter_context(mock.patch.object(S.EncryptedChatClient, "_load_saved_history_for_current_room"))
        self.root = S.MainWindow()
        with mock.patch.object(S.requests, "Session") as session:
            self.client = S.EncryptedChatClient(self.root, defer_startup=True)
            session.assert_not_called()

    def tearDown(self):
        self.client.stop_event.set()
        for timer in self.root.findChildren(QTimer):
            timer.stop()
        self.client.history_load_executor.shutdown(wait=False, cancel_futures=True)
        self.client.image_fetch_executor.shutdown(wait=False, cancel_futures=True)
        for session in (self.client.session, self.client.subscription_session):
            if session is not None:
                session.close()
        self.root.close_callback = None
        self.root.close()
        self.root.deleteLater()
        self.app.processEvents()
        self.stack.close()
        for name, value in self.properties.items():
            self.app.setProperty(name, value)
        self.app.setStyle(QStyleFactory.create("Fusion"))
        self.app.setPalette(self.palette)
        self.app.setStyleSheet(self.sheet)
        self.app.setFont(self.font)

    def test_first_window_has_main_ui_without_hidden_panels_or_services(self):
        self.root.show()
        self.app.processEvents()
        self.assertTrue(self.root.isVisible())
        self.assertTrue(self.client.chat_display.isVisible())
        self.assertEqual(self.client._prepared_popups, set())
        self.assertFalse(self.client._tray_notification_icon_ready)
        self.assertIsNone(self.client.session)
        self.history.assert_not_called()
        self.network.assert_not_called()
        self.updates.assert_not_called()
        self.client._clear_visible_room()
        self.client._suspend_for_tray()

    def test_no_presence_key_is_derived_before_counts_are_available(self):
        self.client.recently_online_counts.clear()
        self.client._last_recently_online_counts.clear()
        with mock.patch.object(S, "derive_ntfy_topic") as derive:
            self.client._update_recently_online_label()
        derive.assert_not_called()
        self.assertTrue(self.client.recently_online_label.isHidden())

    def test_sidebar_rows_are_created_on_first_open(self):
        self.assertEqual(self.client.chatrooms_list.count(), 0)
        self.root.show()
        self.client.chatrooms_toggle.setChecked(True)
        self.assertEqual(self.client.chatrooms_list.count(), len(self.client._chatroom_definitions()))
        self.assertEqual(
            self.client.chatrooms_list.currentItem().data(S.Qt.ItemDataRole.UserRole),
            self.client.active_chatroom_id,
        )
        self.client.chatrooms_toggle.setChecked(False)
        self.client.chatrooms_toggle.setChecked(True)
        self.assertEqual(self.client.chatrooms_list.count(), len(self.client._chatroom_definitions()))

    def test_slow_history_scan_allows_paint_and_delays_chat_start(self):
        entered, release = threading.Event(), threading.Event()
        def scan():
            entered.set()
            release.wait(5)
            return []
        with mock.patch.object(S, "_pending_history_upgrades", side_effect=scan), \
             mock.patch.object(self.client, "_open_notification_chatroom") as open_room:
            self.root.show()
            S.begin_history_startup(self.client, "room-1")
            self.assertTrue(entered.wait(2))
            ticks = []
            QTimer.singleShot(0, lambda: ticks.append(self.root.isVisible()))
            QTest.qWait(50)
            self.assertEqual(ticks, [True])
            self.assertFalse(self.root.isEnabled())
            self.assertFalse(self.client._history_startup_ready)
            self.assertFalse(self.client.notification_activation_timer.isActive())
            self.history.assert_not_called()
            self.network.assert_not_called()
            open_room.assert_not_called()
            release.set()
            for _ in range(100):
                QTest.qWait(10)
                if self.history.called and open_room.called:
                    break
            self.assertTrue(self.root.isEnabled())
            self.assertTrue(self.client._history_startup_ready)
            self.history.assert_called_once()
            open_room.assert_called_once_with("room-1")

    def test_close_during_history_scan_cannot_start_or_modify_history(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        def scan():
            entered.set()
            release.wait(5)
            finished.set()
            return [S.Path("legacy.bin")]
        with mock.patch.object(S, "_pending_history_upgrades", side_effect=scan), \
             mock.patch.object(S, "show_history_upgrade") as upgrade, \
             mock.patch.object(self.client, "_apply_chatroom_history_limit") as prune:
            self.root.show()
            S.begin_history_startup(self.client, None)
            self.assertTrue(entered.wait(2))
            self.client._force_quit = True
            self.root.close()
            release.set()
            self.assertTrue(finished.wait(2))
            QTest.qWait(100)
            self.assertTrue(self.client._closing)
            self.assertFalse(self.client._history_startup_ready)
            upgrade.assert_not_called()
            prune.assert_not_called()
            self.history.assert_not_called()
            self.network.assert_not_called()

    def test_history_upgrade_finishes_before_startup_and_raises_old_limit(self):
        legacy = S.Path("legacy.bin")
        self.client.chatroom_history_limit_var.set(1000)
        with mock.patch.object(S, "_pending_history_upgrades", return_value=[legacy]), \
             mock.patch.object(S, "show_history_upgrade", return_value=True) as upgrade:
            self.root.show()
            S.begin_history_startup(self.client, None)
            for _ in range(100):
                QTest.qWait(10)
                if self.history.called:
                    break
            upgrade.assert_called_once_with(self.client, [legacy])
            self.assertEqual(self.client._chatroom_history_limit(), S.DEFAULT_CHATROOM_HISTORY_LIMIT)
            self.assertTrue(self.client._history_startup_ready)
            self.history.assert_called_once()

    def test_failed_scan_closes_without_loading_or_pruning_history(self):
        with mock.patch.object(S, "_pending_history_upgrades", side_effect=OSError("unavailable")), \
             mock.patch.object(S.messagebox, "showerror") as error, \
             mock.patch.object(self.client, "_apply_chatroom_history_limit") as prune:
            self.root.show()
            S.begin_history_startup(self.client, None)
            for _ in range(100):
                QTest.qWait(10)
                if self.client._closing:
                    break
            error.assert_called_once()
            self.assertTrue(self.client._closing)
            self.history.assert_not_called()
            self.network.assert_not_called()
            prune.assert_not_called()

    def test_early_config_click_and_other_popups_prepare_once_in_current_theme(self):
        self.root.show()
        self.client._on_theme_changed("Classic")
        self.client._on_ui_size_changed("Tiny")
        self.client._on_config_toggled(True)
        config_overlay = self.client.config_overlay
        self.assertTrue(config_overlay.isVisible())
        self.assertEqual(self.client.theme_combo.currentText(), "Classic")
        self.assertEqual(self.client.server_url_entry.isReadOnly(), True)
        self.client._dismiss_config_popup()
        self.client._show_message_limit_popup()
        self.assertTrue(self.client.message_limit_overlay.isVisible())
        self.client._hide_message_limit_popup()
        self.client._show_link_warning_popup("https://example.org/", S.analyze_link_url("https://example.org/"))
        self.assertTrue(self.client.link_warning_overlay.isVisible())
        self.client._hide_link_warning_popup()
        self.client._ensure_popup("image_preview")
        self.client._hide_image_preview_popup()
        while self.client._startup_popups:
            self.client._prepare_next_startup_popup()
        self.client._prepare_next_startup_popup()
        self.assertIs(self.client.config_overlay, config_overlay)
        self.assertEqual(len(self.client._prepared_popups), 4)
        self.assertTrue(self.client._tray_notification_icon_ready)
        self.updates.assert_called_once()

    def test_warmup_is_staged_on_ui_thread_and_worker_does_not_block_paint(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        worker_threads = []
        popup_threads = []
        original = self.client._ensure_popup
        def popup(name):
            popup_threads.append(threading.get_ident())
            original(name)
        def services():
            worker_threads.append(threading.get_ident())
            entered.set()
            release.wait(5)
            finished.set()
        with mock.patch.object(self.client, "_prepare_startup_services", side_effect=services), mock.patch.object(self.client, "_ensure_popup", side_effect=popup):
            self.root.show()
            self.client.prepare_after_show()
            self.client.prepare_after_show()
            self.assertTrue(entered.wait(2))
            self.assertEqual(self.client._prepared_popups, set())
            ticks = []
            QTimer.singleShot(0, lambda: ticks.append(True))
            for _ in range(60):
                QTest.qWait(20)
                if len(self.client._prepared_popups) == 4 and self.updates.called:
                    break
            release.set()
            self.assertTrue(finished.wait(2))
        self.assertTrue(ticks)
        self.assertEqual(len(self.client._prepared_popups), 4)
        self.assertEqual(popup_threads, [threading.get_ident()] * 4)
        self.assertNotEqual(worker_threads, [threading.get_ident()])
        self.assertEqual(len(worker_threads), 1)
        self.history.assert_called_once()
        self.updates.assert_called_once()

    def test_close_during_http_import_discards_sessions_and_skips_network(self):
        entered, release = threading.Event(), threading.Event()
        sessions = [mock.Mock(), mock.Mock()]
        def make_session():
            if not entered.is_set():
                entered.set()
                release.wait(5)
                return sessions[0]
            return sessions[1]
        with mock.patch.object(S.requests, "Session", side_effect=make_session):
            worker = threading.Thread(target=self.client._prepare_startup_services)
            worker.start()
            self.assertTrue(entered.wait(2))
            self.client._force_quit = True
            self.assertTrue(self.client._on_close())
            release.set()
            worker.join(2)
            self.assertFalse(worker.is_alive())
        for session in sessions:
            session.close.assert_called_once()
        self.assertIsNone(self.client.session)
        self.network.assert_not_called()
        self.client._prepare_next_startup_popup()
        self.assertEqual(self.client._prepared_popups, set())

    def test_font_database_is_cached_across_theme_and_popup_preparation(self):
        with mock.patch.object(S.QFontDatabase, "families", wraps=S.QFontDatabase.families) as families:
            self.client._font_families()
            self.client._on_theme_changed("Modern")
            self.client._ensure_popup("link_warning")
            self.client._ui_font_family()
            self.assertLessEqual(families.call_count, 1)

    def test_windows_registration_runs_in_worker_and_uses_latest_startup_setting(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def register():
            calls.append(threading.get_ident())
            entered.set()
            release.wait(5)
        with mock.patch.object(S.os, "name", "nt"), mock.patch.object(self.client, "_prepare_http_sessions", return_value=True), mock.patch.object(S, "register_windows_notification_protocol", side_effect=register), mock.patch.object(S, "set_start_with_windows") as reconcile:
            worker = threading.Thread(target=self.client._prepare_startup_services)
            worker.start()
            self.assertTrue(entered.wait(2))
            self.client.start_with_windows_var.set(True)
            release.set()
            worker.join(2)
            self.assertFalse(worker.is_alive())
        self.assertNotEqual(calls, [threading.get_ident()])
        reconcile.assert_called_once_with(True)


class StartupDependencyTests(unittest.TestCase):
    def test_cold_ui_import_does_not_import_requests(self):
        result = subprocess.run([sys.executable, "-c", """
import importlib.machinery, importlib.util, sys
loader = importlib.machinery.SourceFileLoader('startup_fixture', 'SpriteLink.pyw')
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
sys.modules[loader.name] = module
loader.exec_module(module)
assert 'requests' not in sys.modules
"""], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_startup_saves_only_new_or_changed_settings(self):
        config = S.default_config()
        with mock.patch.object(S, "_loaded_config_snapshot", S._config_snapshot(config)), mock.patch.object(S, "save_config") as save:
            S._save_startup_config(config)
            save.assert_not_called()
            config["window_width"] += 1
            S._save_startup_config(config)
            save.assert_called_once_with(config)
        with mock.patch.object(S, "_loaded_config_snapshot", None), mock.patch.object(S, "save_config") as save:
            S._save_startup_config(config)
            save.assert_called_once_with(config)
