"""Live Windows backdrop sampling and a small, UI-shaped glass lens."""

import ctypes
from ctypes import wintypes
import math
import os
import sys
import threading

from PIL import Image, ImageDraw, ImageFilter, ImageGrab
from PySide6.QtCore import QObject, QPoint, QTimer, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QComboBox, QLineEdit, QListWidget,
    QPlainTextEdit, QTextBrowser,
)

BLUR_RADIUS = 3.0  # Logical pixels; independent of display scaling.
LENS_STRENGTH = 2.0
LENS_WIDTH = 9.0
FRAME_INTERVAL_MS = 100


def lens_offset(x, y, rectangles):
    """A smooth rounded-rectangle normal field, with no displacement seams."""
    dx = dy = 0.0
    for left, top, width, height in rectangles:
        reach = LENS_WIDTH * 3
        if not (left - reach <= x <= left + width + reach
                and top - reach <= y <= top + height + reach):
            continue
        radius = min(7.0, width / 2, height / 2)
        px, py = x - left - width / 2, y - top - height / 2
        qx, qy = abs(px) - width / 2 + radius, abs(py) - height / 2 + radius
        ox, oy = max(qx, 0), max(qy, 0)
        length = math.hypot(ox, oy)
        distance = length + min(max(qx, qy), 0) - radius
        if abs(distance) > reach:
            continue
        # Soften the medial axes as well as the corners. Hard sign/nearest-edge
        # switches cause visible creases inside short buttons.
        soft_x = max(qx, 0) + 2 * math.log1p(math.exp(-abs(qx) / 2))
        soft_y = max(qy, 0) + 2 * math.log1p(math.exp(-abs(qy) / 2))
        soft_length = math.hypot(soft_x, soft_y)
        if soft_length < 1e-12:
            continue
        nx, ny = soft_x / soft_length, soft_y / soft_length
        amount = LENS_STRENGTH * math.exp(-0.5 * (distance / LENS_WIDTH) ** 2)
        dx += nx * amount * math.tanh(px / 4)
        dy += ny * amount * math.tanh(py / 4)
    # Nearby controls must not compound into a strong/wavy lens.
    length = math.hypot(dx, dy)
    if length > LENS_STRENGTH:
        dx *= LENS_STRENGTH / length
        dy *= LENS_STRENGTH / length
    return dx, dy


def lens_mesh(size, rectangles):
    width, height = size
    step = 8
    points = {}

    def point(x, y):
        if (x, y) not in points:
            dx, dy = lens_offset(x, y, rectangles)
            points[x, y] = (
                max(0.0, min(float(width), x + dx)),
                max(0.0, min(float(height), y + dy)),
            )
        return points[x, y]

    mesh = []
    for top in range(0, height, step):
        bottom = min(height, top + step)
        identity_start = None
        for left in range(0, width, step):
            right = min(width, left + step)
            quad = (*point(left, top), *point(left, bottom),
                    *point(right, bottom), *point(right, top))
            identity = (left, top, left, bottom, right, bottom, right, top)
            if all(abs(a - b) < 0.02 for a, b in zip(quad, identity)):
                if identity_start is None:
                    identity_start = left
                continue
            if identity_start is not None:
                mesh.append(((identity_start, top, left, bottom),
                             (identity_start, top, identity_start, bottom,
                              left, bottom, left, top)))
                identity_start = None
            mesh.append(((left, top, right, bottom), quad))
        if identity_start is not None:
            mesh.append(((identity_start, top, width, bottom),
                         (identity_start, top, identity_start, bottom,
                          width, bottom, width, top)))
    return mesh


def render_glass(source, size, rectangles, mesh=None):
    image = source.convert("RGB").resize(size, Image.Resampling.LANCZOS)
    image = image.filter(ImageFilter.GaussianBlur(BLUR_RADIUS))
    if rectangles:
        image = image.transform(
            size, Image.Transform.MESH,
            mesh if mesh is not None else lens_mesh(size, rectangles),
            Image.Resampling.BICUBIC,
        )
        # Soft opposing edge highlights make the subtle lens read as a bevel.
        lighting = Image.new("RGBA", size)
        draw = ImageDraw.Draw(lighting)
        for left, top, width, height in rectangles:
            radius = min(7, width / 2, height / 2)
            box = (left, top, left + width - 1, top + height - 1)
            draw.rounded_rectangle(box, radius=radius, outline=(255, 255, 255, 22))
            draw.line((left + radius, top, left + width - radius, top),
                      fill=(255, 255, 255, 32))
            draw.line((left + radius, top + height - 1,
                       left + width - radius, top + height - 1),
                      fill=(24, 48, 64, 18))
        lighting = lighting.filter(ImageFilter.GaussianBlur(1.2))
        image = Image.alpha_composite(image.convert("RGBA"), lighting).convert("RGB")
    return image


