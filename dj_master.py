#!/usr/bin/env python3
"""
dj_master.py — Mastering automático para DJ (ffmpeg)
════════════════════════════════════════════════════════

Cadena de mastering (en orden):
  1. HPF 30 Hz            — elimina sub-sónico inaudible
  2. EQ paramétrico       — moldea tono según preset de género
  3. Treble shelf          — restaura aire en agudos
  4. Compresor glue        — amarra el bus sin matar dinámica
  5. Stereo widening       — ensancha campo estéreo
  6. Loudnorm 2 pasadas    — normalización EBU R128 lineal
  7. Safety limiter        — techo -0.5 dBTP

Salida: WAV 24-bit / 48 kHz — listo para Rekordbox, Serato o Traktor

Requiere: Python 3.7+ y ffmpeg en el PATH
  → Linux:   sudo apt install ffmpeg
  → macOS:   brew install ffmpeg
  → Windows: winget install --id Gyan.FFmpeg

Uso:
  python dj_master.py cancion.wav
  python dj_master.py cancion.mp3 otra.wav
  python dj_master.py carpeta/
  python dj_master.py *.wav --preset techno --target-lufs -10
  python dj_master.py cancion.wav --analyze-only
"""

import os
import sys
import json
import subprocess
import shutil
import argparse
from pathlib import Path

# ══════════════════════════════════════════════════════════
#  PRESETS DE GÉNERO
# ══════════════════════════════════════════════════════════

PRESETS = {
    "hard_techno": {
        "description": "Hard techno / schranz — agresivo, para club (-9 LUFS)",
        "target_lufs": -9,
        "target_tp": -0.5,
        "target_lra": 8,
        "hpf_freq": 30,
        "eq_bands": [
            # (freq_hz, Q, gain_db)
            (78, 2.0, 1.2),      # refuerza fundamental del kick
            (250, 1.5, -1.0),    # limpia low-mid
            (3000, 2.0, 1.5),    # presencia / transientes
        ],
        "treble_shelf_db": 2.5,  # shelf desde 10 kHz
        "comp_threshold": 0.1,   # ~-20 dBFS (lineal)
        "comp_ratio": 2,
        "comp_attack": 50,       # ms — deja pasar el kick
        "comp_release": 200,     # ms
        "comp_knee": 4,
        "comp_makeup": 1,        # sin makeup, loudnorm maneja ganancia
        "stereo_base": 0.25,     # ensanche moderado
        "limiter_limit": 0.944,  # ~-0.5 dBFS
        "limiter_attack": 5,
        "limiter_release": 50,
    },
    "techno": {
        "description": "Techno melódico / peak time — más conservador (-10 LUFS)",
        "target_lufs": -10,
        "target_tp": -0.5,
        "target_lra": 9,
        "hpf_freq": 30,
        "eq_bands": [
            (70, 2.0, 1.0),
            (300, 1.5, -0.8),
            (3500, 2.0, 1.0),
        ],
        "treble_shelf_db": 2.0,
        "comp_threshold": 0.126,  # ~-18 dBFS
        "comp_ratio": 2,
        "comp_attack": 40,
        "comp_release": 200,
        "comp_knee": 5,
        "comp_makeup": 1,
        "stereo_base": 0.20,
        "limiter_limit": 0.944,
        "limiter_attack": 5,
        "limiter_release": 50,
    },
    "house": {
        "description": "House / tech house — groove y espacio (-11 LUFS)",
        "target_lufs": -11,
        "target_tp": -0.5,
        "target_lra": 10,
        "hpf_freq": 25,
        "eq_bands": [
            (90, 2.0, 1.5),
            (200, 1.5, -0.5),
            (4000, 2.0, 1.2),
        ],
        "treble_shelf_db": 1.5,
        "comp_threshold": 0.178,  # ~-15 dBFS
        "comp_ratio": 2,
        "comp_attack": 60,
        "comp_release": 250,
        "comp_knee": 6,
        "comp_makeup": 1,
        "stereo_base": 0.30,
        "limiter_limit": 0.944,
        "limiter_attack": 5,
        "limiter_release": 60,
    },
    "dnb": {
        "description": "Drum and Bass — transientes rápidos (-9 LUFS)",
        "target_lufs": -9,
        "target_tp": -0.5,
        "target_lra": 8,
        "hpf_freq": 30,
        "eq_bands": [
            (65, 2.0, 1.0),
            (300, 1.2, -1.2),
            (5000, 2.0, 1.8),
        ],
        "treble_shelf_db": 2.0,
        "comp_threshold": 0.126,
        "comp_ratio": 2,
        "comp_attack": 20,
        "comp_release": 150,
        "comp_knee": 4,
        "comp_makeup": 1,
        "stereo_base": 0.20,
        "limiter_limit": 0.944,
        "limiter_attack": 3,
        "limiter_release": 40,
    },
}

