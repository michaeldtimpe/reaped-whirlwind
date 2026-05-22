#!/usr/bin/env python3
"""
Masked Verifier - Ignores non-weather pixels during verification.
Only compares actual radar data, skipping map backgrounds and UI elements.
"""

import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))

from radar_tools import RadarImageVerifier, load_json_data


class MaskedRadarVerifier(RadarImageVerifier):
    """Enhanced verifier that ignores non-weather pixels."""

    def create_weather_mask(self, img_array: np.ndarray, radar_type: str) -> np.ndarray:
        """
        Create a boolean mask identifying weather data pixels.

        Args:
            img_array: RGB image array
            radar_type: 'reflectivity' or 'velocity'

        Returns:
            Boolean array: True = weather data, False = ignore
        """
        height, width = img_array.shape[:2]
        mask = np.ones((height, width), dtype=bool)

        # Extract RGB channels
        r = img_array[:, :, 0].astype(float)
        g = img_array[:, :, 1].astype(float)
        b = img_array[:, :, 2].astype(float)

        # Strategy: Only mask OBVIOUS non-weather elements
        # Be very conservative - when in doubt, include it

        # Mask 1: Pure white or very light (likely no-data or padding)
        # Only exclude if ALL channels are very high
        very_light = (r >= 250) & (g >= 250) & (b >= 245)
        mask &= ~very_light

        # Mask 2: Very dark (text, borders, UI elements)
        # Only exclude if ALL channels are very low
        very_dark = (r <= 40) & (g <= 40) & (b <= 40)
        mask &= ~very_dark

        # That's it! Don't mask "background" colors since they overlap with valid weather
        # The beige/cream background is too similar to some light precipitation colors

        return mask

    def verify_conversion_masked(
        self,
        original_image_path: str,
        data_json_path: str,
        output_dir: str,
        show_difference: bool = True,
    ) -> dict:
        """
        Verify conversion ignoring non-weather pixels.

        Args:
            original_image_path: Path to original radar image
            data_json_path: Path to JSON data file
            output_dir: Directory to save verification outputs
            show_difference: Whether to create a difference image

        Returns:
            Dictionary containing verification metrics
        """
        print("=" * 60)
        print("MASKED VERIFICATION PROCESS")
        print("(Ignoring pure white and very dark UI elements)")
        print("=" * 60)

        # Load the data
        data = load_json_data(data_json_path)
        metadata = data["metadata"]

        # Create output directory
        output_path = Path(output_dir)
        output_path.mkdir(exist_ok=True, parents=True)

        # Reconstruct the image
        sample_rate = metadata.get("sample_rate", 1)
        reconstructed_path = (
            output_path / f"reconstructed_{Path(original_image_path).stem}.png"
        )

        print(
            f"\nReconstructing {metadata['radar_type']} image from {metadata['sampled_dimensions']['width']}x{metadata['sampled_dimensions']['height']} data matrix..."
        )

        self.data_to_image(data, str(reconstructed_path), upscale_factor=sample_rate)

        print(
            f"Upscaled to {metadata['original_dimensions']['width']}x{metadata['original_dimensions']['height']}"
        )
        print(f"Saved reconstructed image to: {reconstructed_path}")

        # Load original and reconstructed images
        original_img = Image.open(original_image_path).convert("RGB")
        reconstructed_img = Image.open(reconstructed_path).convert("RGB")

        # Resize reconstructed to match original for comparison
        if reconstructed_img.size != original_img.size:
            reconstructed_img = reconstructed_img.resize(
                original_img.size, Image.NEAREST
            )

        # Convert to numpy arrays
        original_array = np.array(original_img)
        reconstructed_array = np.array(reconstructed_img)

        # Create weather data mask
        print("\nCreating weather data mask...")
        mask = self.create_weather_mask(original_array, metadata["radar_type"])

        masked_pixels = np.sum(mask)
        total_pixels = mask.size
        weather_percentage = masked_pixels / total_pixels * 100

        print(f"  Total pixels: {total_pixels:,}")
        print(f"  Weather pixels: {masked_pixels:,} ({weather_percentage:.1f}%)")
        print(
            f"  Ignored pixels: {total_pixels - masked_pixels:,} ({100 - weather_percentage:.1f}%)"
        )

        # Calculate pixel-wise differences (all channels)
        diff = np.abs(original_array.astype(float) - reconstructed_array.astype(float))

        # Apply mask to differences (expand mask to 3 channels)
        mask_3d = np.repeat(mask[:, :, np.newaxis], 3, axis=2)
        masked_diff = diff[mask_3d].reshape(-1)  # Only masked pixels

        # Calculate metrics on MASKED pixels only
        metrics = {
            "mean_absolute_error": float(masked_diff.mean())
            if len(masked_diff) > 0
            else 0,
            "max_error": float(masked_diff.max()) if len(masked_diff) > 0 else 0,
            "weather_pixel_count": int(masked_pixels),
            "weather_pixel_percentage": float(weather_percentage),
            "ignored_pixel_count": int(total_pixels - masked_pixels),
            "dimensions": {
                "original": original_img.size,
                "reconstructed": reconstructed_img.size,
            },
        }

        # Calculate threshold-based metrics on masked pixels
        if masked_pixels > 0:
            # Per-pixel max difference across channels
            per_pixel_diff = np.max(diff, axis=2)[mask]

            metrics["pixel_perfect_match"] = float(
                np.sum(per_pixel_diff == 0) / masked_pixels * 100
            )
            metrics["within_5_threshold"] = float(
                np.sum(per_pixel_diff <= 5) / masked_pixels * 100
            )
            metrics["within_10_threshold"] = float(
                np.sum(per_pixel_diff <= 10) / masked_pixels * 100
            )
            metrics["within_15_threshold"] = float(
                np.sum(per_pixel_diff <= 15) / masked_pixels * 100
            )
            metrics["within_20_threshold"] = float(
                np.sum(per_pixel_diff <= 20) / masked_pixels * 100
            )
        else:
            metrics["pixel_perfect_match"] = 0
            metrics["within_5_threshold"] = 0
            metrics["within_10_threshold"] = 0
            metrics["within_15_threshold"] = 0
            metrics["within_20_threshold"] = 0

        # Print metrics
        print("\n" + "=" * 60)
        print("MASKED VERIFICATION METRICS")
        print("(Excluding pure white and very dark pixels)")
        print("=" * 60)
        print(
            f"Mean Absolute Error (per channel): {metrics['mean_absolute_error']:.2f}"
        )
        print(f"Max Error: {metrics['max_error']:.2f}")
        print(f"Pixel Perfect Match: {metrics['pixel_perfect_match']:.2f}%")
        print(f"Within 5 RGB units: {metrics['within_5_threshold']:.2f}%")
        print(f"Within 10 RGB units: {metrics['within_10_threshold']:.2f}%")
        print(f"Within 15 RGB units: {metrics['within_15_threshold']:.2f}%")
        print(f"Within 20 RGB units: {metrics['within_20_threshold']:.2f}%")

        # Create difference visualization
        if show_difference:
            # Enhance difference for visibility (multiply by factor)
            diff_enhanced = np.clip(diff * 10, 0, 255).astype(np.uint8)

            # Gray out masked areas in difference map
            diff_enhanced[~mask_3d] = 128  # Gray for masked areas

            diff_img = Image.fromarray(diff_enhanced)
            diff_path = (
                output_path / f"masked_difference_{Path(original_image_path).stem}.png"
            )
            diff_img.save(diff_path)
            print(f"\nMasked difference map saved to: {diff_path}")
            print("(Gray areas = ignored pixels, colored = actual differences)")

        # Create side-by-side comparison with mask visualization
        comparison_path = (
            output_path / f"masked_comparison_{Path(original_image_path).stem}.png"
        )
        self._create_masked_comparison(
            original_img,
            reconstructed_img,
            mask,
            diff_enhanced if show_difference else None,
            str(comparison_path),
            metadata,
            metrics,
        )
        print(f"Masked comparison saved to: {comparison_path}")

        # Interpretation
        print("\n" + "=" * 60)
        print("INTERPRETATION")
        print("=" * 60)

        # Calculate a composite score based on accuracy thresholds
        # Weight different thresholds
        composite_score = (
            metrics["within_5_threshold"] * 0.4  # Very close matches
            + metrics["within_10_threshold"] * 0.3  # Close matches
            + metrics["within_20_threshold"] * 0.3  # Acceptable matches
        )

        # Adjust for sample rate (higher sample rates are expected to have lower scores)
        sample_rate = metadata.get("sample_rate", 1)
        if sample_rate > 1:
            # Add bonus for downsampled images
            adjustment_factor = 1 + (sample_rate - 1) * 0.15
            adjusted_score = min(100, composite_score * adjustment_factor)
        else:
            adjusted_score = composite_score

        # Determine grade
        if adjusted_score >= 85:
            grade = "A"
            quality = "EXCELLENT"
        elif adjusted_score >= 70:
            grade = "B"
            quality = "GOOD"
        elif adjusted_score >= 55:
            grade = "C"
            quality = "ACCEPTABLE"
        elif adjusted_score >= 40:
            grade = "D"
            quality = "FAIR"
        else:
            grade = "F"
            quality = "POOR"

        print(f"\n{'=' * 60}")
        print(f"PIXEL ACCURACY SCORE: {adjusted_score:.1f}% (Grade: {grade})")
        print(f"Quality: {quality}")
        print(f"{'=' * 60}")

        if sample_rate > 1:
            print(f"\nNote: Score adjusted for sample rate {sample_rate}")
            print(
                f"Raw score: {composite_score:.1f}% → Adjusted: {adjusted_score:.1f}%"
            )

        print("\n⚠️  IMPORTANT:")
        print("This pixel-based score measures reconstruction accuracy.")
        print("For ML training quality, use verify_ml.py instead!")
        print("ML cares about patterns, not pixel-perfect matching.")

        # Add to metrics
        metrics["composite_score"] = adjusted_score
        metrics["grade"] = grade
        metrics["quality"] = quality

        return metrics

    def _create_masked_comparison(
        self,
        original: Image.Image,
        reconstructed: Image.Image,
        mask: np.ndarray,
        difference: np.ndarray,
        output_path: str,
        metadata: dict,
        metrics: dict,
    ):
        """Create a side-by-side comparison image with mask visualization."""
        from PIL import ImageDraw, ImageFont

        # Determine layout (4 images: original, reconstructed, mask, difference)
        width, height = original.size

        # Create canvas for 4 images
        canvas_width = width * 4 + 60  # 4 images + spacing
        canvas_height = height + 80  # Extra space for labels and metrics
        canvas = Image.new("RGB", (canvas_width, canvas_height), color="white")

        # Paste original
        canvas.paste(original, (0, 40))

        # Paste reconstructed
        canvas.paste(reconstructed, (width + 20, 40))

        # Create mask visualization (white = weather, black = ignored)
        mask_vis = Image.fromarray((mask * 255).astype(np.uint8), "L").convert("RGB")
        canvas.paste(mask_vis, (width * 2 + 40, 40))

        # Paste difference if available
        if difference is not None:
            diff_img = Image.fromarray(difference)
            canvas.paste(diff_img, (width * 3 + 60, 40))

        # Add labels
        draw = ImageDraw.Draw(canvas)
        try:
            font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14
            )
            font_small = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11
            )
        except:
            font = ImageFont.load_default()
            font_small = font

        draw.text((10, 10), "Original", fill="black", font=font)
        draw.text((width + 30, 10), "Reconstructed", fill="black", font=font)
        draw.text((width * 2 + 50, 10), "Weather Mask", fill="black", font=font)
        if difference is not None:
            draw.text((width * 3 + 70, 10), "Difference (10x)", fill="black", font=font)

        # Add metadata at bottom
        radar_type = metadata.get("radar_type", "unknown")
        sample_rate = metadata.get("sample_rate", 1)
        weather_pct = metrics.get("weather_pixel_percentage", 0)
        accuracy = metrics.get("within_20_threshold", 0)

        info_text = f"{radar_type.capitalize()} | Sample: {sample_rate} | Weather: {weather_pct:.1f}% | Accuracy@20: {accuracy:.1f}%"
        draw.text((10, canvas_height - 25), info_text, fill="gray", font=font_small)

        canvas.save(output_path)


