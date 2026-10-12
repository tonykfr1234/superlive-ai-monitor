"""Normalized rectangular or polygonal detection regions.

An unset region covers the whole frame. Explicit polygons are simple (no
crossing or touching non-neighbouring edges), may be concave, and may use either
winding. No geometry package, motion, or tracking prerequisites are needed.
"""
import math


def _cross(a, b, c):
    return (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])


def _signed_area_twice(vertices):
    if len(vertices) < 3:
        return 0.0
    # Anchor the sum to a vertex to avoid cancellation for small distant shapes.
    return math.fsum(_cross(vertices[0], vertices[i], vertices[i+1])
                     for i in range(1, len(vertices)-1))


def _has_positive_area(vertices, source_vertices):
    if len(vertices) < 3:
        return False
    area = abs(_signed_area_twice(vertices))
    scale = max(abs(value) for point in source_vertices for value in point)
    coordinate_error = math.ulp(scale)
    xs, ys = zip(*vertices)
    extent = max(max(xs)-min(xs), max(ys)-min(ys))
    # Clipping a decimal diagonal exactly at a box corner can leave a triangle
    # a few representable coordinates wide. Bound that arithmetic noise using
    # coordinate precision and the *clipped* extent, without a fixed minimum
    # region size that would discard small, real intersections.
    tolerance = 32 * len(vertices) * coordinate_error * max(extent, coordinate_error)
    return area > tolerance


def _on_segment(a, b, point):
    return (_cross(a, b, point) == 0
            and min(a[0], b[0]) <= point[0] <= max(a[0], b[0])
            and min(a[1], b[1]) <= point[1] <= max(a[1], b[1]))


def _segments_intersect(a, b, c, d):
    ac, ad, ca, cb = _cross(a, b, c), _cross(a, b, d), _cross(c, d, a), _cross(c, d, b)
    if ((ac > 0 > ad or ad > 0 > ac) and (ca > 0 > cb or cb > 0 > ca)):
        return True
    return ((ac == 0 and _on_segment(a, b, c))
            or (ad == 0 and _on_segment(a, b, d))
            or (ca == 0 and _on_segment(c, d, a))
            or (cb == 0 and _on_segment(c, d, b)))


