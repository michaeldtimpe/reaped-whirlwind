#!/usr/bin/env python3
"""
Command-line interface for weather radar image conversion.
"""

import argparse
from pathlib import Path
from radar_tools import RadarImageConverter


def main():
    """Command-line interface for the radar converter."""
    parser = argparse.ArgumentParser(
        description='Convert weather radar images to structured JSON data',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Convert a reflectivity image
  python convert.py image.png --type reflectivity --output data.json
  
  # Convert with sampling for efficiency
  python convert.py image.png --type velocity --output data.json --sample-rate 4
  
  # Also save as NumPy array
  python convert.py image.png --type reflectivity --output data.json --save-numpy
        """
    )
    
    parser.add_argument('image', help='Path to radar image to convert')
    parser.add_argument('--type', '-t', required=True, 
                       choices=['reflectivity', 'velocity'],
                       help='Type of radar data')
    parser.add_argument('--output', '-o', required=True,
                       help='Output JSON file path')
    parser.add_argument('--reflectivity-scale', 
                       default='base_reflectivity_intensity_scale.png',
                       help='Path to reflectivity scale reference image')
    parser.add_argument('--velocity-scale', 
                       default='base_velocity_intensity_scale.png',
                       help='Path to velocity scale reference image')
    parser.add_argument('--sample-rate', '-s', type=int, default=1,
                       help='Sample every Nth pixel (default: 1 = all pixels)')
    parser.add_argument('--save-numpy', action='store_true',
                       help='Also save data as NumPy .npy file')
    
    args = parser.parse_args()
    
    # Validate input file exists
    if not Path(args.image).exists():
        print(f"Error: Image file not found: {args.image}")
        return 1
    
    # Create converter
    try:
        converter = RadarImageConverter(args.reflectivity_scale, args.velocity_scale)
    except FileNotFoundError as e:
        print(f"Error: Scale image not found: {e}")
        print("Please ensure scale reference images are in the current directory")
        return 1
    
    # Convert and save
    try:
        converter.convert_and_save(
            args.image,
            args.type,
            args.output,
            args.sample_rate,
            args.save_numpy
        )
        print("\n✓ Conversion completed successfully!")
        return 0
    except Exception as e:
        print(f"Error during conversion: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    exit(main())
