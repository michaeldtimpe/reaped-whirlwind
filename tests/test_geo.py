"""common/geo.py — the polygon check behind the "inside the warning polygon"
line of the Tornado Warning readout."""
from common import geo

# A concave "C" shape around Fort Worth, [lon, lat] like GeoJSON.
C_SHAPE = [[-97.60, 32.40], [-97.00, 32.40], [-97.00, 32.55], [-97.40, 32.55],
           [-97.40, 32.75], [-97.00, 32.75], [-97.00, 32.90], [-97.60, 32.90],
           [-97.60, 32.40]]
POLY = {"type": "Polygon", "coordinates": [C_SHAPE]}


def test_point_inside_convex_part():
    assert geo.point_in_geometry(32.45, -97.30, POLY) is True


def test_point_in_the_concave_notch_is_outside():
    # Inside the bounding box, but in the C's mouth.
    assert geo.point_in_geometry(32.65, -97.20, POLY) is False


def test_point_clearly_outside():
    assert geo.point_in_geometry(33.50, -97.30, POLY) is False


def test_point_on_an_edge_counts_as_inside():
    assert geo.point_in_geometry(32.40, -97.30, POLY) is True    # bottom edge
    assert geo.point_in_geometry(32.55, -97.20, POLY) is True    # inner edge of the notch


def test_vertex_counts_as_inside():
    assert geo.point_in_geometry(32.40, -97.60, POLY) is True


def test_unclosed_ring_is_accepted():
    open_ring = {"type": "Polygon", "coordinates": [C_SHAPE[:-1]]}
    assert geo.point_in_geometry(32.45, -97.30, open_ring) is True


def test_hole_excludes_points():
    hole = [[-97.50, 32.42], [-97.10, 32.42], [-97.10, 32.50], [-97.50, 32.50], [-97.50, 32.42]]
    with_hole = {"type": "Polygon", "coordinates": [C_SHAPE, hole]}
    assert geo.point_in_geometry(32.45, -97.30, with_hole) is False
    assert geo.point_in_geometry(32.41, -97.05, with_hole) is True


def test_multipolygon_any_part():
    far = [[-96.20, 33.10], [-96.00, 33.10], [-96.00, 33.30], [-96.20, 33.30], [-96.20, 33.10]]
    multi = {"type": "MultiPolygon", "coordinates": [[C_SHAPE], [far]]}
    assert geo.point_in_geometry(33.20, -96.10, multi) is True
    assert geo.point_in_geometry(33.20, -96.50, multi) is False


def test_missing_geometry_is_none_not_false():
    assert geo.point_in_geometry(32.45, -97.30, None) is None
    assert geo.point_in_geometry(32.45, -97.30, {"type": "Point", "coordinates": [-97.3, 32.45]}) is None
    assert geo.point_in_geometry(None, -97.30, POLY) is None
    assert geo.point_in_geometry(32.45, -97.30, {"type": "Polygon", "coordinates": None}) is None


def test_degenerate_ring_is_outside():
    assert geo.point_in_geometry(32.45, -97.30, {"type": "Polygon", "coordinates": [[[-97.3, 32.4]]]}) is False
