import os
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PIL import Image, ImageDraw, ImageFilter
from PySide6.QtWidgets import QApplication, QPushButton, QWidget
import spritelink_glass as GLASS


class GlassRefractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = QWidget()
        self.window.resize(240, 140)
        self.glass = GLASS.DesktopGlass(self.window)

    def tearDown(self):
        self.glass.stop()
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def test_short_controls_have_no_crease_at_the_center(self):
        rects = ((20, 30, 100, 20),)
        above = GLASS.lens_offset(60, 40 - 0.001, rects)
        below = GLASS.lens_offset(60, 40 + 0.001, rects)
        self.assertLess(abs(above[1] - below[1]), 0.01)
        self.assertAlmostEqual(above[1], -below[1], places=6)

    def test_lens_is_local_and_bounded_even_with_overlapping_controls(self):
        rects = ((20, 30, 100, 40),) * 5
        self.assertEqual(GLASS.lens_offset(200, 120, rects), (0.0, 0.0))
        offset = GLASS.lens_offset(30, 30, rects)
        self.assertLessEqual(GLASS.math.hypot(*offset), GLASS.LENS_STRENGTH + 1e-10)
        self.assertGreater(GLASS.math.hypot(*offset), 0)

    def test_small_blur_preserves_more_detail_than_frosting(self):
        image = Image.new("RGB", (160, 100), "black")
        ImageDraw.Draw(image).rectangle((80, 0, 159, 99), fill="white")
        output = GLASS.render_glass(image, image.size, ())
        frosted = image.filter(ImageFilter.GaussianBlur(12))
        self.assertLess(output.getpixel((70, 50))[0], frosted.getpixel((70, 50))[0])
        self.assertGreater(output.getpixel((90, 50))[0], frosted.getpixel((90, 50))[0])

    def test_refraction_changes_actual_background_pixels(self):
        image = Image.new("RGB", (160, 100))
        ImageDraw.Draw(image).rectangle((0, 40, 159, 99), fill="white")
        plain = GLASS.render_glass(image, image.size, ())
        refracted = GLASS.render_glass(image, image.size, ((20, 40, 120, 40),))
        self.assertNotEqual(plain.tobytes(), refracted.tobytes())
        # The displacement shifts the step edge, rather than only adding a rim.
        self.assertLess(refracted.getpixel((80, 37))[0], plain.getpixel((80, 37))[0])

    def test_visible_control_geometry_tracks_resize(self):
        button = QPushButton("Test", self.window)
        button.setGeometry(20, 30, 80, 24)
        self.window.show()
        self.app.processEvents()
        self.assertEqual(self.glass._rectangles(), ((20, 30, 80, 24),))
        button.setGeometry(40, 50, 100, 24)
        self.assertEqual(self.glass._rectangles(), ((40, 50, 100, 24),))
        button.hide()
        self.assertEqual(self.glass._rectangles(), ())

    def test_old_windows_does_not_enable_capture_exclusion(self):
        with mock.patch.object(GLASS.os, "name", "nt"), mock.patch.object(
            GLASS.sys, "getwindowsversion", return_value=SimpleNamespace(build=18363),
            create=True,
        ), mock.patch.object(GLASS.ctypes, "windll", create=True) as native:
            self.assertFalse(self.glass.start())
            native.user32.SetWindowDisplayAffinity.assert_not_called()

    def test_stop_restores_capture_affinity_and_drops_old_frame(self):
        self.glass.enabled = True
        self.glass.hwnd = 123
        self.glass.previous_affinity = 0
        self.glass.frame = object()
        self.glass.timer.start()
        generation = self.glass.generation
        with mock.patch.object(GLASS.ctypes, "windll", create=True) as native:
            self.glass.stop()
            native.user32.SetWindowDisplayAffinity.assert_called_once_with(123, 0)
        self.assertFalse(self.glass.timer.isActive())
        self.assertIsNone(self.glass.frame)
        self.glass._receive((generation, (generation, (), (1, 1), bytes(3))))
        self.assertIsNone(self.glass.frame)

    def test_popup_pauses_sampling(self):
        self.glass.enabled = True
        self.window.show()
        with mock.patch.object(GLASS.QApplication, "activePopupWidget", return_value=object()), \
                mock.patch.object(self.glass, "_bounds") as bounds:
            self.glass.refresh()
            bounds.assert_not_called()


if __name__ == "__main__":
    unittest.main()
