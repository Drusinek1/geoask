import pytest

from geoask.raster import LandCover


@pytest.fixture(scope="module")
def lc():
    return LandCover()


def test_coverage_is_dc_area(lc):
    c = lc.coverage
    assert -78 < c.west < -77 and -77 < c.east < -76
    assert 38 < c.south < 39 and 39 < c.north < 40


def test_national_mall_is_developed(lc):
    s = lc.summary(38.8895, -77.0353, 300)
    assert s["groups"]["Developed"] > 50
    assert abs(sum(c["pct"] for c in s["classes"]) - 100) < 1.5
    assert s["radius_m"] == 300 and s["year"] == 2021


def test_potomac_is_water(lc):
    # Mid-river, Potomac below Alexandria.
    s = lc.summary(38.78, -77.03, 200)
    assert s["groups"].get("Water", 0) > 50


def test_outside_coverage_raises(lc):
    with pytest.raises(ValueError, match="outside"):
        lc.summary(29.76, -95.37)


def test_radius_clamped(lc):
    assert lc.summary(38.8895, -77.0353, 10)["radius_m"] == 30
    assert lc.summary(38.8895, -77.0353, 99999)["radius_m"] == 5000
