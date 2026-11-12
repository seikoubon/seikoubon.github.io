"""Adapter logic only. No real Windows APIs or real keyboard/mouse are used."""
import os
import threading
from types import SimpleNamespace

import pytest

from capture_windows import WindowsCapture
from core import Cancelled


class FakeUser32:
    def __init__(self, hwnd=12, pid=9999999, key=0):
        self.hwnd, self.pid, self.key = hwnd, pid, key
    def GetForegroundWindow(self):
        return self.hwnd
    def GetWindowThreadProcessId(self, hwnd, pointer):
        pointer._obj.value = self.pid
    def GetAsyncKeyState(self, key):
        assert key == 0x77
        return self.key


def adapter(user32):
    obj = WindowsCapture.__new__(WindowsCapture)
    obj.cancel = threading.Event()
    obj.finished = threading.Event()
    obj.user32 = user32
    obj.pg = SimpleNamespace(failSafeCheck=lambda: None, FailSafeException=RuntimeError)
    return obj


def test_adapter_rejects_own_gui_process():
    obj = adapter(FakeUser32(pid=os.getpid()))
    with pytest.raises(ValueError, match='最前面'):
        obj.target()


def test_adapter_accepts_target_and_stops_on_change():
    obj = adapter(FakeUser32())
    target = obj.target()
    obj.check_target(target)
    obj.user32.hwnd = 99
    with pytest.raises(Cancelled):
        obj.check_target(target)


def test_adapter_f8_sets_cancel():
    obj = adapter(FakeUser32(key=0x8000))
    thread = threading.Thread(target=obj._monitor)
    thread.start()
    try:
        assert obj.cancel.wait(1)
    finally:
        obj.finished.set()
        thread.join(timeout=1)
    assert not thread.is_alive()


@pytest.mark.parametrize('direction', ['right', 'left'])
def test_adapter_sends_selected_arrow(direction):
    obj = adapter(FakeUser32())
    keys = []
    obj.pg.press = keys.append
    obj.turn_page(direction)
    assert keys == [direction]
