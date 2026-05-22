#!/usr/bin/env python3
"""
Example usage and batch processing for radar converter.
"""

from radar_converter import RadarImageConverter
import json
from pathlib import Path
from typing import List
import numpy as np


def example_single_conversion():
    """Example: Convert a single radar image."""
    print("=" * 60)
    print("EXAMPLE 1: Single Image Conversion")
    print("=" * 60)
    
    # Initialize converter with scale reference images
    converter = RadarImageConverter(
        reflectivity_scale_path='/mnt/user-data/uploads/base_reflectivity_intensity_scale.png',
        velocity_scale_path='/mnt/user-data/uploads/base_velocity_intensity_scale.png'
    )
    
    # Example: Convert a reflectivity image
    # Replace with your actual radar image path
    # converter.convert_and_save(
    #     image_path='path/to/your/reflectivity_image.png',
    #     radar_type='reflectivity',
    #     output_path='/mnt/user-data/outputs/reflectivity_data.json',
    #     sample_rate=1,  # Process every pixel
    #     save_numpy=True
    # )
    
    print("To convert your own image, uncomment the code above and provide the path")


def example_batch_processing():
    """Example: Batch process multiple radar images."""
    print("\n" + "=" * 60)
    print("EXAMPLE 2: Batch Processing")
    print("=" * 60)
    
    # Initialize converter
    converter = RadarImageConverter(
        reflectivity_scale_path='/mnt/user-data/uploads/base_reflectivity_intensity_scale.png',
        velocity_scale_path='/mnt/user-data/uploads/base_velocity_intensity_scale.png'
    )
    
    # Define your batch of images
    # image_batch = [
    #     ('path/to/image1.png', 'reflectivity'),
    #     ('path/to/image2.png', 'velocity'),
    #     ('path/to/image3.png', 'reflectivity'),
    # ]
    
    # Process batch
    # for i, (image_path, radar_type) in enumerate(image_batch, 1):
    #     output_path = f'/mnt/user-data/outputs/radar_data_{i}.json'
    #     print(f"\nProcessing {i}/{len(image_batch)}: {image_path}")
    #     converter.convert_and_save(image_path, radar_type, output_path)
    
    print("To batch process, uncomment the code above and provide image paths")


def example_with_sampling():
    """Example: Convert with reduced sampling for efficiency."""
    print("\n" + "=" * 60)
    print("EXAMPLE 3: Efficient Conversion with Sampling")
    print("=" * 60)
    
    converter = RadarImageConverter(
        reflectivity_scale_path='/mnt/user-data/uploads/base_reflectivity_intensity_scale.png',
        velocity_scale_path='/mnt/user-data/uploads/base_velocity_intensity_scale.png'
    )
    
    # Sample every 4th pixel (reduces data size by ~16x)
    # Useful for training ML models where full resolution isn't needed
    # converter.convert_and_save(
    #     image_path='path/to/large_image.png',
    #     radar_type='reflectivity',
    #     output_path='/mnt/user-data/outputs/sampled_data.json',
    #     sample_rate=4,
    #     save_numpy=True
    # )
    
    print("Sample rate of 4 = 1/4 resolution in each dimension")
    print("This reduces file size significantly while preserving patterns")


