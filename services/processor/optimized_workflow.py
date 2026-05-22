#!/usr/bin/env python3
"""
Optimized Radar Processing Workflow
Batch process multiple radar images with optimal settings.
"""

import sys
from pathlib import Path
import time
import json

sys.path.insert(0, str(Path(__file__).parent))

from radar_tools import RadarImageConverter
from verify_masked import MaskedRadarVerifier


class OptimizedRadarProcessor:
    """Optimized batch processor for radar images."""
    
    def __init__(self, 
                 reflectivity_scale: str = 'base_reflectivity_intensity_scale.png',
                 velocity_scale: str = 'base_velocity_intensity_scale.png',
                 sample_rate: int = 4):
        """
        Initialize with optimal settings.
        
        Args:
            reflectivity_scale: Path to reflectivity scale image
            velocity_scale: Path to velocity scale image
            sample_rate: Sample rate (4 recommended for balance)
        """
        self.converter = RadarImageConverter(reflectivity_scale, velocity_scale)
        self.verifier = MaskedRadarVerifier(reflectivity_scale, velocity_scale)
        self.sample_rate = sample_rate
        
        print("=" * 70)
        print("OPTIMIZED RADAR PROCESSOR")
        print("=" * 70)
        print(f"Sample rate: {sample_rate} (reduces size by {sample_rate**2}x)")
        print(f"Verification: Masked (ignores map/UI)")
        print()
    
    def process_single(self, image_path: str, radar_type: str, 
                      output_dir: str, verify: bool = True) -> dict:
        """
        Process a single radar image with optimal settings.
        
        Args:
            image_path: Path to radar image
            radar_type: 'reflectivity' or 'velocity'
            output_dir: Directory for outputs
            verify: Whether to run verification
            
        Returns:
            Dictionary with processing results
        """
        start_time = time.time()
        
        image_path = Path(image_path)
        output_dir = Path(output_dir)
        output_dir.mkdir(exist_ok=True, parents=True)
        
        # Generate output filename
        json_name = f"{image_path.stem}_sr{self.sample_rate}.json"
        json_path = output_dir / json_name
        
        print(f"Processing: {image_path.name}")
        print(f"  Type: {radar_type}")
        print(f"  Sample rate: {self.sample_rate}")
        
        # Convert
        self.converter.convert_and_save(
            str(image_path),
            radar_type,
            str(json_path),
            sample_rate=self.sample_rate,
            save_numpy=False  # JSON is sufficient for most uses
        )
        
        conversion_time = time.time() - start_time
        
        # Get file sizes
        original_size = image_path.stat().st_size / (1024 * 1024)  # MB
        json_size = json_path.stat().st_size / (1024 * 1024)  # MB
        
        print(f"  Original: {original_size:.2f} MB")
        print(f"  JSON output: {json_size:.2f} MB")
        print(f"  Conversion time: {conversion_time:.2f}s")
        
        results = {
            'image_path': str(image_path),
            'json_path': str(json_path),
            'radar_type': radar_type,
            'sample_rate': self.sample_rate,
            'original_size_mb': original_size,
            'json_size_mb': json_size,
            'conversion_time': conversion_time,
        }
        
        # Verify if requested
        if verify:
            print(f"  Running masked verification...")
            verify_dir = output_dir / 'verification'
            
            verify_start = time.time()
            metrics = self.verifier.verify_conversion_masked(
                str(image_path),
                str(json_path),
                str(verify_dir),
                show_difference=True
            )
            verify_time = time.time() - verify_start
            
            results['verification'] = metrics
            results['verification_time'] = verify_time
            
            print(f"  Verification time: {verify_time:.2f}s")
            print(f"  Weather accuracy: {metrics['within_20_threshold']:.1f}%")
        
        print(f"  Total time: {time.time() - start_time:.2f}s")
        print()
        
        return results
    
    def process_batch(self, image_list: list, output_dir: str, 
                     verify: bool = True) -> list:
        """
        Process multiple radar images.
        
        Args:
            image_list: List of (image_path, radar_type) tuples
            output_dir: Base output directory
            verify: Whether to verify each conversion
            
        Returns:
            List of result dictionaries
        """
        print("=" * 70)
        print(f"BATCH PROCESSING: {len(image_list)} images")
        print("=" * 70)
        print()
        
        results = []
        total_start = time.time()
        
        for i, (image_path, radar_type) in enumerate(image_list, 1):
            print(f"[{i}/{len(image_list)}]")
            result = self.process_single(image_path, radar_type, output_dir, verify)
            results.append(result)
        
        total_time = time.time() - total_start
        
        # Summary
        print("=" * 70)
        print("BATCH SUMMARY")
        print("=" * 70)
        print(f"Images processed: {len(results)}")
        print(f"Total time: {total_time:.2f}s")
        print(f"Average per image: {total_time/len(results):.2f}s")
        
        if verify:
            avg_accuracy = sum(r['verification']['within_20_threshold'] 
                             for r in results) / len(results)
            print(f"Average accuracy: {avg_accuracy:.1f}%")
        
        # Save summary
        output_dir = Path(output_dir)
        summary_path = output_dir / 'batch_summary.json'
        with open(summary_path, 'w') as f:
            json.dump({
                'total_images': len(results),
                'total_time': total_time,
                'sample_rate': self.sample_rate,
                'results': results
            }, f, indent=2)
        
        print(f"\nSummary saved to: {summary_path}")
        
        return results
    
    def process_directory(self, input_dir: str, output_dir: str, 
                         radar_type: str, pattern: str = '*.png',
                         verify: bool = True):
        """
        Process all images in a directory.
        
        Args:
            input_dir: Directory containing radar images
            output_dir: Output directory
            radar_type: 'reflectivity' or 'velocity'
            pattern: Filename pattern to match
            verify: Whether to verify conversions
        """
        input_path = Path(input_dir)
        image_files = sorted(input_path.glob(pattern))
        
        if not image_files:
            print(f"No images found matching {pattern} in {input_dir}")
            return []
        
        print(f"Found {len(image_files)} images in {input_dir}")
        print()
        
        image_list = [(str(img), radar_type) for img in image_files]
        return self.process_batch(image_list, output_dir, verify)


