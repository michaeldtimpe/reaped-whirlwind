#!/usr/bin/env python3
"""
Command-line interface for weather radar data verification.
"""

import argparse
from pathlib import Path
from radar_tools import RadarImageVerifier


def main():
    """Command-line interface for the radar verifier."""
    parser = argparse.ArgumentParser(
        description='Verify radar data conversions by reconstructing and comparing images',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Verify a single conversion
  python verify.py original.png data.json --output-dir verification/
  
  # Verify without showing difference map
  python verify.py original.png data.json --output-dir verification/ --no-difference
  
  # Reconstruct image only (no comparison)
  python verify.py --reconstruct-only data.json --output reconstructed.png
        """
    )
    
    # Main arguments
    parser.add_argument('original_image', nargs='?',
                       help='Path to original radar image')
    parser.add_argument('data_json', help='Path to JSON data file')
    parser.add_argument('--output-dir', '-o', default='verification_output',
                       help='Directory for verification outputs (default: verification_output)')
    
    # Options
    parser.add_argument('--reflectivity-scale', 
                       default='base_reflectivity_intensity_scale.png',
                       help='Path to reflectivity scale reference image')
    parser.add_argument('--velocity-scale', 
                       default='base_velocity_intensity_scale.png',
                       help='Path to velocity scale reference image')
    parser.add_argument('--no-difference', action='store_true',
                       help='Do not create difference visualization')
    
    # Reconstruct only mode
    parser.add_argument('--reconstruct-only', action='store_true',
                       help='Only reconstruct image, do not compare')
    parser.add_argument('--output', help='Output path for reconstructed image (reconstruct-only mode)')
    parser.add_argument('--upscale', type=int, default=1,
                       help='Upscale factor for reconstructed image')
    
    args = parser.parse_args()
    
    # Validate JSON file exists
    if not Path(args.data_json).exists():
        print(f"Error: JSON data file not found: {args.data_json}")
        return 1
    
    # Create verifier
    try:
        verifier = RadarImageVerifier(args.reflectivity_scale, args.velocity_scale)
    except FileNotFoundError as e:
        print(f"Error: Scale image not found: {e}")
        print("Please ensure scale reference images are in the current directory")
        return 1
    
    try:
        if args.reconstruct_only:
            # Reconstruct only mode
            if not args.output:
                print("Error: --output is required in --reconstruct-only mode")
                return 1
            
            from radar_tools import load_json_data
            data = load_json_data(args.data_json)
            verifier.data_to_image(data, args.output, args.upscale)
            print("\n✓ Image reconstruction completed successfully!")
            
        else:
            # Full verification mode
            if not args.original_image:
                print("Error: original_image is required for verification")
                print("Use --reconstruct-only if you only want to reconstruct the image")
                return 1
            
            if not Path(args.original_image).exists():
                print(f"Error: Original image not found: {args.original_image}")
                return 1
            
            metrics = verifier.verify_conversion(
                args.original_image,
                args.data_json,
                args.output_dir,
                show_difference=not args.no_difference
            )
            print("\n✓ Verification completed successfully!")
        
        return 0
        
    except Exception as e:
        print(f"Error during verification: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    exit(main())
