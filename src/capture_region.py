"""Capture MuMu's camera below its top toolbar, without trimming camera overlays.

No desktop APIs are imported here. Callers provide their existing capture and
Win32 dependencies; the resolver can also be tested on synthetic images.
"""
from dataclasses import dataclass


class CaptureRegionUnavailable(RuntimeError):
    """The camera boundary is uncertain; do not silently include the toolbar."""


@dataclass(frozen=True)
class CaptureInfo:
    parent_client_rect: tuple
    content_rect: tuple
    method: str
    cached: bool = False


def _client_rect(hwnd, gui):
    left, top = gui.ClientToScreen(hwnd, (0, 0))
    x1, y1, x2, y2 = gui.GetClientRect(hwnd)
    return (int(left), int(top), int(left+x2-x1), int(top+y2-y1))


def _render_top(hwnd, gui, parent):
    """A distinct, dominant render child provides its camera's top boundary."""
    choices = []
    left, top, right, bottom = parent
    width, height = right-left, bottom-top
    def candidate(child, _):
        try:
            if not gui.IsWindowVisible(child):
                return
            name = (gui.GetClassName(child) + " " + gui.GetWindowText(child)).lower()
            if not any(marker in name for marker in ("render", "opengl", "surface", "swapchain", "vulkan", "egl")):
                return
            raw = _client_rect(child, gui)
            clipped = (max(left, raw[0]), max(top, raw[1]), min(right, raw[2]), min(bottom, raw[3]))
            cw, ch = clipped[2]-clipped[0], clipped[3]-clipped[1]
            removed = clipped[1]-top
            # A full-parent render widget may itself include MuMu chrome. Only
            # a distinct top inset is sufficient evidence to remove that bar.
            if cw < width*0.65 or ch < height*0.55 or not max(3, height*0.01) <= removed <= height*0.20:
                return
            choices.append((cw*ch, int(removed)))
        except Exception:
            return
    try:
        gui.EnumChildWindows(hwnd, candidate, None)
    except Exception:
        return None
    return max(choices)[1] if choices else None


def _column_groups(mask):
    groups = []
    start = None
    for index, active in enumerate(mask):
        if active and start is None:
            start = index
        elif not active and start is not None:
            groups.append((start, index))
            start = None
    if start is not None:
        groups.append((start, len(mask)))
    return groups


