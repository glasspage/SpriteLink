"""Isolate Qt/WebEngine cold startup from chat."""
import json
import os
import sys
import threading


def _pipe(name, handle, mode):
    stream = getattr(sys, name)
    if stream is not None:
        return getattr(stream, "buffer", stream)
    # Windowed PyInstaller sets stdio to None; QProcess still provides native pipes.
    import ctypes
    from ctypes import wintypes
    import msvcrt
    get_handle = ctypes.windll.kernel32.GetStdHandle
    get_handle.argtypes = [wintypes.DWORD]
    get_handle.restype = wintypes.HANDLE
    descriptor = msvcrt.open_osfhandle(get_handle(handle), os.O_BINARY | mode)
    return os.fdopen(descriptor, "rb" if name == "stdin" else "wb", buffering=0)


def main(memory_name):
    from multiprocessing.shared_memory import SharedMemory
    from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Qt, QTimer, Signal
    from PySide6.QtGui import QImage, QKeyEvent, QMouseEvent, QWheelEvent
    from PySide6.QtWidgets import QApplication
    from spritelink_video import VideoInfo, VideoLink, VideoPlayer, VideoFrameView
    from spritelink_video_process import FRAME_WIDTH, FRAME_HEIGHT

    memory = SharedMemory(name=memory_name)
    if os.name != "nt":
        # Only the owner may unlink this shared mapping.
        from multiprocessing import resource_tracker
        resource_tracker.unregister(memory._name, "shared_memory")
    incoming = _pipe("stdin", -10, os.O_RDONLY)
    outgoing = _pipe("stdout", -11, os.O_WRONLY)
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)

    class Worker(QObject):
        received = Signal(object)

        def __init__(self):
            super().__init__()
            self.generation = None
            self.sequence = 0
            self.awaiting = None
            self.last_frame = None
            self.last_state = None
            self.player = VideoPlayer(use_process=False, mirror_native=True)
            self.player.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
            self.player.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
            self.player.resize(FRAME_WIDTH, FRAME_HEIGHT + 100)
            self.player.show()
            self.received.connect(self.receive)
            self.timer = QTimer(self)
            self.timer.setInterval(16)
            self.timer.timeout.connect(self.publish)
            self.timer.start()

        def write(self, message):
            try:
                outgoing.write((json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8"))
                outgoing.flush()
            except (OSError, BrokenPipeError):
                app.quit()

        def receive(self, message):
            kind = message.get("type")
            if kind == "quit":
                app.quit()
            elif kind == "ack":
                if message.get("sequence") == self.awaiting:
                    self.awaiting = None
            elif kind == "stop":
                self.generation = None
                self.player.stop()
                self.last_frame = self.last_state = None
            elif kind == "load":
                self.generation = message["generation"]
                self.last_frame = self.last_state = None
                try:
                    raw = dict(message["info"])
                    raw["link"] = VideoLink(**raw["link"])
                    self.player.volume.setValue(message["volume"])
                    self.player.load(VideoInfo(**raw))
                except Exception:
                    self.write({"type": "error", "generation": self.generation})
                    self.player.stop()
            elif kind == "command" and message.get("generation") == self.generation:
                self.player._command(message["command"], message.get("value", 0))
            elif kind == "input" and message.get("generation") == self.generation and self.player.web is not None:
                value = message["value"]
                browser = self.player.web
                target = browser.focusProxy() or browser
                modifiers = Qt.KeyboardModifier(value["modifiers"])
                if value["kind"] == "key":
                    event = QKeyEvent(QEvent.Type.KeyPress if value["pressed"] else QEvent.Type.KeyRelease,
                                      value["key"], modifiers, value["text"])
                elif value["kind"] == "wheel":
                    point = QPointF(value["x"] * browser.width(), value["y"] * browser.height())
                    event = QWheelEvent(point, point, QPoint(*value["pixel"]), QPoint(*value["angle"]),
                                        Qt.MouseButton(value["buttons"]), modifiers,
                                        Qt.ScrollPhase(value["phase"]), value["inverted"])
                else:
                    types = {"press": QEvent.Type.MouseButtonPress, "release": QEvent.Type.MouseButtonRelease,
                             "move": QEvent.Type.MouseMove, "double": QEvent.Type.MouseButtonDblClick}
                    point = QPointF(value["x"] * browser.width(), value["y"] * browser.height())
                    event = QMouseEvent(types[value["kind"]], point, point,
                                        Qt.MouseButton(value["button"]), Qt.MouseButton(value["buttons"]), modifiers)
                QApplication.sendEvent(target, event)

        def publish(self):
            player = self.player
            if self.generation is None:
                return
            state = {"ready": player._ready, "playing": player._playing,
                     "ended": player._ended, "muted": player._muted,
                     "duration": player._duration, "position": player.seek.value() * player._duration / 10000,
                     "volume": player.volume.value()}
            if player.surface.currentWidget() is player.status and player.status.text() != "Loading...":
                state["error"] = player.status.text()
            if state != self.last_state:
                self.write({"type": "state", "generation": self.generation, "state": state})
                self.last_state = state
            view = player.video_widget
            if self.awaiting is not None or not isinstance(view, VideoFrameView) or view.frame.isNull():
                return
            key = view.frame.cacheKey()
            if key == self.last_frame:
                return
            image = view.frame
            if image.width() > FRAME_WIDTH or image.height() > FRAME_HEIGHT:
                image = image.scaled(FRAME_WIDTH, FRAME_HEIGHT, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)
            image = image.convertToFormat(QImage.Format.Format_RGBA8888)
            size = image.sizeInBytes()
            memory.buf[:size] = image.constBits()[:size]
            self.sequence += 1
            self.awaiting = self.sequence
            self.last_frame = key
            self.write({"type": "frame", "generation": self.generation, "sequence": self.sequence,
                        "width": image.width(), "height": image.height()})

    worker = Worker()

    def read():
        try:
            for line in incoming:
                if len(line) > 256 * 1024:
                    break
                try:
                    message = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if isinstance(message, dict):
                    worker.received.emit(message)
        finally:
            worker.received.emit({"type": "quit"})

    threading.Thread(target=read, daemon=True).start()
    try:
        app.exec()
    finally:
        worker.player.stop()
        memory.close()


if __name__ == "__main__":
    main(sys.argv[-1])
