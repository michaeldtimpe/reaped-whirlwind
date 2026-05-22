#!/usr/bin/env python3
"""
Setup script for radar_tools package.
Run this to verify your installation.
"""

import os
import sys
from pathlib import Path

def check_installation():
    """Check if radar_tools is properly installed."""
    print("=" * 60)
    print("RADAR TOOLS INSTALLATION CHECK")
    print("=" * 60)
    
    # Check if radar_tools directory exists
    current_dir = Path.cwd()
    radar_tools_dir = current_dir / 'radar_tools'
    
    print(f"\nCurrent directory: {current_dir}")
    print(f"Looking for: {radar_tools_dir}")
    
    if not radar_tools_dir.exists():
        print("\n❌ ERROR: 'radar_tools' directory not found!")
        print("\nThe radar_tools/ package directory is missing.")
        print("You need to download it from the outputs.")
        print("\nExpected structure:")
        print("  your_project/")
        print("  ├── radar_tools/        ← Missing!")
        print("  │   ├── __init__.py")
        print("  │   ├── color_scale.py")
        print("  │   ├── converter.py")
        print("  │   ├── verifier.py")
        print("  │   └── utils.py")
        print("  ├── convert.py")
        print("  ├── verify.py")
        print("  └── test_modular.py")
        return False
    
    print(f"\n✓ Found radar_tools directory")
    
    # Check for required files
    required_files = [
        '__init__.py',
        'color_scale.py',
        'converter.py',
        'verifier.py',
        'utils.py'
    ]
    
    print("\nChecking package files:")
    all_found = True
    for filename in required_files:
        filepath = radar_tools_dir / filename
        if filepath.exists():
            print(f"  ✓ {filename}")
        else:
            print(f"  ❌ {filename} - MISSING!")
            all_found = False
    
    if not all_found:
        print("\n❌ Some package files are missing!")
        return False
    
    # Try importing
    print("\nTesting import...")
    try:
        sys.path.insert(0, str(current_dir))
        from radar_tools import (
            RadarColorScale,
            RadarImageConverter,
            RadarImageVerifier
        )
        print("  ✓ Successfully imported radar_tools")
        print("  ✓ RadarColorScale available")
        print("  ✓ RadarImageConverter available")
        print("  ✓ RadarImageVerifier available")
    except ImportError as e:
        print(f"  ❌ Import failed: {e}")
        return False
    
    print("\n" + "=" * 60)
    print("✓ INSTALLATION CHECK PASSED!")
    print("=" * 60)
    print("\nYou're ready to use radar_tools!")
    print("\nQuick start:")
    print("  python convert.py --help")
    print("  python verify.py --help")
    print("  python demo_modular.py")
    
    return True


if __name__ == '__main__':
    success = check_installation()
    sys.exit(0 if success else 1)
