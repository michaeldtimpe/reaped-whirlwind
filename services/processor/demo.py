#!/usr/bin/env python3
"""
Demo: Convert the scale images and visualize the results
"""

from radar_converter import RadarImageConverter
import json
import numpy as np


def demo_conversion():
    """Demonstrate the converter with the scale images."""
    print("=" * 70)
    print("WEATHER RADAR CONVERTER - DEMONSTRATION")
    print("=" * 70)
    
    # Initialize converter
    print("\n1. Initializing converter with scale reference images...")
    converter = RadarImageConverter(
        reflectivity_scale_path='/mnt/user-data/uploads/base_reflectivity_intensity_scale.png',
        velocity_scale_path='/mnt/user-data/uploads/base_velocity_intensity_scale.png'
    )
    print("   ✓ Converter ready")
    
    # Convert reflectivity scale
    print("\n2. Converting BASE REFLECTIVITY scale image...")
    print("   This demonstrates the color-to-value mapping")
    converter.convert_and_save(
        image_path='/mnt/user-data/uploads/base_reflectivity_intensity_scale.png',
        radar_type='reflectivity',
        output_path='/mnt/user-data/outputs/reflectivity_scale_demo.json',
        sample_rate=2,
        save_numpy=True
    )
    
    # Convert velocity scale
    print("\n3. Converting BASE VELOCITY scale image...")
    converter.convert_and_save(
        image_path='/mnt/user-data/uploads/base_velocity_intensity_scale.png',
        radar_type='velocity',
        output_path='/mnt/user-data/outputs/velocity_scale_demo.json',
        sample_rate=2,
        save_numpy=True
    )
    
    # Load and display results
    print("\n4. Analysis of converted data...")
    
    # Reflectivity analysis
    print("\n   REFLECTIVITY DATA:")
    with open('/mnt/user-data/outputs/reflectivity_scale_demo.json', 'r') as f:
        ref_data = json.load(f)
    
    ref_array = np.array(ref_data['data'])
    print(f"   - Data shape: {ref_array.shape}")
    print(f"   - Value range: {ref_array.min():.2f} to {ref_array.max():.2f} dBZ")
    print(f"   - Mean: {ref_array.mean():.2f} dBZ")
    print(f"   - Units: {ref_data['metadata']['units']}")
    
    # Show value distribution
    low = np.sum(ref_array < 0)
    moderate = np.sum((ref_array >= 0) & (ref_array < 40))
    high = np.sum(ref_array >= 40)
    print(f"   - Distribution: Low={low} | Moderate={moderate} | High={high} pixels")
    
    # Velocity analysis
    print("\n   VELOCITY DATA:")
    with open('/mnt/user-data/outputs/velocity_scale_demo.json', 'r') as f:
        vel_data = json.load(f)
    
    vel_array = np.array(vel_data['data'])
    print(f"   - Data shape: {vel_array.shape}")
    print(f"   - Value range: {vel_array.min():.2f} to {vel_array.max():.2f} knots")
    print(f"   - Mean: {vel_array.mean():.2f} knots")
    print(f"   - Units: {vel_data['metadata']['units']}")
    
    # Show value distribution
    away = np.sum(vel_array < -20)
    neutral = np.sum((vel_array >= -20) & (vel_array <= 20))
    toward = np.sum(vel_array > 20)
    print(f"   - Distribution: Away={away} | Neutral={neutral} | Toward={toward} pixels")
    
    print("\n5. Output files created:")
    print("   - /mnt/user-data/outputs/reflectivity_scale_demo.json")
    print("   - /mnt/user-data/outputs/reflectivity_scale_demo.npy")
    print("   - /mnt/user-data/outputs/velocity_scale_demo.json")
    print("   - /mnt/user-data/outputs/velocity_scale_demo.npy")
    
    print("\n" + "=" * 70)
    print("DEMO COMPLETE")
    print("=" * 70)
    print("\nNext steps:")
    print("1. Use your actual radar images with the same command")
    print("2. The converter will extract the same color-to-value mappings")
    print("3. Output will be ready for ML training or analysis")
    print("\nExample command:")
    print("  python radar_converter.py your_radar.png --type reflectivity --output data.json")


def show_sample_data():
    """Show a sample of the converted data."""
    print("\n" + "=" * 70)
    print("SAMPLE DATA PREVIEW")
    print("=" * 70)
    
    with open('/mnt/user-data/outputs/reflectivity_scale_demo.json', 'r') as f:
        data = json.load(f)
    
    print("\nFirst 5x5 pixels of reflectivity data (dBZ):")
    data_array = np.array(data['data'])
    sample = data_array[:5, :5]
    print(sample)
    
    print("\n" + "=" * 70)


if __name__ == '__main__':
    demo_conversion()
    show_sample_data()
    
    print("\nTo use with your own radar images:")
    print("  python radar_converter.py <your_image.png> --type <reflectivity|velocity> --output <output.json>")
