# Radar Image Processor Service

Automated Docker service for processing weather radar screenshots into JSON data files. Designed for Synology NAS with web-based monitoring.

## Features

- ✅ Automatic processing of radar images older than 20 minutes
- ✅ Converts images to JSON with configurable sample rates
- ✅ Validates output and manages source files
- ✅ Real-time web dashboard with live monitoring
- ✅ Processing logs and metrics
- ✅ Test mode for single-image processing
- ✅ Debug mode for detailed logging
- ✅ Folder statistics (file counts, sizes)
- ✅ Health checks and auto-restart
- ✅ Synology NAS optimized (PUID/PGID support)

## Architecture

```
weather-screenshots/          ← Input (from screenshot service)
    radar_base_reflectivity_*.png
    radar_base_velocity_*.png
                ↓
    [Radar Processor Service]
         - Waits 20 minutes
         - Converts to JSON
         - Verifies output
         - Deletes source
                ↓
radar-processed/              ← Output (JSON files)
    radar_base_reflectivity_*.json
    radar_base_velocity_*.json
```

## Installation

### Prerequisites

1. **radar_tools package** - Copy your `radar_tools/` directory to the build folder
2. **Scale images** - Copy your cropped scale images:
   - `base_reflectivity_intensity_scale.png`
   - `base_velocity_intensity_scale.png`

### Directory Structure

```
/docker/radar-image-processor/
├── Dockerfile
├── docker-compose.yaml
├── requirements.txt
├── config.yaml
├── processor_service.py
├── web_server.py
├── radar_tools/                          ← Copy your package here
│   ├── __init__.py
│   ├── color_scale.py
│   ├── converter.py
│   ├── verifier.py
│   └── utils.py
├── base_reflectivity_intensity_scale.png ← Copy your scale
└── base_velocity_intensity_scale.png     ← Copy your scale
```

### Step-by-Step Setup

#### 1. Create directories on Synology

```bash
sudo mkdir -p /docker/radar-image-processor
sudo mkdir -p /docker/radar-processed
sudo mkdir -p /docker/radar-processor-logs

# Set permissions
sudo chmod 777 /docker/radar-processed
sudo chmod 777 /docker/radar-processor-logs
```

#### 2. Upload files

Upload all files to `/docker/radar-image-processor/`:
- All `.py` files
- `Dockerfile`, `docker-compose.yaml`, `requirements.txt`, `config.yaml`
- Your `radar_tools/` package directory
- Your scale images (`.png` files)

#### 3. Build and run

```bash
cd /docker/radar-image-processor
sudo docker-compose up -d --build
```

#### 4. Access web interface

Open browser: `http://YOUR_NAS_IP:8080`

## Configuration

### config.yaml

```yaml
processing:
  min_age_seconds: 1200      # 20 minutes - wait before processing
  sample_rate: 4             # 4 recommended (balance size/quality)
  check_interval_seconds: 60 # How often to check for new images
  delete_after_processing: true
  verify_output: true
```

### Environment Variables

Set in `docker-compose.yaml`:

```yaml
environment:
  - PROCESSING_MODE=normal   # normal, test, debug
  - SAMPLE_RATE=4           # Override config sample rate
```

## Operating Modes

### Normal Mode (Default)
```yaml
environment:
  - PROCESSING_MODE=normal
```
- Continuous processing
- Checks for images every 60 seconds
- Processes images older than 20 minutes

### Test Mode
```yaml
environment:
  - PROCESSING_MODE=test
```
- Processes one image and exits
- Useful for testing configuration
- Good for debugging

### Debug Mode
```yaml
environment:
  - PROCESSING_MODE=debug
```
- Verbose logging
- Detailed error messages
- Same as normal but more logs

## Web Dashboard

Access at `http://YOUR_NAS_IP:8080`

### Features:
- **Real-time status** - Current processing state
- **Progress bar** - Live processing progress
- **Metrics**:
  - Total processed images
  - Error count
  - Last processed file
  - System memory usage
- **Folder stats**:
  - Input folder (file count, size)
  - Output folder (file count, size)
- **Live log** - Last 100 log entries
- **Auto-refresh** - Updates every 5 seconds

## Managing the Service

### Start/Stop/Restart

```bash
cd /docker/radar-image-processor
sudo docker-compose start
sudo docker-compose stop
sudo docker-compose restart
```

### View logs

```bash
# Live logs
sudo docker-compose logs -f

# Last 100 lines
sudo docker-compose logs --tail 100
```

### Rebuild (after changes)

```bash
sudo docker-compose down
sudo docker-compose up -d --build
```

### Check status

```bash
sudo docker ps | grep radar-image-processor
```

## File Management

### Check processed files

```bash
# Count files
ls /docker/radar-processed/*.json | wc -l

# List recent
ls -lht /docker/radar-processed/ | head -20

# Check size
du -sh /docker/radar-processed/
```

### Clean old files (optional)

Keep only last 30 days:

```bash
find /docker/radar-processed/ -name "*.json" -mtime +30 -delete
```

Add to Synology Task Scheduler for automatic cleanup.

## Monitoring

### Health Check

```bash
curl http://localhost:8080/health
```

Should return: `{"status":"healthy"}`

### API Endpoint

Get status via API:

```bash
curl http://localhost:8080/api/status
```

