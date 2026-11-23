#!/usr/bin/env python3
"""Audio Analysis Tool - Analyzes WAV files for mastering issues."""

import argparse
import sys
import numpy as np
import soundfile as sf
from scipy import signal
from pathlib import Path


def rms(data):
    return np.sqrt(np.mean(data ** 2))


def db(value, ref=1.0):
    return 20 * np.log10(max(value, 1e-10) / ref)


def analyze(filepath):
    print(f"\n{'='*60}")
    print(f"  AUDIO ANALYSIS REPORT")
    print(f"{'='*60}")
    print(f"  File: {Path(filepath).name}")

    data, sr = sf.read(filepath)
    if data.ndim == 1:
        data = np.column_stack([data, data])

    left, right = data[:, 0], data[:, 1]
    duration = len(data) / sr
    print(f"  Duration: {duration:.1f}s | Sample Rate: {sr}Hz | Channels: {data.shape[1]}")

    # --- LEVEL ANALYSIS ---
    print(f"\n{'─'*60}")
    print("  📊 LEVEL ANALYSIS")
    peak_l, peak_r = np.max(np.abs(left)), np.max(np.abs(right))
    peak_db = db(max(peak_l, peak_r))
    rms_l, rms_r = rms(left), rms(right)
    rms_db_val = db((rms_l + rms_r) / 2)
    crest = peak_db - rms_db_val

    print(f"  Peak L/R:     {db(peak_l):.1f} / {db(peak_r):.1f} dBFS")
    print(f"  RMS L/R:      {db(rms_l):.1f} / {db(rms_r):.1f} dBFS")
    print(f"  Crest Factor: {crest:.1f} dB", end="")
    if crest < 6:
        print(" ⚠️  (over-compressed, low dynamics)")
    elif crest > 20:
        print(" ⚠️  (very dynamic, may need compression)")
    else:
        print(" ✅")

    # --- CLIPPING ---
    clip_threshold = 0.99
    clips = np.sum(np.abs(data) >= clip_threshold)
    clip_pct = clips / data.size * 100
    print(f"  Clipping:     {clips} samples ({clip_pct:.3f}%)", end="")
    print(" ⚠️  CLIPPING DETECTED" if clips > 10 else " ✅")

    # --- DC OFFSET ---
    dc_l, dc_r = np.mean(left), np.mean(right)
    print(f"  DC Offset:    L={dc_l:.6f} R={dc_r:.6f}", end="")
    print(" ⚠️" if abs(dc_l) > 0.001 or abs(dc_r) > 0.001 else " ✅")

    # --- STEREO ANALYSIS ---
    print(f"\n{'─'*60}")
    print("  🎧 STEREO IMAGE")
    correlation = np.corrcoef(left, right)[0, 1]
    balance = db(rms_l) - db(rms_r)
    print(f"  Correlation:  {correlation:.3f}", end="")
    if correlation > 0.95:
        print(" (nearly mono)")
    elif correlation < 0.3:
        print(" ⚠️  (very wide, phase issues possible)")
    else:
        print(" ✅")
    print(f"  Balance:      {balance:+.1f} dB", end="")
    print(" ⚠️" if abs(balance) > 1.5 else " ✅")

    # --- FREQUENCY ANALYSIS ---
    print(f"\n{'─'*60}")
    print("  🔊 FREQUENCY BALANCE")
    mono = (left + right) / 2
    freqs, psd = signal.welch(mono, sr, nperseg=min(8192, len(mono)))

    bands = {
        "Sub (<60Hz)":    (20, 60),
        "Bass (60-250)":  (60, 250),
        "Low-Mid (250-2k)": (250, 2000),
        "Hi-Mid (2k-6k)": (2000, 6000),
        "Presence (6-12k)": (6000, 12000),
        "Air (12k+)":     (12000, sr // 2),
    }

    band_energy = {}
    total = np.sum(psd)
    for name, (lo, hi) in bands.items():
        mask = (freqs >= lo) & (freqs < hi)
        energy = np.sum(psd[mask]) / total * 100
        band_energy[name] = energy
        bar = "█" * int(energy * 2) + "░" * max(0, 30 - int(energy * 2))
        print(f"  {name:>20s}: {bar} {energy:.1f}%")

    # --- DYNAMIC RANGE (LUFS approximation) ---
    print(f"\n{'─'*60}")
    print("  📈 DYNAMICS")
    block_size = int(0.4 * sr)  # 400ms blocks
    blocks = len(mono) // block_size
    block_rms = []
    for i in range(blocks):
        chunk = mono[i * block_size:(i + 1) * block_size]
        block_rms.append(rms(chunk))
    block_rms = np.array(block_rms)
    block_db = np.array([db(r) for r in block_rms])

    loudest = np.percentile(block_db, 95)
    quietest = np.percentile(block_db, 10)
    dynamic_range = loudest - quietest
    print(f"  Loudest 5%:   {loudest:.1f} dBFS")
    print(f"  Quietest 10%: {quietest:.1f} dBFS")
    print(f"  Dynamic Range: {dynamic_range:.1f} dB", end="")
    if dynamic_range < 4:
        print(" ⚠️  (brick-walled)")
    elif dynamic_range > 20:
        print(" ⚠️  (may need leveling)")
    else:
        print(" ✅")

    # --- NOISE FLOOR ---
    sorted_rms = np.sort(block_db)
    noise_floor = np.mean(sorted_rms[:max(1, len(sorted_rms) // 20)])
    print(f"  Noise Floor:  {noise_floor:.1f} dBFS")

    # --- SUMMARY ---
    print(f"\n{'='*60}")
    print("  📋 ISSUES SUMMARY")
    issues = []
    if clips > 10:
        issues.append("🔴 Clipping detected - needs limiting/headroom")
    if abs(dc_l) > 0.001 or abs(dc_r) > 0.001:
        issues.append("🟡 DC offset present - needs HP filter")
    if crest < 6:
        issues.append("🟡 Over-compressed - limited headroom for mastering")
    if abs(balance) > 1.5:
        issues.append("🟡 Stereo imbalance detected")
    if correlation > 0.95:
        issues.append("🟢 Nearly mono - consider stereo widening")
    if band_energy.get("Sub (<60Hz)", 0) > 15:
        issues.append("🟡 Excessive sub-bass energy")
    if band_energy.get("Air (12k+)", 0) < 3:
        issues.append("🟡 Lacking high-frequency air/brightness")
    if noise_floor > -40:
        issues.append("🟡 High noise floor")

    if not issues:
        print("  ✅ No major issues detected!")
    for issue in issues:
        print(f"  {issue}")

    print(f"{'='*60}\n")
    return {
        "peak_db": peak_db, "rms_db": rms_db_val, "crest": crest,
        "clips": clips, "dc_l": dc_l, "dc_r": dc_r,
        "correlation": correlation, "balance": balance,
        "dynamic_range": dynamic_range, "noise_floor": noise_floor,
        "band_energy": band_energy, "sr": sr,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyze audio file for mastering")
    parser.add_argument("input", help="Path to WAV file")
    args = parser.parse_args()

    if not Path(args.input).exists():
        print(f"Error: File not found: {args.input}")
        sys.exit(1)

    analyze(args.input)
