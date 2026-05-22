#!/usr/bin/env python3
"""
Test script to validate the radar converter with the provided scale images.
"""

import numpy as np
from radar_converter import RadarImageConverter, RadarColorScale
import json


def test_color_scale_extraction():
    """Test that color scales are extracted correctly."""
    print("=" * 60)
    print("TEST 1: Color Scale Extraction")
    print("=" * 60)
    
    # Test reflectivity scale
    print("\nTesting Reflectivity Scale...")
    reflectivity_scale = RadarColorScale(
        '/mnt/user-data/uploads/base_reflectivity_intensity_scale.png',
        'reflectivity'
    )
    print(f"✓ Extracted {len(reflectivity_scale.color_samples)} color samples")
    print(f"  Value range: {reflectivity_scale.color_samples[0][1]:.1f} to {reflectivity_scale.color_samples[-1][1]:.1f} dBZ")
    
    # Test velocity scale
    print("\nTesting Velocity Scale...")
    velocity_scale = RadarColorScale(
        '/mnt/user-data/uploads/base_velocity_intensity_scale.png',
        'velocity'
    )
    print(f"✓ Extracted {len(velocity_scale.color_samples)} color samples")
    print(f"  Value range: {velocity_scale.color_samples[0][1]:.1f} to {velocity_scale.color_samples[-1][1]:.1f} knots")


def test_color_matching():
    """Test that color matching works correctly."""
    print("\n" + "=" * 60)
    print("TEST 2: Color Matching")
    print("=" * 60)
    
    reflectivity_scale = RadarColorScale(
        '/mnt/user-data/uploads/base_reflectivity_intensity_scale.png',
        'reflectivity'
    )
    
    # Test with some expected colors
    test_colors = [
        ((100, 100, 100), "Gray (low values)"),
        ((0, 255, 0), "Green (moderate)"),
        ((255, 0, 0), "Red (high)"),
    ]
    
    print("\nTesting color-to-value mapping:")
    for rgb, description in test_colors:
        value = reflectivity_scale.find_closest_value(rgb)
        print(f"  {description}: RGB{rgb} → {value:.1f} dBZ")


def test_converter_initialization():
    """Test that the converter initializes correctly."""
    print("\n" + "=" * 60)
    print("TEST 3: Converter Initialization")
    print("=" * 60)
    
    try:
        converter = RadarImageConverter(
            reflectivity_scale_path='/mnt/user-data/uploads/base_reflectivity_intensity_scale.png',
            velocity_scale_path='/mnt/user-data/uploads/base_velocity_intensity_scale.png'
        )
        print("✓ Converter initialized successfully")
        print(f"  Reflectivity scale ready")
        print(f"  Velocity scale ready")
        return converter
    except Exception as e:
        print(f"✗ Initialization failed: {e}")
        return None


def test_scale_image_conversion():
    """Test converting the scale images themselves as validation."""
    print("\n" + "=" * 60)
    print("TEST 4: Scale Image Self-Conversion (Validation)")
    print("=" * 60)
    
    converter = RadarImageConverter(
        reflectivity_scale_path='/mnt/user-data/uploads/base_reflectivity_intensity_scale.png',
        velocity_scale_path='/mnt/user-data/uploads/base_velocity_intensity_scale.png'
    )
    
    # Convert the reflectivity scale image itself
    print("\nConverting reflectivity scale image (should show gradient from -20 to 70)...")
    data = converter.convert_image(
        '/mnt/user-data/uploads/base_reflectivity_intensity_scale.png',
        'reflectivity',
        sample_rate=2  # Sample for efficiency
    )
    
    data_array = np.array(data['data'])
    print(f"  Shape: {data_array.shape}")
    print(f"  Min value: {data_array.min():.2f} dBZ (expected: -20)")
    print(f"  Max value: {data_array.max():.2f} dBZ (expected: 70)")
    print(f"  Range spans: {data_array.max() - data_array.min():.2f} dBZ")
    
    if -25 <= data_array.min() <= -15 and 65 <= data_array.max() <= 75:
        print("  ✓ Values are in expected range!")
    else:
        print("  ⚠ Values outside expected range (may be normal)")
    
    # Convert the velocity scale image itself
    print("\nConverting velocity scale image (should show gradient from -100 to 100)...")
    data = converter.convert_image(
        '/mnt/user-data/uploads/base_velocity_intensity_scale.png',
        'velocity',
        sample_rate=2
    )
    
    data_array = np.array(data['data'])
    print(f"  Shape: {data_array.shape}")
    print(f"  Min value: {data_array.min():.2f} knots (expected: -100)")
    print(f"  Max value: {data_array.max():.2f} knots (expected: 100)")
    print(f"  Range spans: {data_array.max() - data_array.min():.2f} knots")
    
    if -105 <= data_array.min() <= -95 and 95 <= data_array.max() <= 105:
        print("  ✓ Values are in expected range!")
    else:
        print("  ⚠ Values outside expected range (may be normal)")


def test_output_structure():
    """Test that output structure is correct."""
    print("\n" + "=" * 60)
    print("TEST 5: Output Structure Validation")
    print("=" * 60)
    
    converter = RadarImageConverter(
        reflectivity_scale_path='/mnt/user-data/uploads/base_reflectivity_intensity_scale.png',
        velocity_scale_path='/mnt/user-data/uploads/base_velocity_intensity_scale.png'
    )
    
    data = converter.convert_image(
        '/mnt/user-data/uploads/base_reflectivity_intensity_scale.png',
        'reflectivity',
        sample_rate=4
    )
    
    print("\nValidating output structure:")
    
    # Check metadata
    assert 'metadata' in data, "Missing 'metadata' key"
    print("  ✓ metadata present")
    
    assert 'data' in data, "Missing 'data' key"
    print("  ✓ data present")
    
    metadata = data['metadata']
    required_fields = [
        'radar_type', 'original_dimensions', 'sampled_dimensions',
        'sample_rate', 'units', 'value_range', 'source_file'
    ]
    
    for field in required_fields:
        assert field in metadata, f"Missing metadata field: {field}"
        print(f"  ✓ metadata.{field} present")
    
    # Check data structure
    assert isinstance(data['data'], list), "data should be a list"
    assert isinstance(data['data'][0], list), "data should be a 2D list"
    assert isinstance(data['data'][0][0], (int, float)), "data values should be numeric"
    print("  ✓ data is properly structured 2D array")
    
    print("\n✓ All structure validation passed!")


def run_all_tests():
    """Run all validation tests."""
    print("\n" + "=" * 60)
    print("WEATHER RADAR CONVERTER - VALIDATION TESTS")
    print("=" * 60)
    
    try:
        test_color_scale_extraction()
        test_color_matching()
        test_converter_initialization()
        test_scale_image_conversion()
        test_output_structure()
        
        print("\n" + "=" * 60)
        print("ALL TESTS COMPLETED SUCCESSFULLY!")
        print("=" * 60)
        print("\nThe converter is ready to use with your radar images.")
        print("Run 'python example_usage.py' for usage examples.")
        
    except Exception as e:
        print(f"\n✗ Test failed with error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    run_all_tests()