class DesktopGlass(QObject):
    frame_ready = Signal(object)

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.frame = None
        self.enabled = False
        self.busy = False
        self.generation = 0
        self.hwnd = None
        self.previous_affinity = 0
        self.cache_key = None
        self.mesh = None
        self.timer = QTimer(self)
        self.timer.setInterval(FRAME_INTERVAL_MS)
        self.timer.timeout.connect(self.refresh)
        self.frame_ready.connect(self._receive)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.stop)

    def start(self):
        if self.enabled:
            self.refresh()
            return True
        # Earlier versions interpret 0x11 as WDA_MONITOR (a black rectangle).
        if os.name != "nt" or sys.getwindowsversion().build < 19041:
            return False
        try:
            user32 = ctypes.windll.user32
            self.hwnd = wintypes.HWND(int(self.window.winId()))
            affinity = wintypes.DWORD()
            user32.GetWindowDisplayAffinity.argtypes = [
                wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
            ]
            user32.GetWindowDisplayAffinity.restype = wintypes.BOOL
            user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
            user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
            if not user32.GetWindowDisplayAffinity(self.hwnd, ctypes.byref(affinity)):
                self.hwnd = None
                return False
            self.previous_affinity = affinity.value
            if not user32.SetWindowDisplayAffinity(self.hwnd, 0x11):
                self.hwnd = None
                return False
            ctypes.windll.dwmapi.DwmFlush()
        except (AttributeError, OSError):
            self._restore_affinity()
            return False
        self.enabled = True
        self.generation += 1
        self.timer.start()
        QTimer.singleShot(0, self.refresh)
        return True

    def _restore_affinity(self):
        if self.hwnd is not None:
            try:
                ctypes.windll.user32.SetWindowDisplayAffinity(
                    self.hwnd, self.previous_affinity
                )
            except (AttributeError, OSError):
                pass
            self.hwnd = None

    def stop(self):
        self.enabled = False
        self.generation += 1
        self.timer.stop()
        self.frame = None
        self._restore_affinity()
        self.window.update()

    def _bounds(self):
        user32 = ctypes.windll.user32
        user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetClientRect.restype = wintypes.BOOL
        user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        user32.ClientToScreen.restype = wintypes.BOOL
        rect, origin = wintypes.RECT(), wintypes.POINT()
        if not user32.GetClientRect(self.hwnd, ctypes.byref(rect)):
            raise OSError("Cannot read window bounds")
        if not user32.ClientToScreen(self.hwnd, ctypes.byref(origin)):
            raise OSError("Cannot map window bounds")
        return (origin.x, origin.y, origin.x + rect.right, origin.y + rect.bottom)

    def _rectangles(self):
        controls = (QAbstractButton, QComboBox, QLineEdit, QPlainTextEdit,
                    QTextBrowser, QListWidget)
        rectangles = []
        for widget in self.window.findChildren(QObject):
            if not isinstance(widget, controls) or not widget.isVisibleTo(self.window):
                continue
            origin = widget.mapTo(self.window, QPoint())
            if widget.width() < 4 or widget.height() < 4:
                continue
            rectangles.append((origin.x(), origin.y(), widget.width(), widget.height()))
        return tuple(rectangles)

    def refresh(self):
        if (not self.enabled or self.busy or not self.window.isVisible()
                or self.window.isMinimized()
                or QApplication.activePopupWidget() is not None
                or QApplication.activeModalWidget() is not None):
            return
        try:
            bounds = self._bounds()
        except (OSError, AttributeError):
            self.stop()
            return
        size = (self.window.width(), self.window.height())
        if min(size) < 1 or bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
            return
        rectangles = self._rectangles()
        generation = self.generation
        self.busy = True

        def work():
            payload = None
            try:
                # Sample only the client region. Pixels stay in memory and
                # are never saved or sent anywhere.
                source = ImageGrab.grab(bbox=bounds, all_screens=True)
                key = (size, rectangles)
                if key != self.cache_key:
                    self.mesh = lens_mesh(size, rectangles) if rectangles else None
                    self.cache_key = key
                image = render_glass(source, size, rectangles, self.mesh)
                payload = (generation, bounds, size, image.tobytes())
            except Exception:
                pass  # Native acrylic remains available if sampling fails.
            try:
                self.frame_ready.emit((generation, payload))
            except RuntimeError:
                pass  # Window was destroyed while the worker was finishing.

        threading.Thread(target=work, daemon=True, name="SpriteLink glass").start()

    def _receive(self, result):
        self.busy = False
        generation, payload = result
        if not self.enabled or generation != self.generation:
            return
        if payload is None:
            self.stop()
            return
        _, bounds, size, pixels = payload
        try:
            if bounds != self._bounds() or size != (self.window.width(), self.window.height()):
                return  # Never stretch an old frame after a move/resize.
        except (OSError, AttributeError):
            self.stop()
            return
        self.frame = QImage(pixels, *size, size[0] * 3, QImage.Format.Format_RGB888).copy()
        self.window.update()