def main():
    """CLI for optimized processing."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Optimized radar image processing with best practices',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process single image
  python optimized_workflow.py single image.png reflectivity output/
  
  # Process directory of reflectivity images
  python optimized_workflow.py directory input/ output/ --type reflectivity
  
  # Process with custom sample rate
  python optimized_workflow.py single image.png velocity output/ --sample-rate 2
  
  # Process without verification (faster)
  python optimized_workflow.py directory input/ output/ --type reflectivity --no-verify
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Command to run')
    
    # Single image command
    single_parser = subparsers.add_parser('single', help='Process single image')
    single_parser.add_argument('image', help='Path to radar image')
    single_parser.add_argument('type', choices=['reflectivity', 'velocity'], 
                              help='Radar type')
    single_parser.add_argument('output', help='Output directory')
    
    # Directory command
    dir_parser = subparsers.add_parser('directory', help='Process directory')
    dir_parser.add_argument('input', help='Input directory')
    dir_parser.add_argument('output', help='Output directory')
    dir_parser.add_argument('--type', '-t', required=True,
                           choices=['reflectivity', 'velocity'],
                           help='Radar type')
    dir_parser.add_argument('--pattern', default='*.png',
                           help='Filename pattern (default: *.png)')
    
    # Common arguments
    for p in [single_parser, dir_parser]:
        p.add_argument('--sample-rate', '-s', type=int, default=4,
                      help='Sample rate (default: 4 - optimal)')
        p.add_argument('--no-verify', action='store_true',
                      help='Skip verification (faster)')
        p.add_argument('--reflectivity-scale', 
                      default='base_reflectivity_intensity_scale.png')
        p.add_argument('--velocity-scale',
                      default='base_velocity_intensity_scale.png')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    # Create processor
    processor = OptimizedRadarProcessor(
        args.reflectivity_scale,
        args.velocity_scale,
        args.sample_rate
    )
    
    # Execute command
    if args.command == 'single':
        processor.process_single(
            args.image,
            args.type,
            args.output,
            verify=not args.no_verify
        )
    
    elif args.command == 'directory':
        processor.process_directory(
            args.input,
            args.output,
            args.type,
            args.pattern,
            verify=not args.no_verify
        )


if __name__ == '__main__':
    main()
