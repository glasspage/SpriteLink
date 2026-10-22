import os
import socket as sockets
import subprocess
import sys
import unittest
import uuid
from contextlib import ExitStack
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton
from test_security import SPRITELINK as S
import test_startup


class RestartDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def run_dialog(self, interact):
        original = S.QDialog.exec

        def run(dialog):
            QTimer.singleShot(0, lambda: interact(dialog))
            QTimer.singleShot(2000, dialog.reject)
            return original(dialog)

        with mock.patch.object(S.QDialog, "exec", run):
            return S.show_already_running_dialog()

    @staticmethod
    def button(dialog, text):
        return next(button for button in dialog.findChildren(QPushButton) if button.text() == text)

    def test_restart_is_left_of_ok_and_waits_for_lock_before_continuing(self):
        positions = []
        previous_quit = self.app.quitOnLastWindowClosed()

        def choose(dialog):
            restart = self.button(dialog, "Restart")
            ok = self.button(dialog, "OK")
            positions.append((restart.x(), ok.x(), ok.isDefault()))
            restart.click()

        with mock.patch.object(S, "acquire_single_instance_lock", side_effect=[False, False, True]) as acquire, \
             mock.patch.object(S, "request_instance_restart", return_value=True) as request:
            self.assertTrue(self.run_dialog(choose))
            self.assertEqual(acquire.call_count, 3)
            request.assert_called_once_with()
        self.assertLess(positions[0][0], positions[0][1])
        self.assertTrue(positions[0][2])
        self.assertEqual(self.app.quitOnLastWindowClosed(), previous_quit)

    def test_ok_leaves_existing_instance_alone(self):
        with mock.patch.object(S, "acquire_single_instance_lock") as acquire, \
             mock.patch.object(S, "request_instance_restart") as request:
            self.assertFalse(self.run_dialog(lambda dialog: self.button(dialog, "OK").click()))
        acquire.assert_not_called()
        request.assert_not_called()

    def test_instance_exits_while_popup_is_open(self):
        with mock.patch.object(S, "acquire_single_instance_lock", return_value=True), \
             mock.patch.object(S, "request_instance_restart") as request:
            self.assertTrue(self.run_dialog(lambda dialog: self.button(dialog, "Restart").click()))
        request.assert_not_called()

    def test_unavailable_restart_endpoint_does_not_start_a_second_instance(self):
        messages = []

        def choose(dialog):
            self.button(dialog, "Restart").click()
            messages.extend(label.text() for label in dialog.findChildren(QLabel))
            self.button(dialog, "OK").click()

        with mock.patch.object(S, "acquire_single_instance_lock", return_value=False), \
             mock.patch.object(S, "request_instance_restart", return_value=False):
            self.assertFalse(self.run_dialog(choose))
        self.assertTrue(any("Could not restart" in text for text in messages))

    def test_shutdown_timeout_keeps_single_instance_protection(self):
        messages = []

        def choose(dialog):
            self.button(dialog, "Restart").click()

            def finish():
                messages.extend(label.text() for label in dialog.findChildren(QLabel))
                self.button(dialog, "OK").click()

            QTimer.singleShot(250, finish)

        with mock.patch.object(S, "acquire_single_instance_lock", return_value=False), \
             mock.patch.object(S, "request_instance_restart", return_value=True), \
             mock.patch.object(S.time, "monotonic", side_effect=[100.0, 100.0, 111.0]):
            self.assertFalse(self.run_dialog(choose))
        self.assertTrue(any("Could not restart" in text for text in messages))


class RestartTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        if os.name != "nt":
            try:
                probe = sockets.socket(sockets.AF_UNIX)
            except PermissionError:
                self.skipTest("This environment blocks local sockets.")
            else:
                probe.close()
        self.name = "spritelink-test-" + uuid.uuid4().hex
        self.patch = mock.patch.object(S, "SINGLE_INSTANCE_SERVER_NAME", self.name)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_fragmented_and_repeated_requests_trigger_one_shutdown(self):
        callback = mock.Mock()
        control = S.SingleInstanceControl(callback)
        socket = S.QLocalSocket()
        try:
            self.assertTrue(control.server.isListening())
            socket.connectToServer(self.name)
            self.assertTrue(socket.waitForConnected(1000))
            socket.write(b"res")
            socket.flush()
            QTest.qWait(20)
            callback.assert_not_called()
            socket.write(b"tart\n")
            socket.flush()
            QTest.qWait(20)
            callback.assert_called_once_with()
            self.assertTrue(S.request_instance_restart())
            QTest.qWait(20)
            callback.assert_called_once_with()
        finally:
            socket.abort()
            control.server.close()
            control.deleteLater()
            self.app.processEvents()

    def test_request_reaches_another_process(self):
        source = '''
import sys
sys.path.insert(0, "tests")
from PySide6.QtCore import QCoreApplication, QTimer
from test_security import SPRITELINK as S
app = QCoreApplication([])
S.SINGLE_INSTANCE_SERVER_NAME = sys.argv[1]
def quit_existing():
    print("restart_received", flush=True)
    app.quit()
control = S.SingleInstanceControl(quit_existing)
assert control.server.isListening()
print("ready", flush=True)
QTimer.singleShot(3000, app.quit)
app.exec()
'''
        process = subprocess.Popen([sys.executable, "-u", "-c", source, self.name],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), "ready")
            self.assertTrue(S.request_instance_restart())
            stdout, stderr = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0, stderr)
            self.assertIn("restart_received", stdout)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
            process.stdout.close()
            process.stderr.close()

    def test_missing_server_reports_failure(self):
        self.assertFalse(S.request_instance_restart())


class RestartProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_sender_waits_for_delivery_and_disconnects(self):
        socket = mock.Mock()
        socket.waitForConnected.return_value = True
        socket.bytesToWrite.return_value = 8
        socket.waitForBytesWritten.return_value = True
        with mock.patch.object(S, "QLocalSocket", return_value=socket):
            self.assertTrue(S.request_instance_restart())
        socket.connectToServer.assert_called_once_with(S.SINGLE_INSTANCE_SERVER_NAME)
        socket.write.assert_called_once_with(b"restart\n")
        socket.waitForBytesWritten.assert_called_once_with(1000)
        socket.disconnectFromServer.assert_called_once_with()

    def test_sender_reports_connection_and_write_failures(self):
        for connected in (False, True):
            socket = mock.Mock()
            socket.waitForConnected.return_value = connected
            socket.bytesToWrite.return_value = 8
            socket.waitForBytesWritten.return_value = False
            with mock.patch.object(S, "QLocalSocket", return_value=socket):
                self.assertFalse(S.request_instance_restart())

    def test_receiver_waits_for_a_complete_command_and_only_quits_once(self):
        callback = mock.Mock()
        options = S.QLocalServer.SocketOption
        with mock.patch.object(S, "QLocalServer") as server:
            server.SocketOption = options
            control = S.SingleInstanceControl(callback)
        socket = mock.Mock()
        socket.canReadLine.return_value = False
        control._read_command(socket)
        socket.readLine.assert_not_called()
        socket.canReadLine.return_value = True
        socket.readLine.return_value = b"unknown\n"
        control._read_command(socket)
        QTest.qWait(5)
        callback.assert_not_called()
        socket.readLine.return_value = b"restart\n"
        control._read_command(socket)
        control._read_command(socket)
        callback.assert_not_called()
        QTest.qWait(5)
        callback.assert_called_once_with()
        server.return_value.setSocketOptions.assert_called_once_with(options.UserAccessOption)
        control.deleteLater()


class RestartShutdownTests(unittest.TestCase):
    setUp = test_startup.DeferredStartupTests.setUp
    tearDown = test_startup.DeferredStartupTests.tearDown

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_restart_quits_instead_of_minimizing_to_tray(self):
        self.config["minimize_to_tray"] = True
        self.client.minimize_to_tray_var.set(True)
        self.client._minimized_to_tray = True
        self.root.hide()
        self.client._quit_from_tray()
        with mock.patch.object(self.app, "quit") as quit_app:
            QTest.qWait(20)
        self.assertTrue(self.client._force_quit)
        self.assertTrue(self.client._closing)
        self.assertTrue(self.client.stop_event.is_set())
        S.save_config.assert_called()
        quit_app.assert_called_once_with()


class RestartEntryPointTests(unittest.TestCase):
    def run_main(self, *, restart=False, notification=False):
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(S, "os", SimpleNamespace(name="nt")))
            stack.enter_context(mock.patch.object(S.sys, "excepthook"))
            stack.enter_context(mock.patch.object(S.ctypes, "windll", mock.Mock(), create=True))
            stack.enter_context(mock.patch.object(S, "acquire_single_instance_lock", return_value=False))
            stack.enter_context(mock.patch.object(S, "notification_uri_from_arguments", return_value="uri" if notification else None))
            stack.enter_context(mock.patch.object(S, "chatroom_id_from_notification_uri", return_value="room-a"))
            popup = stack.enter_context(mock.patch.object(S, "show_already_running_dialog", return_value=restart))
            activation = stack.enter_context(mock.patch.object(S, "write_notification_activation"))
            application = stack.enter_context(mock.patch.object(S, "QApplication"))
            root = stack.enter_context(mock.patch.object(S, "MainWindow"))
            client = stack.enter_context(mock.patch.object(S, "EncryptedChatClient"))
            control = stack.enter_context(mock.patch.object(S, "SingleInstanceControl"))
            stack.enter_context(mock.patch.object(S, "QIcon"))
            stack.enter_context(mock.patch.object(S.QTimer, "singleShot"))
            S.main()
        return popup, activation, application, root, client, control

    def test_successful_restart_continues_normal_startup_in_this_process(self):
        popup, activation, application, root, client, control = self.run_main(restart=True)
        popup.assert_called_once_with()
        activation.assert_not_called()
        client.assert_called_once_with(root.return_value, defer_startup=True)
        control.assert_called_once_with(client.return_value._quit_from_tray, root.return_value)
        root.return_value.show.assert_called_once_with()
        application.instance.return_value.exec.assert_called_once_with()

    def test_ok_does_not_construct_another_client(self):
        popup, activation, application, root, client, control = self.run_main()
        popup.assert_called_once_with()
        root.assert_not_called()
        client.assert_not_called()

    def test_notification_launch_activates_existing_room_without_restart_popup(self):
        popup, activation, application, root, client, control = self.run_main(notification=True)
        activation.assert_called_once_with("room-a")
        popup.assert_not_called()
        root.assert_not_called()
        client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
