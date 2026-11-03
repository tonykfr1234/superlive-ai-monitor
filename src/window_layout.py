"""One-time adaptive right-column layout for the emulator and monitor."""
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


def fit_render_aspect(hwnd, rectangle, gui, aspect=EMULATOR_ASPECT):
    """Resize so the emulator's render area, not its outer frame, is 16:9.

    MEmu adds a title bar and a side toolbar; sizing the outer window to 16:9
    leaves dark bands beside and below the camera picture. Returns ``None``
    when the render area cannot be measured (for example on MuMu).
    """
    from capture_region import memu_render_rect
    try:
        render = memu_render_rect(hwnd, gui)
        if not render:
            return None
        left, top, right, bottom = gui.GetWindowRect(hwnd)
        chrome_width = (right - left) - (render[2] - render[0])
        chrome_height = (bottom - top) - (render[3] - render[1])
    except Exception:
        return None
    x, y, width, height = rectangle
    if not (0 <= chrome_width < width and 0 <= chrome_height < height):
        return None
    inner_width = width - chrome_width
    inner_height = round(inner_width / aspect)
    if inner_height + chrome_height > height:
        inner_height = height - chrome_height
        inner_width = round(inner_height * aspect)
    fitted_width, fitted_height = inner_width + chrome_width, inner_height + chrome_height
    # Keep the window right-aligned in the column.
    return (x + width - fitted_width, y, fitted_width, fitted_height)


def apply_default_window_layout(emulator_hwnd, monitor_hwnd, gui=None, api=None):
    """Place MEmu top-right and the monitor bottom-right once at startup."""
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
        emulator, monitor = right_column_layout(work)
        flags = 0x0004 | 0x0010 | 0x0040  # NOZORDER | NOACTIVATE | SHOWWINDOW
        if gui.IsIconic(emulator_hwnd):
            gui.ShowWindow(emulator_hwnd, 9)
        # Measure the frame before moving: MEmu lays out its render child
        # asynchronously, so sizes read right after SetWindowPos are stale.
        emulator = fit_render_aspect(emulator_hwnd, emulator, gui) or emulator
        for hwnd, rectangle in ((emulator_hwnd, emulator), (monitor_hwnd, monitor)):
            if gui.IsIconic(hwnd):
                gui.ShowWindow(hwnd, 9)
            gui.SetWindowPos(hwnd, 0, *rectangle, flags)
        return True
    except Exception:
        return False