SUPPORTED_EXTENSIONS = {".wav", ".mp3", ".flac", ".aiff", ".aif", ".ogg"}

# ══════════════════════════════════════════════════════════
#  UTILIDADES
# ══════════════════════════════════════════════════════════

def check_ffmpeg():
    """Verifica que ffmpeg esté disponible."""
    if shutil.which("ffmpeg") is None:
        print("\n  ✗ ERROR: ffmpeg no está instalado o no está en el PATH.\n")
        print("  Cómo instalarlo:")
        print("    Linux   → sudo apt install ffmpeg")
        print("    macOS   → brew install ffmpeg")
        print("    Windows → winget install --id Gyan.FFmpeg\n")
        sys.exit(1)


def run_ffmpeg(cmd):
    """Ejecuta un comando ffmpeg y retorna stderr."""
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        # Algunos warnings no son errores fatales
        if "Output file is empty" in result.stderr:
            return result.stderr
        raise RuntimeError(f"ffmpeg error (code {result.returncode}):\n{result.stderr[-600:]}")
    return result.stderr


def build_filter_chain(preset, include_loudnorm=False, measured=None):
    """Construye la cadena de filtros ffmpeg a partir de un preset."""
    p = preset
    parts = []

    # 1. HPF
    parts.append(f"highpass=f={p['hpf_freq']}:poles=2")

    # 2. EQ paramétrico
    for freq, q, gain in p["eq_bands"]:
        parts.append(f"equalizer=f={freq}:width_type=q:width={q}:g={gain}")

    # 3. Treble shelf
    parts.append(f"treble=g={p['treble_shelf_db']}:f=10000")

    # 4. Compresor glue
    parts.append(
        f"acompressor=threshold={p['comp_threshold']}:ratio={p['comp_ratio']}"
        f":attack={p['comp_attack']}:release={p['comp_release']}"
        f":knee={p['comp_knee']}:makeup={p['comp_makeup']}"
    )

    # 5. Stereo widening
    if p["stereo_base"] != 0:
        parts.append(f"stereotools=base={p['stereo_base']}")

    # 6. Loudnorm
    if include_loudnorm and measured:
        parts.append(
            f"loudnorm=I={p['target_lufs']}:TP={p['target_tp']}:LRA={p['target_lra']}"
            f":measured_I={measured['input_i']}"
            f":measured_TP={measured['input_tp']}"
            f":measured_LRA={measured['input_lra']}"
            f":measured_thresh={measured['input_thresh']}"
            f":offset={measured['target_offset']}"
            f":linear=true"
        )
    elif include_loudnorm:
        parts.append(
            f"loudnorm=I={p['target_lufs']}:TP={p['target_tp']}:LRA={p['target_lra']}"
            f":print_format=json"
        )

    # 7. Safety limiter
    if include_loudnorm and measured:
        parts.append(
            f"alimiter=level_in=1:level_out=1"
            f":limit={p['limiter_limit']}:attack={p['limiter_attack']}"
            f":release={p['limiter_release']}:level=disabled"
        )

    return ",".join(parts)


# ══════════════════════════════════════════════════════════
#  ANÁLISIS
# ══════════════════════════════════════════════════════════

