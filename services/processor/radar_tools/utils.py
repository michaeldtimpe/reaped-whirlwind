"""
Utilities Module
Common utility functions used across the package.
"""

import json
import numpy as np
from typing import Dict, Any
from pathlib import Path


def load_json_data(json_path: str) -> Dict:
    """
    Load radar data from JSON file.
    
    Args:
        json_path: Path to JSON file
        
    Returns:
        Dictionary containing radar data
    """
    with open(json_path, 'r') as f:
        return json.load(f)


def save_json_data(data: Dict, output_path: str):
    """
    Save radar data to JSON file.
    
    Args:
        data: Data dictionary to save
        output_path: Path for output file
        indent: JSON indentation level
    """
    with open(output_path, 'w') as f:
        json.dump(data, f, separators=(",", ":"))


def calculate_statistics(data: Dict) -> Dict[str, float]:
    """
    Calculate statistics for radar data.
    
    Args:
        data: Dictionary containing radar data
        
    Returns:
        Dictionary of statistical measures
    """
    data_array = np.array(data['data'])
    
    stats = {
        'shape': data_array.shape,
        'min': float(data_array.min()),
        'max': float(data_array.max()),
        'mean': float(data_array.mean()),
        'median': float(np.median(data_array)),
        'std': float(data_array.std()),
        'non_zero_count': int(np.count_nonzero(data_array)),
        'total_pixels': int(data_array.size)
    }
    
    return stats


def compare_data_arrays(array1: np.ndarray, array2: np.ndarray) -> Dict[str, float]:
    """
    Compare two data arrays and return difference metrics.
    
    Args:
        array1: First array
        array2: Second array
        
    Returns:
        Dictionary of comparison metrics
    """
    if array1.shape != array2.shape:
        raise ValueError(f"Arrays have different shapes: {array1.shape} vs {array2.shape}")
    
    diff = np.abs(array1 - array2)
    
    metrics = {
        'mean_absolute_error': float(diff.mean()),
        'max_error': float(diff.max()),
        'rmse': float(np.sqrt(np.mean(diff ** 2))),
        'exact_matches': float(np.sum(diff == 0) / diff.size * 100),
        'correlation': float(np.corrcoef(array1.flatten(), array2.flatten())[0, 1])
    }
    
    return metrics


def create_value_histogram(data: Dict, bins: int = 50) -> Dict[str, Any]:
    """
    Create histogram data for radar values.
    
    Args:
        data: Dictionary containing radar data
        bins: Number of histogram bins
        
    Returns:
        Dictionary with histogram data
    """
    data_array = np.array(data['data']).flatten()
    hist, bin_edges = np.histogram(data_array, bins=bins)
    
    return {
        'counts': hist.tolist(),
        'bin_edges': bin_edges.tolist(),
        'bins': bins
    }


def validate_data_structure(data: Dict) -> tuple[bool, str]:
    """
    Validate that data dictionary has correct structure.
    
    Args:
        data: Dictionary to validate
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    required_fields = ['metadata', 'data']
    required_metadata = ['radar_type', 'units', 'value_range']
    
    # Check top-level fields
    for field in required_fields:
        if field not in data:
            return False, f"Missing required field: {field}"
    
    # Check metadata fields
    metadata = data['metadata']
    for field in required_metadata:
        if field not in metadata:
            return False, f"Missing required metadata field: {field}"
    
    # Check data is 2D array
    if not isinstance(data['data'], list):
        return False, "Data field must be a list"
    
    if len(data['data']) > 0 and not isinstance(data['data'][0], list):
        return False, "Data must be a 2D list (list of lists)"
    
    # Check radar_type is valid
    if metadata['radar_type'] not in ['reflectivity', 'velocity']:
        return False, f"Invalid radar_type: {metadata['radar_type']}"
    
    return True, "Valid"


def format_file_size(size_bytes: int) -> str:
    """
    Format file size in human-readable format.
    
    Args:
        size_bytes: Size in bytes
        
    Returns:
        Formatted string (e.g., "1.5 MB")
    """
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"


def get_file_info(filepath: str) -> Dict[str, Any]:
    """
    Get information about a file.
    
    Args:
        filepath: Path to file
        
    Returns:
        Dictionary with file information
    """
    path = Path(filepath)
    
    if not path.exists():
        return {'exists': False}
    
    stat = path.stat()
    
    return {
        'exists': True,
        'name': path.name,
        'size_bytes': stat.st_size,
        'size_formatted': format_file_size(stat.st_size),
        'modified': stat.st_mtime
    }
