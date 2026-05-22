"""
Weather Radar Tools Package
A modular toolkit for converting and verifying weather radar images.
"""

from .color_scale import RadarColorScale
from .converter import RadarImageConverter
from .verifier import RadarImageVerifier
from .utils import (
    load_json_data, 
    save_json_data, 
    calculate_statistics,
    validate_data_structure,
    compare_data_arrays,
    create_value_histogram
)

__version__ = "1.0.0"
__all__ = [
    'RadarColorScale',
    'RadarImageConverter', 
    'RadarImageVerifier',
    'load_json_data',
    'save_json_data',
    'calculate_statistics',
    'validate_data_structure',
    'compare_data_arrays',
    'create_value_histogram'
]
