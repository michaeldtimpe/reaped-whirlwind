# Radar Image Processor Service - Dockerfile
FROM ubuntu:22.04

# Prevent interactive prompts during build
ENV DEBIAN_FRONTEND=noninteractive

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy application files
COPY processor_service.py .
COPY web_server.py .
COPY config.yaml .

# Copy radar_tools package
COPY radar_tools/ ./radar_tools/

# Create necessary directories
RUN mkdir -p /input /output /logs

# Set permissions for Synology NAS compatibility
RUN chmod -R 755 /app

# Expose web interface port
EXPOSE 8080

# Run the service
CMD ["python3", "-u", "processor_service.py"]
