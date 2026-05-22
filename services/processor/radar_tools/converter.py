"""
OPTIMIZED Converter Module - Vectorized for 100x+ speedup
Compatible with RadarColorScale structure
"""

import numpy as np
from PIL import Image
from pathlib import Path
from typing import Dict
from scipy.spatial import cKDTree
from .color_scale import RadarColorScale
from .utils import save_json_data


class RadarImageConverter:
    """Converts radar images to structured numerical data using vectorized operations."""

    def __init__(self, reflectivity_scale_path: str, velocity_scale_path: str):
        """
        Initialize the converter with color scale references.

        Args:
            reflectivity_scale_path: Path to reflectivity scale image
            velocity_scale_path: Path to velocity scale image
        """
        self.reflectivity_scale = RadarColorScale(reflectivity_scale_path, 'reflectivity')
        self.velocity_scale = RadarColorScale(velocity_scale_path, 'velocity')
        
        # Pre-build KD-trees for fast lookups
        self._build_kdtrees()

    def _build_kdtrees(self):
        """Build KD-trees for ultra-fast color lookups."""
        # Extract from reflectivity scale
        # color_samples is a list of tuples: [(color, value), ...]
        refl_colors = np.array([color for color, value in self.reflectivity_scale.color_samples])
        refl_values = np.array([value for color, value in self.reflectivity_scale.color_samples])
        
        # Extract from velocity scale
        vel_colors = np.array([color for color, value in self.velocity_scale.color_samples])
        vel_values = np.array([value for color, value in self.velocity_scale.color_samples])
        
        # Build KD-trees (log(n) lookup instead of O(n)!)
        self.refl_tree = cKDTree(refl_colors)
        self.refl_values = refl_values
        
        self.vel_tree = cKDTree(vel_colors)
        self.vel_values = vel_values
        
        print(f"Built KD-trees: {len(refl_values)} reflectivity colors, {len(vel_values)} velocity colors")

    def convert_image(self, image_path: str, radar_type: str,
                     sample_rate: int = 1) -> Dict:
        """
        Convert a radar image to structured data using vectorized operations.

        Args:
            image_path: Path to the radar image
            radar_type: Either 'reflectivity' or 'velocity'
            sample_rate: Sample every Nth pixel (1 = all pixels, 2 = every other, etc.)

        Returns:
            Dictionary containing metadata and data matrix
        """
        # Load the image
        img = Image.open(image_path).convert('RGB')
        img_array = np.array(img)

        # Select the appropriate scale and KD-tree
        if radar_type == 'reflectivity':
            scale = self.reflectivity_scale
            tree = self.refl_tree
            values = self.refl_values
        else:
            scale = self.velocity_scale
            tree = self.vel_tree
            values = self.vel_values

        height, width = img_array.shape[:2]

        print(f"Converting {radar_type} image: {width}x{height} pixels (sample rate: {sample_rate})")

        # Sample the image (vectorized!)
        sampled = img_array[::sample_rate, ::sample_rate, :]
        sampled_height, sampled_width = sampled.shape[:2]
        
        # Reshape to (n_pixels, 3) for batch processing
        pixels = sampled.reshape(-1, 3).astype(float)
        
        # Find nearest colors using KD-tree (FAST!)
        # This is O(log n) per pixel instead of O(n)
        distances, indices = tree.query(pixels, k=1)
        
        # Map to values
        pixel_values = values[indices]
        
        # Reshape back to image dimensions
        data_matrix = pixel_values.reshape(sampled_height, sampled_width)
        
        # Round and convert to list
        data_matrix = np.round(data_matrix, 2).tolist()

        # Create structured output
        output = {
            'metadata': {
                'radar_type': radar_type,
                'original_dimensions': {
                    'width': width,
                    'height': height
                },
                'sampled_dimensions': {
                    'width': sampled_width,
                    'height': sampled_height
                },
                'sample_rate': sample_rate,
                'units': scale.get_units(),
                'value_range': {
                    'min': scale.min_value,
                    'max': scale.max_value
                },
                'source_file': Path(image_path).name
            },
            'data': data_matrix
        }

        return output

    def convert_and_save(self, image_path: str, radar_type: str,
                        output_path: str, sample_rate: int = 1,
                        save_numpy: bool = False):
        """
        Convert and save radar image data.

        Args:
            image_path: Path to the radar image
            radar_type: Either 'reflectivity' or 'velocity'
            output_path: Path for output JSON file
            sample_rate: Sample every Nth pixel
            save_numpy: Also save as NumPy .npy file
        """
        # Convert the image
        data = self.convert_image(image_path, radar_type, sample_rate)

        # Save as JSON
        save_json_data(data, output_path)
        print(f"Saved JSON to: {output_path}")

        # Optionally save as NumPy array
        if save_numpy:
            numpy_path = Path(output_path).with_suffix('.npy')
            np.save(numpy_path, np.array(data['data']))
            print(f"Saved NumPy array to: {numpy_path}")

        # Print statistics
        data_array = np.array(data['data'])
        print(f"Data statistics:")
        print(f"  Shape: {data_array.shape}")
        print(f"  Min value: {data_array.min():.2f}")
        print(f"  Max value: {data_array.max():.2f}")
        print(f"  Mean value: {data_array.mean():.2f}")
        print(f"  Non-zero pixels: {np.count_nonzero(data_array)}")