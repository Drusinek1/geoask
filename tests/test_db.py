import pytest

from geoask.db import GeoDB, QueryError


@pytest.fixture(scope="module")
def db():
    return GeoDB()


@pytest.mark.parametrize("sql", [
    "DROP TABLE datacenters",
    "DELETE FROM datacenters",
    "SELECT 1; SELECT 2",
    "INSTALL httpfs",
    "COPY datacenters TO 'x.csv'",
    "ATTACH 'other.db'",
    "",
])
def test_rejects_non_readonly(db, sql):
    with pytest.raises(QueryError):
        db.run_sql(sql)


def test_basic_count(db):
    r = db.run_sql("SELECT count(*) AS n FROM datacenters")
    assert r.rows[0][0] == 18110


def test_row_cap(db):
    r = db.run_sql("SELECT id FROM datacenters")
    assert r.row_count == 2000 and r.truncated


def test_geometry_to_geojson(db):
    r = db.run_sql("SELECT name, geom FROM datacenters WHERE has_coords LIMIT 3")
    assert r.geometry_columns == ["geom"]
    gj = db.to_geojson(r)
    assert len(gj["features"]) == 3
    assert gj["features"][0]["geometry"]["type"] == "Point"
    assert "name" in gj["features"][0]["properties"]


def test_latlon_to_geojson(db):
    r = db.run_sql("SELECT city, latitude, longitude FROM alpr_cities LIMIT 2")
    gj = db.to_geojson(r)
    assert len(gj["features"]) == 2
    assert "latitude" not in gj["features"][0]["properties"]


def test_spatial_function(db):
    r = db.run_sql("""
        SELECT count(*) FROM datacenters d JOIN alpr_cities c
        ON d.has_coords AND ST_Within(d.geom, c.bbox_geom) AND c.city='Dallas'
    """)
    assert r.rows[0][0] > 50


def test_schema_text(db):
    s = db.schema_text()
    assert "TABLE datacenters" in s and "geom: GEOMETRY" in s
