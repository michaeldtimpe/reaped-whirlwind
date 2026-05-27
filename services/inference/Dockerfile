FROM python:3.12-slim

# torch CPU wheels are large; layer ordering keeps the deps cached across code edits.
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install CPU-only torch from the official index to skip CUDA bloat.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.4.1 \
    && pip install --no-cache-dir -r /tmp/requirements.txt

# /app is bind-mounted to ./services/inference at runtime (read-only).
# /data-tools and /ml come in as bind-mounts so we import the shared iem.py and preprocess.py.
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/data-tools:/ml:/app

EXPOSE 9008

CMD ["python", "/app/inference_service.py"]
