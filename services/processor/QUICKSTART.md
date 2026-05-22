# Quick Start Guide - Weather Radar Converter

## Installation

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Place your scale reference images in the same directory:
   - `base_reflectivity_intensity_scale.png`
   - `base_velocity_intensity_scale.png`

## Usage

### Option 1: Command Line (Simplest)

```bash
# Convert a single radar image
python radar_converter.py your_radar_image.png \
    --type reflectivity \
    --output output_data.json
```

### Option 2: Python Script

```python
from radar_converter import RadarImageConverter

# Initialize
converter = RadarImageConverter(
    'base_reflectivity_intensity_scale.png',
    'base_velocity_intensity_scale.png'
)

# Convert
converter.convert_and_save(
    'your_radar_image.png',
    'reflectivity',
    'output.json'
)
```

### Option 3: Batch Processing

```python
images = [
    ('image1.png', 'reflectivity'),
    ('image2.png', 'velocity'),
]

for img, radar_type in images:
    converter.convert_and_save(img, radar_type, f'{img}.json')
```

## Output

The converter creates JSON files with this structure:

```json
{
  "metadata": {
    "radar_type": "reflectivity",
    "units": "dBZ",
    "dimensions": {"width": 800, "height": 600}
  },
  "data": [[val1, val2, ...], [val1, val2, ...], ...]
}
```

## For Machine Learning

```python
import numpy as np
import json

# Load data
with open('output.json', 'r') as f:
    data = json.load(f)

# Convert to NumPy array
array = np.array(data['data'])

# Now use with PyTorch, TensorFlow, etc.
```

## Key Options

- `--sample-rate 4`: Process every 4th pixel (16x smaller files)
- `--save-numpy`: Also save as `.npy` format
- `--type reflectivity` or `--type velocity`

## Examples

See these files for detailed examples:
- `demo.py` - Working demonstration
- `example_usage.py` - Multiple usage patterns
- `test_converter.py` - Validation tests

## Need Help?

Run: `python radar_converter.py --help`

Read: `README.md` for comprehensive documentation
