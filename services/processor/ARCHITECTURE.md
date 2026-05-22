# Radar Tools Architecture

## System Overview

```
┌─────────────────────────────────────────────────────────────┐
│                     RADAR TOOLS SYSTEM                       │
└─────────────────────────────────────────────────────────────┘

                            USER
                              │
                ┌─────────────┼─────────────┐
                │             │             │
            CLI Tools    Python API    Direct Modules
                │             │             │
        ┌───────┴───────┐     │     ┌───────┴────────┐
        │               │     │     │                │
    convert.py    verify.py   │   Custom Scripts   Notebooks
        │               │     │     │                │
        └───────────────┴─────┼─────┴────────────────┘
                              │
                    ┌─────────┴─────────┐
                    │   radar_tools/    │
                    │    (Package)      │
                    └─────────┬─────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
   color_scale.py       converter.py         verifier.py
        │                     │                     │
    Color↔Value          Image→Data           Data→Image
     Mapping             Conversion          Verification
        │                     │                     │
        └─────────────────────┼─────────────────────┘
                              │
                          utils.py
                              │
                    Common Utilities
```

## Module Dependency Graph

```
┌──────────────────┐
│   __init__.py    │  ← Package entry point
└────────┬─────────┘
         │ imports
         ├───────────────────────────────────────┐
         │                                       │
         ▼                                       ▼
┌──────────────────┐                   ┌──────────────────┐
│  color_scale.py  │                   │    utils.py      │
│                  │                   │                  │
│ RadarColorScale  │                   │ Helper functions │
└────────┬─────────┘                   └────────┬─────────┘
         │ uses                                 │ uses
         │                                      │
         ├──────────────┬───────────────────────┤
         │              │                       │
         ▼              ▼                       ▼
┌──────────────────┐  ┌──────────────────┐
│  converter.py    │  │  verifier.py     │
│                  │  │                  │
│ RadarImage       │  │ RadarImage       │
│ Converter        │  │ Verifier         │
└──────────────────┘  └──────────────────┘
         │                     │
         └──────────┬──────────┘
                    │ used by
                    ▼
         ┌────────────────────┐
         │   CLI Scripts &    │
         │   User Code        │
         └────────────────────┘
```

## Data Flow

### Conversion Workflow

```
Original Image              JSON Data               NumPy Array
   (PNG)                     (text)                   (binary)
     │                         │                         │
     ▼                         ▼                         ▼
┌─────────┐              ┌──────────┐            ┌──────────┐
│ 800x600 │              │ metadata │            │ 800x600  │
│ RGB     │   ───────►   │ + data   │   ───────► │ float    │
│ pixels  │  converter   │ matrix   │   optional │ array    │
└─────────┘              └──────────┘            └──────────┘
     │                         │                         │
     │                    Easy to:                  Fast for:
     │                    - inspect                 - ML training
     │                    - edit                    - computation
     │                    - share                   - analysis
     │                         │                         │
     └─────────────────────────┴─────────────────────────┘
                          All formats represent
                          the same information
```

### Verification Workflow

```
Original Image          JSON Data          Reconstructed Image
   (PNG)                                         (PNG)
     │                      │                       │
     │                      │                       │
     ▼                      ▼                       ▼
┌─────────┐          ┌──────────┐           ┌─────────┐
│ 800x600 │          │ metadata │           │ 800x600 │
│ RGB     │          │ + data   │  ────────►│ RGB     │
│ pixels  │          │ matrix   │ verifier  │ pixels  │
└─────────┘          └──────────┘           └─────────┘
     │                                             │
     │    Compare pixel-by-pixel                  │
     └──────────────────┬────────────────────────┘
                        │
                        ▼
              ┌──────────────────┐
              │  Verification    │
              │  Metrics:        │
              │  - MAE           │
              │  - Accuracy %    │
              │  - Difference    │
              │    map           │
              └──────────────────┘
                        │
                        ▼
              ┌──────────────────┐
              │  Visual Outputs: │
              │  - Comparison    │
              │  - Diff map      │
              │  - Side-by-side  │
              └──────────────────┘
```

## Class Relationships

