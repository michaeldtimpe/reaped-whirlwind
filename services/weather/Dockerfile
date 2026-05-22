FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir requests

COPY weather_report_generator.py .

RUN mkdir -p /logs /var/www/html /status && \
    chmod 777 /logs /status && chmod 755 /var/www/html

RUN echo '<html><body style="background:#0b0f1a;color:#64748b;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh"><p>Waiting for first weather report…</p></body></html>' > /var/www/html/index.html

ENV TZ=UTC
EXPOSE 9006

CMD ["python", "-u", "weather_report_generator.py"]