def main():
    """CLI for masked verification."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Verify radar conversions ignoring non-weather pixels"
    )
    parser.add_argument("original_image", help="Path to original radar image")
    parser.add_argument("data_json", help="Path to JSON data file")
    parser.add_argument(
        "--output-dir",
        "-o",
        default="masked_verification",
        help="Directory for verification outputs",
    )
    parser.add_argument(
        "--reflectivity-scale",
        default="base_reflectivity_intensity_scale.png",
        help="Path to reflectivity scale image",
    )
    parser.add_argument(
        "--velocity-scale",
        default="base_velocity_intensity_scale.png",
        help="Path to velocity scale image",
    )
    parser.add_argument(
        "--no-difference",
        action="store_true",
        help="Do not create difference visualization",
    )

    args = parser.parse_args()

    # Create masked verifier
    verifier = MaskedRadarVerifier(args.reflectivity_scale, args.velocity_scale)

    # Run verification
    metrics = verifier.verify_conversion_masked(
        args.original_image,
        args.data_json,
        args.output_dir,
        show_difference=not args.no_difference,
    )

    print(f"\n{'=' * 60}")
    print("VERIFICATION COMPLETE")
    print(f"{'=' * 60}")
    print(
        f"\nFinal Score: {metrics['composite_score']:.1f}% (Grade {metrics['grade']}: {metrics['quality']})"
    )
    print(
        f"\nRecommendation: {'✓ Proceed with this data' if metrics['composite_score'] >= 55 else '⚠ Review conversion settings'}"
    )
    print(f"\nFor ML-focused metrics, run:")
    print(f"  python verify_ml.py {args.original_image} {args.data_json}")


if __name__ == "__main__":
    main()
