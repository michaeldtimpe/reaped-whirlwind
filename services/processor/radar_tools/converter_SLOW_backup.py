"""
Converter Module
Handles conversion of radar images to structured numerical data.
"""

import numpy as np
from PIL import Image
from pathlib import Path
from typing import Dict
from .color_scale import RadarColorScale
from .utils import save_json_data


class RadarImageConverter:
    """Converts radar images to structured numerical data."""
    
    def __init__(self, reflectivity_scale_path: str, velocity_scale_path: str):
        """
        Initialize the converter with color scale references.
        
        Args:
            reflectivity_scale_path: Path to reflectivity scale image
            velocity_scale_path: Path to velocity scale image
        """
        self.reflectivity_scale = RadarColorScale(reflectivity_scale_path, 'reflectivity')
        self.velocity_scale = RadarColorScale(velocity_scale_path, 'velocity')
    
    def convert_image(self, image_path: str, radar_type: str, 
                     sample_rate: int = 1) -> Dict:
        """
        Convert a radar image to structured data.
        
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
        
        # Select the appropriate color scale
        scale = (self.reflectivity_scale if radar_type == 'reflectivity' 
                else self.velocity_scale)
        
        height, width = img_array.shape[:2]
        
        # Create data matrix
        data_matrix = []
        
        print(f"Converting {radar_type} image: {width}x{height} pixels (sample rate: {sample_rate})")
        
        # Process image with sampling
        for y in range(0, height, sample_rate):
            row_data = []
            for x in range(0, width, sample_rate):
                rgb = tuple(img_array[y, x, :])
                value = scale.find_closest_value(rgb)
                row_data.append(round(value, 2))
            data_matrix.append(row_data)
        
        # Create structured output
        output = {
            'metadata': {
                'radar_type': radar_type,
                'original_dimensions': {
                    'width': width,
                    'height': height
                },
                'sampled_dimensions': {
                    'width': len(data_matrix[0]) if data_matrix else 0,
                    'height': len(data_matrix)
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
