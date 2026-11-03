"""Indexed drawing composer and image import controls."""
from collections import deque
import math

from PIL import Image, ImageOps
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QPushButton, QVBoxLayout, QWidget,
)

from spritelink_image_codec import IMAGE_PALETTE, IMAGE_SIZES


COLORS = [QColor(color).rgb() for color in IMAGE_PALETTE]
SIZE_LABELS = [f"{width} × {height}" for width, height in IMAGE_SIZES]
PATTERNS = (
    lambda x, y: False,
    lambda x, y: (x + y) % 2,
    lambda x, y: (x // 2 + y // 2) % 2,
    lambda x, y: (x // 4 + y // 4) % 2,
    lambda x, y: abs(x % 8 - 4) + abs(y % 8 - 4) < 3,
    lambda x, y: y % 2,
    lambda x, y: x % 2,
    lambda x, y: y // 4 % 2,
    lambda x, y: x // 4 % 2,
    lambda x, y: y % 2 == 0 and (x + y // 2 * 2) % 4 == 0,
)


def drawing_image(size: int, pixels: bytes) -> QImage:
    width, height = IMAGE_SIZES[size]
    # Qt pads indexed rows to four-byte boundaries.
    stride = (width + 3) & ~3
    data = b"".join(pixels[i:i + width].ljust(stride, b"\0")
                    for i in range(0, len(pixels), width))
    image = QImage(data, width, height, stride, QImage.Format.Format_Indexed8)
    image.setColorTable(COLORS)
    return image.copy()


class PixelView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.canvas_size = 0
        self.pixels = bytearray()
        self.grid = False
        self.selection = QRect()
        self.setMinimumSize(92, 92)

    def sizeHint(self) -> QSize:
        return QSize(272, 182)

    def image_rect(self) -> QRectF:
        width, height = IMAGE_SIZES[self.canvas_size]
        scale = min((self.width() - 2) / width, (self.height() - 2) / height)
        return QRectF(
            (self.width() - width * scale) / 2, (self.height() - height * scale) / 2,
            width * scale, height * scale,
        )

    def paintEvent(self, event) -> None:
        if not self.pixels:
            return
        rect = self.image_rect()
        width, height = IMAGE_SIZES[self.canvas_size]
        scale = rect.width() / width
        painter = QPainter(self)
        painter.drawImage(rect, drawing_image(self.canvas_size, self.pixels))
        painter.setPen(self.palette().mid().color())
        painter.drawRect(rect.adjusted(-1, -1, 0, 0))
        if self.grid and scale >= 2:
            painter.setPen(QColor(0, 0, 0, 60))
            for x in range(1, width):
                offset = rect.left() + x * scale
                painter.drawLine(QPointF(offset, rect.top()), QPointF(offset, rect.bottom()))
            for y in range(1, height):
                offset = rect.top() + y * scale
                painter.drawLine(QPointF(rect.left(), offset), QPointF(rect.right(), offset))
        if not self.selection.isEmpty():
            box = QRectF(
                rect.left() + self.selection.x() * scale,
                rect.top() + self.selection.y() * scale,
                self.selection.width() * scale, self.selection.height() * scale,
            )
            painter.setPen(QColor("black"))
            painter.drawRect(box)
            painter.setPen(Qt.GlobalColor.white)
            pen = painter.pen()
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawRect(box)


class DrawingCanvas(PixelView):
    changed = Signal()
    resized = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.grid = True
        self.preview_scale = 1
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.tool = "Pencil"
        self.colors = [0, 1]
        self.pattern = 0
        self.edited = False
        self.undo_stack = deque(maxlen=64)
        self.redo_stack = deque(maxlen=64)
        self._before = None
        self.reset()

    def maximum_scale(self) -> int:
        width, height = IMAGE_SIZES[self.canvas_size]
        return max(1, min((self.width() - 2) // width, (self.height() - 2) // height))

    def image_rect(self) -> QRectF:
        width, height = IMAGE_SIZES[self.canvas_size]
        scale = min(self.preview_scale, self.maximum_scale())
        width *= scale
        height *= scale
        return QRectF((self.width() - width) // 2, (self.height() - height) // 2, width, height)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.resized.emit()

    def snapshot(self) -> tuple[int, bytes, bool]:
        return self.canvas_size, bytes(self.pixels), self.edited

    def restore(self, state) -> None:
        self.canvas_size, pixels, self.edited = state
        self.pixels = bytearray(pixels)
        self.selection = QRect()
        self._before = None
        self.changed.emit()
        self.update()

    def reset(self) -> None:
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._before = None
        width, height = IMAGE_SIZES[self.canvas_size]
        self.restore((self.canvas_size, bytes([1]) * (width * height), False))

    def replace(self, size: int, pixels: bytes, *, edited: bool = True) -> None:
        self.undo_stack.append(self.snapshot())
        self.redo_stack.clear()
        self.restore((size, pixels, edited))

    def resize_canvas(self, size: int) -> None:
        if size == self.canvas_size:
            return
        width, height = IMAGE_SIZES[size]
        old_width, old_height = IMAGE_SIZES[self.canvas_size]
        dx, dy = (width - old_width) // 2, (height - old_height) // 2
        pixels = bytes(
            self.pixels[(y - dy) * old_width + x - dx]
            if 0 <= x - dx < old_width and 0 <= y - dy < old_height else 1
            for y in range(height) for x in range(width)
        )
        self.replace(size, pixels, edited=self.edited)

    def step_history(self, source, destination) -> None:
        if source:
            destination.append(self.snapshot())
            self.restore(source.pop())

    def _point(self, position: QPointF) -> QPoint:
        rect = self.image_rect()
        width, height = IMAGE_SIZES[self.canvas_size]
        return QPoint(
            max(0, min(width - 1, int((position.x() - rect.left()) * width / rect.width()))),
            max(0, min(height - 1, int((position.y() - rect.top()) * height / rect.height()))),
        )

    def _color(self, x: int, y: int) -> int:
        return self.colors[self._primary ^ bool(PATTERNS[self.pattern](x, y))]

    def _line(self, start: QPoint, end: QPoint) -> None:
        width, _ = IMAGE_SIZES[self.canvas_size]
        dx, dy = end.x() - start.x(), end.y() - start.y()
        steps = max(1, abs(dx), abs(dy))
        for i in range(steps + 1):
            x = start.x() + round(dx * i / steps)
            y = start.y() + round(dy * i / steps)
            self.pixels[y * width + x] = self._color(x, y)

    def _fill(self, point: QPoint) -> None:
        width, height = IMAGE_SIZES[self.canvas_size]
        original = self._before[1]
        x, y = point.x(), point.y()
        target = original[y * width + x]
        pending = [(x, y)]
        seen = {(x, y)}
        while pending:
            x, y = pending.pop()
            self.pixels[y * width + x] = self._color(x, y)
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if (0 <= nx < width and 0 <= ny < height
                        and (nx, ny) not in seen and original[ny * width + nx] == target):
                    seen.add((nx, ny))
                    pending.append((nx, ny))

    def _move_selection(self, delta: QPoint) -> None:
        width, height = IMAGE_SIZES[self.canvas_size]
        rect = self._move_rect
        dx = max(-rect.left(), min(width - 1 - rect.right(), delta.x()))
        dy = max(-rect.top(), min(height - 1 - rect.bottom(), delta.y()))
        original = self._before[1]
        self.pixels[:] = original
        for y in range(rect.top(), rect.bottom() + 1):
            start = y * width + rect.x()
            self.pixels[start:start + rect.width()] = bytes([1]) * rect.width()
        for y in range(rect.top(), rect.bottom() + 1):
            start = y * width + rect.x()
            moved = start + dy * width + dx
            self.pixels[moved:moved + rect.width()] = original[start:start + rect.width()]
        self.selection = rect.translated(dx, dy)

    def mousePressEvent(self, event) -> None:
        if (self._before is not None or not self.image_rect().contains(event.position())
                or event.button() not in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton)):
            return
        point = self._point(event.position())
        if self.tool == "Move" and not self.selection.contains(point):
            return
        self._before = self.snapshot()
        self._button = event.button()
        self._primary = int(event.button() == Qt.MouseButton.RightButton)
        self._start = self._last = point
        self._move_rect = QRect(self.selection)
        if self.tool == "Pencil":
            self._line(point, point)
        elif self.tool == "Bucket":
            self._fill(point)
        elif self.tool == "Select":
            self.selection = QRect(point, point)
        self.update()

    def mouseMoveEvent(self, event) -> None:
        if self._before is None:
            return
        point = self._point(event.position())
        if self.tool == "Pencil":
            self._line(self._last, point)
        elif self.tool == "Select":
            self.selection = QRect(self._start, point).normalized()
        elif self.tool == "Move":
            self._move_selection(point - self._start)
        self._last = point
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self._before is None or event.button() != self._button:
            return
        self.mouseMoveEvent(event)
        if bytes(self.pixels) != self._before[1]:
            self.undo_stack.append(self._before)
            self.redo_stack.clear()
            self.edited = True
        self._before = None
        self.changed.emit()


class ColorButton(QPushButton):
    colorPicked = Signal(int)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self.colorPicked.emit(1)
            event.accept()
        else:
            super().mousePressEvent(event)


def preview_icon(pixels: bytes, width: int = 16, height: int = 16) -> QIcon:
    image = QImage(pixels, width, height, width, QImage.Format.Format_Indexed8)
    image.setColorTable(COLORS)
    return QIcon(QPixmap.fromImage(image))


class DrawingMenu(QFrame):
    changed = Signal()
    importRequested = Signal()

    def __init__(self, combo_type) -> None:
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        row = QHBoxLayout(self)
        row.setContentsMargins(5, 5, 5, 5)
        row.setSpacing(5)
        tools = QVBoxLayout()
        tool_group = QButtonGroup(self)
        self.canvas = DrawingCanvas()
        for label in ("Pencil", "Bucket", "Select", "Move"):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setChecked(label == "Pencil")
            button.clicked.connect(lambda _checked, name=label: setattr(self.canvas, "tool", name))
            tool_group.addButton(button)
            tools.addWidget(button)
        tools.addStretch()
        row.addLayout(tools)
        row.addWidget(self.canvas, 1)

        palette = QVBoxLayout()
        palette.addWidget(QLabel("Colors"))
        slots = QHBoxLayout()
        slot_group = QButtonGroup(self)
        self.slots = []
        self.active_color = 0
        for index in range(2):
            button = QPushButton(str(index + 1))
            button.setToolTip("Left click" if index == 0 else "Right click")
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.clicked.connect(lambda _checked, slot=index: setattr(self, "active_color", slot))
            slot_group.addButton(button)
            slots.addWidget(button)
            self.slots.append(button)
            if index == 0:
                swap = QPushButton("⇄")
                swap.setToolTip("Swap colors")
                swap.clicked.connect(self._swap_colors)
                slots.addWidget(swap)
        palette.addLayout(slots)
        swatches = QGridLayout()
        swatches.setSpacing(2)
        for index, color in enumerate(IMAGE_PALETTE):
            button = ColorButton()
            button.setFixedSize(25, 24)
            button.setToolTip(color)
            button.setAccessibleName(color)
            button.setIcon(preview_icon(bytes([index]) * 256))
            button.clicked.connect(lambda _checked, value=index: self._pick_color(self.active_color, value))
            button.colorPicked.connect(lambda slot, value=index: self._pick_color(slot, value))
            swatches.addWidget(button, index // 4, index % 4)
        palette.addLayout(swatches)
        palette.addWidget(QLabel("Pattern"))
        patterns = QGridLayout()
        patterns.setSpacing(2)
        pattern_group = QButtonGroup(self)
        self.pattern_buttons = []
        for index in range(len(PATTERNS)):
            button = QPushButton()
            button.setFixedSize(22, 22)
            button.setIconSize(QSize(16, 16))
            button.setAccessibleName(f"Pattern {index + 1}")
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.clicked.connect(lambda _checked, value=index: setattr(self.canvas, "pattern", value))
            pattern_group.addButton(button)
            patterns.addWidget(button, index // 5, index % 5)
            self.pattern_buttons.append(button)
        palette.addLayout(patterns)
        palette.addStretch()
        row.addLayout(palette)
        self._update_colors()

        controls = QVBoxLayout()
        controls.addWidget(QLabel("Image Size"))
        self.size_combo = combo_type()
        self.size_combo.addItems(SIZE_LABELS)
        self.size_combo.setAccessibleName("Canvas size")
        self.size_combo.currentIndexChanged.connect(self.canvas.resize_canvas)
        controls.addWidget(self.size_combo)
        controls.addWidget(QLabel("View"))
        self.preview_scale_combo = combo_type()
        self.preview_scale_combo.setAccessibleName("Preview scale")
        self.preview_scale_combo.setToolTip("Preview scale")
        self.preview_scale_combo.activated.connect(self._select_preview_scale)
        self._preferred_preview_scale = None
        self.canvas.resized.connect(self._update_preview_scales)
        controls.addWidget(self.preview_scale_combo)
        grid = QCheckBox("Grid")
        grid.setChecked(self.canvas.grid)
        grid.toggled.connect(self._set_grid)
        controls.addWidget(grid)
        self.undo = QPushButton("Undo")
        self.redo = QPushButton("Redo")
        self.undo.clicked.connect(lambda: self.canvas.step_history(self.canvas.undo_stack, self.canvas.redo_stack))
        self.redo.clicked.connect(lambda: self.canvas.step_history(self.canvas.redo_stack, self.canvas.undo_stack))
        for keys, button in (("Ctrl+Z", self.undo), ("Ctrl+Y", self.redo)):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(button.click)
        import_button = QPushButton("Import...")
        import_button.clicked.connect(self.importRequested)
        clear = QPushButton("Clear")
        clear.clicked.connect(self._clear)
        for left, right in ((self.undo, self.redo), (import_button, clear)):
            buttons = QHBoxLayout()
            buttons.setSpacing(3)
            buttons.addWidget(left)
            buttons.addWidget(right)
            controls.addLayout(buttons)
        controls.addStretch()
        row.addLayout(controls)
        self.canvas.changed.connect(self._canvas_changed)
        self._canvas_changed()

    def _set_grid(self, enabled: bool) -> None:
        self.canvas.grid = enabled
        self.canvas.update()

    def _select_preview_scale(self, index: int) -> None:
        self._preferred_preview_scale = (
            None if self.preview_scale_combo.itemText(index) == "Max" else index + 1
        )
        self._update_preview_scales()

    def _update_preview_scales(self) -> None:
        maximum = self.canvas.maximum_scale()
        numeric_count = min(maximum, 3)
        combo = self.preview_scale_combo
        if combo.count() != numeric_count + 1:
            combo.clear()
            combo.addItems([f"{scale}x" for scale in range(1, numeric_count + 1)] + ["Max"])
        preferred = self._preferred_preview_scale
        scale = min(preferred, maximum) if preferred is not None else maximum
        combo.setCurrentIndex(scale - 1 if preferred is not None else numeric_count)
        self.canvas.preview_scale = scale
        self.canvas.update()

    def _clear(self) -> None:
        if self.canvas.edited:
            self.canvas.replace(
                self.canvas.canvas_size, bytes([1]) * len(self.canvas.pixels), edited=False,
            )

    def _canvas_changed(self) -> None:
        previous = self.size_combo.blockSignals(True)
        self.size_combo.setCurrentIndex(self.canvas.canvas_size)
        self.size_combo.blockSignals(previous)
        self.undo.setEnabled(bool(self.canvas.undo_stack))
        self.redo.setEnabled(bool(self.canvas.redo_stack))
        self._update_preview_scales()
        self.changed.emit()

    def _swap_colors(self) -> None:
        self.canvas.colors.reverse()
        self._update_colors()

    def _pick_color(self, slot: int, value: int) -> None:
        self.canvas.colors[slot] = value
        self._update_colors()

    def _update_colors(self) -> None:
        for button, color in zip(self.slots, self.canvas.colors):
            button.setIcon(preview_icon(bytes([color]) * 256))
        first, second = self.canvas.colors
        for button, pattern in zip(self.pattern_buttons, PATTERNS):
            button.setIcon(preview_icon(bytes(
                second if pattern(x, y) else first for y in range(16) for x in range(16)
            )))


class ImportPreview(PixelView):
    transformed = Signal()
    zoomRequested = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.pan = QPointF()
        self._last = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._last = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event) -> None:
        if self._last is not None:
            scale = self.image_rect().width() / IMAGE_SIZES[self.canvas_size][0]
            self.pan += (event.position() - self._last) / scale
            self._last = event.position()
            self.transformed.emit()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._last = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if delta:
            self.zoomRequested.emit("+" if delta > 0 else "−")
            event.accept()
        else:
            event.ignore()


def load_import_image(path: str) -> Image.Image:
    with Image.open(path) as source:
        if source.width * source.height > 24_000_000:
            raise ValueError("Choose an image under 24 megapixels.")
        return ImageOps.exif_transpose(source).convert("RGBA")


class DrawingImportPanel(QFrame):
    accepted = Signal(int, bytes)
    dismissed = Signal()

    def __init__(self, source: Image.Image, combo_type) -> None:
        super().__init__()
        self.setObjectName("configPanel")
        self.source = source
        self._reduced_source = source
        self._reduction = 1
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)
        layout.addWidget(QLabel("Import image"))
        self.preview = ImportPreview()
        self.preview.transformed.connect(self.refresh)
        self.preview.zoomRequested.connect(self.zoom_by)
        layout.addWidget(self.preview, 1)
        settings = QHBoxLayout()
        self.size_combo = combo_type()
        self.size_combo.addItems(SIZE_LABELS)
        self.size_combo.setAccessibleName("Canvas size")
        ratio = source.width / source.height
        self.size_combo.setCurrentIndex(min(
            range(len(IMAGE_SIZES)),
            key=lambda i: abs(math.log(ratio * IMAGE_SIZES[i][1] / IMAGE_SIZES[i][0])),
        ))
        settings.addWidget(self.size_combo)
        self.mode = combo_type()
        self.mode.addItems(("Color", "Grayscale"))
        self.mode.setAccessibleName("Conversion")
        settings.addWidget(self.mode)
        self.dither = QCheckBox("Dither")
        settings.addWidget(self.dither)
        settings.addWidget(QLabel("Background"))
        self.background = combo_type()
        self.background.setAccessibleName("Background")
        for index, color in enumerate(IMAGE_PALETTE):
            self.background.addItem(preview_icon(bytes([index]) * 256), "")
            self.background.setItemData(index, color, Qt.ItemDataRole.ToolTipRole)
            self.background.setItemData(index, color, Qt.ItemDataRole.AccessibleTextRole)
        self.background.setCurrentIndex(1)
        settings.addWidget(self.background)
        settings.addStretch()
        layout.addLayout(settings)

        buttons = QHBoxLayout()
        for label in ("Fit", "Fill", "Pixel", "−", "+"):
            button = QPushButton(label)
            if label in ("−", "+"):
                button.setAccessibleName("Zoom out" if label == "−" else "Zoom in")
                button.clicked.connect(lambda _checked, direction=label: self.zoom_by(direction))
            else:
                button.clicked.connect(lambda _checked, preset=label: self.set_zoom(preset))
            buttons.addWidget(button)
        buttons.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.dismissed)
        buttons.addWidget(cancel)
        accept = QPushButton("Use Image")
        accept.clicked.connect(lambda: self.accepted.emit(self.preview.canvas_size, bytes(self.preview.pixels)))
        buttons.addWidget(accept)
        layout.addLayout(buttons)
        self.preset = "Fill"
        self.size_combo.currentIndexChanged.connect(lambda _index: self.set_zoom(self.preset))
        self.mode.currentIndexChanged.connect(self.refresh)
        self.dither.toggled.connect(self.refresh)
        self.background.currentIndexChanged.connect(self.refresh)
        self.set_zoom("Fill")

    def set_zoom(self, preset: str) -> None:
        self.preset = preset
        width, height = IMAGE_SIZES[self.size_combo.currentIndex()]
        scales = width / self.source.width, height / self.source.height
        self.zoom = {"Fit": min(scales), "Fill": max(scales), "Pixel": 1.0}[preset]
        self.preview.pan = QPointF()
        self.refresh()

    def zoom_by(self, direction: str) -> None:
        scale = 1.25 if direction == "+" else 0.8
        min_zoom = 1 / max(self.source.size)
        self.zoom = max(min_zoom, min(128, self.zoom * scale))
        self.refresh()

    def refresh(self) -> None:
        size = self.size_combo.currentIndex()
        width, height = IMAGE_SIZES[size]
        # Prefilter large downscales before sampling the cropped preview.
        reduction = max(1, min(min(self.source.size), int(0.5 / self.zoom)))
        if reduction != self._reduction:
            self._reduction = reduction
            self._reduced_source = self.source.reduce(reduction) if reduction > 1 else self.source
        inv = 1 / self.zoom
        pan = self.preview.pan
        image = self._reduced_source.transform(
            (width, height), Image.Transform.AFFINE,
            (inv / reduction, 0, ((self.source.width - width * inv) / 2 - pan.x() * inv) / reduction,
             0, inv / reduction, ((self.source.height - height * inv) / 2 - pan.y() * inv) / reduction),
            resample=Image.Resampling.NEAREST if self.zoom == 1 else Image.Resampling.BICUBIC,
            fillcolor=(0, 0, 0, 0),
        )
        background = self.background.currentIndex()
        alpha = image.getchannel("A")
        rgb = Image.new("RGB", image.size, IMAGE_PALETTE[background])
        rgb.paste(image.convert("RGB"), mask=alpha)
        grayscale = self.mode.currentIndex() == 1
        indices = (0, 1, 7) if grayscale else tuple(range(len(COLORS)))
        if grayscale:
            rgb = ImageOps.grayscale(rgb).convert("RGB")
        palette = Image.new("P", (1, 1))
        colors = [QColor(IMAGE_PALETTE[index]).getRgb()[:3] for index in indices]
        palette.putpalette([channel for color in colors for channel in color])
        converted = rgb.quantize(palette=palette, dither=Image.Dither.NONE)
        if self.dither.isChecked():
            # Pull colors toward their nearest swatch before diffusing the residual.
            softened = Image.blend(rgb, converted.convert("RGB"), 0.25)
            converted = softened.quantize(palette=palette, dither=Image.Dither.FLOYDSTEINBERG)
        self.preview.canvas_size = size
        self.preview.pixels = bytes(
            indices[index] if opacity else background
            for index, opacity in zip(converted.tobytes(), alpha.tobytes())
        )
        self.preview.update()
