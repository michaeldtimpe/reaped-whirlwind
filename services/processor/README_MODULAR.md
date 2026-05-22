# Weather Radar Tools - Modular Version

A modular, maintainable toolkit for converting weather radar images to structured data and verifying conversions.

## 🎯 Overview

This toolkit provides:
- **Conversion**: Transform color-coded radar images to numerical JSON/NumPy data
- **Verification**: Reconstruct images from data and compare against originals
- **Modular Design**: Easy to maintain, test, and extend

### Supported Radar Types
- **Base Reflectivity**: Precipitation intensity (dBZ: -20 to 70)
- **Base Velocity**: Wind movement (knots: -100 to 100)

## 📦 Installation

```bash
pip install -r requirements.txt
```

**Requirements**: Python 3.7+, NumPy, Pillow

## 🏗️ Modular Architecture

```
radar_tools/
├── __init__.py          # Package exports
├── color_scale.py       # Color-to-value mappings
├── converter.py         # Image to data conversion
├── verifier.py          # Data to image reconstruction & verification
└── utils.py             # Common utilities

Scripts:
├── convert.py           # CLI for conversion
├── verify.py            # CLI for verification
├── demo_modular.py      # Complete demonstration
└── test_modular.py      # Comprehensive test suite
```

### Module Responsibilities

| Module | Purpose | Key Classes/Functions |
|--------|---------|----------------------|
| `color_scale.py` | Color↔Value mapping | `RadarColorScale` |
| `converter.py` | Image→Data conversion | `RadarImageConverter` |
| `verifier.py` | Data→Image reconstruction | `RadarImageVerifier` |
| `utils.py` | Helper functions | `load_json_data`, `calculate_statistics` |

## 🚀 Quick Start

### Command Line Usage

**Convert an image:**
```bash
python convert.py radar_image.png \
    --type reflectivity \
    --output data.json
```

**Verify conversion:**
```bash
python verify.py original_image.png data.json \
    --output-dir verification/
```

**Reconstruct only (no comparison):**
```bash
python verify.py --reconstruct-only data.json \
    --output reconstructed.png
```

### Python API Usage

**Convert:**
```python
from radar_tools import RadarImageConverter

converter = RadarImageConverter(
    'base_reflectivity_intensity_scale.png',
    'base_velocity_intensity_scale.png'
)

converter.convert_and_save(
    'radar_image.png',
    'reflectivity',
    'output.json',
    sample_rate=1,
    save_numpy=True
)
```

**Verify:**
```python
from radar_tools import RadarImageVerifier

verifier = RadarImageVerifier(
    'base_reflectivity_intensity_scale.png',
    'base_velocity_intensity_scale.png'
)

metrics = verifier.verify_conversion(
    'original.png',
    'data.json',
    'verification_output/'
)

print(f"Accuracy: {metrics['within_10_threshold']:.1f}%")
```

## 🔍 Verification System

The verification tool helps you validate conversions by:

1. **Reconstructing** the image from numerical data
2. **Comparing** pixel-by-pixel with the original
3. **Generating** visual comparisons and difference maps
4. **Computing** accuracy metrics

### Verification Metrics

- **Mean Absolute Error**: Average RGB difference per pixel
- **Max Error**: Largest RGB difference found
- **Pixel Perfect Match**: % of exact matches
- **Within N threshold**: % of pixels within N RGB units

### Verification Outputs

The verifier generates:
- **Reconstructed image**: Image created from your data
- **Difference map**: Shows where differences occur (enhanced 10x)
- **Side-by-side comparison**: Original | Reconstructed | Difference

Example verification result:
```
Mean Absolute Error: 9.44
Pixel Perfect Match: 54.64%
Within 10 RGB units: 81.48%
✓ GOOD: Reconstruction is acceptable
```

## 📊 Output Format

### JSON Structure
```json
{
  "metadata": {
    "radar_type": "reflectivity",
    "original_dimensions": {"width": 800, "height": 600},
    "sampled_dimensions": {"width": 800, "height": 600},
    "sample_rate": 1,
    "units": "dBZ",
    "value_range": {"min": -20, "max": 70},
    "source_file": "radar.png"
  },
  "data": [
    [0.0, 5.2, 10.5, ...],
    [2.1, 8.3, 15.7, ...],
    ...
  ]
}
```

## 🔧 Advanced Usage

### Batch Processing
```python
images = [
    ('image1.png', 'reflectivity'),
    ('image2.png', 'velocity'),
]

for img_path, radar_type in images:
    # Convert
    converter.convert_and_save(
        img_path, 
        radar_type, 
        f'{img_path}.json'
    )
    
    # Verify
    verifier.verify_conversion(
        img_path,
        f'{img_path}.json',
        'verification/'
    )
```

### Using Utilities
```python
from radar_tools import (
    load_json_data,
    calculate_statistics,
    validate_data_structure,
    compare_data_arrays
)

# Load and validate
data = load_json_data('radar_data.json')
is_valid, message = validate_data_structure(data)

# Calculate statistics
stats = calculate_statistics(data)
print(f"Mean: {stats['mean']:.2f}")
print(f"Range: {stats['min']:.2f} to {stats['max']:.2f}")

# Compare two datasets
import numpy as np
array1 = np.array(data1['data'])
array2 = np.array(data2['data'])
metrics = compare_data_arrays(array1, array2)
```

