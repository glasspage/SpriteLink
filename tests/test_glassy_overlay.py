import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QLineEdit, QVBoxLayout, QWidget

from test_security import SPRITELINK


class GlassyOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.parent = QWidget()
        self.parent.resize(320, 200)
        self.parent.setStyleSheet("background-color: rgb(240, 240, 240);")
        self.parent.show()
        self.app.processEvents()
        self.overlay = SPRITELINK.ConfigOverlay(self.parent)
        self.overlay.hide()
        self.overlay.setGeometry(self.parent.rect())
        self.overlay.apply_theme(True)

    def tearDown(self) -> None:
        self.parent.close()
        self.parent.deleteLater()
        self.app.processEvents()

    def test_translucent_capture_blurs_to_an_opaque_layer(self) -> None:
        source = QPixmap(160, 100)
        source.fill(QColor(224, 240, 250, 188))
        painter = QPainter(source)
        painter.fillRect(0, 75, 160, 25, QColor(255, 255, 255, 226))
        painter.end()
        blurred = self.overlay._blur_pixmap(source)
        self.assertIsNotNone(blurred)
        image = blurred.toImage()
        for y in range(image.height()):
            for x in range(image.width()):
                self.assertEqual(image.pixelColor(x, y).alpha(), 255)

    def test_blurred_overlay_does_not_reveal_changed_live_controls(self) -> None:
        self.parent.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.parent.setStyleSheet("background-color: rgba(240, 240, 240, 100);")
        self.overlay.show()
        before = self.overlay.grab().toImage()
        self.parent.setStyleSheet("background-color: rgba(0, 0, 0, 100);")
        # Leave the snapshot stale while changing the live UI.
        after = self.overlay.grab().toImage()
        self.assertEqual(before, after)

    def test_high_dpi_blur_keeps_the_entire_source(self) -> None:
        for ratio in (1.0, 1.5, 2.0):
            with self.subTest(ratio=ratio):
                source = QPixmap(round(160 * ratio), round(100 * ratio))
                source.setDevicePixelRatio(ratio)
                source.fill(Qt.GlobalColor.black)
                painter = QPainter(source)
                painter.fillRect(QRectF(80, 0, 80, 100), Qt.GlobalColor.white)
                painter.end()
                blurred = self.overlay._blur_pixmap(source)
                self.assertEqual(blurred.size(), source.size())
                self.assertEqual(blurred.devicePixelRatio(), ratio)
                image = blurred.toImage()
                self.assertLess(
                    image.pixelColor(round(30 * ratio), round(50 * ratio)).red(),
                    10,
                )
                self.assertGreater(
                    image.pixelColor(round(130 * ratio), round(50 * ratio)).red(),
                    245,
                )
                midpoint = image.pixelColor(
                    round(80 * ratio), round(50 * ratio)
                ).red()
                self.assertGreater(midpoint, 95)
                self.assertLess(midpoint, 160)

    def test_resize_recaptures_after_the_layout_changes(self) -> None:
        self.overlay.show()
        self.app.processEvents()
        with mock.patch.object(
            self.overlay, "_blur_pixmap", wraps=self.overlay._blur_pixmap,
        ) as blur:
            self.parent.resize(400, 260)
            self.parent.setStyleSheet("background-color: rgb(180, 200, 220);")
            self.overlay.setGeometry(self.parent.rect())
            self.assertEqual(blur.call_count, 0)
            self.app.processEvents()
            self.assertEqual(blur.call_count, 1)
            captured = blur.call_args.args[0]
            self.assertEqual(
                captured.deviceIndependentSize().toSize(), self.parent.size()
            )
            self.assertEqual(
                captured.toImage().pixelColor(200, 100), QColor(180, 200, 220)
            )

    def test_theme_refresh_is_deferred_and_excludes_the_overlay(self) -> None:
        layout = QVBoxLayout(self.overlay)
        entry = QLineEdit("Config contents must not enter the capture")
        layout.addWidget(entry)
        self.overlay.show()
        entry.setFocus()
        self.app.processEvents()
        with mock.patch.object(
            self.overlay, "_blur_pixmap", wraps=self.overlay._blur_pixmap,
        ) as blur:
            self.overlay.apply_theme(True)
            self.parent.setStyleSheet("background-color: rgb(120, 160, 200);")
            self.assertEqual(blur.call_count, 0)
            self.app.processEvents()
            self.assertEqual(blur.call_count, 1)
            captured = blur.call_args.args[0].toImage()
            self.assertEqual(
                captured.pixelColor(160, 100), QColor(120, 160, 200)
            )
            self.assertTrue(self.overlay.isVisible())
            self.assertIs(self.app.focusWidget(), entry)

    def test_background_refresh_does_not_hide_or_reopen_embedded_widgets(self) -> None:
        class EmbeddedRenderer(QWidget):
            hides = 0
            shows = 0

            def hideEvent(self, event):
                self.hides += 1
                super().hideEvent(event)

            def showEvent(self, event):
                self.shows += 1
                super().showEvent(event)

        renderer = EmbeddedRenderer(self.overlay)
        renderer.resize(160, 90)
        renderer.show()
        self.overlay.show()
        self.app.processEvents()
        shows = renderer.shows
        for size in ((400, 260), (300, 220), (450, 300)):
            self.parent.resize(*size)
            self.overlay.setGeometry(self.parent.rect())
            self.app.processEvents()
            self.assertEqual(renderer.hides, 0)
            self.assertEqual(renderer.shows, shows)
            self.assertTrue(renderer.isVisible())

    def test_background_capture_includes_siblings_without_capturing_the_player(self) -> None:
        sibling = QWidget(self.parent)
        sibling.setGeometry(100, 70, 120, 60)
        sibling.setStyleSheet("background: rgb(0, 0, 255)")
        sibling.show()
        player = QWidget(self.overlay)
        player.setGeometry(100, 70, 120, 60)
        player.setStyleSheet("background: rgb(255, 0, 0)")
        player.show()
        with mock.patch.object(self.overlay, "_blur_pixmap", wraps=self.overlay._blur_pixmap) as blur:
            self.overlay.show()
            capture = blur.call_args.args[0].toImage()
            self.assertEqual(capture.pixelColor(160, 100), QColor("blue"))
        self.assertTrue(player.isVisible())

    def test_leaving_glassy_cancels_pending_capture(self) -> None:
        self.overlay.show()
        self.app.processEvents()
        with mock.patch.object(self.overlay, "_blur_pixmap") as blur:
            self.overlay.resize(400, 260)
            self.overlay.apply_theme(False)
            self.app.processEvents()
            self.assertEqual(blur.call_count, 0)
            self.assertIsNone(self.overlay._blurred_background)

    def test_hiding_during_pending_refresh_does_not_reopen_overlay(self) -> None:
        self.overlay.show()
        self.app.processEvents()
        with mock.patch.object(self.overlay, "_blur_pixmap") as blur:
            self.overlay.resize(400, 260)
            self.overlay.hide()
            self.app.processEvents()
            self.assertEqual(blur.call_count, 0)
            self.assertFalse(self.overlay.isVisible())

    def test_finished_theme_log_rebuild_schedules_a_new_capture(self) -> None:
        client = mock.Mock()
        client._history_render_updates_suppressed = True
        client.chat_content = self.parent
        self.overlay.show()
        self.app.processEvents()
        with mock.patch.object(
            self.overlay, "_blur_pixmap", wraps=self.overlay._blur_pixmap,
        ) as blur:
            SPRITELINK.EncryptedChatClient._set_history_render_updates_suppressed(
                client, False
            )
            self.assertEqual(blur.call_count, 0)
            self.app.processEvents()
            self.assertEqual(blur.call_count, 1)


if __name__ == "__main__":
    unittest.main()