def analyze_file(input_path):
    """Analiza un archivo de audio con ffmpeg y retorna métricas."""
    # Loudness EBU R128
    cmd_loud = [
        "ffmpeg", "-hide_banner", "-i", str(input_path), "-vn",
        "-af", "loudnorm=print_format=json",
        "-f", "null", "-"
    ]
    stderr = run_ffmpeg(cmd_loud)
    start = stderr.rfind("{")
    end = stderr.rfind("}") + 1
    loudness = {}
    if start != -1:
        loudness = json.loads(stderr[start:end])

    # Peak, RMS, stereo width via astats
    cmd_stats = [
        "ffmpeg", "-hide_banner", "-i", str(input_path), "-vn",
        "-af", "astats=metadata=1:measure_overall=Peak_level+RMS_level:measure_perchannel=Peak_level+RMS_level",
        "-f", "null", "-"
    ]
    stderr_stats = run_ffmpeg(cmd_stats)

    # Parse basic info
    info = {
        "lufs": float(loudness.get("input_i", -99)),
        "tp": float(loudness.get("input_tp", -99)),
        "lra": float(loudness.get("input_lra", 0)),
    }

    # Extraer peak y RMS de la salida de astats
    for line in stderr_stats.split("\n"):
        line_lower = line.strip().lower()
        if "overall" in line_lower or "lavfi" in line_lower:
            if "peak_level" in line_lower or "rms_level" in line_lower:
                pass  # astats output varies, loudnorm values are more reliable

    # Stereo width (mid/side ratio)
    cmd_stereo = [
        "ffmpeg", "-hide_banner", "-i", str(input_path), "-vn",
        "-af", "stereotools=mode=ms2lr",
        "-f", "null", "-"
    ]
    # Simplified: use loudnorm values which are more reliable
    info["peak_dbfs"] = info["tp"]

    return info, loudness


def print_analysis(label, info):
    """Imprime métricas de un archivo."""
    print(f"  │  {label}:")
    print(f"  │    LUFS integrado: {info['lufs']:.1f}")
    print(f"  │    True Peak:      {info['tp']:.1f} dBTP")
    print(f"  │    LRA:            {info['lra']:.1f} LU")


# ══════════════════════════════════════════════════════════
#  MASTERING
# ══════════════════════════════════════════════════════════

def measure_loudness(input_path, preset):
    """Pasada 1: mide loudness EBU R128 después de EQ+comp."""
    chain = build_filter_chain(preset, include_loudnorm=True, measured=None)
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-i", str(input_path), "-vn",
        "-af", chain,
        "-f", "null", "-"
    ]
    stderr = run_ffmpeg(cmd)

    start = stderr.rfind("{")
    end = stderr.rfind("}") + 1
    if start == -1:
        raise RuntimeError("No se pudo leer el loudness. ¿El archivo de audio es válido?")
    return json.loads(stderr[start:end])


