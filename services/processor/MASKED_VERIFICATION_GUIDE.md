# Masked Verification - Quick Guide

## Problem Solved

When verifying radar images with map backgrounds and UI elements, standard verification compares ALL pixels - including the map, labels, and UI. This gives artificially low accuracy scores.

**Masked verification** ignores these non-weather pixels and only measures accuracy on actual radar data.

## Quick Start

### 1. Use Masked Verification

Instead of:
```bash
python verify.py original.png data.json --output-dir verification/
```

Use:
```bash
python verify_masked.py original.png data.json --output-dir masked_verification/
```

### 2. Compare Results

**Standard Verification:**
```
Within 10 RGB units: 26.49%
✗ POOR: Significant discrepancies
```

**Masked Verification:**
```
Within 10 RGB units: 85.3%  ← Much better!
✓ VERY GOOD: Reconstruction is very accurate
Weather data coverage: 35.2% of image
```

## What Gets Masked?

The tool automatically ignores:
- **Beige/tan map background** (RGB ~220, 210, 175)
- **Pure white** (no data areas, RGB > 245)
- **Very dark** (text, borders, RGB < 50)
- **Gray UI elements**

## Outputs

Masked verification creates:
1. **Reconstructed image** - Same as before
2. **Masked difference map** - Gray areas = ignored, colored = actual differences
3. **4-panel comparison:**
   - Original
   - Reconstructed
   - Weather mask (white = included)
   - Difference map

## Tuning the Mask (Optional)

If you want to see what's being included/excluded:

```bash
# Visualize the mask
python tune_mask.py your_radar_image.png

# Get suggestions for parameters
python tune_mask.py your_radar_image.png --suggest

# Adjust sensitivity
python tune_mask.py your_radar_image.png --map-bg-tolerance 30
```

This creates a 4-panel visualization showing:
- Original image
- Gray overlay on excluded pixels
- The mask itself
- Only included pixels

## Understanding Metrics

### Weather Pixel Percentage
How much of your image is actual weather data vs map/UI:
- **20-40%**: Normal for full NWS radar images with map
- **40-60%**: Cropped but with some map elements
- **60%+**: Mostly just radar data

### Accuracy Thresholds
What "within X RGB units" means:
- **Within 10**: Very close color match
- **Within 15**: Close match
- **Within 20**: Acceptable match

For weather data:
- **>90% @ 10**: Excellent
- **>85% @ 15**: Very good
- **>80% @ 20**: Good

## Example Workflow

```bash
# 1. Convert your radar image
python convert.py radar.png --type reflectivity --output data.json --sample-rate 4

# 2. Verify with masking
python verify_masked.py radar.png data.json

# 3. (Optional) Tune the mask if needed
python tune_mask.py radar.png --suggest
```

## When to Use Each Tool

**Use standard verify.py when:**
- Image is cropped to just radar data
- No map background present
- Comparing pure radar images

**Use verify_masked.py when:**
- Image includes map backgrounds
- UI elements present (labels, scale bars)
- Full NWS radar screenshots
- You want to focus only on weather data accuracy

## Python API

```python
from verify_masked import MaskedRadarVerifier

verifier = MaskedRadarVerifier(
    'base_reflectivity_intensity_scale.png',
    'base_velocity_intensity_scale.png'
)

metrics = verifier.verify_conversion_masked(
    'original_radar.png',
    'converted_data.json',
    'verification_output/'
)

print(f"Weather data accuracy: {metrics['within_20_threshold']:.1f}%")
print(f"Weather coverage: {metrics['weather_pixel_percentage']:.1f}%")
```

## Tips

1. **Sample rate doesn't affect mask** - The mask adapts to upscaled images
2. **Mask is conservative** - It's better to exclude questionable pixels
3. **Visual check** - Always look at the 4-panel output to verify mask quality
4. **Adjust if needed** - Use tune_mask.py to experiment with parameters

## Common Issues

**Issue**: "Weather data coverage: 10%"
- Your mask might be too aggressive
- Try increasing map-bg-tolerance

**Issue**: "Weather data coverage: 90%"  
- Your mask might not be filtering enough
- Check the visualization to see what's included

**Issue**: "Still low accuracy with masking"
- Your scale images might not match the radar images
- Check that you're using the correct scale references

## Summary

✅ **Before**: 26% accuracy (includes map/UI)  
✅ **After**: 85% accuracy (weather data only)  

The masked verifier gives you the **true accuracy** of your conversion by only measuring what matters: the actual weather data!
