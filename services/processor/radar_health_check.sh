#!/bin/bash
# Radar Processor Health Check
# Run via Synology Task Scheduler every 10 minutes (user: root)

HEALTH_URL="http://localhost:9005/health"
STATE_FILE="/tmp/radar_health_alert_sent"

RESPONSE=$(curl -s -o /tmp/radar_health.json -w "%{http_code}" --max-time 10 "$HEALTH_URL" 2>/dev/null)

if [ "$RESPONSE" = "200" ]; then
    # Healthy — clear any previous alert
    if [ -f "$STATE_FILE" ]; then
        rm -f "$STATE_FILE"
        synodsmnotify -c @administrators "Radar Processor RECOVERED" "Processing normally again."
    fi
    exit 0
fi

# Unhealthy or unreachable — alert once
if [ ! -f "$STATE_FILE" ]; then
    if [ "$RESPONSE" = "503" ]; then
        REASON=$(python3 -c "import json; print(json.load(open('/tmp/radar_health.json')).get('reason','unknown'))" 2>/dev/null || echo "unknown")
        synodsmnotify -c @administrators "Radar Processor STALE" "Stopped processing: $REASON. Check http://alpha.local:9005/"
    elif ! docker ps --format '{{.Names}}' | grep -q '^radar-image-processor$'; then
        synodsmnotify -c @administrators "Radar Processor DOWN" "Container is not running."
    else
        synodsmnotify -c @administrators "Radar Processor ERROR" "Health check failed (HTTP $RESPONSE). Check http://alpha.local:9005/"
    fi
    echo "$RESPONSE" > "$STATE_FILE"
fi
exit 1