def master_file(input_path, output_path, preset, analyze=True):
    """Aplica la cadena completa de mastering a un archivo."""
    name = input_path.name
    p = preset
    print()
    print(f"  ┌─ {name}")

    # ── Análisis pre-master ──────────────────────────────
    if analyze:
        print("  │  [0/3] Analizando original...", end=" ", flush=True)
        try:
            pre_info, _ = analyze_file(input_path)
            print("OK")
            print_analysis("ANTES", pre_info)
        except Exception:
            print("SKIP (no se pudo analizar)")
            pre_info = None

    # ── Pasada 1: medir ──────────────────────────────────
    print("  │  [1/3] Midiendo loudness...", end=" ", flush=True)
    try:
        measured = measure_loudness(input_path, p)
    except Exception as e:
        print(f"ERROR\n  │       {e}")
        raise

    i_lufs = float(measured["input_i"])
    i_tp = float(measured["input_tp"])
    i_lra = float(measured["input_lra"])
    print(f"OK  ({i_lufs:.1f} LUFS | peak {i_tp:.1f} dBTP | LRA {i_lra:.1f} LU)")

    # ── Pasada 2: aplicar ────────────────────────────────
    print("  │  [2/3] Masterizando...", end=" ", flush=True)

    full_chain = build_filter_chain(p, include_loudnorm=True, measured=measured)

    cmd = [
        "ffmpeg", "-y", "-hide_banner",
        "-i", str(input_path),
        "-vn",                     # ignorar streams de video (carátulas)
        "-af", full_chain,
        "-c:a", "pcm_s24le",       # 24-bit PCM
        "-ar", "48000",            # 48 kHz
        "-map_metadata", "0",      # preservar metadata del original
        str(output_path)
    ]

    try:
        run_ffmpeg(cmd)
    except Exception as e:
        print(f"ERROR\n  │       {e}")
        raise

    # ── Verificar output ─────────────────────────────────
    if not output_path.exists() or output_path.stat().st_size < 1000:
        raise RuntimeError(f"El archivo de salida no se generó correctamente: {output_path}")

    size_mb = output_path.stat().st_size / (1024 * 1024)
    print("OK")

    # ── Análisis post-master ─────────────────────────────
    if analyze:
        print("  │  [3/3] Verificando resultado...", end=" ", flush=True)
        try:
            post_info, _ = analyze_file(output_path)
            print("OK")
            print_analysis("DESPUÉS", post_info)
            # Comparación
            if pre_info:
                print("  │")
                print(f"  │  📊 Diferencia:")
                print(f"  │    LUFS: {pre_info['lufs']:.1f} → {post_info['lufs']:.1f} ({post_info['lufs'] - pre_info['lufs']:+.1f})")
                print(f"  │    Peak: {pre_info['tp']:.1f} → {post_info['tp']:.1f} dBTP")
        except Exception:
            print("SKIP")

    print(f"  │")
    print(f"  │  ✓ Guardado: {output_path.name}")
    print(f"  │    Formato:  24-bit WAV · 48 kHz · stereo")
    print(f"  │    Tamaño:   {size_mb:.1f} MB")
    print(f"  └─ Target: {p['target_lufs']} LUFS · {p['target_tp']} dBTP")


# ══════════════════════════════════════════════════════════
#  UTILIDADES DE ARCHIVOS
# ══════════════════════════════════════════════════════════

def collect_inputs(paths):
    """Recolecta archivos de audio de la lista (archivos o carpetas)."""
    inputs = []
    for arg in paths:
        p = Path(arg)
        if p.is_dir():
            for ext in SUPPORTED_EXTENSIONS:
                inputs.extend(sorted(p.glob(f"*{ext}")))
                inputs.extend(sorted(p.glob(f"*{ext.upper()}")))
        elif p.is_file():
            if p.suffix.lower() in SUPPORTED_EXTENSIONS:
                inputs.append(p)
            else:
                print(f"  ! Ignorado (extensión no soportada): {p.name}")
        else:
            print(f"  ! No encontrado: {arg}")
    # Eliminar duplicados
    seen = set()
    unique = []
    for p in inputs:
        rp = str(p.resolve())
        if rp not in seen:
            seen.add(rp)
            unique.append(p)
    return unique


def make_output_path(input_path, suffix="_MASTERED"):
    """Genera el path de salida: mismo directorio, nombre + sufijo .wav"""
    return input_path.parent / (input_path.stem + suffix + ".wav")


# ══════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════

