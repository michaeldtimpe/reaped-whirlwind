#!/usr/bin/env python3
"""
Radar Image Processor Service
Processes radar images from screenshot service, converts to JSON, and manages files.
MULTI-THREADED VERSION for better performance on multi-core systems.
"""

import os
import sys
import yaml
import time
import logging
import json
import threading
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import glob
from queue import Queue
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing

# Add radar_tools to path
sys.path.insert(0, '/app')
from radar_tools import RadarImageConverter


def process_single_image(image_path_str: str, config: dict, status_queue=None) -> tuple:
    """
    Standalone function for processing a single image.
    Must be top-level for multiprocessing to work.

    Returns:
        (success: bool, filename: str, time: float, error: str, details: dict)
    """
    from PIL import Image
    import json

    image_path = Path(image_path_str)
    filename = image_path.name

    timing = {}

    try:
        # Initialize converter (each process needs its own)
        start = time.time()
        converter = RadarImageConverter(
            config['paths']['reflectivity_scale'],
            config['paths']['velocity_scale']
        )
        timing['init'] = time.time() - start

        # Determine radar type
        if 'reflectivity' in filename.lower():
            radar_type = 'reflectivity'
        elif 'velocity' in filename.lower():
            radar_type = 'velocity'
        else:
            return (False, filename, 0, "Unknown radar type", {})

        # Generate output path
        output_filename = image_path.stem + '.json'
        output_path = Path(config['paths']['output_dir']) / output_filename

        # Load image to check size
        start = time.time()
        img = Image.open(image_path_str)
        img_size = (img.width, img.height)
        timing['load_image'] = time.time() - start

        # Convert
        start = time.time()
        converter.convert_and_save(
            str(image_path),
            radar_type,
            str(output_path),
            sample_rate=config['processing']['sample_rate'],
            save_numpy=False
        )
        timing['conversion'] = time.time() - start

        # Verify output
        start = time.time()
        if config['processing']['verify_output']:
            if not output_path.exists():
                return (False, filename, sum(timing.values()), "Output file not created", timing)

            file_size = output_path.stat().st_size
            if file_size < config['processing']['min_json_size']:
                return (False, filename, sum(timing.values()), f"Output too small: {file_size} bytes", timing)

            # Quick JSON validation
            try:
                with open(output_path, 'r') as f:
                    data = json.load(f)
                if 'metadata' not in data or 'data' not in data:
                    return (False, filename, sum(timing.values()), "Invalid JSON structure", timing)
            except:
                return (False, filename, sum(timing.values()), "Invalid JSON", timing)
        timing['verify'] = time.time() - start

        # Delete source if configured
        start = time.time()
        if config['processing']['delete_after_processing']:
            image_path.unlink()
        timing['cleanup'] = time.time() - start

        details = {
            'timing': timing,
            'img_size': img_size,
            'input_mb': image_path.stat().st_size / (1024*1024) if image_path.exists() else 0,
            'output_mb': output_path.stat().st_size / (1024*1024)
        }

        return (True, filename, sum(timing.values()), None, details)

    except Exception as e:
        import traceback
        error_detail = f"{str(e)}\n{traceback.format_exc()}"
        return (False, filename, sum(timing.values()) if timing else 0, error_detail, timing)