def detect_toolbar_top(frame, np):
    """Recognize a pale flat toolbar with distributed icons and a strong edge.

    We only examine the top 20%. Camera labels/timestamps and the bottom of the
    image are never independently cropped. An ambiguous image fails closed.
    """
    height, width = frame.shape[:2]
    if height < 100 or width < 160:
        raise CaptureRegionUnavailable("MuMu image is too small to identify its toolbar")
    margin = max(2, int(width*0.015))
    rgb = frame[:, margin:width-margin, :3].astype("float32")
    gray = rgb.mean(axis=2)
    start = max(2, int(height*0.006))
    minimum = max(12, int(height*0.035))
    maximum = int(height*0.20)
    background = float(np.median(gray[start:max(start+2, minimum)]))
    if background < 165:
        raise CaptureRegionUnavailable("No recognizable pale MuMu toolbar")
    background_rows = (np.abs(gray-background) <= 20).mean(axis=1)
    candidates = []
    for boundary in range(minimum, maximum+1):
        # Last toolbar row -> first non-toolbar row. A separator can be skipped
        # below, but this prevents selecting unrelated edges inside the scene.
        if background_rows[boundary-1] < 0.67 or background_rows[boundary] > 0.40:
            continue
        band = gray[start:boundary]
        if band.size == 0 or float((np.abs(band-background) <= 20).mean()) < 0.72:
            continue
        row_medians = np.median(band, axis=1)
        if float(np.percentile(row_medians, 90)-np.percentile(row_medians, 10)) > 25:
            continue
        chroma = rgb[start:boundary].max(axis=2)-rgb[start:boundary].min(axis=2)
        if float(np.median(chroma)) > 22:
            continue
        dark = band < background-45
        if not 0.002 <= float(dark.mean()) <= 0.18:
            continue
        # Real chrome has small icon/text islands spread horizontally; a bright
        # natural sky or a featureless wall alone is not enough evidence.
        groups = [(a, b) for a, b in _column_groups(dark.mean(axis=0) > 0.04)
                  if max(2, width*0.003) <= b-a <= width*0.20]
        if len(groups) < 3 or groups[-1][1]-groups[0][0] < width*0.45:
            continue
        below = gray[boundary:min(height, boundary+max(8, int(height*0.045)))]
        above = gray[max(start, boundary-3):boundary]
        below_median = float(np.median(below))
        contrast = abs(float(np.median(above))-below_median)
        texture = float(np.percentile(below, 90)-np.percentile(below, 10))
        # Recognized pale chrome can sit over a nearly black night feed. Its
        # distributed icons plus very strong dark boundary remain evidence even
        # when there is no useful camera texture at startup.
        dark_camera = below_median < 60 and contrast > 100
        if contrast < 28 or (texture < 28 and not dark_camera):
            continue
        top = boundary
        # MuMu can draw a thin cyan separator. Remove only that nearly full-width
        # colored line; never discard the camera's white channel label below it.
        for _ in range(max(1, int(height*0.006))):
            row = rgb[top]
            if float(((row.max(axis=1)-row.min(axis=1)) > 45).mean()) < 0.75:
                break
            top += 1
        if top <= maximum:
            candidates.append((contrast, top))
    if not candidates:
        raise CaptureRegionUnavailable("Cannot reliably distinguish MuMu top toolbar from camera image")
    return max(candidates)[1]


class CaptureRegionResolver:
    """Cache a confirmed boundary while the same window/chrome remains visible."""
    def __init__(self):
        self.key = None
        self.top = None
        self.method = None
        self.chrome_reference = None

    def clear(self):
        self.key = self.top = self.method = self.chrome_reference = None

    def _signature(self, frame, top, np):
        # The camera below the toolbar is excluded, so a dark/night scene does
        # not move the boundary. Resampling tolerates tiny toolbar icon changes.
        band = frame[max(0, int(top*0.15)):max(1, int(top*0.85)), :, :3]
        return np.median(band, axis=(0, 1))

    def _boundary_unchanged(self, frame, top, signature, np):
        """Check the actual edge, not only the appearance of the toolbar above it.

        MuMu can resize its own toolbar without resizing its outer client. A
        local pale-to-camera transition must still land on the cached crop row.
        This also catches one-pixel changes and preserves a dark camera feed.
        """
        height, width = frame.shape[:2]
        margin = max(2, int(width*0.015))
        radius = max(6, int(height*0.025))
        start, end = max(1, top-radius), min(height-1, top+radius)
        strip = frame[start-1:end+1, margin:width-margin, :3].astype("float32")
        pale = (np.abs(strip-signature).max(axis=2) <= 24).mean(axis=1)
        for offset in range(1, len(pale)):
            if pale[offset-1] < 0.67 or pale[offset] > 0.40:
                continue
            boundary = start-1+offset
            for _ in range(max(1, int(height*0.006))):
                row = frame[boundary, margin:width-margin, :3].astype("float32")
                if float(((row.max(axis=1)-row.min(axis=1)) > 45).mean()) < 0.75:
                    break
                boundary += 1
            if boundary == top:
                return True
        return False

    def resolve(self, frame, hwnd, parent_client_rect, gui, np):
        height, width = frame.shape[:2]
        key = (hwnd, width, height)
        if self.key == key and self.top is not None:
            if self.method == "render_child":
                unchanged = _render_top(hwnd, gui, parent_client_rect) == self.top
            else:
                signature = self._signature(frame, self.top, np)
                # A fullscreen/theme/layout change replaces the former chrome band.
                band = frame[max(1, int(self.top*0.15)):max(2, int(self.top*0.85)), :, :3].astype("float32")
                flat_fraction = float((np.abs(band-signature).max(axis=2) < 24).mean())
                unchanged = (float(np.abs(signature-self.chrome_reference).max()) < 20
                             and flat_fraction >= 0.65
                             and self._boundary_unchanged(frame, self.top, signature, np))
            if unchanged:
                left, top, right, bottom = parent_client_rect
                return CaptureInfo(parent_client_rect, (left, top+self.top, right, bottom), self.method, True)
        self.clear()
        removed = _render_top(hwnd, gui, parent_client_rect)
        method = "render_child"
        if removed is None:
            removed = detect_toolbar_top(frame, np)
            method = "toolbar_detection"
        if not 0 < removed < height:
            raise CaptureRegionUnavailable("Invalid MuMu camera boundary")
        self.key, self.top, self.method = key, removed, method
        self.chrome_reference = self._signature(frame, removed, np)
        left, top, right, bottom = parent_client_rect
        return CaptureInfo(parent_client_rect, (left, top+removed, right, bottom), method)


