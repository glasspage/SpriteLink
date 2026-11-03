"""Indexed drawing composer and image import controls."""
from collections import deque
import math

from PIL import Image, ImageChops, ImageOps
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from spritelink_image_codec import IMAGE_PALETTES, IMAGE_SIZES


PALETTE_ORDER = (0, 3, 2, 4, 1)
PALETTE_NAMES = ("Basic", "Grayscale", "Nature", "Pastel", "Sunset")
BASIC_SWATCH_ORDER = (0, 7, 1, 2, 3, 4, 5, 6)
GRAYSCALE_SWATCH_ORDER = (0, 2, 3, 4, 5, 6, 7, 1)
REVERSED_SWATCH_ORDER = tuple(range(7, -1, -1))
SWATCH_ORDERS = (
    BASIC_SWATCH_ORDER, GRAYSCALE_SWATCH_ORDER, REVERSED_SWATCH_ORDER,
    BASIC_SWATCH_ORDER, REVERSED_SWATCH_ORDER,
)
# Shared slots start with shadow and highlight, then the six middle shades.
REVERSED_PALETTE_SLOTS = (7, 0, 6, 5, 4, 3, 2, 1)
PALETTE_SLOTS = (
    tuple(range(8)), tuple(range(8)), REVERSED_PALETTE_SLOTS,
    tuple(range(8)), REVERSED_PALETTE_SLOTS,
)
PALETTE_BACKGROUNDS = (1, 1, 0, 1, 0)
COLOR_TABLES = [[QColor(color).rgb() for color in palette] for palette in IMAGE_PALETTES]
BACKGROUND_NAMES = (
    ("Black", "White", "Red", "Yellow", "Green", "Blue", "Purple", "Gray"),
    ("Black", "White", "14% gray", "29% gray", "43% gray", "57% gray", "71% gray", "86% gray"),
    ("Cream", "Sky blue", "Blue", "Light green", "Green", "Tan", "Brown", "Dark green"),
    ("Charcoal", "White", "Pink", "Yellow", "Green", "Blue", "Lavender", "Gray"),
    ("Pale yellow", "Amber", "Coral", "Rose", "Lavender", "Slate blue", "Indigo", "Midnight blue"),
)
def palette_translation(source: int, target: int) -> bytes:
    old_slots = PALETTE_SLOTS[source]
    new_slots = PALETTE_SLOTS[target]
    return bytes(new_slots[old_slots.index(index)] for index in range(8)) + bytes(range(8, 256))


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


def drawing_image(size: int, pixels: bytes, palette: int = 0) -> QImage:
    width, height = IMAGE_SIZES[size]
    # Qt pads indexed rows to four-byte boundaries.
    stride = (width + 3) & ~3
    data = b"".join(pixels[i:i + width].ljust(stride, b"\0")
                    for i in range(0, len(pixels), width))
    image = QImage(data, width, height, stride, QImage.Format.Format_Indexed8)
    image.setColorTable(COLOR_TABLES[palette])
    return image.copy()


