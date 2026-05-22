FROM python:3.12-slim

WORKDIR /app

# Install docker-compose (v1, standalone) for rebuild commands.
# We talk to the Docker daemon directly via Unix socket REST API for
# status queries — no docker CLI needed (avoids API version mismatches).
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    curl -SL "https://github.com/docker/compose/releases/download/v2.24.7/docker-compose-linux-$(uname -m)" \
      -o /usr/local/bin/docker-compose && \
    chmod +x /usr/local/bin/docker-compose && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir flask

COPY dashboard_server.py .
COPY static/ ./static/

RUN mkdir -p /status /screenshots /processed

ENV TZ=UTC
EXPOSE 9007

CMD ["python", "-u", "dashboard_server.py"]
