"""Static DFW-area place table for human-readable rotation readouts.

`compute_readout()` (services/rotation/rotation_core.py) turns a grid cell into
"0.0121 s^-1 near Burleson, 18 km SSW of KFWS" by looking the cell's lat/lon up
in this table. It is deliberately a hardcoded ~30-row tuple rather than a
geocoder call: the rotation service must produce a readout with no dependency
beyond the MRMS fetch, and the annotation is only ever a coarse "near <town>"
hint on top of an NWS warning that already carries the authoritative polygon.

Coverage is the 100 km disc around KFWS (32.5728, -97.3031) plus a little
margin, so every cell the service can report has a name within ~25 km.
Coordinates are town centres to 3 dp (~100 m), which is far finer than the
"near" wording implies.
"""
from typing import Optional, Sequence, Tuple

# (name, lat, lon) — lon negative (the usual -180..180 convention, NOT the
# MRMS grid's 0-360; rotation_core reports cell longitudes in this convention).
DFW_PLACES: Tuple[Tuple[str, float, float], ...] = (
    ("Fort Worth",    32.755, -97.331),
    ("Dallas",        32.777, -96.797),
    ("Arlington",     32.736, -97.108),
    ("Irving",        32.814, -96.949),
    ("Burleson",      32.542, -97.321),
    ("Crowley",       32.579, -97.363),
    ("Cleburne",      32.348, -97.387),
    ("Mansfield",     32.563, -97.142),
    ("Grand Prairie", 32.746, -96.998),
    ("Denton",        33.215, -97.133),
    ("McKinney",      33.198, -96.615),
    ("Plano",         33.020, -96.699),
    ("Frisco",        33.151, -96.824),
    ("Garland",       32.913, -96.639),
    ("Mesquite",      32.767, -96.599),
    ("Waxahachie",    32.386, -96.848),
    ("Weatherford",   32.759, -97.798),
    ("Granbury",      32.442, -97.794),
    ("Mineral Wells", 32.808, -98.113),
    ("Decatur",       33.234, -97.586),
    ("Stephenville",  32.221, -98.202),
    ("Corsicana",     32.095, -96.469),
    ("Greenville",    33.138, -96.111),
    ("Terrell",       32.736, -96.275),
    ("Ennis",         32.329, -96.625),
    ("Benbrook",      32.674, -97.461),
    ("Keller",        32.934, -97.252),
    ("Lewisville",    33.046, -96.994),
    ("Rockwall",      32.931, -96.460),
    ("Midlothian",    32.482, -96.994),
)


def nearest_place(lat: float, lon: float,
                  places: Optional[Sequence[Tuple[str, float, float]]] = None
                  ) -> Optional[str]:
    """Name of the nearest entry in `places` (default DFW_PLACES), or None.

    Equirectangular distance, not haversine: over a 100 km disc the two agree to
    far better than the spacing between towns, and this runs per top-cell.
    """
    import math

    table = DFW_PLACES if places is None else places
    if not table:
        return None
    scale = math.cos(math.radians(lat))
    best_name, best_d2 = None, float("inf")
    for name, plat, plon in table:
        dy = plat - lat
        dx = (plon - lon) * scale
        d2 = dy * dy + dx * dx
        if d2 < best_d2:
            best_name, best_d2 = name, d2
    return best_name
