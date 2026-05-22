#!/usr/bin/env python3
"""
Comprehensive test suite for modular radar tools.
"""

import sys
from pathlib import Path

# Add radar_tools to path
sys.path.insert(0, str(Path(__file__).parent))

from radar_tools import (
    RadarColorScale, 
    RadarImageConverter, 
    RadarImageVerifier,
    load_json_data,
    calculate_statistics,
    validate_data_structure
)


def test_color_scale_module():
    """Test the color scale module."""
    print("=" * 60)
    print("TEST 1: Color Scale Module")
    print("=" * 60)
    
    # Test reflectivity scale
    print("\nTesting RadarColorScale (reflectivity)...")
    try:
        ref_scale = RadarColorScale(
            '/mnt/user-data/uploads/base_reflectivity_intensity_scale.png',
            'reflectivity'
        )
        assert len(ref_scale.color_samples) > 0
        assert ref_scale.min_value == -20
        assert ref_scale.max_value == 70
        assert ref_scale.get_units() == 'dBZ'
        print("  ✓ Reflectivity scale initialized correctly")
        print(f"  ✓ Value range: {ref_scale.min_value} to {ref_scale.max_value} {ref_scale.get_units()}")
    except Exception as e:
        print(f"  ✗ Failed: {e}")
        return False
    
    # Test velocity scale
    print("\nTesting RadarColorScale (velocity)...")
    try:
        vel_scale = RadarColorScale(
            '/mnt/user-data/uploads/base_velocity_intensity_scale.png',
            'velocity'
        )
        assert len(vel_scale.color_samples) > 0
        assert vel_scale.min_value == -100
        assert vel_scale.max_value == 100
        assert vel_scale.get_units() == 'knots'
        print("  ✓ Velocity scale initialized correctly")
        print(f"  ✓ Value range: {vel_scale.min_value} to {vel_scale.max_value} {vel_scale.get_units()}")
    except Exception as e:
        print(f"  ✗ Failed: {e}")
        return False
    
    # Test color matching
    print("\nTesting color-to-value conversion...")
    test_value = ref_scale.find_closest_value((100, 100, 100))
    print(f"  ✓ Gray color maps to: {test_value:.1f} dBZ")
    
    # Test value-to-color conversion
    print("\nTesting value-to-color conversion...")
    test_rgb = ref_scale.value_to_rgb(50.0)
    print(f"  ✓ Value 50.0 dBZ maps to RGB: {test_rgb}")
    
    return True


def test_converter_module():
    """Test the converter module."""
    print("\n" + "=" * 60)
    print("TEST 2: Converter Module")
    print("=" * 60)
    
    try:
        converter = RadarImageConverter(
            '/mnt/user-data/uploads/base_reflectivity_intensity_scale.png',
            '/mnt/user-data/uploads/base_velocity_intensity_scale.png'
        )
        print("  ✓ Converter initialized successfully")
    except Exception as e:
        print(f"  ✗ Initialization failed: {e}")
        return False
    
    # Test conversion
    print("\nTesting image conversion...")
    try:
        data = converter.convert_image(
            '/mnt/user-data/uploads/base_reflectivity_intensity_scale.png',
            'reflectivity',
            sample_rate=4
        )
        
        assert 'metadata' in data
        assert 'data' in data
        assert data['metadata']['radar_type'] == 'reflectivity'
        print("  ✓ Image converted successfully")
        print(f"  ✓ Output shape: {len(data['data'])}x{len(data['data'][0])}")
    except Exception as e:
        print(f"  ✗ Conversion failed: {e}")
        return False
    
    return True


def test_verifier_module():
    """Test the verifier module."""
    print("\n" + "=" * 60)
    print("TEST 3: Verifier Module")
    print("=" * 60)
    
    try:
        verifier = RadarImageVerifier(
            '/mnt/user-data/uploads/base_reflectivity_intensity_scale.png',
            '/mnt/user-data/uploads/base_velocity_intensity_scale.png'
        )
        print("  ✓ Verifier initialized successfully")
    except Exception as e:
        print(f"  ✗ Initialization failed: {e}")
        return False
    
    # Test reconstruction
    print("\nTesting image reconstruction...")
    try:
        # First convert an image
        converter = RadarImageConverter(
            '/mnt/user-data/uploads/base_reflectivity_intensity_scale.png',
            '/mnt/user-data/uploads/base_velocity_intensity_scale.png'
        )
        data = converter.convert_image(
            '/mnt/user-data/uploads/base_reflectivity_intensity_scale.png',
            'reflectivity',
            sample_rate=2
        )
        
        # Reconstruct it
        output_path = '/home/claude/test_reconstructed.png'
        verifier.data_to_image(data, output_path, upscale_factor=2)
        
        assert Path(output_path).exists()
        print("  ✓ Image reconstructed successfully")
        print(f"  ✓ Output saved to: {output_path}")
    except Exception as e:
        print(f"  ✗ Reconstruction failed: {e}")
        return False
    
    return True


