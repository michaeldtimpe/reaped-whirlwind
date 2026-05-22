#!/usr/bin/env python3
"""
Verification Analysis Tool
Helps understand why verification accuracy might be low.
"""

import sys
from pathlib import Path
import numpy as np
from PIL import Image
import json

sys.path.insert(0, str(Path(__file__).parent))

from radar_tools import load_json_data


def analyze_image_colors(image_path: str):
    """Analyze what colors are actually in the image."""
    print("=" * 70)
    print("IMAGE COLOR ANALYSIS")
    print("=" * 70)
    
    img = Image.open(image_path).convert('RGB')
    img_array = np.array(img)
    
    print(f"\nImage: {Path(image_path).name}")
    print(f"Dimensions: {img.size[0]}x{img.size[1]}")
    
    # Get unique colors and their frequencies
    pixels = img_array.reshape(-1, 3)
    unique_colors, counts = np.unique(pixels, axis=0, return_counts=True)
    
    # Sort by frequency
    sorted_indices = np.argsort(counts)[::-1]
    top_colors = unique_colors[sorted_indices][:20]
    top_counts = counts[sorted_indices][:20]
    
    total_pixels = len(pixels)
    
    print(f"\nTotal unique colors: {len(unique_colors)}")
    print(f"\nTop 20 most common colors:")
    print(f"{'Rank':<6} {'RGB':<20} {'Count':<12} {'%':<8} {'Likely Type'}")
    print("-" * 70)
    
    for i, (color, count) in enumerate(zip(top_colors, top_counts)):
        percent = count / total_pixels * 100
        color_type = classify_color(color)
        print(f"{i+1:<6} RGB{tuple(color):<17} {count:<12} {percent:>6.2f}%  {color_type}")
    
    return unique_colors, counts


def classify_color(rgb):
    """Classify what type of color this likely is."""
    r, g, b = rgb
    
    # Beige/tan (map background)
    if 200 <= r <= 240 and 190 <= g <= 230 and 150 <= b <= 200:
        return "🗺️  Map Background"
    
    # White (no data / cloud returns)
    if r > 240 and g > 240 and b > 240:
        return "⬜ White/No Data"
    
    # Dark colors (text, borders)
    if r < 50 and g < 50 and b < 50:
        return "📝 Text/UI"
    
    # Blue tones (light precip)
    if b > r and b > g and b > 100:
        return "🌧️  Light Precip"
    
    # Green tones (moderate precip)
    if g > r and g > b and g > 100:
        return "🌧️  Moderate Precip"
    
    # Yellow/Orange (heavy precip)
    if r > 150 and g > 100 and b < 100:
        return "⚠️  Heavy Precip"
    
    # Red/Magenta (severe)
    if r > 150 and b > 100 and g < 100:
        return "🚨 Severe Weather"
    
    return "❓ Other"


def compare_with_scale(image_path: str, scale_path: str):
    """Compare image colors with scale colors."""
    print("\n" + "=" * 70)
    print("SCALE COMPARISON")
    print("=" * 70)
    
    # Load both images
    img = Image.open(image_path).convert('RGB')
    scale = Image.open(scale_path).convert('RGB')
    
    img_pixels = np.array(img).reshape(-1, 3)
    scale_pixels = np.array(scale).reshape(-1, 3)
    
    img_colors = set(map(tuple, img_pixels))
    scale_colors = set(map(tuple, scale_pixels))
    
    # Find colors in image but not in scale
    non_scale_colors = img_colors - scale_colors
    scale_only_colors = scale_colors - img_colors
    matching_colors = img_colors & scale_colors
    
    print(f"\nRadar image: {len(img_colors)} unique colors")
    print(f"Scale image: {len(scale_colors)} unique colors")
    print(f"Matching colors: {len(matching_colors)}")
    print(f"In radar but not scale: {len(non_scale_colors)} (this is normal - includes map/UI)")
    
    return non_scale_colors, scale_colors, matching_colors


