FROM mcr.microsoft.com/playwright/python:v1.41.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY screenshot_service.py .
COPY config.yaml .

RUN mkdir -p /screenshots /status && chmod 777 /screenshots /status

ENV TZ=UTC
USER root

CMD ["python", "-u", "screenshot_service.py"]
