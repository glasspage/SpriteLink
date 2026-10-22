"""Isolated video playback with acknowledged shared frames."""
import json
from multiprocessing.shared_memory import SharedMemory
from pathlib import Path
import sys

from PySide6.QtCore import QObject, QProcess, Signal
from PySide6.QtGui import QImage


FRAME_WIDTH = 1920
FRAME_HEIGHT = 1080
FRAME_BYTES = FRAME_WIDTH * FRAME_HEIGHT * 4


def worker_arguments(memory_name):
    arguments = ["--spritelink-video-worker", memory_name]
    if not getattr(sys, "frozen", False):
        arguments.insert(0, str(Path(__file__).with_name("spritelink_video_worker.py")))
    return arguments


class VideoProcess(QObject):
    frame_ready = Signal(object)
    state_ready = Signal(object)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.readyReadStandardError.connect(self.process.readAllStandardError)
        self.process.errorOccurred.connect(self._error)
        self.process.finished.connect(self._finished)
        self.process.started.connect(self._flush)
        self.memory = None
        self.generation = None
        self._input = bytearray()
        self._pending = []
        self._closing = False

    def load(self, info, volume, generation):
        from dataclasses import asdict
        self.generation = generation
        if self.process.state() == QProcess.ProcessState.NotRunning:
            self._closing = False
            self._input.clear()
            self._close_memory()
            self.memory = SharedMemory(create=True, size=FRAME_BYTES)
            self.process.setProgram(sys.executable)
            self.process.setArguments(worker_arguments(self.memory.name))
            self.process.start()
        self.send({"type": "load", "info": asdict(info), "volume": volume, "generation": generation})

    def send(self, message):
        if self._closing:
            return
        payload = (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")
        if self.process.state() == QProcess.ProcessState.Starting:
            self._pending.append(payload)
        elif self.process.state() == QProcess.ProcessState.Running:
            self.process.write(payload)

    def _flush(self):
        for payload in self._pending:
            self.process.write(payload)
        self._pending.clear()

    def command(self, command, value=0):
        self.send({"type": "command", "command": command, "value": value, "generation": self.generation})

    def stop(self):
        self.generation = None
        self.send({"type": "stop"})

    def _read(self):
        self._input.extend(bytes(self.process.readAllStandardOutput()))
        if len(self._input) > 256 * 1024:
            self._error(QProcess.ProcessError.ReadError)
            self.shutdown()
            return
        while b"\n" in self._input:
            raw, _, remaining = self._input.partition(b"\n")
            self._input = bytearray(remaining)
            try:
                message = json.loads(raw)
            except (ValueError, UnicodeError):
                continue
            if not isinstance(message, dict):
                continue
            kind = message.get("type")
            current = self.generation is not None and message.get("generation") == self.generation
            if kind == "frame":
                try:
                    width, height = int(message["width"]), int(message["height"])
                    if current and self.memory is not None and 0 < width <= FRAME_WIDTH and 0 < height <= FRAME_HEIGHT:
                        # Copy pixels before acknowledging so the helper cannot overwrite them.
                        image = QImage(self.memory.buf, width, height, width * 4,
                                       QImage.Format.Format_RGBA8888).copy()
                        self.frame_ready.emit(image)
                except (KeyError, TypeError, ValueError):
                    pass
                self.send({"type": "ack", "sequence": message.get("sequence")})
            elif current and kind == "state":
                self.state_ready.emit(message.get("state"))
            elif current and kind == "error":
                self.failed.emit("Video player unavailable. Open in Browser to watch.")

    def _error(self, error):
        if not self._closing and self.generation is not None:
            self.failed.emit("Video player unavailable. Open in Browser to watch.")
        if error == QProcess.ProcessError.FailedToStart:
            self._pending.clear()
            self._close_memory()

    def _finished(self, *args):
        self._error(None)
        self._pending.clear()
        self._close_memory()

    def _close_memory(self):
        if self.memory is not None:
            self.memory.close()
            try:
                self.memory.unlink()
            except FileNotFoundError:
                pass
            self.memory = None

    def shutdown(self):
        self._closing = True
        self.generation = None
        self._pending.clear()
        self.process.kill()
        self._close_memory()