def load_and_analyze_converted_data(json_path: str):
    """Example: Load and analyze converted data."""
    print("\n" + "=" * 60)
    print("EXAMPLE 4: Loading and Analyzing Converted Data")
    print("=" * 60)
    
    # Load JSON data
    with open(json_path, 'r') as f:
        data = json.load(f)
    
    # Access metadata
    metadata = data['metadata']
    print(f"Radar Type: {metadata['radar_type']}")
    print(f"Dimensions: {metadata['sampled_dimensions']['width']}x{metadata['sampled_dimensions']['height']}")
    print(f"Units: {metadata['units']}")
    
    # Convert to NumPy for analysis
    data_array = np.array(data['data'])
    
    # Perform analysis
    print(f"\nData Analysis:")
    print(f"  Shape: {data_array.shape}")
    print(f"  Mean: {data_array.mean():.2f}")
    print(f"  Std Dev: {data_array.std():.2f}")
    print(f"  Min: {data_array.min():.2f}")
    print(f"  Max: {data_array.max():.2f}")
    
    # Find areas of high intensity
    if metadata['radar_type'] == 'reflectivity':
        # High reflectivity (severe weather)
        severe_threshold = 50  # dBZ
        severe_pixels = np.sum(data_array > severe_threshold)
        print(f"  Severe weather pixels (>{severe_threshold} dBZ): {severe_pixels}")
    else:
        # High velocity (strong winds)
        high_velocity = np.sum(np.abs(data_array) > 50)
        print(f"  High velocity pixels (>50 knots): {high_velocity}")
    
    return data_array


def create_training_dataset(image_list: List[tuple], output_dir: str):
    """
    Create a training dataset from multiple radar images.
    
    Args:
        image_list: List of (image_path, radar_type) tuples
        output_dir: Directory to save the dataset
    """
    print("\n" + "=" * 60)
    print("EXAMPLE 5: Creating ML Training Dataset")
    print("=" * 60)
    
    converter = RadarImageConverter(
        reflectivity_scale_path='/mnt/user-data/uploads/base_reflectivity_intensity_scale.png',
        velocity_scale_path='/mnt/user-data/uploads/base_velocity_intensity_scale.png'
    )
    
    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True, parents=True)
    
    dataset = {
        'samples': [],
        'metadata': {
            'total_samples': len(image_list),
            'description': 'Weather radar training dataset'
        }
    }
    
    for i, (image_path, radar_type) in enumerate(image_list):
        print(f"Processing sample {i+1}/{len(image_list)}")
        
        # Convert image
        data = converter.convert_image(image_path, radar_type, sample_rate=2)
        
        # Add to dataset
        dataset['samples'].append({
            'id': i,
            'source': Path(image_path).name,
            'type': radar_type,
            'data': data['data']
        })
    
    # Save dataset
    dataset_path = output_path / 'training_dataset.json'
    with open(dataset_path, 'w') as f:
        json.dump(dataset, f, indent=2)
    
    print(f"\nDataset saved to: {dataset_path}")
    print(f"Total samples: {len(dataset['samples'])}")


def demonstrate_ml_preparation():
    """Show how to prepare data for ML frameworks."""
    print("\n" + "=" * 60)
    print("EXAMPLE 6: Preparing Data for ML Frameworks")
    print("=" * 60)
    
    print("""
# For PyTorch:
import torch
data_array = np.array(data['data'])
tensor = torch.from_numpy(data_array).float()
# Normalize to [0, 1] range
tensor_normalized = (tensor - tensor.min()) / (tensor.max() - tensor.min())

# For TensorFlow/Keras:
import tensorflow as tf
data_array = np.array(data['data'])
# Add batch and channel dimensions
data_expanded = np.expand_dims(data_array, axis=(0, -1))
tensor = tf.convert_to_tensor(data_expanded, dtype=tf.float32)

# For scikit-learn:
data_array = np.array(data['data'])
# Flatten for traditional ML models
data_flattened = data_array.flatten().reshape(1, -1)
    """)


if __name__ == '__main__':
    print("Weather Radar Converter - Usage Examples")
    print("=" * 60)
    
    # Run examples
    example_single_conversion()
    example_batch_processing()
    example_with_sampling()
    demonstrate_ml_preparation()
    
    print("\n" + "=" * 60)
    print("All examples completed!")
    print("=" * 60)
    print("\nNext steps:")
    print("1. Uncomment the examples above and add your image paths")
    print("2. Run: python example_usage.py")
    print("3. Or use the CLI: python radar_converter.py --help")
