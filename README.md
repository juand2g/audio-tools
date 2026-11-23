# Audio Tools

Herramientas de audio para análisis, remastering y mastering de DJ — **parte del [Diegod Sound Engine](https://juand2g.entrelaniebla.org)**.

Pipeline de audio en Python para llevar tracks a estándar de club: cadena de
mastering profesional, remastering nativo y análisis técnico. Optimizado para
música electrónica y pistas generadas.

## Qué incluye

| Herramienta | Qué hace | Stack |
|---|---|---|
| `dj_master.py` | Mastering para DJ (cadena profesional) | FFmpeg |
| `remaster.py` | Remastering general (nativo) | Python + scipy/numpy |
| `analyze.py` | Análisis técnico de audio | Python |

## `dj_master.py` — Mastering para DJ

Cadena de mastering profesional vía FFmpeg, optimizada para pistas electrónicas:
HPF → EQ → Compresión → Estéreo → Loudnorm (2 pasadas EBU R128) → Limiter.

**Salida:** WAV 24-bit / 48 kHz — listo para Rekordbox, Serato o Traktor.

```bash
python3 dj_master.py cancion.wav                    # preset hard_techno
python3 dj_master.py cancion.wav --preset techno    # otro preset
python3 dj_master.py carpeta_suno/                  # batch (carpeta)
python3 dj_master.py cancion.wav --target-lufs -8   # loudness objetivo
python3 dj_master.py cancion.wav --analyze-only     # solo analizar
python3 dj_master.py --list-presets                 # ver presets
```

**Presets:** `hard_techno` (-9 LUFS) · `techno` (-10) · `house` (-11) · `dnb` (-9)

**Requiere:** FFmpeg

## `remaster.py` — Remastering general

Procesamiento nativo en Python: reducción de ruido, EQ, compresión multibanda y
normalización.

```bash
python3 remaster.py cancion.wav --preset loud --target-lufs -11
```

**Requiere:** `numpy`, `scipy`, `soundfile`

## `analyze.py` — Análisis de audio

Niveles, clipping, DC offset, imagen estéreo, balance de frecuencias, rango
dinámico y piso de ruido.

```bash
python3 analyze.py cancion.wav
```

## Instalación

```bash
git clone https://github.com/juand2g/audio-tools.git
cd audio-tools
sudo apt install ffmpeg            # para dj_master.py
pip install numpy scipy soundfile   # para remaster.py / analyze.py
```
