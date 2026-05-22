# Getting Started with Weather Radar Tools

This guide will get you up and running with the modular radar tools in 5 minutes.

## 📋 Prerequisites

- Python 3.7 or higher
- pip (Python package installer)
- Your radar scale reference images

## 🚀 Installation

### Step 1: Install Dependencies

```bash
pip install -r requirements.txt
```

This installs:
- NumPy (numerical operations)
- Pillow (image processing)

### Step 2: Verify Installation

```bash
python test_modular.py
```

You should see:
```
============================================================
ALL TESTS PASSED! ✓
============================================================
```

## 🎯 First Steps

### Convert Your First Image

**Command Line:**
```bash
python convert.py your_radar_image.png \
    --type reflectivity \
    --output my_data.json
```

**Python Script:**
```python
from radar_tools import RadarImageConverter

converter = RadarImageConverter(
    'base_reflectivity_intensity_scale.png',
    'base_velocity_intensity_scale.png'
)

converter.convert_and_save(
    'your_radar_image.png',
    'reflectivity',
    'my_data.json'
)
```

### Verify Your Conversion

**Command Line:**
```bash
python verify.py your_radar_image.png my_data.json \
    --output-dir verification/
```

**Python Script:**
```python
from radar_tools import RadarImageVerifier

verifier = RadarImageVerifier(
    'base_reflectivity_intensity_scale.png',
    'base_velocity_intensity_scale.png'
)

metrics = verifier.verify_conversion(
    'your_radar_image.png',
    'my_data.json',
    'verification_output/'
)

print(f"Accuracy: {metrics['within_10_threshold']:.1f}%")
```

## 📊 Understanding the Output

### Converted Data (JSON)

```json
{
  "metadata": {
    "radar_type": "reflectivity",
    "units": "dBZ",
    "dimensions": {...}
  },
  "data": [
    [0.0, 5.2, 10.5, ...],
    [2.1, 8.3, 15.7, ...]
  ]
}
```

- `metadata`: Information about the conversion
- `data`: 2D array of radar values

### Verification Output

The verification creates:
1. **Reconstructed image**: What your data looks like as an image
2. **Difference map**: Shows where original ≠ reconstructed
3. **Comparison image**: Side-by-side view
4. **Metrics**: Numerical accuracy measures

Example metrics:
```
Mean Absolute Error: 9.44
Pixel Perfect Match: 54.64%
Within 10 RGB units: 81.48%
✓ GOOD: Reconstruction is acceptable
```

## 🎓 Learning Path

### 1. Run the Demo
```bash
python demo_modular.py
```

This shows the complete workflow and creates example outputs.

### 2. Try Different Sample Rates

**Full resolution:**
```bash
python convert.py image.png --type reflectivity --output full.json --sample-rate 1
```

**Quarter resolution (4x faster, smaller files):**
```bash
python convert.py image.png --type reflectivity --output quarter.json --sample-rate 4
```

### 3. Batch Process Multiple Images

```python
from radar_tools import RadarImageConverter, RadarImageVerifier

converter = RadarImageConverter(
    'base_reflectivity_intensity_scale.png',
    'base_velocity_intensity_scale.png'
)

verifier = RadarImageVerifier(
    'base_reflectivity_intensity_scale.png',
    'base_velocity_intensity_scale.png'
)

images = ['image1.png', 'image2.png', 'image3.png']

for img in images:
    # Convert
    output = f'{img}_data.json'
    converter.convert_and_save(img, 'reflectivity', output)
    
    # Verify
    verifier.verify_conversion(img, output, 'verification/')
```

### 4. Use the Data in Machine Learning

**With PyTorch:**
```python
import torch
import json
import numpy as np

# Load data
with open('my_data.json', 'r') as f:
    data = json.load(f)

# Convert to tensor
array = np.array(data['data'])
tensor = torch.from_numpy(array).float()

# Normalize
tensor = (tensor - tensor.min()) / (tensor.max() - tensor.min())

# Add dimensions for CNN: [batch, channels, height, width]
tensor = tensor.unsqueeze(0).unsqueeze(0)

print(f"Tensor shape: {tensor.shape}")
# Ready for model.forward(tensor)
```

