"""
Color Scale Module
Handles extraction and management of color-to-value mappings from radar scale images.
"""

import numpy as np
from PIL import Image
from typing import Tuple, List


class RadarColorScale:
    """Extracts and manages color-to-value mappings from radar scale images."""
    
    def __init__(self, scale_image_path: str, scale_type: str):
        """
        Initialize the color scale extractor.
        
        Args:
            scale_image_path: Path to the scale reference image
            scale_type: Either 'reflectivity' or 'velocity'
        """
        self.scale_type = scale_type
        self.scale_image = Image.open(scale_image_path).convert('RGB')
        self.color_samples: List[Tuple[Tuple[int, int, int], float]] = []
        self.min_value, self.max_value = self._get_value_range()
        self._extract_color_scale()
    
    def _get_value_range(self) -> Tuple[float, float]:
        """Get the value range based on scale type."""
        if self.scale_type == 'reflectivity':
            return -20, 70  # dBZ scale
        else:  # velocity
            return -100, 100  # Knots scale
    
    def _extract_color_scale(self):
        """Extract the color-to-value mapping from the scale image."""
        img_array = np.array(self.scale_image)
        height, width = img_array.shape[:2]
        
        # Sample colors from the middle row of the scale bar
        # This assumes the color scale is horizontal
        sample_row = height // 2
        
        # Create mapping from colors to values
        for x in range(width):
            color = tuple(img_array[sample_row, x, :])
            # Linear interpolation of value based on position
            value = self.min_value + (self.max_value - self.min_value) * (x / width)
            self.color_samples.append((color, value))
        
        print(f"Extracted {len(self.color_samples)} color samples for {self.scale_type}")
    
    def find_closest_value(self, rgb_color: Tuple[int, int, int]) -> float:
        """
        Find the value corresponding to the closest color in the scale.
        
        Args:
            rgb_color: RGB tuple (r, g, b)
            
        Returns:
            The interpolated value for this color
        """
        if not self.color_samples:
            return 0.0
        
        # Convert to float to avoid overflow
        r, g, b = float(rgb_color[0]), float(rgb_color[1]), float(rgb_color[2])
        
        # Calculate color distance (Euclidean distance in RGB space)
        min_distance = float('inf')
        closest_value = 0.0
        
        for sample_color, value in self.color_samples:
            sr, sg, sb = float(sample_color[0]), float(sample_color[1]), float(sample_color[2])
            distance = np.sqrt(
                (r - sr)**2 +
                (g - sg)**2 +
                (b - sb)**2
            )
            
            if distance < min_distance:
                min_distance = distance
                closest_value = value
        
        return closest_value
    
    def value_to_rgb(self, value: float) -> Tuple[int, int, int]:
        """
        Convert a value back to its corresponding RGB color.
        
        Args:
            value: The radar value to convert
            
        Returns:
            RGB tuple (r, g, b)
        """
        if not self.color_samples:
            return (0, 0, 0)
        
        # Clamp value to valid range
        value = max(self.min_value, min(self.max_value, value))
        
        # Find the two closest color samples
        min_diff = float('inf')
        closest_color = self.color_samples[0][0]
        
        for sample_color, sample_value in self.color_samples:
            diff = abs(sample_value - value)
            if diff < min_diff:
                min_diff = diff
                closest_color = sample_color
        
        return closest_color
    
    def get_units(self) -> str:
        """Get the units for this scale type."""
        return 'dBZ' if self.scale_type == 'reflectivity' else 'knots'
