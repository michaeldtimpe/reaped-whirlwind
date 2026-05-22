#!/usr/bin/env python3
"""
Extract Scale Bar from Radar Images
Extracts the color scale directly from your radar screenshots.
"""

import sys
from pathlib import Path
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))


def extract_scale_bar(radar_image_path: str, output_path: str = None,
                     scale_region: tuple = None):
    """
    Extract the scale bar from a radar image.
    
    Args:
        radar_image_path: Path to radar image with scale bar
        output_path: Where to save extracted scale (optional)
        scale_region: (x, y, width, height) or None for auto-detect
        
    Returns:
        PIL Image of the scale bar
    """
    print("=" * 70)
    print("SCALE BAR EXTRACTION")
    print("=" * 70)
    
    # Load image
    img = Image.open(radar_image_path).convert('RGB')
    img_array = np.array(img)
    
    width, height = img.size
    print(f"\nRadar image: {width}x{height}")
    
    if scale_region is None:
        # Auto-detect scale bar location
        # For NWS images, scale bar is typically at bottom
        # Approximately: bottom 100 pixels, left 400 pixels
        
        # Look for the colorful horizontal bar
        # Scale bars have high color variance horizontally
        
        print("\nAuto-detecting scale bar...")
        
        # Check bottom 150 pixels
        bottom_section = img_array[-150:, :, :]
        
        # Find row with highest horizontal color variance
        max_variance = 0
        best_row = 0
        
        for i in range(len(bottom_section)):
            row = bottom_section[i]
            # Calculate variance across the row
            variance = np.var(row)
            if variance > max_variance:
                max_variance = variance
                best_row = i
        
        # The scale bar is likely around this row
        scale_y_bottom = height - 150 + best_row
        scale_y_top = max(0, scale_y_bottom - 40)  # Scale bar ~40 pixels tall
        
        # Find horizontal extent
        # Scale bars usually span 300-400 pixels
        scale_x_left = 0
        scale_x_right = min(400, width)
        
        print(f"  Detected scale region: x={scale_x_left}-{scale_x_right}, y={scale_y_top}-{scale_y_bottom}")
    else:
        scale_x_left, scale_y_top, scale_width, scale_height = scale_region
        scale_x_right = scale_x_left + scale_width
        scale_y_bottom = scale_y_top + scale_height
        print(f"  Using provided region: x={scale_x_left}-{scale_x_right}, y={scale_y_top}-{scale_y_bottom}")
    
    # Extract scale bar
    scale_bar = img.crop((scale_x_left, scale_y_top, scale_x_right, scale_y_bottom))
    
    print(f"  Extracted scale: {scale_bar.size[0]}x{scale_bar.size[1]}")
    
    # Analyze the scale
    scale_array = np.array(scale_bar)
    unique_colors = len(np.unique(scale_array.reshape(-1, 3), axis=0))
    print(f"  Unique colors: {unique_colors}")
    
    # Save if requested
    if output_path is None:
        output_path = f"extracted_scale_{Path(radar_image_path).stem}.png"
    
    scale_bar.save(output_path)
    print(f"\n✓ Scale bar saved to: {output_path}")
    
    # Show sample colors
    print("\nSample colors from scale (left to right):")
    for x in [0, scale_bar.size[0]//4, scale_bar.size[0]//2, 3*scale_bar.size[0]//4, scale_bar.size[0]-1]:
        # Sample from middle row
        y = scale_bar.size[1] // 2
        color = scale_array[y, x]
        print(f"  Position {x}: RGB{tuple(color)}")
    
    return scale_bar


def extract_from_multiple(image_paths: list, output_dir: str = "extracted_scales"):
    """
    Extract scales from multiple radar images.
    
    Args:
        image_paths: List of radar image paths
        output_dir: Directory to save extracted scales
    """
    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True, parents=True)
    
    print(f"\nExtracting scales from {len(image_paths)} images...")
    print()
    
    for i, img_path in enumerate(image_paths, 1):
        print(f"[{i}/{len(image_paths)}] {Path(img_path).name}")
        
        # Determine output filename
        stem = Path(img_path).stem
        if 'reflectivity' in stem.lower():
            output_name = 'extracted_reflectivity_scale.png'
        elif 'velocity' in stem.lower():
            output_name = 'extracted_velocity_scale.png'
        else:
            output_name = f'extracted_scale_{stem}.png'
        
        output_file = output_path / output_name
        
        try:
            extract_scale_bar(img_path, str(output_file))
            print()
        except Exception as e:
            print(f"  ✗ Failed: {e}")
            print()


def compare_scales(original_scale_path: str, extracted_scale_path: str):
    """Compare original scale with extracted scale."""
    print("=" * 70)
    print("SCALE COMPARISON")
    print("=" * 70)
    
    orig_img = Image.open(original_scale_path).convert('RGB')
    extr_img = Image.open(extracted_scale_path).convert('RGB')
    
    orig_array = np.array(orig_img)
    extr_array = np.array(extr_img)
    
    # Get unique colors
    orig_colors = set(map(tuple, orig_array.reshape(-1, 3)))
    extr_colors = set(map(tuple, extr_array.reshape(-1, 3)))
    
    matching = orig_colors & extr_colors
    
    print(f"\nOriginal scale: {len(orig_colors)} unique colors")
    print(f"Extracted scale: {len(extr_colors)} unique colors")
    print(f"Matching colors: {len(matching)}")
    print(f"Match rate: {len(matching) / len(orig_colors) * 100:.1f}%")
    
    if len(matching) / len(orig_colors) < 0.5:
        print("\n⚠ LOW MATCH RATE!")
        print("The extracted scale doesn't match your original scale image.")
        print("You should use the extracted scale for better accuracy.")


def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Extract scale bar from radar images',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Extract scale from single image
  python extract_scale.py radar_image.png
  
  # Extract with custom region (x, y, width, height)
  python extract_scale.py radar_image.png --region 0 1000 400 50
  
  # Extract from multiple images
  python extract_scale.py image1.png image2.png image3.png --output-dir scales/
  
  # Compare with existing scale
  python extract_scale.py radar_image.png --compare base_reflectivity_scale.png
        """
    )
    
    parser.add_argument('images', nargs='+', help='Radar image(s) to process')
    parser.add_argument('--output', '-o', help='Output path for scale image')
    parser.add_argument('--output-dir', help='Output directory for multiple images')
    parser.add_argument('--region', nargs=4, type=int, metavar=('X', 'Y', 'W', 'H'),
                       help='Scale region: x y width height')
    parser.add_argument('--compare', help='Compare with existing scale image')
    
    args = parser.parse_args()
    
    # Convert region if provided
    scale_region = tuple(args.region) if args.region else None
    
    if len(args.images) == 1:
        # Single image
        extracted_path = extract_scale_bar(
            args.images[0],
            args.output,
            scale_region
        )
        
        # Compare if requested
        if args.compare:
            print()
            compare_scales(args.compare, args.output or extracted_path)
    else:
        # Multiple images
        output_dir = args.output_dir or "extracted_scales"
        extract_from_multiple(args.images, output_dir)


if __name__ == '__main__':
    main()
