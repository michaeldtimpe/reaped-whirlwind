"""ml.preprocess.decode_crop — the transform the inference service and training
share. This is the train/serve bit-equivalence guarantee, so the test asserts
shape, dtype, palette-index semantics and determinism on synthetic PNGs.

ml/preprocess.py itself is deliberately NOT touched by these tests.
"""
import numpy as np
import pytest
from PIL import Image

from common.nws import KFWS_LAT, KFWS_LON
from ml.preprocess import BOX_KM, OUT, PREPROCESS_VERSION, REFL, VEL, decode_crop, load_wld


# A world file whose pixel size (0.01 deg) makes the 120 km box land at 128 px,
# fully inside a 400x400 image centred near KFWS.
WLD_TEXT = "0.0100000000\n0.0000000000\n0.0000000000\n-0.0100000000\n-98.0000000000\n34.0000000000\n"
SIZE = 400


def write_paletted_png(path, index_array):
    """IEM RIDGE PNGs are paletted; decode_crop reads the raw palette INDEX."""
    img = Image.fromarray(index_array.astype(np.uint8), mode="P")
    # Palette content is irrelevant to decode_crop — only the index matters.
    img.putpalette([(i * 7) % 256 for i in range(768)])
    img.save(path)
    return path


@pytest.fixture
def wld(tmp_path):
    p = tmp_path / "KFWS.wld"
    p.write_text(WLD_TEXT)
    return load_wld(p)


def test_load_wld_reads_the_four_georeference_terms(wld):
    A, E, C, F = wld
    assert (A, E, C, F) == (0.01, -0.01, -98.0, 34.0)


def test_decode_crop_returns_the_canonical_shape_and_dtype(tmp_path, wld):
    png = write_paletted_png(tmp_path / "refl.png",
                             np.full((SIZE, SIZE), 128, dtype=np.uint8))
    out, in_bounds = decode_crop(png, KFWS_LAT, KFWS_LON, wld, REFL)

    assert out.shape == (OUT, OUT) == (128, 128)
    assert out.dtype == np.float32
    assert in_bounds == pytest.approx(1.0)      # crop entirely inside the image
    assert 0.0 <= out.min() and out.max() <= 1.0


def test_decode_crop_is_deterministic(tmp_path, wld):
    rng = np.random.default_rng(20260527)
    png = write_paletted_png(tmp_path / "refl.png",
                             rng.integers(0, 256, size=(SIZE, SIZE), dtype=np.uint8))
    a, ib_a = decode_crop(png, KFWS_LAT, KFWS_LON, wld, REFL)
    b, ib_b = decode_crop(png, KFWS_LAT, KFWS_LON, wld, REFL)

    assert np.array_equal(a, b)                 # bit-equal, not merely close
    assert ib_a == ib_b


def test_reflectivity_normalises_by_the_palette_index(tmp_path, wld):
    png = write_paletted_png(tmp_path / "refl.png",
                             np.full((SIZE, SIZE), 255, dtype=np.uint8))
    out, _ = decode_crop(png, KFWS_LAT, KFWS_LON, wld, REFL)
    assert out == pytest.approx(np.ones((OUT, OUT), dtype=np.float32))


def test_reflectivity_index_zero_is_missing(tmp_path, wld):
    png = write_paletted_png(tmp_path / "refl.png", np.zeros((SIZE, SIZE), dtype=np.uint8))
    out, _ = decode_crop(png, KFWS_LAT, KFWS_LON, wld, REFL)
    assert not out.any()


def test_velocity_masks_range_folded_pixels(tmp_path, wld):
    """N0S index 15 is range-folding, not a 15-knot bin — it must decode to 0."""
    folded = write_paletted_png(tmp_path / "fold.png",
                                np.full((SIZE, SIZE), 15, dtype=np.uint8))
    out, _ = decode_crop(folded, KFWS_LAT, KFWS_LON, wld, VEL)
    assert not out.any()

    real = write_paletted_png(tmp_path / "vel.png",
                              np.full((SIZE, SIZE), 14, dtype=np.uint8))
    out, _ = decode_crop(real, KFWS_LAT, KFWS_LON, wld, VEL)
    assert out == pytest.approx(np.full((OUT, OUT), 14 / 15, dtype=np.float32), abs=1e-6)


def test_out_of_bounds_crop_is_zero_padded_and_reported(tmp_path, wld):
    """Events near the edge of a scan still produce a full-size tensor."""
    png = write_paletted_png(tmp_path / "refl.png",
                             np.full((SIZE, SIZE), 200, dtype=np.uint8))
    # Far north-west of the image origin, so most of the box falls outside.
    out, in_bounds = decode_crop(png, 33.98, -97.98, wld, REFL)
    assert out.shape == (OUT, OUT)
    assert 0.0 < in_bounds < 1.0


def test_the_two_channels_stack_the_way_the_service_builds_its_tensor(tmp_path, wld):
    refl = write_paletted_png(tmp_path / "n0b.png",
                              np.full((SIZE, SIZE), 100, dtype=np.uint8))
    vel = write_paletted_png(tmp_path / "n0s.png",
                             np.full((SIZE, SIZE), 10, dtype=np.uint8))
    rch, _ = decode_crop(refl, KFWS_LAT, KFWS_LON, wld, REFL)
    vch, _ = decode_crop(vel, KFWS_LAT, KFWS_LON, wld, VEL)
    arr = np.stack([rch, vch]).astype(np.float32)
    assert arr.shape == (2, OUT, OUT)
    assert arr.dtype == np.float32


def test_preprocess_version_is_pinned():
    """The inference service refuses to start when this drifts from the model
    manifest — so a bump here is always a deliberate, model-invalidating change."""
    assert PREPROCESS_VERSION == "1.0"
    assert (OUT, BOX_KM) == (128, 120.0)