def test_utilities_module():
    """Test the utilities module."""
    print("\n" + "=" * 60)
    print("TEST 4: Utilities Module")
    print("=" * 60)
    
    # Test data validation
    print("\nTesting data structure validation...")
    valid_data = {
        'metadata': {
            'radar_type': 'reflectivity',
            'units': 'dBZ',
            'value_range': {'min': -20, 'max': 70}
        },
        'data': [[1, 2, 3], [4, 5, 6]]
    }
    
    is_valid, message = validate_data_structure(valid_data)
    if is_valid:
        print(f"  ✓ Valid data structure recognized: {message}")
    else:
        print(f"  ✗ Validation failed: {message}")
        return False
    
    # Test invalid data
    invalid_data = {'data': [[1, 2, 3]]}  # Missing metadata
    is_valid, message = validate_data_structure(invalid_data)
    if not is_valid:
        print(f"  ✓ Invalid data correctly rejected: {message}")
    else:
        print(f"  ✗ Should have rejected invalid data")
        return False
    
    # Test statistics calculation
    print("\nTesting statistics calculation...")
    stats = calculate_statistics(valid_data)
    print(f"  ✓ Statistics calculated: mean={stats['mean']:.2f}")
    
    return True


def test_end_to_end_workflow():
    """Test complete conversion and verification workflow."""
    print("\n" + "=" * 60)
    print("TEST 5: End-to-End Workflow")
    print("=" * 60)
    
    # Step 1: Convert
    print("\nStep 1: Converting image...")
    converter = RadarImageConverter(
        '/mnt/user-data/uploads/base_reflectivity_intensity_scale.png',
        '/mnt/user-data/uploads/base_velocity_intensity_scale.png'
    )
    
    test_output = '/home/claude/test_e2e_data.json'
    converter.convert_and_save(
        '/mnt/user-data/uploads/base_reflectivity_intensity_scale.png',
        'reflectivity',
        test_output,
        sample_rate=2
    )
    print("  ✓ Conversion completed")
    
    # Step 2: Load and validate
    print("\nStep 2: Loading and validating...")
    data = load_json_data(test_output)
    is_valid, message = validate_data_structure(data)
    if is_valid:
        print(f"  ✓ Data structure is valid")
    else:
        print(f"  ✗ Invalid data: {message}")
        return False
    
    # Step 3: Verify
    print("\nStep 3: Verifying conversion...")
    verifier = RadarImageVerifier(
        '/mnt/user-data/uploads/base_reflectivity_intensity_scale.png',
        '/mnt/user-data/uploads/base_velocity_intensity_scale.png'
    )
    
    metrics = verifier.verify_conversion(
        '/mnt/user-data/uploads/base_reflectivity_intensity_scale.png',
        test_output,
        '/home/claude/test_verification',
        show_difference=True
    )
    
    print(f"  ✓ Verification completed")
    print(f"  ✓ Accuracy (within 10 RGB): {metrics['within_10_threshold']:.1f}%")
    
    return True


def run_all_tests():
    """Run all test modules."""
    print("\n" + "=" * 70)
    print("MODULAR RADAR TOOLS - COMPREHENSIVE TEST SUITE")
    print("=" * 70)
    
    tests = [
        ("Color Scale Module", test_color_scale_module),
        ("Converter Module", test_converter_module),
        ("Verifier Module", test_verifier_module),
        ("Utilities Module", test_utilities_module),
        ("End-to-End Workflow", test_end_to_end_workflow),
    ]
    
    results = []
    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"\n✗ {test_name} crashed: {e}")
            import traceback
            traceback.print_exc()
            results.append((test_name, False))
    
    # Summary
    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        status = "✓ PASSED" if result else "✗ FAILED"
        print(f"{status}: {test_name}")
    
    print(f"\n{passed}/{total} tests passed")
    
    if passed == total:
        print("\n" + "=" * 70)
        print("ALL TESTS PASSED! ✓")
        print("=" * 70)
        print("\nThe modular radar tools are ready to use!")
        return True
    else:
        print("\n" + "=" * 70)
        print("SOME TESTS FAILED")
        print("=" * 70)
        return False


if __name__ == '__main__':
    success = run_all_tests()
    sys.exit(0 if success else 1)
