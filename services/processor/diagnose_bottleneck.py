#!/usr/bin/env python3
"""
Bottleneck Diagnostic Tool
Profiles the conversion process to identify slow operations.
"""

import time
import sys
from pathlib import Path
import cProfile
import pstats
import io

sys.path.insert(0, '/app')
from radar_tools import RadarImageConverter

def profile_conversion(image_path: str, radar_type: str):
    """Profile a single image conversion."""
    
    print("=" * 70)
    print("CONVERSION PROFILING")
    print("=" * 70)
    
    # Initialize converter
    print("\n1. Initializing converter...")
    start = time.time()
    converter = RadarImageConverter(
        '/app/base_reflectivity_intensity_scale.png',
        '/app/base_velocity_intensity_scale.png'
    )
    print(f"   Time: {time.time() - start:.2f}s")
    
    # Create profiler
    profiler = cProfile.Profile()
    
    # Profile the conversion
    print(f"\n2. Converting image (with profiling)...")
    output_path = '/tmp/test_output.json'
    
    start = time.time()
    profiler.enable()
    
    converter.convert_and_save(
        image_path,
        radar_type,
        output_path,
        sample_rate=4,
        save_numpy=False
    )
    
    profiler.disable()
    total_time = time.time() - start
    
    print(f"   Total Time: {total_time:.2f}s")
    
    # Analyze results
    print("\n" + "=" * 70)
    print("TOP 20 SLOWEST OPERATIONS")
    print("=" * 70)
    
    s = io.StringIO()
    ps = pstats.Stats(profiler, stream=s).sort_stats('cumulative')
    ps.print_stats(20)
    
    print(s.getvalue())
    
    # Also sort by time spent in function itself
    print("\n" + "=" * 70)
    print("TOP 10 BY TIME IN FUNCTION (excluding subcalls)")
    print("=" * 70)
    
    s2 = io.StringIO()
    ps2 = pstats.Stats(profiler, stream=s2).sort_stats('time')
    ps2.print_stats(10)
    
    print(s2.getvalue())
    
    # Break down by major operations
    print("\n" + "=" * 70)
    print("BOTTLENECK ANALYSIS")
    print("=" * 70)
    
    # Rough estimates of where time goes
    if total_time > 10:
        print("\n⚠️  SLOW CONVERSION (>10s)")
        print("\nLikely causes:")
        print("1. Color lookup is slow (nested loops)")
        print("2. Image I/O is slow (disk speed)")
        print("3. JSON serialization is slow (large data)")
        print("4. PIL operations are slow")
    else:
        print("\n✓ Conversion speed is reasonable")
    
    print(f"\nConversion rate: {total_time:.2f}s per image")
    print(f"Throughput: {3600/total_time:.1f} images per hour (single-threaded)")
    
    # Check if it's I/O bound or CPU bound
    print("\n" + "=" * 70)
    print("OPTIMIZATION RECOMMENDATIONS")
    print("=" * 70)
    
    print("\nFor I/O bound operations:")
    print("  - Use ThreadPoolExecutor (good for I/O)")
    print("  - Increase threads (3-5 for I/O)")
    
    print("\nFor CPU bound operations:")
    print("  - Use ProcessPoolExecutor (bypasses GIL)")
    print("  - Use number of CPU cores (4 for your system)")
    print("  - Consider Cython or numba for hot loops")


if __name__ == '__main__':
    import sys
    
    if len(sys.argv) < 3:
        print("Usage: python diagnose_bottleneck.py <image_path> <type>")
        print("Example: python diagnose_bottleneck.py /input/radar_reflectivity.png reflectivity")
        sys.exit(1)
    
    image_path = sys.argv[1]
    radar_type = sys.argv[2]
    
    profile_conversion(image_path, radar_type)
