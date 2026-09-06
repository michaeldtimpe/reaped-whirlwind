"""services/processor/radar_tools — the colour-scale → value converter.

Uses the real in-repo scale images (services/processor/base_*_intensity_scale.png,
the same files the container bind-mounts to /app) against a small synthetic radar
PNG. The older services/processor/test_converter.py pointed at a
/mnt/user-data/uploads path that does not exist in this repo.
"""
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from services.processor.radar_tools import RadarColorScale, RadarImageConverter

PROCESSOR_DIR = Path(__file__).resolve().parents[1] / "services" / "processor"
REFL_SCALE = PROCESSOR_DIR / "base_reflectivity_intensity_scale.png"
VEL_SCALE = PROCESSOR_DIR / "base_velocity_intensity_scale.png"


@pytest.fixture(scope="module")
def converter():
    assert REFL_SCALE.exists() and VEL_SCALE.exists(), "scale images missing from the repo"
    return RadarImageConverter(str(REFL_SCALE), str(VEL_SCALE))


@pytest.fixture(scope="module")
def scale_colors():
    """Real colours lifted from the reflectivity scale bar, so the KD-tree lookup
    has exact matches to find."""
    img = Image.open(REFL_SCALE).convert("RGB")
    w, h = img.size
    return [img.getpixel((x, h // 2)) for x in range(0, w, w // 16)]


@pytest.fixture
def radar_png(tmp_path, scale_colors):
    """A 32x24 synthetic 'radar image' painted from scale colours."""
    w, h = 32, 24
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    for y in range(h):
        for x in range(w):
            arr[y, x] = scale_colors[(x + y) % len(scale_colors)]
    path = tmp_path / "radar_base_reflectivity_20260527_180000_UTC.png"
    Image.fromarray(arr, mode="RGB").save(path)
    return path


# ---- colour scale -----------------------------------------------------------

def test_reflectivity_scale_spans_the_dbz_range():
    scale = RadarColorScale(str(REFL_SCALE), "reflectivity")
    assert (scale.min_value, scale.max_value) == (-20, 70)
    assert scale.get_units() == "dBZ"
    assert len(scale.color_samples) == Image.open(REFL_SCALE).size[0]


def test_velocity_scale_spans_the_knots_range():
    scale = RadarColorScale(str(VEL_SCALE), "velocity")
    assert (scale.min_value, scale.max_value) == (-100, 100)
    assert scale.get_units() == "knots"


# ---- converter --------------------------------------------------------------

def test_convert_image_metadata_and_shape(converter, radar_png):
    out = converter.convert_image(str(radar_png), "reflectivity", sample_rate=1)
    meta = out["metadata"]

    assert meta["radar_type"] == "reflectivity"
    assert meta["original_dimensions"] == {"width": 32, "height": 24}
    assert meta["sampled_dimensions"] == {"width": 32, "height": 24}
    assert meta["sample_rate"] == 1
    assert meta["units"] == "dBZ"
    assert meta["value_range"] == {"min": -20, "max": 70}
    assert meta["source_file"] == radar_png.name

    data = np.array(out["data"])
    assert data.shape == (24, 32)
    assert data.min() >= -20 and data.max() <= 70


def test_sample_rate_subsamples_both_axes(converter, radar_png):
    out = converter.convert_image(str(radar_png), "reflectivity", sample_rate=4)
    assert out["metadata"]["sampled_dimensions"] == {"width": 8, "height": 6}
    assert np.array(out["data"]).shape == (6, 8)


def test_conversion_is_deterministic(converter, radar_png):
    a = converter.convert_image(str(radar_png), "reflectivity", sample_rate=2)
    b = converter.convert_image(str(radar_png), "reflectivity", sample_rate=2)
    assert a == b


def test_velocity_conversion_uses_the_velocity_scale(converter, radar_png):
    out = converter.convert_image(str(radar_png), "velocity", sample_rate=4)
    assert out["metadata"]["units"] == "knots"
    data = np.array(out["data"])
    assert data.min() >= -100 and data.max() <= 100


def test_exact_scale_colors_map_close_to_their_own_value(converter, scale_colors, tmp_path):
    """A pixel painted with a scale colour must decode near that colour's value —
    otherwise the KD-tree and the scale have drifted apart."""
    img = Image.open(REFL_SCALE).convert("RGB")
    w, h = img.size
    x = w // 3
    color = img.getpixel((x, h // 2))
    expected = -20 + (70 - -20) * (x / w)

    path = tmp_path / "radar_base_reflectivity_solid.png"
    Image.new("RGB", (4, 4), color).save(path)
    data = converter.convert_image(str(path), "reflectivity", sample_rate=1)["data"]
    assert all(abs(v - expected) < 10 for row in data for v in row)


def test_convert_and_save_writes_json_and_npy(converter, radar_png, tmp_path):
    out_path = tmp_path / "out.json"
    converter.convert_and_save(str(radar_png), "reflectivity", str(out_path),
                               sample_rate=2, save_numpy=True)

    payload = json.loads(out_path.read_text())
    assert payload["metadata"]["sample_rate"] == 2
    assert np.array(payload["data"]).shape == (12, 16)
    assert np.load(out_path.with_suffix(".npy")).shape == (12, 16)
