#!/usr/bin/env python3
"""
Audio Remastering Tool - Clean, balance, and master WAV files.

Usage:
    python remaster.py input.wav                     # Auto output name
    python remaster.py input.wav -o output.wav       # Custom output
    python remaster.py input.wav --target-lufs -14   # Custom loudness
    python remaster.py input.wav --no-noise          # Skip noise reduction
    python remaster.py input.wav --preset loud       # Preset: loud/balanced/dynamic
"""

import argparse
import sys
import numpy as np
import soundfile as sf
from scipy import signal
from scipy.signal import sosfilt, butter, sosfiltfilt
from pathlib import Path

try:
    import noisereduce as nr
    HAS_NR = True
except ImportError:
    HAS_NR = False


# ─── UTILITIES ───────────────────────────────────────────────

def rms(data):
    return np.sqrt(np.mean(data ** 2))


def db_to_linear(db_val):
    return 10 ** (db_val / 20.0)


def linear_to_db(val):
    return 20 * np.log10(max(val, 1e-10))


def progress(msg, end="\n"):
    print(f"  ⚙️  {msg}", end=end, flush=True)


# ─── PROCESSING STAGES ──────────────────────────────────────

def remove_dc_offset(data):
    """Remove DC offset from audio."""
    return data - np.mean(data, axis=0)


def highpass_filter(data, sr, cutoff=30):
    """Remove sub-bass rumble below cutoff Hz."""
    sos = butter(4, cutoff, btype='highpass', fs=sr, output='sos')
    return sosfiltfilt(sos, data, axis=0)


def reduce_noise(data, sr, strength=0.7):
    """Adaptive noise reduction using spectral gating."""
    if not HAS_NR:
        print("  ⚠️  noisereduce not available, skipping")
        return data

    result = np.zeros_like(data)
    for ch in range(data.shape[1]):
        result[:, ch] = nr.reduce_noise(
            y=data[:, ch],
            sr=sr,
            prop_decrease=strength,
            stationary=False,
            n_std_thresh_stationary=1.5,
        )
    return result


def apply_eq(data, sr, profile="balanced"):
    """Parametric EQ for tonal balance correction."""
    profiles = {
        "balanced": [
            # (freq, gain_db, Q) - corrections for typical AI-generated audio
            (30, -4.0, 0.5),     # cut sub rumble aggressively
            (50, -3.0, 0.7),     # tame sub-bass bloom
            (80, 0.5, 0.8),      # keep bass body
            (200, -2.5, 1.0),    # reduce muddiness
            (400, -1.0, 1.2),    # reduce boxiness
            (2500, 2.0, 1.0),    # presence/vocal clarity
            (5000, 2.5, 0.8),    # definition/bite
            (8000, 2.0, 0.7),    # air/brilliance
            (12000, 1.5, 0.6),   # sparkle
            (16000, 1.0, 0.5),   # ultra-high shimmer
        ],
        "loud": [
            (60, 2.0, 0.7),
            (250, -2.0, 1.0),
            (2500, 2.0, 1.0),
            (5000, 1.5, 0.8),
            (10000, 1.0, 0.7),
        ],
        "dynamic": [
            (40, -1.5, 0.7),
            (100, 0.5, 0.8),
            (300, -1.0, 1.0),
            (4000, 1.0, 1.2),
            (10000, 0.8, 0.8),
        ],
    }

    bands = profiles.get(profile, profiles["balanced"])
    result = data.copy()

    for freq, gain_db, Q in bands:
        if abs(gain_db) < 0.1 or freq >= sr / 2:
            continue
        # Peaking EQ filter
        w0 = 2 * np.pi * freq / sr
        A = db_to_linear(gain_db / 2)
        alpha = np.sin(w0) / (2 * Q)

        b0 = 1 + alpha * A
        b1 = -2 * np.cos(w0)
        b2 = 1 - alpha * A
        a0 = 1 + alpha / A
        a1 = -2 * np.cos(w0)
        a2 = 1 - alpha / A

        b = np.array([b0 / a0, b1 / a0, b2 / a0])
        a = np.array([1.0, a1 / a0, a2 / a0])

        for ch in range(result.shape[1]):
            result[:, ch] = signal.lfilter(b, a, result[:, ch])

    return result


def stereo_enhance(data, width=1.15):
    """Subtle stereo widening via M/S processing."""
    if data.shape[1] < 2:
        return data

    mid = (data[:, 0] + data[:, 1]) / 2
    side = (data[:, 0] - data[:, 1]) / 2

    # Enhance sides
    side = side * width

    result = np.column_stack([mid + side, mid - side])
    return result