def _coordinate(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("roi coordinates must be finite numbers between 0 and 1") from error
    if not math.isfinite(number) or not 0 <= number <= 1:
        raise ValueError("roi coordinates must be finite and between 0 and 1")
    return number


def validate_roi(roi):
    """Return detached coordinates, preserving legacy rectangle representation."""
    if roi is None or roi == []:
        return None
    if not isinstance(roi, (list, tuple)):
        raise ValueError("roi must be null, [left, top, right, bottom], or [[x, y], ...]")
    if len(roi) == 4 and not any(isinstance(value, (list, tuple)) for value in roi):
        values = [_coordinate(value) for value in roi]
        if values[0] >= values[2] or values[1] >= values[3]:
            raise ValueError("roi must have positive width and height")
        return values
    if len(roi) < 3 or not all(isinstance(point, (list, tuple)) and len(point) == 2 for point in roi):
        raise ValueError("polygon roi must contain at least three [x, y] vertices")
    vertices = [[_coordinate(x), _coordinate(y)] for x, y in roi]
    if len({tuple(point) for point in vertices}) != len(vertices):
        raise ValueError("polygon roi must not repeat vertices")
    count = len(vertices)
    for i, vertex in enumerate(vertices):
        previous, following = vertices[i-1], vertices[(i+1) % count]
        if (_cross(previous, vertex, following) == 0
                and ((previous[0]-vertex[0])*(following[0]-vertex[0])
                     + (previous[1]-vertex[1])*(following[1]-vertex[1])) > 0):
            raise ValueError("polygon roi edges must not overlap")
        for j in range(i+1, count):
            if j == i+1 or (i == 0 and j == count-1):
                continue  # Adjacent edges share their one common endpoint.
            if _segments_intersect(vertex, following, vertices[j], vertices[(j+1) % count]):
                raise ValueError("polygon roi must not have crossing or touching edges")
    if _signed_area_twice(vertices) == 0:
        raise ValueError("polygon roi must have positive area")
    return vertices


def _vertices(roi):
    if isinstance(roi[0], list):
        return roi
    left, top, right, bottom = roi
    return [[left, top], [right, top], [right, bottom], [left, bottom]]


def roi_vertices(roi):
    """Return normalized polygon vertices for either ROI format, or None."""
    roi = validate_roi(roi)
    return None if roi is None else _vertices(roi)


def pixel_roi(roi, width, height):
    """Return the bounding rectangle; use box_in_roi for actual containment."""
    roi = validate_roi(roi)
    if roi is None:
        return (0, 0, width, height)
    if isinstance(roi[0], list):
        xs, ys = zip(*roi)
        return (min(xs)*width, min(ys)*height, max(xs)*width, max(ys)*height)
    return (roi[0] * width, roi[1] * height, roi[2] * width, roi[3] * height)


def _clip_polygon(vertices, rectangle):
    """Clip to an axis-aligned rectangle, retaining winding and concavity.

    A disconnected result can include retraced boundary segments. Its signed
    area is still the intersection area, but validate_roi must accept it before
    it can be saved as a single polygon.
    """
    left, top, right, bottom = rectangle
    result = vertices
    for axis, limit, keep_greater in ((0, left, True), (0, right, False),
                                      (1, top, True), (1, bottom, False)):
        if not result:
            break
        source, result = result, []
        previous = source[-1]
        previous_inside = previous[axis] >= limit if keep_greater else previous[axis] <= limit
        for current in source:
            current_inside = current[axis] >= limit if keep_greater else current[axis] <= limit
            if current_inside != previous_inside:
                fraction = (limit-previous[axis]) / (current[axis]-previous[axis])
                other = 1-axis
                crossing = [0.0, 0.0]
                crossing[axis] = limit
                crossing[other] = previous[other] + fraction*(current[other]-previous[other])
                result.append(crossing)
            if current_inside:
                result.append(list(current))
            previous, previous_inside = current, current_inside
        # Boundary vertices can be emitted once by each incident edge.
        result = [point for i, point in enumerate(result) if point != result[i-1]]
    return result


def _without_straight_vertices(vertices):
    """Remove redundant middle vertices without erasing retraced edges."""
    result = vertices
    while len(result) >= 3:
        simplified = [point for i, point in enumerate(result)
                      if not _on_segment(result[i-1], result[(i+1) % len(result)], point)]
        if len(simplified) == len(result):
            break
        result = simplified
    return result


def rebase_roi(roi, parent_client_rect, content_rect):
    """Preserve an old client-relative ROI when capturing camera content only.

    Both rectangles use absolute (left, top, right, bottom) coordinates. Clip
    the selected area to camera content before normalizing it to that content.
    An explicit selection with no remaining area is an error, never full frame.
    """
    def rectangle(value, name):
        if not isinstance(value, (list, tuple)) or len(value) != 4:
            raise ValueError("%s must contain left, top, right, bottom" % name)
        try:
            left, top, right, bottom = map(float, value)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("%s coordinates must be finite numbers" % name) from error
        if (not all(math.isfinite(v) for v in (left, top, right, bottom))
                or not all(math.isfinite(v) for v in (right-left, bottom-top))
                or right <= left or bottom <= top):
            raise ValueError("%s must have finite coordinates and positive dimensions" % name)
        return left, top, right, bottom

    parent_left, parent_top, parent_right, parent_bottom = rectangle(parent_client_rect, "parent_client_rect")
    content_left, content_top, content_right, content_bottom = rectangle(content_rect, "content_rect")
    roi = validate_roi(roi)
    if roi is None:
        return None
    parent_width, parent_height = parent_right-parent_left, parent_bottom-parent_top
    content_width, content_height = content_right-content_left, content_bottom-content_top
    if isinstance(roi[0], list):
        vertices = [[(parent_left+x*parent_width-content_left)/content_width,
                     (parent_top+y*parent_height-content_top)/content_height] for x, y in roi]
        if not all(math.isfinite(value) for point in vertices for value in point):
            raise ValueError("Existing ROI cannot be rebased; select a new region")
        clipped = _without_straight_vertices(_clip_polygon(vertices, (0, 0, 1, 1)))
        if not _has_positive_area(clipped, vertices):
            raise ValueError("Existing ROI is outside camera content; select a new region")
        try:
            return validate_roi(clipped)
        except ValueError as error:
            raise ValueError("Existing ROI is split by camera content; select a new region") from error
    left = max(content_left, parent_left + roi[0]*parent_width)
    top = max(content_top, parent_top + roi[1]*parent_height)
    right = min(content_right, parent_left + roi[2]*parent_width)
    bottom = min(content_bottom, parent_top + roi[3]*parent_height)
    if right <= left or bottom <= top:
        raise ValueError("Existing ROI is outside camera content; select a new region")
    return validate_roi([
        min(1.0, max(0.0, (left-content_left)/content_width)),
        min(1.0, max(0.0, (top-content_top)/content_height)),
        min(1.0, max(0.0, (right-content_left)/content_width)),
        min(1.0, max(0.0, (bottom-content_top)/content_height)),
    ])


def box_in_roi(box, roi, width, height):
    """Any positive-area person-box overlap counts; mere edge contact does not."""
    roi = validate_roi(roi)
    x1, y1, x2, y2 = map(float, box)
    if not all(math.isfinite(value) for value in (x1, y1, x2, y2)) or x2 <= x1 or y2 <= y1:
        return False
    if roi is not None and isinstance(roi[0], list):
        vertices = [[x*width, y*height] for x, y in roi]
        overlap = _clip_polygon(vertices, (x1, y1, x2, y2))
        return _has_positive_area(overlap, vertices)
    left, top, right, bottom = pixel_roi(roi, width, height)
    return min(right, x2) > max(left, x1) and min(bottom, y2) > max(top, y1)


def selection_roi(selection, width, height):
    x, y, w, h = selection
    if w <= 0 or h <= 0:
        return None
    return validate_roi([x / width, y / height, (x + w) / width, (y + h) / height])


def people_in_region(boxes, classes, confidences, roi, width, height, minimum_confidence):
    people = []
    for box, cls, confidence in zip(boxes, classes, confidences):
        if int(cls) == 0 and float(confidence) >= minimum_confidence and box_in_roi(box, roi, width, height):
            people.append({"box": tuple(map(int, box)), "confidence": float(confidence)})
    return people
