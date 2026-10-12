"""Native Windows preview/taskbar identity; harmless on other platforms."""
import ctypes
import os
from pathlib import Path

_icons = []


def set_app_identity():
    if os.name == "nt":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("SuperLive.AI.Monitor")


def apply_window_icon(title):
    if os.name != "nt":
        return False
    from ctypes import wintypes
    user = ctypes.windll.user32
    user.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user.FindWindowW.restype = wintypes.HWND
    user.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                               ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user.LoadImageW.restype = wintypes.HANDLE
    user.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
        wintypes.LPARAM, wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
    user.SendMessageTimeoutW.restype = ctypes.c_ssize_t
    hwnd = user.FindWindowW(None, title)
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    owner = wintypes.DWORD()
    user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
    icon = Path(__file__).resolve().parent.parent / "assets" / "superlive-ai.ico"
    if not hwnd or owner.value != os.getpid() or not icon.is_file():
        return False
    for kind, size in ((0, 16), (1, 48)):
        handle = user.LoadImageW(None, str(icon), 1, size, size, 0x10)
        if handle:
            result = ctypes.c_size_t()
            user.SendMessageTimeoutW(hwnd, 0x80, kind, handle, 2, 500, ctypes.byref(result))
            _icons.append(handle)  # HICON must live as long as the window.
    return bool(_icons)
