"""
Verifier Module
Reconstructs radar images from numerical data for verification purposes.
"""

import numpy as np
from PIL import Image
from pathlib import Path
from typing import Dict, Optional
from .color_scale import RadarColorScale
from .utils import load_json_data, calculate_statistics


class RadarImageVerifier:
    """Verifies radar data by reconstructing images and comparing them."""
    
    def __init__(self, reflectivity_scale_path: str, velocity_scale_path: str):
        """
        Initialize the verifier with color scale references.
        
        Args:
            reflectivity_scale_path: Path to reflectivity scale image
            velocity_scale_path: Path to velocity scale image
        """
        self.reflectivity_scale = RadarColorScale(reflectivity_scale_path, 'reflectivity')
        self.velocity_scale = RadarColorScale(velocity_scale_path, 'velocity')
    
    def data_to_image(self, data: Dict, output_path: str, 
                     upscale_factor: int = 1) -> str:
        """
        Convert numerical data back to a radar image.
        
        Args:
            data: Dictionary containing metadata and data matrix
            output_path: Path to save the reconstructed image
            upscale_factor: Factor to upscale the image (for better visibility)
            
        Returns:
            Path to the saved image
        """
        # Extract data and metadata
        data_matrix = np.array(data['data'])
        metadata = data['metadata']
        radar_type = metadata['radar_type']
        
        # Select the appropriate color scale
        scale = (self.reflectivity_scale if radar_type == 'reflectivity' 
                else self.velocity_scale)
        
        height, width = data_matrix.shape
        
        print(f"Reconstructing {radar_type} image from {width}x{height} data matrix...")
        
        # Create RGB image array
        img_array = np.zeros((height, width, 3), dtype=np.uint8)
        
        # Convert each value back to RGB
        for y in range(height):
            for x in range(width):
                value = data_matrix[y, x]
                rgb = scale.value_to_rgb(value)
                img_array[y, x] = rgb
        
        # Create PIL Image
        img = Image.fromarray(img_array, 'RGB')
        
        # Upscale if requested (for better visibility when sample_rate was > 1)
        if upscale_factor > 1:
            new_size = (width * upscale_factor, height * upscale_factor)
            img = img.resize(new_size, Image.NEAREST)
            print(f"Upscaled to {new_size[0]}x{new_size[1]}")
        
        # Save the image
        img.save(output_path)
        print(f"Saved reconstructed image to: {output_path}")
        
        return output_path
    
    def verify_conversion(self, original_image_path: str, 
                         data_json_path: str,
                         output_dir: str,
                         show_difference: bool = True) -> Dict:
        """
        Verify conversion by comparing original and reconstructed images.
        
        Args:
            original_image_path: Path to original radar image
            data_json_path: Path to JSON data file
            output_dir: Directory to save verification outputs
            show_difference: Whether to create a difference image
            
        Returns:
            Dictionary containing verification metrics
        """
        print("=" * 60)
        print("VERIFICATION PROCESS")
        print("=" * 60)
        
        # Load the data
        data = load_json_data(data_json_path)
        metadata = data['metadata']
        
        # Create output directory
        output_path = Path(output_dir)
        output_path.mkdir(exist_ok=True, parents=True)
        
        # Reconstruct the image
        sample_rate = metadata.get('sample_rate', 1)
        reconstructed_path = output_path / f"reconstructed_{Path(original_image_path).stem}.png"
        self.data_to_image(data, str(reconstructed_path), upscale_factor=sample_rate)
        
        # Load original and reconstructed images
        original_img = Image.open(original_image_path).convert('RGB')
        reconstructed_img = Image.open(reconstructed_path).convert('RGB')
        
        # Resize reconstructed to match original for comparison
        if reconstructed_img.size != original_img.size:
            reconstructed_img = reconstructed_img.resize(original_img.size, Image.NEAREST)
        
        # Convert to numpy arrays
        original_array = np.array(original_img)
        reconstructed_array = np.array(reconstructed_img)
        
        # Calculate pixel-wise differences
        diff = np.abs(original_array.astype(float) - reconstructed_array.astype(float))
        
        # Calculate metrics
        metrics = {
            'mean_absolute_error': float(diff.mean()),
            'max_error': float(diff.max()),
            'pixel_perfect_match': float(np.sum(diff == 0) / diff.size * 100),
            'within_5_threshold': float(np.sum(diff <= 5) / diff.size * 100),
            'within_10_threshold': float(np.sum(diff <= 10) / diff.size * 100),
            'dimensions': {
                'original': original_img.size,
                'reconstructed': reconstructed_img.size
            }
        }
        
        # Print metrics
        print("\n" + "=" * 60)
        print("VERIFICATION METRICS")
        print("=" * 60)
        print(f"Mean Absolute Error (per channel): {metrics['mean_absolute_error']:.2f}")
        print(f"Max Error: {metrics['max_error']:.2f}")
        print(f"Pixel Perfect Match: {metrics['pixel_perfect_match']:.2f}%")
        print(f"Within 5 RGB units: {metrics['within_5_threshold']:.2f}%")
        print(f"Within 10 RGB units: {metrics['within_10_threshold']:.2f}%")
        
        # Create difference visualization
        if show_difference:
            # Enhance difference for visibility (multiply by factor)
            diff_enhanced = np.clip(diff * 10, 0, 255).astype(np.uint8)
            diff_img = Image.fromarray(diff_enhanced)
            diff_path = output_path / f"difference_{Path(original_image_path).stem}.png"
            diff_img.save(diff_path)
            print(f"\nDifference map saved to: {diff_path}")
            print("(Note: Differences are enhanced 10x for visibility)")
        
        # Create side-by-side comparison
        comparison_path = output_path / f"comparison_{Path(original_image_path).stem}.png"
        self._create_comparison_image(
            original_img, 
            reconstructed_img, 
            diff_enhanced if show_difference else None,
            str(comparison_path),
            metadata
        )
        print(f"Side-by-side comparison saved to: {comparison_path}")
        
        # Interpretation
        print("\n" + "=" * 60)
        print("INTERPRETATION")
        print("=" * 60)
        if metrics['within_10_threshold'] > 95:
            print("✓ EXCELLENT: Reconstruction is highly accurate")
        elif metrics['within_10_threshold'] > 85:
            print("✓ GOOD: Reconstruction is acceptable")
        elif metrics['within_10_threshold'] > 70:
            print("⚠ FAIR: Some discrepancies present")
        else:
            print("✗ POOR: Significant discrepancies - check scale images")
        
        return metrics
    
    def _create_comparison_image(self, original: Image.Image, 
                                reconstructed: Image.Image,
                                difference: Optional[np.ndarray],
                                output_path: str,
                                metadata: Dict):
        """Create a side-by-side comparison image with labels."""
        from PIL import ImageDraw, ImageFont
        
        # Determine layout
        num_images = 3 if difference is not None else 2
        width, height = original.size
        
        # Create canvas
        canvas_width = width * num_images + 20 * (num_images - 1)
        canvas_height = height + 40  # Extra space for labels
        canvas = Image.new('RGB', (canvas_width, canvas_height), color='white')
        
        # Paste images
        canvas.paste(original, (0, 40))
        canvas.paste(reconstructed, (width + 20, 40))
        if difference is not None:
            diff_img = Image.fromarray(difference)
            canvas.paste(diff_img, (width * 2 + 40, 40))
        
        # Add labels
        draw = ImageDraw.Draw(canvas)
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
        except:
            font = ImageFont.load_default()
        
        draw.text((10, 10), "Original", fill='black', font=font)
        draw.text((width + 30, 10), "Reconstructed", fill='black', font=font)
        if difference is not None:
            draw.text((width * 2 + 50, 10), "Difference (10x)", fill='black', font=font)
        
        # Add metadata
        radar_type = metadata.get('radar_type', 'unknown')
        sample_rate = metadata.get('sample_rate', 1)
        info_text = f"{radar_type.capitalize()} | Sample Rate: {sample_rate}"
        draw.text((10, canvas_height - 25), info_text, fill='gray', font=font)
        
        canvas.save(output_path)
    
    def batch_verify(self, verification_list: list, output_dir: str):
        """
        Verify multiple conversions at once.
        
        Args:
            verification_list: List of (original_image, json_data) tuples
            output_dir: Directory to save all verification outputs
        """
        print("=" * 60)
        print("BATCH VERIFICATION")
        print("=" * 60)
        
        results = []
        for i, (original_path, json_path) in enumerate(verification_list, 1):
            print(f"\n[{i}/{len(verification_list)}] Verifying {Path(original_path).name}")
            metrics = self.verify_conversion(original_path, json_path, output_dir)
            results.append({
                'file': Path(original_path).name,
                'metrics': metrics
            })
        
        # Summary
        print("\n" + "=" * 60)
        print("BATCH VERIFICATION SUMMARY")
        print("=" * 60)
        avg_mae = np.mean([r['metrics']['mean_absolute_error'] for r in results])
        avg_match = np.mean([r['metrics']['within_10_threshold'] for r in results])
        print(f"Average MAE: {avg_mae:.2f}")
        print(f"Average accuracy (within 10 RGB): {avg_match:.2f}%")
        
        return results
