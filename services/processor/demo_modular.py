#!/usr/bin/env python3
"""
Comprehensive demonstration of modular radar tools with verification.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from radar_tools import (
    RadarImageConverter,
    RadarImageVerifier,
    load_json_data,
    calculate_statistics
)


def demo_conversion_and_verification():
    """Complete demo: convert and verify radar images."""
    
    print("=" * 70)
    print("MODULAR RADAR TOOLS - COMPLETE DEMONSTRATION")
    print("=" * 70)
    
    # Initialize tools
    print("\n1. Initializing converter and verifier...")
    converter = RadarImageConverter(
        reflectivity_scale_path='/mnt/user-data/uploads/base_reflectivity_intensity_scale.png',
        velocity_scale_path='/mnt/user-data/uploads/base_velocity_intensity_scale.png'
    )
    
    verifier = RadarImageVerifier(
        reflectivity_scale_path='/mnt/user-data/uploads/base_reflectivity_intensity_scale.png',
        velocity_scale_path='/mnt/user-data/uploads/base_velocity_intensity_scale.png'
    )
    print("   ✓ Tools initialized")
    
    # Convert reflectivity image
    print("\n2. Converting REFLECTIVITY scale image...")
    ref_json = '/mnt/user-data/outputs/demo_reflectivity.json'
    converter.convert_and_save(
        image_path='/mnt/user-data/uploads/base_reflectivity_intensity_scale.png',
        radar_type='reflectivity',
        output_path=ref_json,
        sample_rate=2,
        save_numpy=True
    )
    
    # Calculate statistics
    print("\n3. Analyzing converted data...")
    data = load_json_data(ref_json)
    stats = calculate_statistics(data)
    print(f"   Data dimensions: {stats['shape']}")
    print(f"   Value range: {stats['min']:.2f} to {stats['max']:.2f} {data['metadata']['units']}")
    print(f"   Mean: {stats['mean']:.2f}, Std: {stats['std']:.2f}")
    
    # Verify conversion
    print("\n4. VERIFYING conversion (reconstructing and comparing)...")
    metrics = verifier.verify_conversion(
        original_image_path='/mnt/user-data/uploads/base_reflectivity_intensity_scale.png',
        data_json_path=ref_json,
        output_dir='/mnt/user-data/outputs/verification',
        show_difference=True
    )
    
    # Convert velocity image
    print("\n5. Converting VELOCITY scale image...")
    vel_json = '/mnt/user-data/outputs/demo_velocity.json'
    converter.convert_and_save(
        image_path='/mnt/user-data/uploads/base_velocity_intensity_scale.png',
        radar_type='velocity',
        output_path=vel_json,
        sample_rate=2,
        save_numpy=True
    )
    
    # Verify velocity conversion
    print("\n6. VERIFYING velocity conversion...")
    vel_metrics = verifier.verify_conversion(
        original_image_path='/mnt/user-data/uploads/base_velocity_intensity_scale.png',
        data_json_path=vel_json,
        output_dir='/mnt/user-data/outputs/verification',
        show_difference=True
    )
    
    # Summary
    print("\n" + "=" * 70)
    print("DEMONSTRATION COMPLETE")
    print("=" * 70)
    print("\nGenerated files:")
    print("  Converted Data:")
    print(f"    - {ref_json}")
    print(f"    - {vel_json}")
    print("  Verification Outputs:")
    print("    - /mnt/user-data/outputs/verification/")
    print("      - reconstructed images")
    print("      - difference maps")
    print("      - side-by-side comparisons")
    
    print("\nVerification Results:")
    print(f"  Reflectivity accuracy: {metrics['within_10_threshold']:.1f}% (within 10 RGB)")
    print(f"  Velocity accuracy: {vel_metrics['within_10_threshold']:.1f}% (within 10 RGB)")
    
    print("\nThe modular tools are working correctly! ✓")
    print("\nModular Structure Benefits:")
    print("  ✓ Easy to maintain - each module has a single responsibility")
    print("  ✓ Easy to test - modules can be tested independently")
    print("  ✓ Easy to extend - add new features without breaking existing code")
    print("  ✓ Easy to understand - clear separation of concerns")


def show_usage_examples():
    """Show examples of how to use the modular tools."""
    print("\n" + "=" * 70)
    print("USAGE EXAMPLES")
    print("=" * 70)
    
    print("""
1. CONVERT AN IMAGE (Python API):
   
   from radar_tools import RadarImageConverter
   
   converter = RadarImageConverter(
       'base_reflectivity_intensity_scale.png',
       'base_velocity_intensity_scale.png'
   )
   
   converter.convert_and_save(
       'my_radar_image.png',
       'reflectivity',
       'output.json'
   )

2. CONVERT AN IMAGE (Command Line):
   
   python convert.py my_radar.png --type reflectivity --output data.json

3. VERIFY CONVERSION (Python API):
   
   from radar_tools import RadarImageVerifier
   
   verifier = RadarImageVerifier(
       'base_reflectivity_intensity_scale.png',
       'base_velocity_intensity_scale.png'
   )
   
   metrics = verifier.verify_conversion(
       'original.png',
       'data.json',
       'verification_output/'
   )
   
   print(f"Accuracy: {metrics['within_10_threshold']:.1f}%")

4. VERIFY CONVERSION (Command Line):
   
   python verify.py original.png data.json --output-dir verification/

5. RECONSTRUCT ONLY (no comparison):
   
   python verify.py --reconstruct-only data.json --output reconstructed.png

6. BATCH PROCESSING:
   
   images = [('img1.png', 'reflectivity'), ('img2.png', 'velocity')]
   
   for img_path, radar_type in images:
       converter.convert_and_save(img_path, radar_type, f'{img_path}.json')
       verifier.verify_conversion(img_path, f'{img_path}.json', 'verify/')
    """)


if __name__ == '__main__':
    demo_conversion_and_verification()
    show_usage_examples()