### Efficient Sampling
```bash
# Process every 4th pixel (16x smaller files)
python convert.py image.png --type reflectivity --output data.json --sample-rate 4

# Reconstruct with upscaling to restore original size
python verify.py --reconstruct-only data.json --output restored.png --upscale 4
```

## 🧪 Testing

Run the comprehensive test suite:
```bash
python test_modular.py
```

Tests cover:
- Color scale extraction
- Image conversion
- Image reconstruction
- Data validation
- End-to-end workflows

## 🎨 Customization & Extension

### Adding a New Radar Type

1. **Update `color_scale.py`**:
```python
def _get_value_range(self) -> Tuple[float, float]:
    if self.scale_type == 'reflectivity':
        return -20, 70
    elif self.scale_type == 'velocity':
        return -100, 100
    elif self.scale_type == 'your_new_type':
        return min_val, max_val
```

2. **Update validation** in `utils.py`:
```python
if metadata['radar_type'] not in ['reflectivity', 'velocity', 'your_new_type']:
    return False, f"Invalid radar_type: {metadata['radar_type']}"
```

### Adding Custom Processing

Create a new module in `radar_tools/`:
```python
# radar_tools/custom_processor.py
from .converter import RadarImageConverter

class CustomProcessor:
    def __init__(self, converter: RadarImageConverter):
        self.converter = converter
    
    def process(self, image_path: str):
        # Your custom logic here
        pass
```

## 📈 Performance

On M1 MacBook Pro:
- Full resolution (800x600): ~2-3 seconds
- Sample rate 2 (400x300): ~0.5 seconds  
- Sample rate 4 (200x150): ~0.1 seconds
- Verification: ~1-2 seconds

## 🎯 Use Cases

1. **Storm Prediction**: Train ML models on historical patterns
2. **Pattern Recognition**: Identify tornadic signatures, hail cores
3. **Movement Tracking**: Analyze storm trajectories
4. **Data Validation**: Verify radar data integrity
5. **Research**: Analyze weather patterns statistically

## 🔄 Maintenance & Updates

### Updating Color Scales
Replace the scale reference images and the tool automatically adapts:
```bash
cp new_reflectivity_scale.png base_reflectivity_intensity_scale.png
python test_modular.py  # Verify everything still works
```

### Code Updates
The modular structure makes updates easy:
- Update `color_scale.py` for color mapping changes
- Update `converter.py` for conversion logic changes
- Update `verifier.py` for verification improvements
- Other modules remain unaffected

## 🆚 Advantages Over Monolithic Design

| Aspect | Monolithic | Modular |
|--------|-----------|---------|
| **Maintenance** | Hard - changes affect everything | Easy - update one module |
| **Testing** | Complex - must test entire system | Simple - test each module |
| **Understanding** | Requires reading all code | Clear - each module is focused |
| **Extension** | Risky - might break existing code | Safe - add new modules |
| **Debugging** | Difficult - interconnected code | Easy - isolated responsibilities |
| **Reusability** | Low - tightly coupled | High - import what you need |

## 📝 Command Reference

### convert.py
```bash
python convert.py IMAGE --type TYPE --output OUTPUT [OPTIONS]

Required:
  IMAGE                 Radar image to convert
  --type, -t           'reflectivity' or 'velocity'
  --output, -o         Output JSON path

Options:
  --sample-rate, -s    Sample every Nth pixel (default: 1)
  --save-numpy         Also save as .npy file
  --reflectivity-scale Path to reflectivity scale image
  --velocity-scale     Path to velocity scale image
```

### verify.py
```bash
python verify.py ORIGINAL DATA_JSON --output-dir DIR [OPTIONS]

Arguments:
  ORIGINAL             Original radar image
  DATA_JSON            JSON data file to verify

Options:
  --output-dir, -o     Verification output directory
  --no-difference      Skip difference visualization
  --reconstruct-only   Only reconstruct, don't compare
  --output            Output path (reconstruct-only mode)
  --upscale           Upscale factor for reconstruction
```

## 📚 API Reference

See inline documentation in each module:
```python
help(RadarImageConverter)
help(RadarImageVerifier)
help(RadarColorScale)
```

## 🐛 Troubleshooting

**Import Error**: Ensure you're running from the correct directory or add to path:
```python
import sys
sys.path.insert(0, '/path/to/radar_tools/parent')
```

**Scale Image Not Found**: Place scale images in working directory or specify path:
```bash
python convert.py image.png --type reflectivity --output data.json \
    --reflectivity-scale /path/to/scale.png
```

**Low Verification Accuracy**: 
- Check scale images match radar images
- Some color variation is normal (80%+ is good)
- Sample rate >1 reduces accuracy slightly

## 📄 License

Designed for weather radar data analysis and research.

## 🤝 Contributing

The modular design makes contributions easy:
1. Create new module in `radar_tools/`
2. Add exports to `__init__.py`
3. Write tests in `test_modular.py`
4. Update documentation

## 💡 Examples

See these files for complete examples:
- `demo_modular.py` - Full demonstration
- `test_modular.py` - Testing patterns
- `convert.py` - CLI usage
- `verify.py` - Verification usage

---

**Version**: 1.0.0  
**Python**: 3.7+  
**Status**: Production Ready ✓
