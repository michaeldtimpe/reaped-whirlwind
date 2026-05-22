#!/usr/bin/env python3
"""
ML-Focused Verification
Reports metrics that actually matter for machine learning training.
"""

import sys
from pathlib import Path
import numpy as np
from PIL import Image
import json

sys.path.insert(0, str(Path(__file__).parent))

from radar_tools import load_json_data, RadarColorScale


class MLVerifier:
    """Verify conversion quality with ML-relevant metrics."""
    
    def __init__(self, reflectivity_scale_path: str, velocity_scale_path: str):
        """Initialize with color scales."""
        self.reflectivity_scale = RadarColorScale(reflectivity_scale_path, 'reflectivity')
        self.velocity_scale = RadarColorScale(velocity_scale_path, 'velocity')
    
    def verify_ml_quality(self, original_image_path: str, data_json_path: str,
                         output_dir: str = None) -> dict:
        """
        Verify conversion with ML-focused metrics.
        
        Args:
            original_image_path: Path to original radar image
            data_json_path: Path to converted JSON data
            output_dir: Directory to save visualizations (optional)
            
        Returns:
            Dictionary with ML-relevant metrics
        """
        print("=" * 70)
        print("ML-FOCUSED VERIFICATION")
        print("Quality metrics that matter for machine learning")
        print("=" * 70)
        
        # Load data
        data = load_json_data(data_json_path)
        metadata = data['metadata']
        data_matrix = np.array(data['data'])
        
        # Load original
        original = Image.open(original_image_path).convert('RGB')
        original_array = np.array(original)
        
        print(f"\nOriginal: {original.size[0]}x{original.size[1]}")
        print(f"Converted: {metadata['sampled_dimensions']['width']}x{metadata['sampled_dimensions']['height']}")
        print(f"Sample rate: {metadata['sample_rate']}")
        print(f"Radar type: {metadata['radar_type']}")
        
        # Get scale
        scale = (self.reflectivity_scale if metadata['radar_type'] == 'reflectivity'
                else self.velocity_scale)
        
        # === METRIC 1: Value Range Coverage ===
        value_min = data_matrix.min()
        value_max = data_matrix.max()
        value_mean = data_matrix.mean()
        value_std = data_matrix.std()
        
        expected_min = scale.min_value
        expected_max = scale.max_value
        
        range_coverage = (value_max - value_min) / (expected_max - expected_min) * 100
        
        print("\n" + "=" * 70)
        print("METRIC 1: VALUE RANGE COVERAGE")
        print("=" * 70)
        print(f"Data range: {value_min:.1f} to {value_max:.1f} {scale.get_units()}")
        print(f"Expected range: {expected_min:.1f} to {expected_max:.1f} {scale.get_units()}")
        print(f"Coverage: {range_coverage:.1f}%")
        
        if range_coverage > 50:
            print("✓ GOOD: Wide range of values captured")
        else:
            print("⚠ LIMITED: Narrow range (may be light weather)")
        
        # === METRIC 2: Pattern Preservation ===
        # Check if data has structure (not uniform/random)
        
        # Calculate spatial autocorrelation (simple version)
        # If data is structured, nearby pixels should be similar
        spatial_consistency = self._calculate_spatial_consistency(data_matrix)
        
        print("\n" + "=" * 70)
        print("METRIC 2: PATTERN PRESERVATION")
        print("=" * 70)
        print(f"Spatial consistency: {spatial_consistency:.1f}%")
        
        if spatial_consistency > 70:
            print("✓ EXCELLENT: Strong spatial patterns preserved")
        elif spatial_consistency > 50:
            print("✓ GOOD: Clear patterns visible")
        else:
            print("⚠ WEAK: Patterns may be degraded")
        
        # === METRIC 3: Dynamic Range ===
        # Check if using full value spectrum (not collapsed)
        unique_values = len(np.unique(data_matrix))
        possible_values = data_matrix.size
        value_diversity = (unique_values / min(1000, possible_values)) * 100
        
        print("\n" + "=" * 70)
        print("METRIC 3: DYNAMIC RANGE")
        print("=" * 70)
        print(f"Unique values: {unique_values:,}")
        print(f"Value diversity: {value_diversity:.1f}%")
        
        if value_diversity > 30:
            print("✓ EXCELLENT: Rich variation captured")
        elif value_diversity > 15:
            print("✓ GOOD: Adequate variation")
        else:
            print("⚠ LIMITED: May have quantization issues")
        
        # === METRIC 4: Non-Background Content ===
        # Estimate how much is actual weather vs background
        
        if metadata['radar_type'] == 'reflectivity':
            # For reflectivity, low values (< 0) are often background/noise
            weather_mask = data_matrix > 5.0
        else:
            # For velocity, values near 0 might be valid
            weather_mask = np.abs(data_matrix) > 2.0
        
        weather_coverage = np.sum(weather_mask) / weather_mask.size * 100
        
        print("\n" + "=" * 70)
        print("METRIC 4: WEATHER DATA CONTENT")
        print("=" * 70)
        print(f"Weather coverage: {weather_coverage:.1f}%")
        print(f"Background/noise: {100 - weather_coverage:.1f}%")
        
        if weather_coverage > 30:
            print("✓ SUBSTANTIAL: Significant weather activity")
        elif weather_coverage > 10:
            print("✓ MODERATE: Some weather present")
        else:
            print("ℹ LIGHT: Mostly clear conditions")
        
        # === METRIC 5: Gradient Smoothness ===
        # For ML, we want smooth gradients (not noisy)
        gradient_quality = self._calculate_gradient_quality(data_matrix)
        
        print("\n" + "=" * 70)
        print("METRIC 5: GRADIENT SMOOTHNESS")
        print("=" * 70)
        print(f"Smoothness score: {gradient_quality:.1f}%")
        
        if gradient_quality > 70:
            print("✓ SMOOTH: Clean gradients for ML")
        elif gradient_quality > 50:
            print("✓ ACCEPTABLE: Minor noise present")
        else:
            print("⚠ NOISY: May need smoothing")
        
        # === OVERALL ML READINESS SCORE ===
        
        # Weight the metrics
        weights = {
            'range_coverage': 0.15,
            'spatial_consistency': 0.35,
            'value_diversity': 0.20,
            'weather_coverage': 0.15,
            'gradient_quality': 0.15
        }
        
        # Normalize weather coverage (30% is "perfect" for typical radar)
        weather_score = min(100, weather_coverage / 0.30 * 100)
        
        overall_score = (
            range_coverage * weights['range_coverage'] +
            spatial_consistency * weights['spatial_consistency'] +
            value_diversity * weights['value_diversity'] +
            weather_score * weights['weather_coverage'] +
            gradient_quality * weights['gradient_quality']
        )
        
        print("\n" + "=" * 70)
        print("OVERALL ML READINESS")
        print("=" * 70)
        print(f"Composite Score: {overall_score:.1f}%")
        
        if overall_score >= 80:
            grade = "A - EXCELLENT"
            message = "Ready for ML training!"
        elif overall_score >= 70:
            grade = "B - GOOD"
            message = "Suitable for ML training"
        elif overall_score >= 60:
            grade = "C - ACCEPTABLE"
            message = "Usable but could be improved"
        else:
            grade = "D - NEEDS WORK"
            message = "Check conversion settings"
        
        print(f"Grade: {grade}")
        print(f"Status: {message}")
        
        # Create visualization if requested
        if output_dir:
            output_path = Path(output_dir)
            output_path.mkdir(exist_ok=True, parents=True)
            self._create_visualization(
                original_array, data_matrix, scale, metadata,
                str(output_path / f"ml_verification_{Path(original_image_path).stem}.png")
            )
        
        # Compile metrics
        metrics = {
            'overall_score': overall_score,
            'grade': grade,
            'range_coverage': range_coverage,
            'spatial_consistency': spatial_consistency,
            'value_diversity': value_diversity,
            'weather_coverage': weather_coverage,
            'gradient_quality': gradient_quality,
            'value_stats': {
                'min': float(value_min),
                'max': float(value_max),
                'mean': float(value_mean),
                'std': float(value_std)
            }
        }
        
        return metrics
    
    def _calculate_spatial_consistency(self, data: np.ndarray) -> float:
        """
        Calculate how consistent nearby values are.
        High consistency = preserved patterns.
        """
        # Simple approach: compare each pixel with its neighbors
        # Use horizontal and vertical differences
        
        diff_right = np.abs(data[:, 1:] - data[:, :-1])
        diff_down = np.abs(data[1:, :] - data[:-1, :])
        
        # Smooth data should have small differences between neighbors
        # Calculate what percentage of neighbor differences are small
        
        threshold = 5.0  # Small difference in dBZ or m/s
        
        small_diffs_h = np.sum(diff_right < threshold)
        small_diffs_v = np.sum(diff_down < threshold)
        
        total_comparisons = diff_right.size + diff_down.size
        consistency = (small_diffs_h + small_diffs_v) / total_comparisons * 100
        
        return consistency
    
    def _calculate_gradient_quality(self, data: np.ndarray) -> float:
        """
        Calculate how smooth the gradients are.
        Smooth gradients = good for ML.
        """
        # Calculate second derivatives (detect sharp changes)
        # Good data has gradual changes, not spikes
        
        # Horizontal second derivative
        diff2_h = np.abs(data[:, 2:] - 2*data[:, 1:-1] + data[:, :-2])
        
        # Vertical second derivative  
        diff2_v = np.abs(data[2:, :] - 2*data[1:-1, :] + data[:-2, :])
        
        # Smooth data has low second derivatives
        threshold = 3.0
        
        smooth_h = np.sum(diff2_h < threshold)
        smooth_v = np.sum(diff2_v < threshold)
        
        total = diff2_h.size + diff2_v.size
        smoothness = (smooth_h + smooth_v) / total * 100
        
        return smoothness
    
    def _create_visualization(self, original: np.ndarray, data: np.ndarray,
                             scale: RadarColorScale, metadata: dict,
                             output_path: str):
        """Create visualization of the converted data."""
        from PIL import ImageDraw, ImageFont
        
        # Reconstruct image from data
        height, width = data.shape
        reconstructed = np.zeros((height, width, 3), dtype=np.uint8)
        
        for y in range(height):
            for x in range(width):
                value = data[y, x]
                rgb = scale.value_to_rgb(value)
                reconstructed[y, x] = rgb
        
        # Upscale to match original
        sample_rate = metadata.get('sample_rate', 1)
        if sample_rate > 1:
            reconstructed_img = Image.fromarray(reconstructed)
            orig_size = (metadata['original_dimensions']['width'],
                        metadata['original_dimensions']['height'])
            reconstructed_img = reconstructed_img.resize(orig_size, Image.NEAREST)
            reconstructed = np.array(reconstructed_img)
        
        # Create side-by-side
        h, w = original.shape[:2]
        canvas = Image.new('RGB', (w * 2 + 20, h + 80), color='white')
        
        canvas.paste(Image.fromarray(original), (0, 40))
        canvas.paste(Image.fromarray(reconstructed), (w + 20, 40))
        
        # Add labels
        draw = ImageDraw.Draw(canvas)
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
            font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
        except:
            font = ImageFont.load_default()
            font_small = font
        
        draw.text((10, 10), "Original", fill='black', font=font)
        draw.text((w + 30, 10), "Reconstructed", fill='black', font=font)
        
        # Add info
        info = f"Sample Rate: {sample_rate} | Type: {metadata['radar_type']}"
        draw.text((10, h + 50), info, fill='gray', font=font_small)
        
        canvas.save(output_path)
        print(f"\n✓ Visualization saved: {output_path}")


