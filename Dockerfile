# GeoAsk on Hugging Face Spaces (Docker SDK). Port 7860 is the Spaces default.
FROM python:3.12-slim

WORKDIR /app
# rasterio's bundled GDAL links against libexpat, which the slim image lacks.
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 \
 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
# Build the DuckDB database from the raw source files at image build time.
RUN python etl/build_db.py --raw data/raw --out data/geoask.duckdb \
 && python -c "import duckdb; c=duckdb.connect('data/geoask.duckdb', read_only=True); c.execute('LOAD spatial'); print(c.execute('select count(*) from datacenters').fetchone())"

ENV PORT=7860
EXPOSE 7860
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
