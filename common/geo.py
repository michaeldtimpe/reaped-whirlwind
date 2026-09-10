"""Point-in-polygon for NWS alert geometry — pure Python, no shapely.

The alerting service uses this to say whether the strongest MRMS rotation cell
lies inside a Tornado Warning's polygon (docs/MRMS_MIGRATION.md §2.2). GeoJSON
coordinates are [lon, lat]; callers pass (lat, lon) like everything else in
this repo.

Return contract of `point_in_geometry`:
  True   — inside (a point exactly on an edge counts as inside)
  False  — outside
  None   — no usable polygon (geometry null / not a Polygon or MultiPolygon /
           malformed). The readout prints "polygon unavailable" for None;
           it must never be collapsed into False.
"""
from typing import Any, Optional, Sequence

_EPS = 1e-12


def _on_segment(x: float, y: float, x1: float, y1: float, x2: float, y2: float) -> bool:
    """True when (x, y) lies on the closed segment (x1,y1)-(x2,y2)."""
    cross = (x - x1) * (y2 - y1) - (y - y1) * (x2 - x1)
    if abs(cross) > _EPS:
        return False
    return (min(x1, x2) - _EPS <= x <= max(x1, x2) + _EPS
            and min(y1, y2) - _EPS <= y <= max(y1, y2) + _EPS)


def point_in_ring(lat: float, lon: float, ring: Sequence[Sequence[float]]) -> bool:
    """Even-odd ray cast against one linear ring of [lon, lat] pairs. The ring
    may or may not repeat its first vertex at the end. Edge points are inside."""
    pts = [(float(p[0]), float(p[1])) for p in ring if len(p) >= 2]
    if len(pts) < 3:
        return False
    if pts[0] == pts[-1]:
        pts = pts[:-1]
    x, y = float(lon), float(lat)
    inside = False
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        if _on_segment(x, y, x1, y1, x2, y2):
            return True
        if (y1 > y) != (y2 > y):
            x_cross = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < x_cross:
                inside = not inside
    return inside


def point_in_polygon(lat: float, lon: float, rings: Sequence[Sequence[Sequence[float]]]) -> bool:
    """GeoJSON Polygon coordinates: ring 0 is the exterior, the rest are holes."""
    if not rings or not point_in_ring(lat, lon, rings[0]):
        return False
    for hole in rings[1:]:
        if point_in_ring(lat, lon, hole):
            return False
    return True


def point_in_geometry(lat: Optional[float], lon: Optional[float],
                      geometry: Optional[Any]) -> Optional[bool]:
    """See the module docstring for the True / False / None contract."""
    if lat is None or lon is None or not isinstance(geometry, dict):
        return None
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if not isinstance(coords, (list, tuple)) or not coords:
        return None
    try:
        if gtype == "Polygon":
            return point_in_polygon(lat, lon, coords)
        if gtype == "MultiPolygon":
            return any(point_in_polygon(lat, lon, poly) for poly in (coords or []))
    except (TypeError, ValueError, IndexError):
        return None
    return None