def analyze_verification_result(original_path: str, data_json_path: str):
    """Analyze why verification accuracy might be low."""
    print("\n" + "=" * 70)
    print("VERIFICATION ACCURACY ANALYSIS")
    print("=" * 70)
    
    # Load data
    data = load_json_data(data_json_path)
    metadata = data['metadata']
    
    # Load original image
    img = Image.open(original_path).convert('RGB')
    img_array = np.array(img)
    
    print(f"\nOriginal image size: {img.size[0]}x{img.size[1]}")
    print(f"Sampled data size: {metadata['sampled_dimensions']['width']}x{metadata['sampled_dimensions']['height']}")
    print(f"Sample rate: {metadata['sample_rate']}")
    
    # Analyze what's being compared
    total_pixels = img.size[0] * img.size[1]
    
    # Count likely non-weather pixels
    pixels = img_array.reshape(-1, 3)
    
    # Beige/tan (map background)
    map_bg = np.sum((pixels[:, 0] > 200) & (pixels[:, 1] > 190) & (pixels[:, 2] > 150))
    
    # White (no data)
    white_pixels = np.sum((pixels[:, 0] > 240) & (pixels[:, 1] > 240) & (pixels[:, 2] > 240))
    
    # Very dark (text, UI)
    dark_pixels = np.sum((pixels[:, 0] < 50) & (pixels[:, 1] < 50) & (pixels[:, 2] < 50))
    
    non_weather = map_bg + white_pixels + dark_pixels
    weather_pixels = total_pixels - non_weather
    
    print(f"\nPixel breakdown (estimated):")
    print(f"  Weather data: {weather_pixels:,} ({weather_pixels/total_pixels*100:.1f}%)")
    print(f"  Map background: {map_bg:,} ({map_bg/total_pixels*100:.1f}%)")
    print(f"  No data (white): {white_pixels:,} ({white_pixels/total_pixels*100:.1f}%)")
    print(f"  UI/Text: {dark_pixels:,} ({dark_pixels/total_pixels*100:.1f}%)")
    
    print("\n" + "=" * 70)
    print("INTERPRETATION")
    print("=" * 70)
    
    weather_percent = weather_pixels / total_pixels * 100
    
    if weather_percent < 30:
        print("\n⚠️  LOW WEATHER DATA PERCENTAGE")
        print("Your image contains a lot of non-weather elements (map, UI, etc.)")
        print("\nRecommendations:")
        print("  1. Crop to just the radar sweep (circular pattern)")
        print("  2. Remove map background, labels, and UI elements")
        print("  3. Focus on the actual weather data area")
        print("\nThe verification tool measures ALL pixels, including non-weather")
        print("elements that won't match the color scale.")
    elif weather_percent < 50:
        print("\n⚡ MODERATE WEATHER DATA")
        print("About half your image is actual weather data.")
        print("Verification accuracy is lower because of map/UI elements.")
    else:
        print("\n✓ HIGH WEATHER DATA PERCENTAGE")
        print("Most of your image is actual weather data.")
        print("If accuracy is still low, check that scale images match.")


def main():
    """Main analysis function."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Analyze verification results and image composition'
    )
    parser.add_argument('original_image', help='Path to original radar image')
    parser.add_argument('--data-json', help='Path to JSON data file (for verification analysis)')
    parser.add_argument('--scale', help='Path to scale reference image')
    
    args = parser.parse_args()
    
    # Analyze image colors
    analyze_image_colors(args.original_image)
    
    # Compare with scale if provided
    if args.scale:
        compare_with_scale(args.original_image, args.scale)
    
    # Analyze verification if data provided
    if args.data_json:
        analyze_verification_result(args.original_image, args.data_json)
    
    print("\n" + "=" * 70)
    print("ANALYSIS COMPLETE")
    print("=" * 70)


if __name__ == '__main__':
    main()
