"""
Enhanced Converter with Progress Bar
"""

import numpy as np
from PIL import Image
from pathlib import Path
from typing import Dict
import sys


class RadarImageConverterWithProgress:
    """Converter with progress tracking for large images."""

    def __init__(self, reflectivity_scale, velocity_scale):
        """Initialize with existing color scales."""
        self.reflectivity_scale = reflectivity_scale
        self.velocity_scale = velocity_scale

    def convert_image_with_progress(self, image_path: str, radar_type: str,
                                    sample_rate: int = 1, show_progress: bool = True) -> Dict:
        """
        Convert image with progress indicator.

        Args:
            image_path: Path to radar image
            radar_type: 'reflectivity' or 'velocity'
            sample_rate: Sample every Nth pixel
            show_progress: Show progress bar

        Returns:
            Dictionary with metadata and data
        """
        # Load image
        img = Image.open(image_path).convert('RGB')
        img_array = np.array(img)

        # Select scale
        scale = (self.reflectivity_scale if radar_type == 'reflectivity'
                else self.velocity_scale)

        height, width = img_array.shape[:2]

        # Calculate processing dimensions
        rows_to_process = len(range(0, height, sample_rate))
        cols_to_process = len(range(0, width, sample_rate))

        print(f"Converting {radar_type} image: {width}x{height} pixels")
        print(f"Sample rate: {sample_rate} → Output: {cols_to_process}x{rows_to_process}")
        print(f"Processing {rows_to_process} rows...")

        data_matrix = []

        # Process with progress
        for i, y in enumerate(range(0, height, sample_rate)):
            row_data = []
            for x in range(0, width, sample_rate):
                rgb = tuple(img_array[y, x, :])
                value = scale.find_closest_value(rgb)
                row_data.append(round(value, 2))
            data_matrix.append(row_data)

            # Show progress every 10% or every 100 rows
            if show_progress and (i % max(1, rows_to_process // 10) == 0 or i % 100 == 0):
                percent = (i + 1) / rows_to_process * 100
                bar_length = 40
                filled = int(bar_length * (i + 1) / rows_to_process)
                bar = '█' * filled + '░' * (bar_length - filled)
                sys.stdout.write(f'\r  Progress: [{bar}] {percent:.1f}% ({i+1}/{rows_to_process} rows)')
                sys.stdout.flush()

        if show_progress:
            sys.stdout.write('\n')
            sys.stdout.flush()

        print("✓ Conversion complete!")

        # Create output
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


# Monkey-patch the existing converter to add progress
def add_progress_to_converter():
    """Adds progress tracking to existing RadarImageConverter."""
    from radar_tools.converter import RadarImageConverter

    # Save original method
    _original_convert = RadarImageConverter.convert_image

    def convert_with_progress(self, image_path: str, radar_type: str,
                             sample_rate: int = 1, show_progress: bool = True):
        """Enhanced version with progress bar."""
        # Use the new implementation
        enhanced = RadarImageConverterWithProgress(
            self.reflectivity_scale,
            self.velocity_scale
        )
        return enhanced.convert_image_with_progress(
            image_path, radar_type, sample_rate, show_progress
        )

    # Replace method
    RadarImageConverter.convert_image = convert_with_progress

    print("✓ Progress tracking enabled for RadarImageConverter")


if __name__ == '__main__':
    # Enable progress tracking
    add_progress_to_converter()

    print("\nUsage:")
    print("  from radar_tools import RadarImageConverter")
    print("  from converter_with_progress import add_progress_to_converter")
    print("  ")
    print("  add_progress_to_converter()  # Enable progress bars")
    print("  converter = RadarImageConverter(...)")
    print("  data = converter.convert_image(...)")