def main():
    """CLI for ML verification."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Verify radar conversion with ML-relevant metrics',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
This verification focuses on metrics that matter for machine learning:
  - Pattern preservation (spatial structure)
  - Dynamic range (value variety)
  - Weather content (non-background data)
  - Gradient smoothness (noise level)

NOT pixel-perfect color matching (irrelevant for ML).

Examples:
  python verify_ml.py original.png converted.json
  python verify_ml.py original.png converted.json --output-dir viz/
        """
    )
    
    parser.add_argument('original_image', help='Path to original radar image')
    parser.add_argument('data_json', help='Path to converted JSON data')
    parser.add_argument('--output-dir', '-o', help='Save visualization')
    parser.add_argument('--reflectivity-scale', 
                       default='base_reflectivity_intensity_scale.png')
    parser.add_argument('--velocity-scale',
                       default='base_velocity_intensity_scale.png')
    
    args = parser.parse_args()
    
    # Create verifier
    verifier = MLVerifier(args.reflectivity_scale, args.velocity_scale)
    
    # Run verification
    metrics = verifier.verify_ml_quality(
        args.original_image,
        args.data_json,
        args.output_dir
    )
    
    print("\n" + "=" * 70)
    print("VERIFICATION COMPLETE")
    print("=" * 70)
    print(f"\nOverall Score: {metrics['overall_score']:.1f}% ({metrics['grade']})")
    print("\nMetric Breakdown:")
    print(f"  Range Coverage:       {metrics['range_coverage']:.1f}%")
    print(f"  Pattern Preservation: {metrics['spatial_consistency']:.1f}%")
    print(f"  Dynamic Range:        {metrics['value_diversity']:.1f}%")
    print(f"  Weather Content:      {metrics['weather_coverage']:.1f}%")
    print(f"  Gradient Smoothness:  {metrics['gradient_quality']:.1f}%")
    
    print("\n✓ Ready for ML training!" if metrics['overall_score'] >= 70 
          else "\n⚠ Review conversion settings")


if __name__ == '__main__':
    main()