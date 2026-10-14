"""Quick spatial sanity checks against the built database."""
import sys
import duckdb

con = duckdb.connect(sys.argv[1] if len(sys.argv) > 1 else "data/geoask.duckdb", read_only=True)
con.execute("LOAD spatial;")

q = {
    "state normalization (top US states w/ coords)":
        "SELECT state_code, count(*) n FROM datacenters WHERE country='United States' AND has_coords GROUP BY 1 ORDER BY n DESC LIMIT 6",
    "operator aliasing":
        "SELECT operator, count(*) n FROM datacenters GROUP BY 1 ORDER BY n DESC LIMIT 6",
    "AWS within 50 km of Microsoft (distinct AWS sites)":
        """SELECT count(DISTINCT a.id) FROM datacenters a JOIN datacenters m
           ON a.operator='Amazon AWS' AND m.operator='Microsoft' AND a.has_coords AND m.has_coords
           AND ST_Distance_Sphere(a.geom, m.geom) < 50000""",
    "data centers inside ALPR city bboxes, top cities":
        """SELECT c.city, c.state_code, c.cameras, count(d.id) dcs
           FROM alpr_cities c JOIN datacenters d ON d.has_coords AND ST_Within(d.geom, c.bbox_geom)
           GROUP BY 1,2,3 ORDER BY dcs DESC LIMIT 6""",
}
for title, sql in q.items():
    print(f"\n== {title}")
    for row in con.execute(sql).fetchall():
        print("  ", row)