Returns JSON with:
- Current status
- Processing progress
- Statistics
- Folder metrics
- System resources

## Troubleshooting

### No images being processed

**Check age requirement:**
- Images must be >20 minutes old
- Adjust `min_age_seconds` in config if needed

**Check file patterns:**
```bash
ls /docker/weather-screenshots/radar_*.png
```

**Check logs:**
```bash
sudo docker-compose logs -f
```

### Container won't start

**Check scale images:**
```bash
ls -l /docker/radar-image-processor/*.png
```

**Check radar_tools package:**
```bash
ls -l /docker/radar-image-processor/radar_tools/
```

**View error logs:**
```bash
sudo docker-compose logs
```

### Web dashboard not loading

**Check port:**
```bash
sudo netstat -tulpn | grep 8080
```

**Check container is running:**
```bash
sudo docker ps | grep radar
```

**Check logs:**
```bash
sudo docker-compose logs web
```

### Processing errors

**Test with single image:**
```yaml
# docker-compose.yaml
environment:
  - PROCESSING_MODE=test
```

Then:
```bash
sudo docker-compose restart
sudo docker-compose logs -f
```

### Permission issues

```bash
sudo chown -R 106808:106808 /docker/radar-processed/
sudo chown -R 106808:106808 /docker/radar-processor-logs/
sudo chmod -R 755 /docker/radar-processed/
```

## Performance

### Typical Processing Times (Sample Rate 4)

- 1920x1080 image: ~2-3 seconds
- File size reduction: 1-2 MB → 1-2 MB (similar)
- Memory usage: ~300-500 MB during processing

### Recommended Settings

```yaml
processing:
  sample_rate: 4              # Good balance
  check_interval_seconds: 60  # Don't check too frequently
  min_age_seconds: 1200       # Ensure file is complete
```

## Integration with Screenshot Service

This service works with the `weather-screenshot-service`:

```
Screenshot Service               Processor Service
      ↓                               ↓
Captures radar images          Processes old images
Every 10 minutes              After 20 minutes
      ↓                               ↓
/docker/weather-screenshots → /docker/radar-processed
```

**Flow:**
1. Screenshot service captures at 12:00
2. Processor waits until 12:20
3. Processor converts to JSON
4. Processor deletes original PNG
5. JSON ready for ML training

## Updating Scale Images

If you need to update scale images:

1. Replace files:
```bash
sudo cp new_reflectivity_scale.png /docker/radar-image-processor/base_reflectivity_intensity_scale.png
sudo cp new_velocity_scale.png /docker/radar-image-processor/base_velocity_intensity_scale.png
```

2. Rebuild:
```bash
cd /docker/radar-image-processor
sudo docker-compose down
sudo docker-compose up -d --build
```

## Storage Management

### Automatic Cleanup Script

Create in Synology Task Scheduler:

**Task**: User-defined script  
**Schedule**: Daily at 3:00 AM  
**User**: root  

```bash
#!/bin/bash
# Keep last 60 days of processed JSON
find /docker/radar-processed/ -name "*.json" -mtime +60 -delete

# Clean old logs
find /docker/radar-processor-logs/ -name "*.log" -mtime +14 -delete

# Log cleanup
echo "$(date): Cleaned old files" >> /docker/radar-processor-logs/cleanup.log
```

## API Reference

### GET /
Returns web dashboard HTML

### GET /api/status
Returns JSON:
```json
{
  "current_image": "radar_base_reflectivity_20260128_183401_UTC.png",
  "current_status": "Processing",
  "progress_percent": 45,
  "total_processed": 1234,
  "total_errors": 5,
  "folders": {
    "input": {"count": 10, "size_mb": 15.5},
    "output": {"count": 1234, "size_mb": 2100.8}
  },
  "system": {
    "memory_percent": 35.2,
    "cpu_percent": 12.5
  },
  "logs": [...]
}
```

### GET /health
Returns: `{"status": "healthy"}`

## Common Issues

### "No module named 'radar_tools'"

**Solution**: Ensure `radar_tools/` directory is in build folder with all `.py` files

### "Scale image not found"

**Solution**: Copy both `.png` scale files to build directory

### "Permission denied"

**Solution**: Set correct PUID/PGID and folder permissions

### "Images not being processed"

**Solution**: Check `min_age_seconds` - images must be old enough

## Advanced Configuration

### Custom Processing Interval

Process more/less frequently:

```yaml
processing:
  check_interval_seconds: 30  # Check every 30 seconds
```

### Custom Age Threshold

Process sooner/later:

```yaml
processing:
  min_age_seconds: 600  # Process after 10 minutes
```

### Keep Source Images

Don't delete after processing:

```yaml
processing:
  delete_after_processing: false
```

## Support

Check logs for errors:
```bash
sudo docker-compose logs -f
```

View web dashboard:
```
http://YOUR_NAS_IP:8080
```

Test mode:
```yaml
environment:
  - PROCESSING_MODE=test
```

## Summary

✅ Automated radar image processing  
✅ Web-based monitoring dashboard  
✅ Configurable processing pipeline  
✅ Synology NAS optimized  
✅ Production-ready with health checks  
✅ Easy integration with screenshot service  

Your radar images are automatically processed and ready for ML training! 🚀
