#!/usr/bin/env python3
"""
Color Matching Diagnostic Tool
Helps identify why reconstruction accuracy might be low.
"""

import sys
from pathlib import Path
import numpy as np
from PIL import Image
import json

sys.path.insert(0, str(Path(__file__).parent))


def analyze_color_matching(radar_image_path: str, scale_image_path: str, 
                          json_data_path: str = None):
    """
    Analyze how well radar colors match scale colors.
    
    Args:
        radar_image_path: Path to radar image
        scale_image_path: Path to scale reference image  
        json_data_path: Optional path to converted JSON to check reconstruction
    """
    print("=" * 70)
    print("COLOR MATCHING DIAGNOSTIC")
    print("=" * 70)
    
    # Load images
    radar_img = Image.open(radar_image_path).convert('RGB')
    scale_img = Image.open(scale_image_path).convert('RGB')
    
    radar_array = np.array(radar_img)
    scale_array = np.array(scale_img)
    
    print(f"\nRadar image: {radar_img.size[0]}x{radar_img.size[1]}")
    print(f"Scale image: {scale_img.size[0]}x{scale_img.size[1]}")
    
    # Get unique colors from each
    radar_pixels = radar_array.reshape(-1, 3)
    scale_pixels = scale_array.reshape(-1, 3)
    
    radar_colors = np.unique(radar_pixels, axis=0)
    scale_colors = np.unique(scale_pixels, axis=0)
    
    print(f"\nUnique colors in radar: {len(radar_colors)}")
    print(f"Unique colors in scale: {len(scale_colors)}")
    
    # Find weather-like colors in radar (not background)
    # Exclude beige background
    weather_colors = []
    for color in radar_colors:
        r, g, b = color
        # Skip if looks like background
        if abs(r - 247) < 30 and abs(g - 246) < 30 and abs(b - 213) < 30:
            continue
        # Skip if pure white
        if r > 245 and g > 245 and b > 245:
            continue
        # Skip if very dark
        if r < 50 and g < 50 and b < 50:
            continue
        weather_colors.append(color)
    
    print(f"Weather colors in radar (estimated): {len(weather_colors)}")
    
    # Check how many radar weather colors are close to scale colors
    matched = 0
    unmatched_samples = []
    
    for radar_color in weather_colors[:100]:  # Check first 100
        r, g, b = radar_color
        
        # Find closest scale color
        distances = np.sqrt(
            (scale_colors[:, 0] - float(r))**2 +
            (scale_colors[:, 1] - float(g))**2 +
            (scale_colors[:, 2] - float(b))**2
        )
        
        min_dist = distances.min()
        
        if min_dist < 20:  # Within 20 RGB units
            matched += 1
        else:
            if len(unmatched_samples) < 10:
                closest_idx = distances.argmin()
                closest = scale_colors[closest_idx]
                unmatched_samples.append((radar_color, closest, min_dist))
    
    match_rate = matched / min(100, len(weather_colors)) * 100
    
    print(f"\nColor matching (sample of 100 weather colors):")
    print(f"  Matched (within 20 RGB): {matched}/100 ({match_rate:.1f}%)")
    print(f"  Unmatched: {100 - matched}/100")
    
    if unmatched_samples:
        print(f"\nSample of unmatched colors (radar → closest scale):")
        for radar_col, scale_col, dist in unmatched_samples[:5]:
            print(f"  RGB{tuple(radar_col)} → RGB{tuple(scale_col)} (distance: {dist:.1f})")
    
    # Analyze if reconstruction exists
    if json_data_path:
        print("\n" + "=" * 70)
        print("RECONSTRUCTION ANALYSIS")
        print("=" * 70)
        
        with open(json_data_path) as f:
            data = json.load(f)
        
        # Sample some values and see what they reconstruct to
        data_array = np.array(data['data'])
        sample_values = [
            data_array[135, 240],  # Center
            data_array[100, 200],  # Upper left quadrant
            data_array[170, 280],  # Lower right quadrant
        ]
        
        print("\nSample values from data:")
        for i, val in enumerate(sample_values):
            print(f"  Sample {i+1}: {val:.2f} dBZ")
    
    # Interpretation
    print("\n" + "=" * 70)
    print("DIAGNOSIS")
    print("=" * 70)
    
    if match_rate > 80:
        print("\n✓ GOOD: Radar colors match scale well")
        print("  Low verification accuracy is likely due to:")
        print("  - Mask being too aggressive")
        print("  - Sample rate artifacts")
    elif match_rate > 60:
        print("\n⚠ MODERATE: Some radar colors don't match scale")
        print("  This is normal - radar uses interpolated colors")
        print("  Verification should still be 70-80%+")
    else:
        print("\n✗ POOR: Many radar colors don't match scale")
        print("  Possible causes:")
        print("  1. Wrong scale image (doesn't match radar source)")
        print("  2. Different color profiles")
        print("  3. Image compression artifacts")
        print("\n  Try:")
        print("  - Verify you're using the correct scale image")
        print("  - Check if scale bar in radar matches scale image")
    
    print(f"\nExpected verification accuracy: {max(30, match_rate - 10):.0f}-{min(95, match_rate + 5):.0f}%")


def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Diagnose color matching issues'
    )
    parser.add_argument('radar_image', help='Path to radar image')
    parser.add_argument('scale_image', help='Path to scale reference image')
    parser.add_argument('--json-data', help='Path to converted JSON (optional)')
    
    args = parser.parse_args()
    
    analyze_color_matching(args.radar_image, args.scale_image, args.json_data)


if __name__ == '__main__':
    main()