def capture_frame(sct, hwnd, win32gui, cv2, np, resolver=None):
    """Return camera BGR pixels plus absolute source/content rectangles."""
    parent = _client_rect(hwnd, win32gui)
    render = memu_render_rect(hwnd, win32gui)
    if render is not None:
        left, top, right, bottom = render
        shot = sct.grab(dict(left=left, top=top, width=right-left, height=bottom-top))
        frame = cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)
        if frame.shape[:2] != (bottom-top, right-left):
            raise CaptureRegionUnavailable("MEmu capture dimensions changed during capture")
        return frame, CaptureInfo(parent, render, "memu_render_child")
    left, top, right, bottom = parent
    if right-left < 160 or bottom-top < 100:
        raise CaptureRegionUnavailable("MuMu client area is too small")
    shot = sct.grab(dict(left=left, top=top, width=right-left, height=bottom-top))
    frame = cv2.cvtColor(np.array(shot), cv2.COLOR_BGRA2BGR)
    if frame.shape[1] != right-left or frame.shape[0] != bottom-top:
        raise CaptureRegionUnavailable("MuMu capture dimensions changed during capture")
    info = (resolver or CaptureRegionResolver()).resolve(frame, hwnd, parent, win32gui, np)
    removed = info.content_rect[1]-parent[1]
    return frame[removed:, :, :].copy(), info


def find_emulator_window(gui, processes, psutil):
    """Select the largest supported visible window, with stable HWND tie-break."""
    choices = []
    def candidate(hwnd, _):
        try:
            if not gui.IsWindowVisible(hwnd):
                return
            _, pid = processes.GetWindowThreadProcessId(hwnd)
            if psutil.Process(pid).name().lower() not in ("mumunxdevice.exe", "memu.exe"):
                return
            left, top, right, bottom = gui.GetWindowRect(hwnd)
            if gui.IsIconic(hwnd) or (right-left >= 200 and bottom-top >= 180):
                choices.append(((right-left)*(bottom-top), hwnd))
        except Exception:
            pass
    gui.EnumWindows(candidate, None)
    return max(choices)[1] if choices else None


def memu_render_rect(hwnd, gui):
    """MEmu's named render widget excludes its title bar and side controls."""
    choices = []
    def candidate(child, _):
        try:
            if gui.IsWindowVisible(child) and gui.GetWindowText(child) == "RenderWindowWindow":
                rect = _client_rect(child, gui)
                width, height = rect[2]-rect[0], rect[3]-rect[1]
                if width >= 160 and height >= 100:
                    choices.append((width*height, rect))
        except Exception:
            pass
    try:
        gui.EnumChildWindows(hwnd, candidate, None)
    except Exception:
        pass
    return max(choices)[1] if choices else None