def build_parser():
    parser = argparse.ArgumentParser(
        description="🎧 DJ Master — Mastering automático para DJ vía ffmpeg",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Presets disponibles:
  hard_techno  -9 LUFS — Hard techno / schranz, agresivo para club (default)
  techno      -10 LUFS — Techno melódico / peak time
  house       -11 LUFS — House / tech house, groove y espacio
  dnb          -9 LUFS — Drum and Bass, transientes rápidos

Ejemplos:
  %(prog)s cancion.wav
  %(prog)s cancion.mp3 otra.wav --preset techno
  %(prog)s carpeta_suno/ --target-lufs -8
  %(prog)s cancion.wav --analyze-only
  %(prog)s cancion.wav --no-analyze --stereo-base 0.4
        """
    )
    parser.add_argument("inputs", nargs="*", help="Archivos o carpetas a procesar")
    parser.add_argument("-p", "--preset", choices=list(PRESETS.keys()),
                        default="hard_techno", help="Preset de género (default: hard_techno)")
    parser.add_argument("--target-lufs", type=float, default=None,
                        help="Override del target LUFS (ej: -9, -10, -11)")
    parser.add_argument("--target-tp", type=float, default=None,
                        help="Override del true peak máximo en dBTP (default: -0.5)")
    parser.add_argument("--stereo-base", type=float, default=None,
                        help="Override del ensanche estéreo (0.0-1.0, default: depende del preset)")
    parser.add_argument("--suffix", type=str, default="_MASTERED",
                        help="Sufijo para archivos de salida (default: _MASTERED)")
    parser.add_argument("--no-analyze", action="store_true",
                        help="Saltar análisis antes/después (más rápido)")
    parser.add_argument("--analyze-only", action="store_true",
                        help="Solo analizar, no masterizar")
    parser.add_argument("--list-presets", action="store_true",
                        help="Mostrar detalles de todos los presets y salir")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    print()
    print("╔══════════════════════════════════════════════════╗")
    print("║         dj_master.py  ·  Mastering para DJ       ║")
    print("║   Cadena: EQ → Comp → Stereo → Loudnorm → WAV   ║")
    print("╚══════════════════════════════════════════════════╝")

    # Listar presets
    if args.list_presets:
        print()
        for name, p in PRESETS.items():
            print(f"  {name:15s}  {p['description']}")
            print(f"                   EQ: {', '.join(f'{f}Hz {g:+.1f}dB' for f, _, g in p['eq_bands'])}")
            print(f"                   Shelf: +{p['treble_shelf_db']}dB@10kHz | Stereo: +{p['stereo_base']}")
            print()
        sys.exit(0)

    check_ffmpeg()

    # Validar que hay archivos de entrada
    if not args.inputs:
        parser.error("se requiere al menos un archivo o carpeta de entrada")

    # Preparar preset con overrides
    preset = PRESETS[args.preset].copy()
    if args.target_lufs is not None:
        preset["target_lufs"] = args.target_lufs
    if args.target_tp is not None:
        preset["target_tp"] = args.target_tp
    if args.stereo_base is not None:
        preset["stereo_base"] = args.stereo_base

    inputs = collect_inputs(args.inputs)
    if not inputs:
        print(f"\n  No se encontraron archivos de audio compatibles.")
        print(f"  Formatos soportados: {', '.join(sorted(SUPPORTED_EXTENSIONS))}\n")
        sys.exit(1)

    print()
    print(f"  {len(inputs)} archivo(s) a procesar")
    print(f"  Preset: {args.preset} | Target: {preset['target_lufs']} LUFS · {preset['target_tp']} dBTP")
    print(f"  Stereo base: {preset['stereo_base']} | Treble shelf: +{preset['treble_shelf_db']}dB")

    # Solo analizar
    if args.analyze_only:
        for input_path in inputs:
            print(f"\n  ── {input_path.name} ──")
            try:
                info, raw = analyze_file(input_path)
                print_analysis("Análisis", info)
            except Exception as e:
                print(f"  ✗ Error: {e}")
        print()
        sys.exit(0)

    # Masterizar
    ok_count = 0
    error_files = []

    for input_path in inputs:
        output_path = make_output_path(input_path, suffix=args.suffix)
        try:
            master_file(input_path, output_path, preset, analyze=not args.no_analyze)
            ok_count += 1
        except Exception as e:
            error_files.append((input_path.name, str(e)))

    print()
    print(f"  ══ Resultado: {ok_count}/{len(inputs)} OK ", end="")
    if error_files:
        print(f"· {len(error_files)} error(es) ══")
        for name, err in error_files:
            print(f"    ✗ {name}: {err[:120]}")
    else:
        print("══")
    print()


if __name__ == "__main__":
    main()