```
┌────────────────────────────────────────────────────┐
│              RadarColorScale                       │
│ -------------------------------------------------- │
│ - scale_type: str                                  │
│ - color_samples: List[Tuple[RGB, float]]          │
│ - min_value, max_value: float                     │
│ -------------------------------------------------- │
│ + find_closest_value(rgb) → float                 │
│ + value_to_rgb(value) → RGB                       │
│ + get_units() → str                               │
└────────────────┬───────────────────────────────────┘
                 │
                 │ composed by (2 instances)
                 │
    ┌────────────┴──────────────┐
    │                           │
    ▼                           ▼
┌─────────────────┐     ┌──────────────────┐
│ RadarImage      │     │ RadarImage       │
│ Converter       │     │ Verifier         │
├─────────────────┤     ├──────────────────┤
│ - ref_scale     │     │ - ref_scale      │
│ - vel_scale     │     │ - vel_scale      │
├─────────────────┤     ├──────────────────┤
│ + convert_image │     │ + data_to_image  │
│ + convert_and_  │     │ + verify_        │
│   save          │     │   conversion     │
└─────────────────┘     └──────────────────┘
```

## Color Scale Mechanism

```
Scale Reference Image                Color-to-Value Mapping
┌────────────────────┐              ┌─────────────────────┐
│  ■■■■■■■■■■■■■■■■   │              │ RGB(0,0,100)   → -20│
│  Brown Blue Green  │  ──extract──►│ RGB(0,255,0)   →  20│
│  Yellow Orange Red │   colors     │ RGB(255,255,0) →  40│
└────────────────────┘              │ RGB(255,0,0)   →  60│
                                    └─────────────────────┘
                                              │
                                              │ lookup
                                              ▼
Radar Image Pixel                    Find Closest Match
┌────────────┐                       ┌─────────────────────┐
│ RGB(50,200,│                       │ Euclidean Distance: │
│      30)   │ ──────────────────►   │ d = √(Δr²+Δg²+Δb²) │
└────────────┘      search           │                     │
                                     │ Return value of     │
                                     │ closest color       │
                                     └─────────────────────┘
                                              │
                                              ▼
                                        Radar Value
                                        (e.g., 25 dBZ)
```

## Verification Process

```
Step 1: Load Original         Step 2: Load JSON Data
┌────────────────┐            ┌─────────────────────┐
│  Original      │            │ {"metadata": {...}, │
│  Image         │            │  "data": [[1,2,3],  │
│  (PNG)         │            │          [4,5,6]]}  │
└────────────────┘            └─────────────────────┘
        │                              │
        │                              ▼
        │                     Step 3: Reconstruct
        │                     ┌─────────────────────┐
        │                     │ For each value:     │
        │                     │   value → RGB       │
        │                     │ Create image        │
        │                     └─────────────────────┘
        │                              │
        │                              ▼
        │                     Reconstructed Image
        │                     ┌─────────────────────┐
        │                     │  Generated from     │
        │                     │  data matrix        │
        │                     └─────────────────────┘
        │                              │
        └──────────────┬───────────────┘
                       │
                       ▼
        Step 4: Pixel-by-Pixel Comparison
        ┌──────────────────────────────────┐
        │ For each pixel (x,y):            │
        │   diff = |orig_RGB - recon_RGB|  │
        │ Calculate metrics:               │
        │   - Mean Absolute Error          │
        │   - Pixel Perfect %              │
        │   - Within threshold %           │
        └──────────────────────────────────┘
                       │
                       ▼
        Step 5: Generate Visualizations
        ┌──────────────────────────────────┐
        │ - Side-by-side comparison        │
        │ - Difference map (enhanced)      │
        │ - Metrics report                 │
        └──────────────────────────────────┘
```

## File Organization

```
project_root/
│
├── radar_tools/              # Package directory
│   ├── __init__.py          # Exports public API
│   ├── color_scale.py       # Color mapping logic
│   ├── converter.py         # Conversion logic
│   ├── verifier.py          # Verification logic
│   └── utils.py             # Utilities
│
├── convert.py               # CLI for conversion
├── verify.py                # CLI for verification
├── demo_modular.py          # Demo script
├── test_modular.py          # Test suite
│
├── requirements.txt         # Dependencies
├── README_MODULAR.md        # Main documentation
└── ARCHITECTURE.md          # This file
```

## Design Principles

### 1. Single Responsibility
Each module has one clear purpose:
- `color_scale.py`: Only handles color↔value mapping
- `converter.py`: Only handles image→data conversion
- `verifier.py`: Only handles data→image reconstruction
- `utils.py`: Only provides common utilities

