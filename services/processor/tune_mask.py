#!/usr/bin/env python3
"""
Mask Tuning Tool
Helps you visualize and adjust what pixels are included/excluded from verification.
"""

import sys
from pathlib import Path
import numpy as np
from PIL import Image
import argparse

sys.path.insert(0, str(Path(__file__).parent))


def create_adjustable_mask(img_array: np.ndarray, 
                          map_bg_tolerance: int = 25,
                          dark_threshold: int = 50,
                          white_threshold: int = 245) -> np.ndarray:
    """
    Create mask with adjustable parameters.
    
    Args:
        img_array: RGB image array
        map_bg_tolerance: How much variation to allow in map background detection
        dark_threshold: Max RGB value to consider "dark" (text/UI)
        white_threshold: Min RGB value to consider "white" (no data)
        
    Returns:
        Boolean mask (True = include, False = exclude)
    """
    height, width = img_array.shape[:2]
    mask = np.ones((height, width), dtype=bool)
    
    r = img_array[:, :, 0].astype(float)
    g = img_array[:, :, 1].astype(float)
    b = img_array[:, :, 2].astype(float)
    
    # Map background (beige/tan) - centered around (220, 210, 175)
    map_center_r, map_center_g, map_center_b = 220, 210, 175
    map_bg = (
        (np.abs(r - map_center_r) <= map_bg_tolerance) &
        (np.abs(g - map_center_g) <= map_bg_tolerance) &
        (np.abs(b - map_center_b) <= map_bg_tolerance)
    )
    mask &= ~map_bg
    
    # White (no data)
    white = (r >= white_threshold) & (g >= white_threshold) & (b >= white_threshold)
    mask &= ~white
    
    # Dark (text/borders)
    dark = (r <= dark_threshold) & (g <= dark_threshold) & (b <= dark_threshold)
    mask &= ~dark
    
    return mask


def visualize_mask(image_path: str, 
                  map_bg_tolerance: int = 25,
                  dark_threshold: int = 50,
                  white_threshold: int = 245,
                  output_path: str = None):
    """
    Visualize what the mask includes/excludes.
    
    Args:
        image_path: Path to radar image
        map_bg_tolerance: Tolerance for map background detection
        dark_threshold: Threshold for dark pixels
        white_threshold: Threshold for white pixels
        output_path: Where to save visualization
    """
    print("=" * 70)
    print("MASK VISUALIZATION")
    print("=" * 70)
    
    # Load image
    img = Image.open(image_path).convert('RGB')
    img_array = np.array(img)
    
    print(f"\nImage: {Path(image_path).name}")
    print(f"Dimensions: {img.size[0]}x{img.size[1]}")
    print(f"\nMask parameters:")
    print(f"  Map background tolerance: ±{map_bg_tolerance}")
    print(f"  Dark threshold: ≤{dark_threshold}")
    print(f"  White threshold: ≥{white_threshold}")
    
    # Create mask
    mask = create_adjustable_mask(img_array, map_bg_tolerance, dark_threshold, white_threshold)
    
    # Calculate statistics
    total_pixels = mask.size
    included_pixels = np.sum(mask)
    excluded_pixels = total_pixels - included_pixels
    
    print(f"\nMask results:")
    print(f"  Included (weather): {included_pixels:,} ({included_pixels/total_pixels*100:.1f}%)")
    print(f"  Excluded (map/UI): {excluded_pixels:,} ({excluded_pixels/total_pixels*100:.1f}%)")
    
    # Create visualization
    # Original image
    original = img_array.copy()
    
    # Masked areas (gray out excluded pixels)
    masked_overlay = img_array.copy()
    masked_overlay[~mask] = [180, 180, 180]  # Gray
    
    # Mask only (white = included, black = excluded)
    mask_vis = (mask * 255).astype(np.uint8)
    mask_rgb = np.stack([mask_vis, mask_vis, mask_vis], axis=2)
    
    # Highlighted (show only included pixels, rest black)
    highlighted = img_array.copy()
    highlighted[~mask] = [0, 0, 0]  # Black
    
    # Create side-by-side comparison
    h, w = img_array.shape[:2]
    canvas = np.zeros((h * 2 + 20, w * 2 + 20, 3), dtype=np.uint8)
    canvas.fill(255)  # White background
    
    # Place images
    canvas[0:h, 0:w] = original
    canvas[0:h, w+20:w*2+20] = masked_overlay
    canvas[h+20:h*2+20, 0:w] = mask_rgb
    canvas[h+20:h*2+20, w+20:w*2+20] = highlighted
    
    # Save
    if output_path is None:
        output_path = f"mask_visualization_{Path(image_path).stem}.png"
    
    Image.fromarray(canvas).save(output_path)
    print(f"\nVisualization saved to: {output_path}")
    print("\nVisualization layout:")
    print("  Top-left:     Original image")
    print("  Top-right:    Gray overlay on excluded pixels")
    print("  Bottom-left:  Mask (white=included, black=excluded)")
    print("  Bottom-right: Only included pixels (rest black)")
    
    return mask, included_pixels, total_pixels