def multiband_compress(data, sr, preset="balanced"):
    """Simple multiband dynamics processing."""
    presets = {
        "balanced": {"threshold": -18, "ratio": 3.0, "attack_ms": 15, "release_ms": 150},
        "loud":     {"threshold": -14, "ratio": 4.0, "attack_ms": 10, "release_ms": 100},
        "dynamic":  {"threshold": -22, "ratio": 2.5, "attack_ms": 20, "release_ms": 200},
    }
    params = presets.get(preset, presets["balanced"])

    # Split into bands
    bands_def = [
        (20, 200),
        (200, 2000),
        (2000, sr // 2 - 1),
    ]

    result = np.zeros_like(data)

    for lo, hi in bands_def:
        # Bandpass filter
        sos = butter(3, [lo, min(hi, sr // 2 - 1)], btype='bandpass', fs=sr, output='sos')
        band = sosfiltfilt(sos, data, axis=0)

        # Compress each band
        band = _compress(band, sr, **params)
        result += band

    return result


def _compress(data, sr, threshold=-18, ratio=3.0, attack_ms=15, release_ms=150):
    """Single-band compressor with envelope following."""
    threshold_lin = db_to_linear(threshold)
    attack = np.exp(-1.0 / (sr * attack_ms / 1000))
    release = np.exp(-1.0 / (sr * release_ms / 1000))

    result = data.copy()
    envelope = np.zeros(len(data))

    # Compute envelope from mono sum
    mono = np.mean(np.abs(data), axis=1)

    for i in range(1, len(mono)):
        if mono[i] > envelope[i - 1]:
            envelope[i] = attack * envelope[i - 1] + (1 - attack) * mono[i]
        else:
            envelope[i] = release * envelope[i - 1] + (1 - release) * mono[i]

    # Apply gain reduction
    for i in range(len(envelope)):
        if envelope[i] > threshold_lin:
            gain_db = linear_to_db(envelope[i]) - threshold
            reduction = gain_db * (1 - 1 / ratio)
            gain = db_to_linear(-reduction)
            result[i] = data[i] * gain

    return result


def soft_clip(data, ceiling_db=-0.3):
    """Soft clipper / limiter for final stage."""
    ceiling = db_to_linear(ceiling_db)

    # Tanh soft clipping
    data = np.tanh(data / ceiling) * ceiling
    return data


def normalize_loudness(data, target_db=-14.0, ceiling_db=-0.3):
    """Normalize to target integrated loudness (RMS-based LUFS approximation)."""
    current_rms = rms(data)
    current_db = linear_to_db(current_rms)
    gain = db_to_linear(target_db - current_db)

    # Apply gain
    data = data * gain

    # Ensure we don't exceed ceiling
    peak = np.max(np.abs(data))
    ceiling = db_to_linear(ceiling_db)
    if peak > ceiling:
        data = data * (ceiling / peak)

    return data


def apply_dither(data, bits=24):
    """Add TPDF dither for bit depth reduction."""
    amplitude = 1.0 / (2 ** (bits - 1))
    dither = (np.random.random(data.shape) + np.random.random(data.shape) - 1.0) * amplitude
    return data + dither


# ─── MAIN REMASTER PIPELINE ─────────────────────────────────

def remaster(input_path, output_path, target_lufs=-14.0, preset="balanced",
             noise_reduce=True, noise_strength=0.5, stereo_width=1.15,
             eq_enabled=True, compress_enabled=True, dither=True,
             output_bits=24):
    """Full remastering pipeline."""

    print(f"\n{'='*60}")
    print(f"  🎵 AUDIO REMASTERING")
    print(f"{'='*60}")
    print(f"  Input:  {Path(input_path).name}")
    print(f"  Output: {Path(output_path).name}")
    print(f"  Preset: {preset} | Target: {target_lufs} LUFS")
    print(f"{'─'*60}")

    # Load
    progress("Loading audio...")
    data, sr = sf.read(input_path, dtype='float64')
    if data.ndim == 1:
        data = np.column_stack([data, data])
    original_peak = np.max(np.abs(data))
    original_rms = linear_to_db(rms(data))
    progress(f"Loaded: {len(data)/sr:.1f}s @ {sr}Hz, peak={linear_to_db(original_peak):.1f}dBFS, RMS={original_rms:.1f}dBFS")

    # Stage 1: DC removal + Highpass
    progress("Stage 1/7: Removing DC offset & sub-bass rumble...")
    data = remove_dc_offset(data)
    data = highpass_filter(data, sr, cutoff=25)

    # Stage 2: Noise reduction
    if noise_reduce and HAS_NR:
        progress("Stage 2/7: Noise reduction (spectral gating)...")
        data = reduce_noise(data, sr, strength=noise_strength)
    else:
        progress("Stage 2/7: Noise reduction skipped")

    # Stage 3: EQ
    if eq_enabled:
        progress(f"Stage 3/7: Applying EQ ({preset} profile)...")
        data = apply_eq(data, sr, profile=preset)
    else:
        progress("Stage 3/7: EQ skipped")

    # Stage 4: Stereo enhancement
    if data.shape[1] >= 2 and stereo_width != 1.0:
        progress(f"Stage 4/7: Stereo enhancement (width={stereo_width:.2f})...")
        data = stereo_enhance(data, width=stereo_width)
    else:
        progress("Stage 4/7: Stereo enhancement skipped")

    # Stage 5: Multiband compression
    if compress_enabled:
        progress(f"Stage 5/7: Multiband compression ({preset})...")
        data = multiband_compress(data, sr, preset=preset)
    else:
        progress("Stage 5/7: Compression skipped")

    # Stage 6: Loudness normalization + limiting
    progress(f"Stage 6/7: Loudness normalization (target={target_lufs} LUFS)...")
    data = normalize_loudness(data, target_db=target_lufs, ceiling_db=-0.3)
    data = soft_clip(data, ceiling_db=-0.3)

    # Stage 7: Dither
    if dither:
        progress(f"Stage 7/7: Applying {output_bits}-bit dither...")
        data = apply_dither(data, bits=output_bits)
    else:
        progress("Stage 7/7: Dither skipped")

    # Final stats
    final_peak = np.max(np.abs(data))
    final_rms = linear_to_db(rms(data))
    progress(f"Final: peak={linear_to_db(final_peak):.1f}dBFS, RMS={final_rms:.1f}dBFS")

    # Write output
    subtype_map = {16: 'PCM_16', 24: 'PCM_24', 32: 'FLOAT'}
    subtype = subtype_map.get(output_bits, 'PCM_24')

    progress(f"Writing {output_bits}-bit WAV...")
    sf.write(output_path, data, sr, subtype=subtype)

    file_size = Path(output_path).stat().st_size / (1024 * 1024)

    print(f"\n{'─'*60}")
    print(f"  ✅ REMASTER COMPLETE")
    print(f"  📊 RMS: {original_rms:.1f} → {final_rms:.1f} dBFS ({final_rms - original_rms:+.1f})")
    print(f"  📊 Peak: {linear_to_db(original_peak):.1f} → {linear_to_db(final_peak):.1f} dBFS")
    print(f"  📁 Output: {output_path} ({file_size:.1f} MB)")
    print(f"{'='*60}\n")

    return output_path


# ─── CLI ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="🎵 Audio Remastering Tool - Clean, balance, and master WAV files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Presets:
  balanced  - Natural, clean master (default). Good for most music.
  loud      - Louder, more aggressive. Good for electronic/pop.
  dynamic   - Preserves dynamics. Good for acoustic/classical.

Examples:
  %(prog)s song.wav
  %(prog)s song.wav --preset loud --target-lufs -11
  %(prog)s song.wav -o remastered.wav --no-noise --stereo-width 1.3
        """
    )

    parser.add_argument("input", help="Input WAV file path")
    parser.add_argument("-o", "--output", help="Output WAV file path (default: auto-generated)")
    parser.add_argument("--preset", choices=["balanced", "loud", "dynamic"],
                        default="balanced", help="Mastering preset (default: balanced)")
    parser.add_argument("--target-lufs", type=float, default=-14.0,
                        help="Target loudness in LUFS (default: -14)")
    parser.add_argument("--no-noise", action="store_true",
                        help="Skip noise reduction")
    parser.add_argument("--noise-strength", type=float, default=0.5,
                        help="Noise reduction strength 0.0-1.0 (default: 0.5)")
    parser.add_argument("--stereo-width", type=float, default=1.15,
                        help="Stereo width multiplier (default: 1.15, 1.0=no change)")
    parser.add_argument("--no-eq", action="store_true", help="Skip EQ")
    parser.add_argument("--no-compress", action="store_true", help="Skip compression")
    parser.add_argument("--no-dither", action="store_true", help="Skip dither")
    parser.add_argument("--bits", type=int, choices=[16, 24, 32], default=24,
                        help="Output bit depth (default: 24)")
    parser.add_argument("--analyze-only", action="store_true",
                        help="Only analyze, don't remaster")

    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"❌ Error: File not found: {args.input}")
        sys.exit(1)

    if args.analyze_only:
        from analyze import analyze
        analyze(str(input_path))
        sys.exit(0)

    # Auto-generate output name
    if args.output:
        output_path = args.output
    else:
        import re
        stem = input_path.stem
        # Remove existing remaster/copy tags
        stem = re.sub(r'\s*\(Remastered\)', '', stem, flags=re.IGNORECASE)
        stem = re.sub(r'\s*_remastered', '', stem, flags=re.IGNORECASE)
        stem = re.sub(r'\s*\(\d+\)', '', stem)  # remove (1), (2) etc
        stem = stem.strip()
        output_path = str(input_path.parent / f"{stem} (Remastered).wav")

    remaster(
        input_path=str(input_path),
        output_path=output_path,
        target_lufs=args.target_lufs,
        preset=args.preset,
        noise_reduce=not args.no_noise,
        noise_strength=args.noise_strength,
        stereo_width=args.stereo_width,
        eq_enabled=not args.no_eq,
        compress_enabled=not args.no_compress,
        dither=not args.no_dither,
        output_bits=args.bits,
    )


if __name__ == "__main__":
    main()