### 2. Dependency Inversion
High-level modules depend on abstractions:
```python
# converter.py depends on color_scale.py interface
class RadarImageConverter:
    def __init__(self, ref_scale, vel_scale):
        self.ref_scale = RadarColorScale(ref_scale, 'reflectivity')
        self.vel_scale = RadarColorScale(vel_scale, 'velocity')
```

### 3. Open/Closed Principle
Open for extension, closed for modification:
- Add new radar types without changing existing code
- Extend with new verification metrics
- Add custom processors as separate modules

### 4. Interface Segregation
Small, focused interfaces:
```python
# Users only import what they need
from radar_tools import RadarImageConverter  # Just conversion
from radar_tools import RadarImageVerifier   # Just verification
from radar_tools import load_json_data       # Just utilities
```

## Extension Points

### Adding New Features

1. **New Radar Type**:
   - Update `color_scale.py` value ranges
   - Add scale reference image
   - No other changes needed

2. **Custom Processing**:
   ```python
   # radar_tools/custom.py
   class CustomProcessor:
       def __init__(self, converter):
           self.converter = converter
       
       def process(self, image):
           # Custom logic here
           pass
   ```

3. **New Output Format**:
   ```python
   # radar_tools/exporters.py
   def export_to_csv(data, output_path):
       # Custom export logic
       pass
   ```

4. **Advanced Verification**:
   ```python
   # radar_tools/advanced_verifier.py
   class AdvancedVerifier(RadarImageVerifier):
       def verify_with_ml(self, ...):
           # ML-based verification
           pass
   ```

## Performance Characteristics

### Time Complexity
- Color scale extraction: O(W) where W = scale image width
- Image conversion: O(H × W) where H, W = image dimensions
- Verification: O(H × W) for comparison
- Reconstruction: O(H × W) for image generation

### Space Complexity
- Color samples: O(W) stored colors
- Data matrix: O(H × W) values
- Reconstructed image: O(H × W × 3) RGB values

### Optimization Strategies
1. **Sampling**: Reduce resolution by sample_rate²
2. **Numpy**: Fast array operations
3. **Lazy loading**: Load data only when needed
4. **Caching**: Store color lookups (future enhancement)

## Error Handling Strategy

```
User Input
    │
    ▼
┌────────────────┐
│ Validate Input │  ← Check file exists, type valid, etc.
└────────┬───────┘
         │
         ▼
┌────────────────┐
│ Try Operation  │  ← Wrapped in try/except
└────────┬───────┘
         │
    ┌────┴────┐
    │         │
    ▼         ▼
Success    Error
    │         │
    │    ┌────┴─────────┐
    │    │ Log error    │
    │    │ Clean up     │
    │    │ Return       │
    │    │ gracefully   │
    │    └──────────────┘
    │
    ▼
Continue
```

## Testing Strategy

```
Unit Tests
    │
    ├─ test_color_scale_module()
    │   ├─ Test extraction
    │   ├─ Test value lookup
    │   └─ Test RGB conversion
    │
    ├─ test_converter_module()
    │   ├─ Test initialization
    │   ├─ Test conversion
    │   └─ Test file output
    │
    ├─ test_verifier_module()
    │   ├─ Test reconstruction
    │   ├─ Test comparison
    │   └─ Test metrics
    │
    └─ test_utilities_module()
        ├─ Test validation
        ├─ Test statistics
        └─ Test file operations

Integration Tests
    │
    └─ test_end_to_end_workflow()
        ├─ Convert → Verify
        ├─ Batch processing
        └─ Error handling
```

## Future Enhancements

1. **Performance**:
   - Parallel processing for batch operations
   - Color lookup caching
   - GPU acceleration option

2. **Features**:
   - Geographic coordinate mapping
   - Timestamp extraction
   - Animation generation
   - Data augmentation

3. **Verification**:
   - ML-based quality assessment
   - Automated threshold tuning
   - Statistical significance tests

4. **Integration**:
   - REST API wrapper
   - Cloud storage support
   - Database connectors

---

This modular architecture ensures the codebase remains:
- **Maintainable**: Easy to update and fix
- **Testable**: Each part can be tested independently  
- **Extensible**: New features don't break existing code
- **Understandable**: Clear separation of concerns
