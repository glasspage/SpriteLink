"""Keep HTTP dependencies out of the first-window import path."""

import importlib
import threading


class LazyRequests:
    def __init__(self):
        self._module = None
        self._lock = threading.Lock()

    def __getattr__(self, name):
        with self._lock:
            if self._module is None:
                self._module = importlib.import_module("requests")
            module = self._module
        return getattr(module, name)


requests = LazyRequests()
