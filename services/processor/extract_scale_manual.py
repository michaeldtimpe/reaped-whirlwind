#!/usr/bin/env python3
"""
Manual Scale Extractor with Visual Preview
Helps you manually select the scale bar region.
"""

import sys
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parent))


def extract_with_coordinates(image_path: str, x: int, y: int, width: int, height: int,
                             output_path: str = None):
    """
    Extract scale with exact coordinates.
    
    Args:
        image_path: Path to radar image
        x, y: Top-left corner of scale bar
        width, height: Dimensions of scale bar
        output_path: Where to save (optional)
    """
    img = Image.open(image_path).convert('RGB')
    
    # Extract region
    scale_bar = img.crop((x, y, x + width, y + height))
    
    # Save
    if output_path is None:
        radar_type = 'reflectivity' if 'reflectivity' in Path(image_path).stem.lower() else 'velocity'
        output_path = f'extracted_{radar_type}_scale.png'
    
    scale_bar.save(output_path)
    
    # Show info
    scale_array = np.array(scale_bar)
    unique_colors = len(np.unique(scale_array.reshape(-1, 3), axis=0))
    
    print(f"Extracted scale: {width}x{height}")
    print(f"Unique colors: {unique_colors}")
    print(f"Saved to: {output_path}")
    
    # Sample colors
    print("\nColor samples (left to right):")
    for i in [0, width//4, width//2, 3*width//4, width-1]:
        y_mid = height // 2
        color = scale_array[y_mid, i]
        print(f"  Position {i:3d}: RGB{tuple(color)}")
    
    return output_path


def create_selection_guide(image_path: str, output_path: str = 'scale_selection_guide.png'):
    """
    Create a guide image with coordinate grid to help select scale region.
    """
    img = Image.open(image_path).convert('RGB')
    width, height = img.size
    
    # Create copy for drawing
    guide = img.copy()
    draw = ImageDraw.Draw(guide)
    
    # Draw grid every 100 pixels
    for x in range(0, width, 100):
        draw.line([(x, 0), (x, height)], fill='red', width=1)
        if x > 0:
            draw.text((x, 10), str(x), fill='red')
    
    for y in range(0, height, 100):
        draw.line([(0, y), (width, y)], fill='red', width=1)
        if y > 0:
            draw.text((10, y), str(y), fill='red')
    
    # Highlight likely scale region
    # Bottom 150 pixels
    draw.rectangle([(0, height-150), (width, height)], outline='yellow', width=3)
    draw.text((10, height-160), "Scale bar likely in this region", fill='yellow')
    
    guide.save(output_path)
    print(f"Selection guide saved to: {output_path}")
    print(f"Image size: {width}x{height}")
    print("\nUse the yellow highlighted region as a starting point.")
    print("The scale bar is typically:")
    print("  - At the bottom of the image")
    print("  - About 300-400 pixels wide")
    print("  - About 30-50 pixels tall")
    
    return output_path


def suggest_reflectivity_region():
    """Suggest common reflectivity scale bar locations."""
    print("\nCommon REFLECTIVITY scale bar regions:")
    print("  NWS Standard: x=0, y=1030, width=350, height=35")
    print("  Alternative 1: x=50, y=1020, width=300, height=40")
    print("  Alternative 2: x=0, y=1000, width=400, height=50")


def suggest_velocity_region():
    """Suggest common velocity scale bar locations."""
    print("\nCommon VELOCITY scale bar regions:")
    print("  NWS Standard: x=0, y=1030, width=350, height=35")
    print("  Alternative 1: x=50, y=1020, width=300, height=40")
    print("  Alternative 2: x=0, y=1000, width=400, height=50")


def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Manual scale extraction with visual guide',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Create selection guide
  python extract_scale_manual.py guide radar.png
  
  # Extract with coordinates
  python extract_scale_manual.py extract radar.png 0 1030 350 35
  
  # Get suggestions
  python extract_scale_manual.py suggest reflectivity
        """
    )
    
    subparsers = parser.add_subparsers(dest='command')
    
    # Guide command
    guide_parser = subparsers.add_parser('guide', help='Create selection guide')
    guide_parser.add_argument('image', help='Radar image')
    
    # Extract command
    extract_parser = subparsers.add_parser('extract', help='Extract scale')
    extract_parser.add_argument('image', help='Radar image')
    extract_parser.add_argument('x', type=int, help='X coordinate')
    extract_parser.add_argument('y', type=int, help='Y coordinate')
    extract_parser.add_argument('width', type=int, help='Width')
    extract_parser.add_argument('height', type=int, help='Height')
    extract_parser.add_argument('--output', '-o', help='Output path')
    
    # Suggest command
    suggest_parser = subparsers.add_parser('suggest', help='Suggest regions')
    suggest_parser.add_argument('type', choices=['reflectivity', 'velocity'])
    
    args = parser.parse_args()
    
    if args.command == 'guide':
        create_selection_guide(args.image)
    elif args.command == 'extract':
        extract_with_coordinates(
            args.image,
            args.x, args.y,
            args.width, args.height,
            args.output
        )
    elif args.command == 'suggest':
        if args.type == 'reflectivity':
            suggest_reflectivity_region()
        else:
            suggest_velocity_region()
    else:
        parser.print_help()


if __name__ == '__main__':
    main()