class ProcessorStatus:
    """Thread-safe status tracking for the processor."""

    def __init__(self):
        self._lock = threading.Lock()
        self.current_images = {}  # Dict of {filename: progress}
        self.current_status = "Idle"
        self.last_processed = None
        self.last_processed_time = None
        self.total_processed = 0
        self.total_errors = 0
        self.logs = []
        self.max_logs = 100
        self.active_workers = 0

    def update(self, **kwargs):
        """Thread-safe update of status."""
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self, key):
                    setattr(self, key, value)

    def set_image_progress(self, filename: str, progress: int):
        """Set progress for a specific image."""
        with self._lock:
            self.current_images[filename] = progress

    def remove_image(self, filename: str):
        """Remove image from tracking."""
        with self._lock:
            if filename in self.current_images:
                del self.current_images[filename]

    def add_log(self, level: str, message: str):
        """Add a log entry."""
        with self._lock:
            timestamp = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
            log_entry = {
                'timestamp': timestamp,
                'level': level,
                'message': message
            }
            self.logs.append(log_entry)
            # Keep only recent logs
            if len(self.logs) > self.max_logs:
                self.logs = self.logs[-self.max_logs:]

    def get_status(self) -> dict:
        """Get current status as dictionary."""
        with self._lock:
            # Calculate overall progress if processing multiple images
            if self.current_images:
                avg_progress = sum(self.current_images.values()) / len(self.current_images)
                current_image = list(self.current_images.keys())[0] if len(self.current_images) == 1 else f"{len(self.current_images)} images"
            else:
                avg_progress = 0
                current_image = None

            return {
                'current_image': current_image,
                'current_status': self.current_status,
                'progress_percent': round(avg_progress),
                'last_processed': self.last_processed,
                'last_processed_time': self.last_processed_time,
                'total_processed': self.total_processed,
                'total_errors': self.total_errors,
                'logs': list(self.logs),
                'active_workers': self.active_workers,
                'processing_count': len(self.current_images)
            }


class ProgressTracker:
    """Tracks conversion progress by monitoring pixel processing."""

    def __init__(self, total_rows: int, status: ProcessorStatus, filename: str):
        self.total_rows = total_rows
        self.status = status
        self.filename = filename
        self.last_update = 0

    def update(self, current_row: int):
        """Update progress. Only update every 10 rows to reduce overhead."""
        if current_row % 10 == 0 or current_row == self.total_rows:
            progress = int((current_row / self.total_rows) * 90) + 10  # 10-100%
            self.status.set_image_progress(self.filename, progress)


