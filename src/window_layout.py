"""One-time adaptive placement of the monitor; the emulator keeps the user's size."""
import os


EMULATOR_ASPECT = 16 / 9
MONITOR_ASPECT = 1280 / 820
TITLE_CHROME = 36


def right_column_layout(work_area):
    """Return non-overlapping top-right and bottom-right outer rectangles.

    Rectangles use Win32 coordinates: ``(left, top, width, height)``.
    """
    left, top, right, bottom = (int(value) for value in work_area)
    width, height = right - left, bottom - top
    if width < 320 or height < 360:
        raise ValueError("Work area is too small for two usable windows")
    margin = max(4, min(10, round(min(width, height) * 0.006)))
    gap = max(6, min(18, round(height * 0.008)))
    available = height - margin * 2 - gap
    aspect_total = 1 / EMULATOR_ASPECT + 1 / MONITOR_ASPECT
    width_for_height = (available - TITLE_CHROME * 2) / aspect_total
    column_width = max(
        320,
        min(900, round(width * 0.42), max(320, int(width_for_height))),
    )
    column_width = min(column_width, width - margin * 2)
    emulator_height = max(160, round(column_width / EMULATOR_ASPECT) + TITLE_CHROME)
    monitor_height = max(180, round(column_width / MONITOR_ASPECT) + TITLE_CHROME)
    total = emulator_height + monitor_height + gap
    if total > available:
        scale = available / total
        emulator_height = max(160, int(emulator_height * scale))
        monitor_height = max(180, int(monitor_height * scale))
    x = right - margin - column_width
    emulator = (x, top + margin, column_width, emulator_height)
    monitor = (
        x,
        bottom - margin - monitor_height,
        column_width,
        monitor_height,
    )
    return emulator, monitor


def apply_default_window_layout(emulator_hwnd, monitor_hwnd, gui=None, api=None):
    """Place the monitor bottom-right once at startup, on the emulator's display.

    The emulator window is never moved or resized: the user sizes it. Capture
    measures its render area every frame, and ADB taps use device coordinates.
    """
    if os.name != "nt":
        return False
    if gui is None:
        import win32gui as gui
    if api is None:
        import win32api as api
    try:
        if (not emulator_hwnd or not monitor_hwnd
                or not gui.IsWindow(emulator_hwnd)
                or not gui.IsWindow(monitor_hwnd)):
            return False
        monitor_handle = api.MonitorFromWindow(emulator_hwnd, 2)
        work = api.GetMonitorInfo(monitor_handle)["Work"]
        _, monitor = right_column_layout(work)
        flags = 0x0004 | 0x0010 | 0x0040  # NOZORDER | NOACTIVATE | SHOWWINDOW
        if gui.IsIconic(monitor_hwnd):
            gui.ShowWindow(monitor_hwnd, 9)
        gui.SetWindowPos(monitor_hwnd, 0, *monitor, flags)
        return True
    except Exception:
        return False