def suggest_parameters(image_path: str):
    """Analyze image and suggest optimal mask parameters."""
    print("\n" + "=" * 70)
    print("PARAMETER SUGGESTIONS")
    print("=" * 70)
    
    img = Image.open(image_path).convert('RGB')
    img_array = np.array(img)
    
    # Sample colors to find dominant background
    pixels = img_array.reshape(-1, 3)
    unique_colors, counts = np.unique(pixels, axis=0, return_counts=True)
    
    # Find most common non-white, non-dark colors (likely map background)
    sorted_indices = np.argsort(counts)[::-1]
    
    for color, count in zip(unique_colors[sorted_indices][:20], counts[sorted_indices][:20]):
        r, g, b = color
        if 100 < r < 250 and 100 < g < 250 and 100 < b < 250:  # Not pure white/black
            print(f"\nMost common background color: RGB{tuple(color)}")
            print(f"  Appears {count:,} times ({count/len(pixels)*100:.1f}%)")
            print(f"\nSuggested parameters:")
            print(f"  --map-bg-tolerance: Try values between 20-40")
            print(f"  Centered around RGB({r}, {g}, {b})")
            break
    
    print("\nTo adjust the mask, try:")
    print("  python tune_mask.py image.png --map-bg-tolerance 30")
    print("  python tune_mask.py image.png --dark-threshold 60")
    print("  python tune_mask.py image.png --white-threshold 240")


def main():
    parser = argparse.ArgumentParser(
        description='Visualize and tune the weather data mask'
    )
    parser.add_argument('image', help='Path to radar image')
    parser.add_argument('--map-bg-tolerance', type=int, default=25,
                       help='Tolerance for map background detection (default: 25)')
    parser.add_argument('--dark-threshold', type=int, default=50,
                       help='Max RGB for dark pixels (default: 50)')
    parser.add_argument('--white-threshold', type=int, default=245,
                       help='Min RGB for white pixels (default: 245)')
    parser.add_argument('--output', '-o', help='Output path for visualization')
    parser.add_argument('--suggest', action='store_true',
                       help='Analyze image and suggest parameters')
    
    args = parser.parse_args()
    
    if args.suggest:
        suggest_parameters(args.image)
    
    # Create visualization
    mask, included, total = visualize_mask(
        args.image,
        args.map_bg_tolerance,
        args.dark_threshold,
        args.white_threshold,
        args.output
    )
    
    # Give feedback
    weather_pct = included / total * 100
    print("\n" + "=" * 70)
    print("FEEDBACK")
    print("=" * 70)
    
    if weather_pct < 20:
        print("\n⚠️  Very few pixels included!")
        print("Your mask might be too aggressive. Try:")
        print(f"  --map-bg-tolerance {args.map_bg_tolerance + 10}")
    elif weather_pct > 80:
        print("\n⚠️  Most pixels included!")
        print("Your mask might not be filtering enough. Try:")
        print(f"  --map-bg-tolerance {max(5, args.map_bg_tolerance - 10)}")
    else:
        print("\n✓ Looks reasonable!")
        print(f"{weather_pct:.1f}% of pixels identified as weather data.")
    
    print("\nOnce you're happy with the mask, use it in verify_masked.py")
    print("(You'll need to update the tolerance values in the code)")


if __name__ == '__main__':
    main()