class RadarProcessor:
    """Main radar image processor with multi-threading support."""

    def __init__(self, config_path: str = "/app/config.yaml"):
        """Initialize the processor."""
        self.config = self.load_config(config_path)
        self.setup_logging()
        self.status = ProcessorStatus()

        # Get processing mode from environment
        self.mode = os.getenv('PROCESSING_MODE', 'normal').lower()

        # Override sample rate from environment if set
        env_sample_rate = os.getenv('SAMPLE_RATE')
        if env_sample_rate:
            self.config['processing']['sample_rate'] = int(env_sample_rate)

        # Determine number of worker threads
        # Use cores - 1 to leave one for system, min 1, max 4
        cpu_count = os.cpu_count() or 2
        self.max_workers = 1  # Pinned to 1 to reduce CPU load on NAS

        self.logger.info(f"System has {cpu_count} CPU cores, using {self.max_workers} worker threads")
        self.status.add_log('INFO', f'Initializing with {self.max_workers} worker threads')

        # Initialize converter
        self.logger.info("Initializing RadarImageConverter...")
        try:
            self.converter = RadarImageConverter(
                self.config['paths']['reflectivity_scale'],
                self.config['paths']['velocity_scale']
            )
            self.logger.info("Converter initialized successfully")
            self.status.add_log('INFO', 'Converter initialized')
        except Exception as e:
            self.logger.error(f"Failed to initialize converter: {e}")
            self.status.add_log('ERROR', f'Converter initialization failed: {e}')
            raise

        # Ensure directories exist
        self.ensure_directories()

        self.logger.info(f"Processor initialized in {self.mode} mode")
        self.status.add_log('INFO', f'Processor started in {self.mode} mode')

    def load_config(self, config_path: str) -> dict:
        """Load configuration from YAML."""
        with open(config_path, 'r') as f:
            return yaml.safe_load(f)

    def setup_logging(self):
        """Setup logging configuration."""
        log_level = getattr(logging, self.config['logging']['level'], logging.INFO)

        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(log_level)

        # File handler
        log_dir = Path(self.config['paths']['logs_dir'])
        log_dir.mkdir(parents=True, exist_ok=True)

        log_file = log_dir / f"processor_{datetime.utcnow().strftime('%Y%m%d')}.log"
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(log_level)

        # Format
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        console_handler.setFormatter(formatter)
        file_handler.setFormatter(formatter)

        # Logger
        self.logger = logging.getLogger('RadarProcessor')
        self.logger.setLevel(log_level)
        self.logger.addHandler(console_handler)
        self.logger.addHandler(file_handler)

    def ensure_directories(self):
        """Ensure all required directories exist."""
        for path_key in ['input_dir', 'output_dir', 'logs_dir']:
            path = Path(self.config['paths'][path_key])
            path.mkdir(parents=True, exist_ok=True)

    def get_folder_stats(self, folder_path: str) -> dict:
        """Get statistics about a folder."""
        path = Path(folder_path)
        if not path.exists():
            return {'count': 0, 'size_mb': 0}

        files = list(path.glob('*'))
        total_size = sum(f.stat().st_size for f in files if f.is_file())

        return {
            'count': len(files),
            'size_mb': round(total_size / (1024 * 1024), 2)
        }

    def find_images_to_process(self) -> List[Path]:
        """Find images that are old enough to process."""
        input_dir = Path(self.config['paths']['input_dir'])
        min_age = self.config['processing']['min_age_seconds']
        cutoff_time = time.time() - min_age

        images = []

        # Find reflectivity images
        for pattern in [self.config['patterns']['reflectivity'],
                       self.config['patterns']['velocity']]:
            for img_path in input_dir.glob(pattern):
                # Check if old enough
                if img_path.stat().st_mtime < cutoff_time:
                    images.append(img_path)

        # Sort by modification time (oldest first)
        images.sort(key=lambda p: p.stat().st_mtime)

        return images

    def determine_radar_type(self, filename: str) -> str:
        """Determine radar type from filename."""
        if 'reflectivity' in filename.lower():
            return 'reflectivity'
        elif 'velocity' in filename.lower():
            return 'velocity'
        else:
            return 'unknown'

    def process_image(self, image_path: Path) -> bool:
        """
        Process a single image with progress tracking.

        Returns:
            bool: True if successful, False otherwise
        """
        filename = image_path.name

        self.status.set_image_progress(filename, 0)
        self.status.update(active_workers=self.status.active_workers + 1)

        self.logger.info(f"[{threading.current_thread().name}] Processing: {filename}")
        self.status.add_log('INFO', f'Processing: {filename}')

        try:
            # Determine radar type
            radar_type = self.determine_radar_type(filename)
            if radar_type == 'unknown':
                self.logger.warning(f"Unknown radar type for: {filename}")
                self.status.add_log('WARNING', f'Unknown radar type: {filename}')
                return False

            self.status.set_image_progress(filename, 5)

            # Generate output filename
            output_filename = image_path.stem + '.json'
            output_path = Path(self.config['paths']['output_dir']) / output_filename

            self.logger.info(f"Converting {filename} (type={radar_type}, sample_rate={self.config['processing']['sample_rate']})")
            self.status.set_image_progress(filename, 10)

            # Convert with progress tracking
            start_time = time.time()

            # Load image to get dimensions for progress tracking
            from PIL import Image
            img = Image.open(image_path)
            height = img.height
            sample_rate = self.config['processing']['sample_rate']
            total_rows = height // sample_rate

            # Create progress tracker
            # Note: This is a simplified version. For true progress,
            # we'd need to modify the converter to accept a callback
            self.status.set_image_progress(filename, 20)

            # Do conversion (this is the slow part)
            self.converter.convert_and_save(
                str(image_path),
                radar_type,
                str(output_path),
                sample_rate=self.config['processing']['sample_rate'],
                save_numpy=False
            )

            conversion_time = time.time() - start_time
            self.status.set_image_progress(filename, 80)

            # Verify output
            if self.config['processing']['verify_output']:
                if not self.verify_json_output(output_path):
                    self.logger.error(f"Verification failed for: {output_path.name}")
                    self.status.add_log('ERROR', f'Verification failed: {output_path.name}')
                    return False

            self.status.set_image_progress(filename, 90)

            # Get file sizes
            input_size = image_path.stat().st_size / (1024 * 1024)  # MB
            output_size = output_path.stat().st_size / (1024 * 1024)  # MB

            self.logger.info(f"✓ {filename}: {input_size:.2f}MB → {output_size:.2f}MB ({conversion_time:.2f}s)")
            self.status.add_log('SUCCESS', f'Converted {filename}: {output_size:.2f}MB in {conversion_time:.1f}s')

            # Delete source if configured
            if self.config['processing']['delete_after_processing']:
                self.logger.debug(f"Deleting source: {filename}")
                image_path.unlink()
                self.status.add_log('INFO', f'Deleted source: {filename}')

            self.status.set_image_progress(filename, 100)
            self.status.update(
                last_processed=filename,
                last_processed_time=datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC'),
                total_processed=self.status.total_processed + 1
            )

            return True

        except Exception as e:
            self.logger.error(f"Error processing {filename}: {type(e).__name__}: {e}")
            self.status.add_log('ERROR', f'Error processing {filename}: {e}')
            self.status.update(total_errors=self.status.total_errors + 1)
            return False

        finally:
            # Remove from tracking and decrement worker count
            self.status.remove_image(filename)
            self.status.update(active_workers=max(0, self.status.active_workers - 1))

    def verify_json_output(self, json_path: Path) -> bool:
        """Verify that JSON output is valid."""
        try:
            # Check file exists
            if not json_path.exists():
                self.logger.error(f"JSON file does not exist: {json_path}")
                return False

            # Check file size
            min_size = self.config['processing']['min_json_size']
            file_size = json_path.stat().st_size
            if file_size < min_size:
                self.logger.error(f"JSON file too small: {file_size} < {min_size} bytes")
                return False

            # Check JSON is valid
            with open(json_path, 'r') as f:
                data = json.load(f)

            # Check structure
            if 'metadata' not in data or 'data' not in data:
                self.logger.error("JSON missing required fields")
                return False

            # Check data is not empty
            if not data['data']:
                self.logger.error("JSON data is empty")
                return False

            return True

        except Exception as e:
            self.logger.error(f"JSON verification failed: {e}")
            return False

    def _write_status_file(self):
        """Write processor_status.json to the shared service-status volume for the dashboard."""
        status_path = Path('/status/processor_status.json')
        if not status_path.parent.exists():
            return  # Volume not mounted, skip silently
        try:
            status = self.status.get_status()
            tmp_path = status_path.with_suffix('.tmp')
            with open(tmp_path, 'w') as f:
                json.dump(status, f, indent=2)
            tmp_path.replace(status_path)  # Atomic write
        except Exception as e:
            self.logger.warning(f"Could not write status file: {e}")

    def run_test_mode(self):
        """Process a single image and exit."""
        self.logger.info("Running in TEST mode - processing one image")
        self.status.add_log('INFO', 'TEST MODE: Processing single image')

        images = self.find_images_to_process()
        if not images:
            self.logger.warning("No images found to process")
            self.status.add_log('WARNING', 'No images found')
            return

        image = images[0]
        self.logger.info(f"Test processing: {image.name}")

        success = self.process_image(image)

        if success:
            self.logger.info("Test completed successfully")
            self.status.add_log('SUCCESS', 'Test completed')
        else:
            self.logger.error("Test failed")
            self.status.add_log('ERROR', 'Test failed')

    def run_normal_mode(self):
        """Run continuous processing loop with true multiprocessing."""
        self.logger.info(f"Running in NORMAL mode - continuous processing with {self.max_workers} workers")
        self.status.add_log('INFO', f'NORMAL MODE: Using {self.max_workers} worker PROCESSES (true parallelism)')

        interval = self.config['processing']['check_interval_seconds']

        # Set multiprocessing start method
        try:
            multiprocessing.set_start_method('spawn')
        except RuntimeError:
            pass  # Already set

        while True:
            try:
                # Get folder stats
                input_stats = self.get_folder_stats(self.config['paths']['input_dir'])
                output_stats = self.get_folder_stats(self.config['paths']['output_dir'])

                self.logger.info(f"Input: {input_stats['count']} files ({input_stats['size_mb']}MB), "
                               f"Output: {output_stats['count']} files ({output_stats['size_mb']}MB)")

                # Find images to process
                images = self.find_images_to_process()

                if images:
                    self.logger.info(f"Found {len(images)} images to process")
                    self.status.add_log('INFO', f'Found {len(images)} images to process')
                    self.status.update(current_status='Processing')

                    # Update status for all images being processed
                    for img in images[:self.max_workers]:
                        self.status.set_image_progress(img.name, 0)

                    self.status.update(active_workers=min(len(images), self.max_workers))

                    # Process images in parallel using process pool
                    start_time = time.time()
                    with ProcessPoolExecutor(max_workers=self.max_workers) as executor:
                        # Submit all images with config
                        futures = {
                            executor.submit(process_single_image, str(img), self.config): img
                            for img in images
                        }

                        # Process results as they complete
                        for future in as_completed(futures):
                            img = futures[future]
                            try:
                                success, filename, conv_time, error, details = future.result()

                                if success:
                                    timing = details.get('timing', {})
                                    timing_str = f"init:{timing.get('init', 0):.2f}s, " \
                                               f"load:{timing.get('load_image', 0):.2f}s, " \
                                               f"convert:{timing.get('conversion', 0):.2f}s, " \
                                               f"verify:{timing.get('verify', 0):.2f}s"

                                    self.logger.info(f"✓ {filename}: {conv_time:.2f}s total ({timing_str})")
                                    self.status.add_log('SUCCESS', f'{filename}: {conv_time:.1f}s')

                                    # Log detailed performance
                                    if 'input_mb' in details and 'output_mb' in details:
                                        self.logger.debug(f"  {details['input_mb']:.2f}MB → {details['output_mb']:.2f}MB")

                                    self.status.update(
                                        last_processed=filename,
                                        last_processed_time=datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC'),
                                        total_processed=self.status.total_processed + 1
                                    )
                                else:
                                    self.logger.error(f"✗ {filename}: {error}")
                                    self.status.add_log('ERROR', f'{filename}: {error[:100]}')
                                    self.status.update(total_errors=self.status.total_errors + 1)

                                # Remove from tracking
                                self.status.remove_image(filename)

                            except Exception as e:
                                self.logger.error(f"Process exception for {img.name}: {e}")
                                self.status.add_log('ERROR', f'Process error: {img.name}: {e}')
                                self.status.remove_image(img.name)

                    total_time = time.time() - start_time
                    self.logger.info(f"Batch complete: {len(images)} images in {total_time:.2f}s ({total_time/len(images):.2f}s per image)")

                    self.status.update(current_status='Idle', active_workers=0)
                else:
                    self.logger.debug("No images ready for processing")
                    self.status.update(current_status='Idle', active_workers=0)

                # Write status file for pipeline dashboard
                self._write_status_file()

                # Wait for next check
                self.logger.info(f"Waiting {interval}s until next check...")
                time.sleep(interval)

            except KeyboardInterrupt:
                self.logger.info("Received shutdown signal")
                self.status.add_log('INFO', 'Processor shutting down')
                break
            except Exception as e:
                self.logger.error(f"Error in main loop: {e}")
                self.status.add_log('ERROR', f'Main loop error: {e}')
                self.status.update(current_status='Error', active_workers=0)
                time.sleep(interval)

    def run(self):
        """Run the processor based on mode."""
        if self.mode == 'test':
            self.run_test_mode()
        else:
            self.run_normal_mode()


def main():
    """Main entry point."""
    # Start web server in background thread
    from web_server import start_web_server

    processor = RadarProcessor()

    # Start web server
    web_config = processor.config['web']
    web_thread = threading.Thread(
        target=start_web_server,
        args=(processor, web_config['host'], web_config['port']),
        daemon=True
    )
    web_thread.start()

    # Give web server time to start
    time.sleep(2)
    processor.logger.info(f"Web interface available at http://localhost:{web_config['port']}")
    processor.status.add_log('INFO', f"Web interface started on port {web_config['port']}")

    # Run processor
    processor.run()


if __name__ == "__main__":
    main()