class PixelView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.canvas_size = 0
        self.palette_index = 0
        self.pixels = bytearray()
        self.grid = False
        self.selection = QRect()
        self.setMinimumSize(92, 92)

    def sizeHint(self) -> QSize:
        return QSize(272, 182)

    def image_size(self) -> tuple[int, int]:
        return IMAGE_SIZES[self.canvas_size]

    def image_rect(self) -> QRectF:
        width, height = self.image_size()
        scale = min((self.width() - 2) / width, (self.height() - 2) / height)
        return QRectF(
            (self.width() - width * scale) / 2, (self.height() - height * scale) / 2,
            width * scale, height * scale,
        )

    def preview_image(self) -> QImage:
        return drawing_image(self.canvas_size, self.pixels, self.palette_index) if self.pixels else QImage()

    def paintEvent(self, event) -> None:
        image = self.preview_image()
        if image.isNull():
            return
        rect = self.image_rect()
        width, height = self.image_size()
        scale = rect.width() / width
        painter = QPainter(self)
        painter.drawImage(rect, image)
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
    selectionFinished = Signal()

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

    def snapshot(self) -> tuple[int, bytes, bool, int]:
        return self.canvas_size, bytes(self.pixels), self.edited, self.palette_index

    def restore(self, state) -> None:
        self.canvas_size, pixels, self.edited, self.palette_index = state
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
        self.restore((self.canvas_size, bytes([PALETTE_BACKGROUNDS[self.palette_index]]) * (width * height), False, self.palette_index))

    def replace(self, size: int, pixels: bytes, *, edited: bool = True, palette: int | None = None) -> None:
        self.undo_stack.append(self.snapshot())
        self.redo_stack.clear()
        self.restore((size, pixels, edited, self.palette_index if palette is None else palette))

    def resize_canvas(self, size: int) -> None:
        if size == self.canvas_size:
            return
        width, height = IMAGE_SIZES[size]
        old_width, old_height = IMAGE_SIZES[self.canvas_size]
        dx, dy = (width - old_width) // 2, (height - old_height) // 2
        pixels = bytes(
            self.pixels[(y - dy) * old_width + x - dx]
            if 0 <= x - dx < old_width and 0 <= y - dy < old_height else PALETTE_BACKGROUNDS[self.palette_index]
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
        slot = self.colors[self._primary ^ bool(PATTERNS[self.pattern](x, y))]
        return PALETTE_SLOTS[self.palette_index][slot]

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
            self.pixels[start:start + rect.width()] = bytes([PALETTE_BACKGROUNDS[self.palette_index]]) * rect.width()
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
        if self.tool in ("Pencil", "Line"):
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
        elif self.tool == "Line":
            self.pixels[:] = self._before[1]
            self._line(self._start, point)
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
        if self.tool == "Select":
            self.selectionFinished.emit()
        self.changed.emit()


class ColorButton(QPushButton):
    colorPicked = Signal(int)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self.colorPicked.emit(1)
            event.accept()
        else:
            super().mousePressEvent(event)


def preview_icon(pixels: bytes, palette: int) -> QIcon:
    image = QImage(pixels, 16, 16, 16, QImage.Format.Format_Indexed8)
    image.setColorTable(COLOR_TABLES[palette])
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
        self.undo = QPushButton("Undo")
        self.redo = QPushButton("Redo")
        self.undo.clicked.connect(lambda: self.canvas.step_history(self.canvas.undo_stack, self.canvas.redo_stack))
        self.redo.clicked.connect(lambda: self.canvas.step_history(self.canvas.redo_stack, self.canvas.undo_stack))
        for keys, button in (("Ctrl+Z", self.undo), ("Ctrl+Y", self.redo)):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(button.click)
        for label in ("Pencil", "Line", "Bucket", "Select", "Move"):
            if label == "Select":
                tools.addStretch(1)
                tools.addWidget(self.undo)
                tools.addWidget(self.redo)
                tools.addStretch(1)
            button = QPushButton(label)
            button.setCheckable(True)
            button.setChecked(label == "Pencil")
            button.clicked.connect(lambda _checked, name=label: setattr(self.canvas, "tool", name))
            tool_group.addButton(button)
            tools.addWidget(button)
            if label == "Move":
                self.canvas.selectionFinished.connect(button.click)
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
        self._shown_palette = None
        self.swatch_buttons = []
        for index in range(8):
            button = ColorButton()
            button.setFixedSize(25, 24)
            button.clicked.connect(lambda _checked, value=index: self._pick_color(self.active_color, value))
            button.colorPicked.connect(lambda slot, value=index: self._pick_color(slot, value))
            swatches.addWidget(button, index // 4, index % 4)
            self.swatch_buttons.append(button)
        palette.addLayout(swatches)
        self.palette_combo = combo_type()
        self.palette_combo.setAccessibleName("Color palette")
        self.palette_combo.addItems([f"{PALETTE_NAMES[index]} Palette" for index in PALETTE_ORDER])
        self.palette_combo.currentIndexChanged.connect(self._select_palette)
        palette.addWidget(self.palette_combo)
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
        row.addSpacing(5)

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
        self.preview_scale_combo.addItems(("1x", "2x", "3x", "Max"))
        self.preview_scale_combo.setCurrentIndex(3)
        self.preview_scale_combo.activated.connect(self._select_preview_scale)
        self._preferred_preview_scale = None
        self.canvas.resized.connect(self._update_preview_scales)
        controls.addWidget(self.preview_scale_combo)
        grid = QCheckBox("Grid")
        grid.setChecked(self.canvas.grid)
        grid.toggled.connect(self._set_grid)
        controls.addWidget(grid)
        import_button = QPushButton("Import...")
        import_button.clicked.connect(self.importRequested)
        clear = QPushButton("Clear")
        clear.clicked.connect(self._clear)
        for button in (import_button, clear):
            button.setStyleSheet("QPushButton { padding-left: 6px; padding-right: 6px; }")
        controls.addStretch()
        buttons = QHBoxLayout()
        buttons.setSpacing(3)
        buttons.addWidget(import_button)
        buttons.addWidget(clear)
        controls.addLayout(buttons)
        row.addLayout(controls)
        self.canvas.changed.connect(self._canvas_changed)
        self._canvas_changed()

    def _set_grid(self, enabled: bool) -> None:
        self.canvas.grid = enabled
        self.canvas.update()

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        scale = getattr(self, "_preferred_preview_scale", None)
        if scale is not None:
            _, height = self.canvas.image_size()
            margins = self.layout().contentsMargins()
            canvas_height = height * scale + 2 + margins.top() + margins.bottom() + 2 * self.frameWidth()
            hint.setHeight(max(self.minimumSizeHint().height(), canvas_height))
        return hint

    def _select_preview_scale(self, index: int) -> None:
        self._preferred_preview_scale = None if index == 3 else index + 1
        policy = self.sizePolicy()
        policy.setVerticalPolicy(
            QSizePolicy.Policy.Preferred if self._preferred_preview_scale is None
            else QSizePolicy.Policy.Maximum
        )
        self.setSizePolicy(policy)
        self.updateGeometry()
        self._update_preview_scales()

    def _update_preview_scales(self) -> None:
        self.canvas.preview_scale = self._preferred_preview_scale or self.canvas.maximum_scale()
        self.canvas.update()

    def _clear(self) -> None:
        if self.canvas.edited:
            self.canvas.replace(
                self.canvas.canvas_size, bytes([PALETTE_BACKGROUNDS[self.canvas.palette_index]]) * len(self.canvas.pixels), edited=False,
            )

    def _select_palette(self, choice: int) -> None:
        index = PALETTE_ORDER[choice]
        canvas = self.canvas
        if index != canvas.palette_index:
            translation = palette_translation(canvas.palette_index, index)
            pixels = bytes(canvas.pixels).translate(translation)
            canvas.replace(canvas.canvas_size, pixels, edited=canvas.edited, palette=index)

    def _canvas_changed(self) -> None:
        previous = self.palette_combo.blockSignals(True)
        self.palette_combo.setCurrentIndex(PALETTE_ORDER.index(self.canvas.palette_index))
        self.palette_combo.blockSignals(previous)
        if self._shown_palette != self.canvas.palette_index:
            self._update_colors()
        previous = self.size_combo.blockSignals(True)
        self.size_combo.setCurrentIndex(self.canvas.canvas_size)
        self.size_combo.blockSignals(previous)
        self.undo.setEnabled(bool(self.canvas.undo_stack))
        self.redo.setEnabled(bool(self.canvas.redo_stack))
        self.updateGeometry()
        self._update_preview_scales()
        self.changed.emit()

    def _swap_colors(self) -> None:
        self.canvas.colors.reverse()
        self._update_colors()

    def _pick_color(self, slot: int, value: int) -> None:
        palette = self.canvas.palette_index
        color = SWATCH_ORDERS[palette][value]
        self.canvas.colors[slot] = PALETTE_SLOTS[palette].index(color)
        self._update_colors()

    def _update_colors(self) -> None:
        palette = self.canvas.palette_index
        if self._shown_palette != palette:
            self._shown_palette = palette
            for index, button in zip(SWATCH_ORDERS[palette], self.swatch_buttons):
                color = IMAGE_PALETTES[palette][index]
                button.setToolTip(color)
                button.setAccessibleName(color)
                button.setIcon(preview_icon(bytes([index]) * 256, palette))
        active_colors = [PALETTE_SLOTS[palette][slot] for slot in self.canvas.colors]
        for button, color in zip(self.slots, active_colors):
            button.setIcon(preview_icon(bytes([color]) * 256, palette))
        first, second = active_colors
        for button, pattern in zip(self.pattern_buttons, PATTERNS):
            button.setIcon(preview_icon(bytes(
                second if pattern(x, y) else first for y in range(16) for x in range(16)
            ), palette))


class ImportPreview(PixelView):
    transformed = Signal()
    zoomRequested = Signal(str)

    def __init__(self, icon_size: int | None = None) -> None:
        super().__init__()
        self.icon_size = icon_size
        self.icon_frame = QImage()
        self.pan = QPointF()
        self._last = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def image_size(self) -> tuple[int, int]:
        if self.icon_size is not None:
            return self.icon_size, self.icon_size
        return super().image_size()

    def preview_image(self) -> QImage:
        return self.icon_frame if self.icon_size is not None else super().preview_image()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._last = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event) -> None:
        if self._last is not None:
            scale = self.image_rect().width() / self.image_size()[0]
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
    accepted = Signal(int, bytes, int)
    iconAccepted = Signal(bytes)
    dismissed = Signal()

    def __init__(
        self, source: Image.Image, combo_type, *, icon_size: int | None = None, icon_encoder=None,
    ) -> None:
        super().__init__()
        self.setObjectName("configPanel")
        self.source = source
        self._reduced_source = source
        self._reduction = 1
        self._icon_encoder = icon_encoder
        self._icon_data = b""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)
        layout.addWidget(QLabel("Import profile icon" if icon_size is not None else "Import image"))
        self.preview = ImportPreview(icon_size)
        self.preview.transformed.connect(self.refresh)
        self.preview.zoomRequested.connect(self.zoom_by)
        layout.addWidget(self.preview, 1)
        settings = QHBoxLayout()
        if icon_size is None:
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
            self.mode.addItems([f"{PALETTE_NAMES[index]} Palette" for index in PALETTE_ORDER])
            self.mode.setAccessibleName("Conversion")
            settings.addWidget(self.mode)
            self.background = combo_type()
            self.background.setAccessibleName("Background")
            order = SWATCH_ORDERS[0]
            self.background.addItems([f"{BACKGROUND_NAMES[0][index]} background" for index in order])
            self.background.setCurrentIndex(order.index(PALETTE_BACKGROUNDS[0]))
            settings.addWidget(self.background)
        self.dither = QCheckBox("Dither")
        settings.addWidget(self.dither)
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
        accept = QPushButton("Use Icon" if icon_size is not None else "Use Image")
        accept.clicked.connect(self._accept)
        buttons.addWidget(accept)
        layout.addLayout(buttons)
        self.preset = "Fill"
        if icon_size is None:
            self.size_combo.currentIndexChanged.connect(lambda _index: self.set_zoom(self.preset))
            self.mode.currentIndexChanged.connect(self._update_import_palette)
            self.background.currentIndexChanged.connect(self.refresh)
        self.dither.toggled.connect(self.refresh)
        self.set_zoom("Fill")
        if icon_size is None:
            self.mode.setCurrentIndex(PALETTE_ORDER.index(self._best_palette()))

    def _accept(self) -> None:
        if self._icon_encoder is not None:
            self.iconAccepted.emit(self._icon_data)
        else:
            self.accepted.emit(self.preview.canvas_size, bytes(self.preview.pixels), self.preview.palette_index)

    def _target_size(self) -> tuple[int, int]:
        if self.preview.icon_size is not None:
            return self.preview.image_size()
        return IMAGE_SIZES[self.size_combo.currentIndex()]

    def _update_import_palette(self, choice: int) -> None:
        index = PALETTE_ORDER[choice]
        old_palette = self.preview.palette_index
        old_background = SWATCH_ORDERS[old_palette][self.background.currentIndex()]
        background = palette_translation(old_palette, index)[old_background]
        order = SWATCH_ORDERS[index]
        blocked = self.background.blockSignals(True)
        for position, color in enumerate(order):
            self.background.setItemText(position, f"{BACKGROUND_NAMES[index][color]} background")
        self.background.setCurrentIndex(order.index(background))
        self.background.blockSignals(blocked)
        self.refresh()

    def set_zoom(self, preset: str) -> None:
        self.preset = preset
        width, height = self._target_size()
        scales = width / self.source.width, height / self.source.height
        self.zoom = {"Fit": min(scales), "Fill": max(scales), "Pixel": 1.0}[preset]
        self.preview.pan = QPointF()
        self.refresh()

    def zoom_by(self, direction: str) -> None:
        scale = 1.25 if direction == "+" else 0.8
        min_zoom = 1 / max(self.source.size)
        zoom = max(min_zoom, min(128, self.zoom * scale))
        self.preview.pan *= zoom / self.zoom
        self.zoom = zoom
        self.refresh()

    def _sample_image(self) -> Image.Image:
        width, height = self._target_size()
        # Prefilter large downscales before sampling the cropped preview.
        reduction = max(1, min(min(self.source.size), int(0.5 / self.zoom)))
        if reduction != self._reduction:
            self._reduction = reduction
            self._reduced_source = self.source.reduce(reduction) if reduction > 1 else self.source
        inv = 1 / self.zoom
        pan = self.preview.pan
        return self._reduced_source.transform(
            (width, height), Image.Transform.AFFINE,
            (inv / reduction, 0, ((self.source.width - width * inv) / 2 - pan.x() * inv) / reduction,
             0, inv / reduction, ((self.source.height - height * inv) / 2 - pan.y() * inv) / reduction),
            resample=Image.Resampling.NEAREST if self.zoom == 1 else Image.Resampling.BICUBIC,
            fillcolor=(0, 0, 0, 0),
        )

    @staticmethod
    def _quantize(rgb: Image.Image, index: int, dither: bool = False) -> Image.Image:
        if index == 1:
            rgb = ImageOps.grayscale(rgb).convert("RGB")
        palette = Image.new("P", (1, 1))
        colors = [QColor(color).getRgb()[:3] for color in IMAGE_PALETTES[index]]
        palette.putpalette([channel for color in colors for channel in color])
        converted = rgb.quantize(palette=palette, dither=Image.Dither.NONE)
        if dither:
            # Pull colors toward their nearest swatch before diffusing the residual.
            softened = Image.blend(rgb, converted.convert("RGB"), 0.25)
            converted = softened.quantize(palette=palette, dither=Image.Dither.FLOYDSTEINBERG)
        return converted

    def _best_palette(self) -> int:
        image = self._sample_image()
        rgb = image.convert("RGB")
        alpha = image.getchannel("A")

        def error(index: int) -> int:
            converted = self._quantize(rgb, index).convert("RGB")
            difference = ImageChops.difference(rgb, converted)
            return sum(
                (level % 256) ** 2 * count
                for level, count in enumerate(difference.histogram(mask=alpha))
            )

        # A dense gray ramp can win on shade accuracy while losing visible color.
        chroma_squared = 0
        visible_weight = 0
        channels = (channel.tobytes() for channel in rgb.split())
        for red, green, blue, opacity in zip(*channels, alpha.tobytes()):
            spread = max(red, green, blue) - min(red, green, blue)
            chroma_squared += opacity * spread ** 2
            visible_weight += opacity
        near_grayscale = chroma_squared <= visible_weight * 16 ** 2
        candidates = (index for index in PALETTE_ORDER if index != 1 or near_grayscale)
        return min(candidates, key=error)

    def refresh(self) -> None:
        image = self._sample_image()
        if self._icon_encoder is not None:
            self._icon_data = self._icon_encoder(image, self.dither.isChecked())
            self.preview.icon_frame = QImage.fromData(self._icon_data)
            self.preview.update()
            return
        palette_index = PALETTE_ORDER[self.mode.currentIndex()]
        background = SWATCH_ORDERS[palette_index][self.background.currentIndex()]
        alpha = image.getchannel("A")
        rgb = Image.new("RGB", image.size, IMAGE_PALETTES[palette_index][background])
        rgb.paste(image.convert("RGB"), mask=alpha)
        converted = self._quantize(rgb, palette_index, self.dither.isChecked())
        self.preview.canvas_size = self.size_combo.currentIndex()
        self.preview.palette_index = palette_index
        self.preview.pixels = bytes(
            index if opacity else background
            for index, opacity in zip(converted.tobytes(), alpha.tobytes())
        )
        self.preview.update()
