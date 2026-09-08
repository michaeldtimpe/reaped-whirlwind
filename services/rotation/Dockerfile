FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# No torch here — this service decodes an operational NOAA GRIB2 product.
# `eccodes` pulls the `eccodeslib` manylinux wheel with the C library bundled,
# so no apt libeccodes0 is needed (docs/MRMS_MIGRATION.md §1 "Decode").
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /tmp/requirements.txt

# /app is bind-mounted to ./services/rotation at runtime (read-only) and holds
# rotation_service.py, rotation_core.py and mrms_fetch.py.
# /srv/reaped/common is bind-mounted too; the service puts its PARENT
# ("/srv/reaped") on sys.path at startup so it imports as the `common` package —
# a dedicated parent dir, so no other top-level dir becomes importable.
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

EXPOSE 9010

CMD ["python", "/app/rotation_service.py"]