**With TensorFlow:**
```python
import tensorflow as tf
import numpy as np

# Load and convert
array = np.array(data['data'])
array = np.expand_dims(array, axis=(0, -1))  # Add batch & channel dims
tensor = tf.convert_to_tensor(array, dtype=tf.float32)

# Normalize
tensor = (tensor - tf.reduce_min(tensor)) / (tf.reduce_max(tensor) - tf.reduce_min(tensor))

print(f"Tensor shape: {tensor.shape}")
```

## 🔧 Common Tasks

### Task 1: Convert and Save Both Formats
```bash
python convert.py image.png --type reflectivity --output data.json --save-numpy
```

This creates:
- `data.json` (human-readable, shareable)
- `data.npy` (fast loading for computation)

### Task 2: Quick Verification (Reconstruct Only)
```bash
python verify.py --reconstruct-only data.json --output reconstructed.png
```

Use this to quickly visualize what your data looks like.

### Task 3: Process with Efficient Sampling
```bash
# Convert with sample rate 4 (16x smaller)
python convert.py large_image.png --type reflectivity --output data.json --sample-rate 4

# Reconstruct with upscaling to restore size
python verify.py --reconstruct-only data.json --output restored.png --upscale 4
```

### Task 4: Analyze the Data
```python
from radar_tools import load_json_data, calculate_statistics

# Load
data = load_json_data('my_data.json')

# Analyze
stats = calculate_statistics(data)

print(f"Mean: {stats['mean']:.2f}")
print(f"Range: {stats['min']:.2f} to {stats['max']:.2f}")
print(f"Non-zero pixels: {stats['non_zero_count']}")
```

## 🐛 Troubleshooting

### Problem: "ModuleNotFoundError: No module named 'radar_tools'"

**Solution**: Ensure you're in the correct directory:
```bash
cd /path/to/weather_radar_tools/
python your_script.py
```

Or add to Python path:
```python
import sys
sys.path.insert(0, '/path/to/weather_radar_tools/')
from radar_tools import RadarImageConverter
```

### Problem: "FileNotFoundError: scale image not found"

**Solution**: Ensure scale images are in your working directory:
```bash
ls base_reflectivity_intensity_scale.png
ls base_velocity_intensity_scale.png
```

Or specify the path explicitly:
```bash
python convert.py image.png --type reflectivity --output data.json \
    --reflectivity-scale /path/to/base_reflectivity_intensity_scale.png \
    --velocity-scale /path/to/base_velocity_intensity_scale.png
```

### Problem: Low verification accuracy (<70%)

**Causes & Solutions**:
1. **Scale images don't match radar images**: Ensure you're using the correct scale references
2. **Different color profiles**: Some variation is normal; 80%+ is good
3. **Compression artifacts**: Original image may have JPEG compression
4. **Sample rate effects**: Higher sample rates reduce accuracy slightly

### Problem: Out of memory

**Solution**: Use sampling to reduce data size:
```bash
python convert.py large_image.png --type reflectivity --output data.json --sample-rate 8
```

Sample rate 8 reduces memory by 64x.

## 📚 Next Steps

1. **Read the full documentation**: `README_MODULAR.md`
2. **Understand the architecture**: `ARCHITECTURE.md`
3. **Explore examples**: `demo_modular.py`, `example_usage.py`
4. **Run tests**: `test_modular.py`

## 💡 Quick Reference

### Command Cheat Sheet

```bash
# Convert
python convert.py IMAGE --type TYPE --output OUT.json

# Verify
python verify.py IMAGE DATA.json --output-dir DIR/

# Reconstruct
python verify.py --reconstruct-only DATA.json --output IMG.png

# Help
python convert.py --help
python verify.py --help
```

### Import Cheat Sheet

```python
# Full toolkit
from radar_tools import (
    RadarImageConverter,
    RadarImageVerifier,
    load_json_data,
    calculate_statistics
)

# Just conversion
from radar_tools import RadarImageConverter

# Just verification
from radar_tools import RadarImageVerifier

# Just utilities
from radar_tools import load_json_data, calculate_statistics
```

## 🎉 You're Ready!

You now have:
- ✅ A working radar conversion tool
- ✅ A verification system for quality assurance
- ✅ Modular code that's easy to maintain
- ✅ Data ready for machine learning

Start converting your radar images!

---

**Need help?** Check:
- `README_MODULAR.md` - Comprehensive guide
- `ARCHITECTURE.md` - System design
- `demo_modular.py` - Working examples
- `test_modular.py` - Test patterns
