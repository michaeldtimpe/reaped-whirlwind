#!/usr/bin/env python3
"""
Performance Diagnostic Tool
Profiles the conversion process to identify bottlenecks.
"""

import sys
import time
import cProfile
import pstats
import io
from pathlib import Path

sys.path.insert(0, '/app')
from radar_tools import RadarImageConverter

def profile_conversion(image_path: str, output_path: str, radar_type: str, sample_rate: int = 4):
    """Profile a single conversion."""
    
    print("=" * 70)
    print("PERFORMANCE DIAGNOSTIC")
    print("=" * 70)
    print(f"Image: {image_path}")
    print(f"Type: {radar_type}")
    print(f"Sample rate: {sample_rate}")
    print()
    
    # Initialize converter
    print("Initializing converter...")
    start = time.time()
    converter = RadarImageConverter(
        'base_reflectivity_intensity_scale.png',
        'base_velocity_intensity_scale.png'
    )
    init_time = time.time() - start
    print(f"✓ Converter initialized in {init_time:.3f}s")
    print()
    
    # Profile the conversion
    print("Starting profiled conversion...")
    profiler = cProfile.Profile()
    
    start = time.time()
    profiler.enable()
    
    converter.convert_and_save(
        image_path,
        radar_type,
        output_path,
        sample_rate=sample_rate,
        save_numpy=False
    )
    
    profiler.disable()
    total_time = time.time() - start
    
    print(f"✓ Conversion completed in {total_time:.3f}s")
    print()
    
    # Print detailed statistics
    print("=" * 70)
    print("TOP 20 TIME CONSUMERS")
    print("=" * 70)
    
    stream = io.StringIO()
    stats = pstats.Stats(profiler, stream=stream)
    stats.sort_stats('cumulative')
    stats.print_stats(20)
    
    print(stream.getvalue())
    
    # Performance breakdown
    print("=" * 70)
    print("PERFORMANCE ANALYSIS")
    print("=" * 70)
    
    # Estimate expected time
    from PIL import Image
    img = Image.open(image_path)
    pixels = img.width * img.height
    sampled_pixels = (img.width // sample_rate) * (img.height // sample_rate)
    
    print(f"Image size: {img.width}x{img.height} = {pixels:,} pixels")
    print(f"Sampled size: {img.width//sample_rate}x{img.height//sample_rate} = {sampled_pixels:,} pixels")
    print(f"Processing rate: {sampled_pixels/total_time:,.0f} pixels/second")
    print()
    
    # Expected performance
    expected_time = sampled_pixels / 40000  # ~40k pixels/sec is typical
    print(f"Expected time (typical): {expected_time:.2f}s")
    print(f"Actual time: {total_time:.2f}s")
    
    if total_time > expected_time * 3:
        print()
        print("⚠️  EXTREMELY SLOW - Potential issues:")
        print("  1. Disk I/O bottleneck (slow storage)")
        print("  2. Memory pressure (swapping)")
        print("  3. CPU throttling")
        print("  4. Scale image loading issue")
    elif total_time > expected_time * 1.5:
        print()
        print("⚠️  SLOWER THAN EXPECTED - Check:")
        print("  1. System load")
        print("  2. Disk performance")
    else:
        print()
        print("✓ Performance is normal")
    
    # File size info
    input_size = Path(image_path).stat().st_size / (1024 * 1024)
    output_size = Path(output_path).stat().st_size / (1024 * 1024)
    print()
    print(f"Input: {input_size:.2f} MB")
    print(f"Output: {output_size:.2f} MB")
    print(f"Compression ratio: {output_size/input_size*100:.1f}%")

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Profile radar conversion performance')
    parser.add_argument('image', help='Path to radar image')
    parser.add_argument('--type', choices=['reflectivity', 'velocity'], required=True)
    parser.add_argument('--output', default='diagnostic_output.json')
    parser.add_argument('--sample-rate', type=int, default=4)
    
    args = parser.parse_args()
    
    profile_conversion(args.image, args.output, args.type, args.sample_rate)
