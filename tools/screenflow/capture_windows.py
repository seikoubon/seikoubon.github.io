"""Windows-only desktop adapter. Imported lazily: no desktop access on module import."""
import ctypes
from ctypes import wintypes
import os
import threading
import time

from core import Cancelled, check_cancel


class WindowsCapture:
    def __init__(self, cancel):
        if os.name != "nt":
            raise RuntimeError("実際の画面撮影はWindows 11で実行してください。")
        import pyautogui
        self.pg = pyautogui
        self.pg.FAILSAFE = True
        self.pg.PAUSE = 0.05
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self.user32.GetAsyncKeyState.restype = ctypes.c_short
        self.cancel = cancel
        self.finished = threading.Event()
        # F8 polling continues even while a screenshot/PNG save is running.
        self.monitor = threading.Thread(target=self._monitor, daemon=True)
        self.monitor.start()

    def _monitor(self):
        while not self.finished.wait(0.025):
            if self.user32.GetAsyncKeyState(0x77) & 0x8000:  # F8
                self.cancel.set()

    def close(self):
        self.finished.set()
        self.monitor.join(timeout=1)

    def guard(self):
        check_cancel(self.cancel)
        try:
            self.pg.failSafeCheck()
        except self.pg.FailSafeException as exc:
            raise Cancelled("マウスを画面の隅へ移動したため停止しました。") from exc

    def target(self):
        self.guard()
        hwnd = self.user32.GetForegroundWindow()
        pid = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not hwnd or not pid.value or pid.value == os.getpid():
            raise ValueError("撮影対象を最前面にできませんでした。準備時間を増やしてください。")
        return hwnd

    def check_target(self, target):
        self.guard()
        if self.user32.GetForegroundWindow() != target:
            raise Cancelled("最前面のウィンドウが変わったため停止しました。保存済み画像は保持されています。")

    def wait(self, seconds, cancel):
        end = time.monotonic() + seconds
        while True:
            self.guard()
            left = end - time.monotonic()
            if left <= 0:
                return
            cancel.wait(min(left, 0.05))

    def screenshot(self):
        self.guard()
        return self.pg.screenshot()  # Primary display only, as documented in README.

    def turn_page(self, direction):
        if direction not in {"right", "left"}:
            raise ValueError("ページ送り方向が不正です。")
        self.guard()
        self.pg.press(direction)
