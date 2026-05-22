#!/usr/bin/env python3
"""
Scale Extraction Refiner
Fine-tune your scale extraction with instant visual feedback.
"""

import sys
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).parent))


def refine_extraction(image_path: str, 
                     current_x: int, current_y: int, 
                     current_width: int, current_height: int,
                     adjust_x: int = 0, adjust_y: int = 0,
                     adjust_width: int = 0, adjust_height: int = 0,
                     output_path: str = None):
    """
    Refine scale extraction with adjustments.
    
    Args:
        image_path: Path to radar image
        current_x, current_y: Current top-left position
        current_width, current_height: Current dimensions
        adjust_x: Pixels to move right (+) or left (-)
        adjust_y: Pixels to move down (+) or up (-)
        adjust_width: Pixels to add to width (+) or subtract (-)
        adjust_height: Pixels to add to height (+) or subtract (-)
        output_path: Where to save
    """
    # Calculate new coordinates
    new_x = current_x + adjust_x
    new_y = current_y + adjust_y
    new_width = current_width + adjust_width
    new_height = current_height + adjust_height
    
    print("=" * 70)
    print("SCALE EXTRACTION REFINEMENT")
    print("=" * 70)
    print(f"\nOriginal: x={current_x}, y={current_y}, w={current_width}, h={current_height}")
    print(f"Adjusted: x={new_x}, y={new_y}, w={new_width}, h={new_height}")
    print(f"Changes:  Δx={adjust_x:+d}, Δy={adjust_y:+d}, Δw={adjust_width:+d}, Δh={adjust_height:+d}")
    
    # Load image
    img = Image.open(image_path).convert('RGB')
    
    # Extract new region
    scale_bar = img.crop((new_x, new_y, new_x + new_width, new_y + new_height))
    
    # Analyze
    scale_array = np.array(scale_bar)
    unique_colors = len(np.unique(scale_array.reshape(-1, 3), axis=0))
    
    print(f"\nExtracted: {new_width}x{new_height}")
    print(f"Unique colors: {unique_colors}")
    
    # Sample colors across the scale
    print("\nColor gradient (left to right):")
    sample_points = [0, new_width//6, new_width//3, new_width//2, 
                     2*new_width//3, 5*new_width//6, new_width-1]
    
    for i, x_pos in enumerate(sample_points):
        y_mid = new_height // 2
        color = scale_array[y_mid, x_pos]
        color_name = describe_color(color)
        print(f"  Position {i+1}/7 (x={x_pos:3d}): RGB{tuple(color):<20} {color_name}")
    
    # Save
    if output_path is None:
        output_path = 'refined_scale.png'
    
    scale_bar.save(output_path)
    print(f"\n✓ Saved to: {output_path}")
    
    # Create comparison if previous scale exists
    if Path('extracted_reflectivity_scale.png').exists():
        create_comparison(
            'extracted_reflectivity_scale.png',
            output_path,
            'scale_comparison.png'
        )
    
    # Suggestions
    print("\n" + "=" * 70)
    print("SUGGESTIONS")
    print("=" * 70)
    
    if unique_colors < 300:
        print("⚠️  Few unique colors - might be too small or in wrong area")
    elif unique_colors > 800:
        print("✓ Good color variety!")
    
    # Check if we have dark bar at top
    top_row = scale_array[0, :]
    if np.mean(top_row) < 100:
        print("⚠️  Dark bar at top - try: --adjust-y +5 --adjust-height -5")
    
    # Check if we have full gradient
    left_colors = scale_array[:, :20]
    right_colors = scale_array[:, -20:]
    
    left_mean = np.mean(left_colors)
    right_mean = np.mean(right_colors)
    
    if left_mean > right_mean + 50:
        print("⚠️  Missing right side colors - try: --adjust-width +20")
    
    print("\nNext steps:")
    print("  python refine_scale.py ... --adjust-x X --adjust-y Y --adjust-width W --adjust-height H")
    
    return new_x, new_y, new_width, new_height


def describe_color(rgb):
    """Give a human-readable color description."""
    r, g, b = rgb
    
    # Dark/black
    if r < 60 and g < 60 and b < 60:
        return "🖤 Dark/Black (UI)"
    
    # Blue tones
    if b > r + 30 and b > g + 30:
        if b > 150:
            return "🔵 Bright Blue (light precip)"
        else:
            return "🔵 Blue (very light)"
    
    # Green tones
    if g > r + 20 and g > b + 20:
        return "🟢 Green (moderate precip)"
    
    # Yellow
    if r > 150 and g > 150 and b < 100:
        return "🟡 Yellow (heavy precip)"
    
    # Orange/Red
    if r > 150 and g < 150:
        if r > 200:
            return "🔴 Red (severe)"
        else:
            return "🟠 Orange (heavy)"
    
    # Purple/Magenta
    if r > 100 and b > 100 and g < 100:
        return "🟣 Purple (extreme)"
    
    # Gray
    if abs(r - g) < 20 and abs(g - b) < 20:
        return "⚪ Gray"
    
    return "❓ Other"


def create_comparison(old_path: str, new_path: str, output_path: str):
    """Create side-by-side comparison of old and new scales."""
    old_img = Image.open(old_path)
    new_img = Image.open(new_path)
    
    # Create canvas
    max_width = max(old_img.width, new_img.width)
    total_height = old_img.height + new_img.height + 60
    
    canvas = Image.new('RGB', (max_width, total_height), color='white')
    
    # Paste images
    canvas.paste(old_img, (0, 30))
    canvas.paste(new_img, (0, old_img.height + 60))
    
    # Add labels
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
    except:
        font = ImageFont.load_default()
    
    draw.text((10, 5), "Before (old extraction)", fill='black', font=font)
    draw.text((10, old_img.height + 35), "After (refined)", fill='black', font=font)
    
    canvas.save(output_path)
    print(f"✓ Comparison saved to: {output_path}")


def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Refine scale extraction',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Move down 5px and make 5px shorter (remove top UI bar)
  python refine_scale.py image.png 5 1030 380 35 --adjust-y +5 --adjust-height -5
  
  # Extend 30px to the right (get more colors)
  python refine_scale.py image.png 5 1030 380 35 --adjust-width +30
  
  # Move up and make taller
  python refine_scale.py image.png 5 1030 380 35 --adjust-y -2 --adjust-height +2
        """
    )
    
    parser.add_argument('image', help='Radar image path')
    parser.add_argument('x', type=int, help='Current X coordinate')
    parser.add_argument('y', type=int, help='Current Y coordinate')
    parser.add_argument('width', type=int, help='Current width')
    parser.add_argument('height', type=int, help='Current height')
    
    parser.add_argument('--adjust-x', type=int, default=0,
                       help='Move left (-) or right (+)')
    parser.add_argument('--adjust-y', type=int, default=0,
                       help='Move up (-) or down (+)')
    parser.add_argument('--adjust-width', type=int, default=0,
                       help='Make narrower (-) or wider (+)')
    parser.add_argument('--adjust-height', type=int, default=0,
                       help='Make shorter (-) or taller (+)')
    parser.add_argument('--output', '-o', default='refined_scale.png',
                       help='Output path')
    
    args = parser.parse_args()
    
    refine_extraction(
        args.image,
        args.x, args.y, args.width, args.height,
        args.adjust_x, args.adjust_y,
        args.adjust_width, args.adjust_height,
        args.output
    )


if __name__ == '__main__':
    main()