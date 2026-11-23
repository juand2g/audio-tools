#!/usr/bin/env python3
"""
process_suno.py
════════════════════════════════════════════════════════
Automatización para:
  1. Masterizar pistas de Suno con dj_master.py (preset: techno).
  2. Mover los másters de alta calidad a la carpeta cronológica limpia music/20260626/.
  3. Mover los originales de Suno a la carpeta de respaldo suno_originals/ (fuera del escaneo).
  4. Generar bitácora added_tracks.md detallando el procesamiento.
  5. Solicitar escaneo y actualización de UMAP al ID System (FastAPI).
"""

import sys
import os
import json
import shutil
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

# Añadir el directorio actual al path para importar dj_master
AUDIO_TOOLS_DIR = Path(__file__).resolve().parent
sys.path.append(str(AUDIO_TOOLS_DIR))

try:
    import dj_master
except ImportError as e:
    print(f"❌ Error al importar dj_master: {e}")
    sys.exit(1)

# Configuración de rutas
BASE_MUSIC_DIR = Path("/home/juand2g/Música")
ORIGINALS_DIR = BASE_MUSIC_DIR / "music" / "SUNO'S PRODUCTIONS"
TODAY_STR = datetime.now().strftime("%Y%m%d")  # Formato: YYYYMMDD (e.g. 20260626)
TARGET_DIR = BASE_MUSIC_DIR / "music" / TODAY_STR
BACKUP_DIR = BASE_MUSIC_DIR / "suno_originals"

TRACKS_TO_PROCESS = [
    "absorbthepressure.wav",
    "Diegod Intro6.wav",
    "Intro THX.wav",
    "Terminal agony.wav"
]

def make_request(url):
    """Realiza una petición POST simple a la API local."""
    req = urllib.request.Request(url, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode())
            return True, data
    except urllib.error.URLError as e:
        return False, str(e)
    except Exception as e:
        return False, str(e)

def main():
    print("🎬 Iniciando proceso de masterización y organización de tracks de Suno...")
    print(f"📅 Fecha de procesamiento: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📂 Carpeta destino: {TARGET_DIR}")
    print(f"📦 Respaldo originales: {BACKUP_DIR}\n")

    # Asegurar que existan los directorios
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    # Cargar el preset
    preset_name = "techno"
    if preset_name not in dj_master.PRESETS:
        print(f"❌ Preset '{preset_name}' no encontrado en dj_master.py. Usando 'hard_techno'.")
        preset_name = "hard_techno"
    
    preset = dj_master.PRESETS[preset_name].copy()
    print(f"🎛️ Preset seleccionado: {preset_name} ({preset['description']})")

    results = []

    for track_name in TRACKS_TO_PROCESS:
        original_path = ORIGINALS_DIR / track_name
        if not original_path.exists():
            print(f"⚠️ Archivo original no encontrado: {original_path}")
            continue

        # Definir ruta del masterizado en la carpeta del día
        # Mantener nombre limpio + sufijo _MASTERED.wav
        mastered_name = original_path.stem + "_MASTERED.wav"
        output_path = TARGET_DIR / mastered_name

        print(f"\n──────────────────────────────────────────────────────────")
        print(f"🎵 Procesando: {track_name}")
        
        pre_info = None
        post_info = None

        try:
            # 1. Analizar original
            pre_info, _ = dj_master.analyze_file(original_path)
            
            # 2. Masterizar directamente hacia la carpeta de destino
            dj_master.master_file(original_path, output_path, preset, analyze=True)
            
            # 3. Analizar resultado masterizado
            post_info, _ = dj_master.analyze_file(output_path)
            
            # 4. Mover el archivo original a la carpeta de respaldo (fuera del escaneo)
            backup_path = BACKUP_DIR / track_name
            shutil.move(str(original_path), str(backup_path))
            print(f"📥 Archivo original respaldado en: {backup_path.name}")
            
            status = "Completado"
        except Exception as e:
            print(f"❌ Error procesando {track_name}: {e}")
            status = f"Error: {str(e)[:50]}"

        results.append({
            "original_name": track_name,
            "mastered_name": mastered_name,
            "status": status,
            "pre_info": pre_info,
            "post_info": post_info
        })

    # 5. Generar bitácora en Markdown
    log_path = TARGET_DIR / "added_tracks.md"
    print(f"\n📝 Generando bitácora de adición en {log_path}...")
    
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"# 📅 Bitácora de Tracks Añadidos — {datetime.now().strftime('%Y-%m-%d')}\n\n")
        f.write("Sesión de masterización y organización cronológica de producciones de Suno.\n\n")
        f.write("## 🎛️ Detalles del Procesamiento\n")
        f.write(f"- **Preset de Mastering**: `{preset_name}` ({preset['description']})\n")
        f.write(f"- **Carpeta de Másters**: `music/{TODAY_STR}/` (lista para Rekordbox)\n")
        f.write(f"- **Carpeta de Respaldos**: `suno_originals/` (originales sin masterizar)\n\n")
        
        f.write("## 📊 Tabla Comparativa de Audio\n\n")
        f.write("| Track Original | Track Masterizado | Sonoridad Inicial (LUFS) | Sonoridad Final (LUFS) | Dinámica (LRA Inicial → Final) | True Peak Final (dBTP) | Estado |\n")
        f.write("| :--- | :--- | :---: | :---: | :---: | :---: | :---: |\n")
        
        for r in results:
            orig = r["original_name"]
            mast = r["mastered_name"]
            stat = r["status"]
            
            if r["pre_info"] and r["post_info"]:
                lufs_pre = f"{r['pre_info']['lufs']:.1f}"
                lufs_post = f"{r['post_info']['lufs']:.1f}"
                lra_pre = f"{r['pre_info']['lra']:.1f}"
                lra_post = f"{r['post_info']['lra']:.1f}"
                tp_post = f"{r['post_info']['tp']:.1f}"
                lra_str = f"{lra_pre} → {lra_post} LU"
            else:
                lufs_pre = lufs_post = lra_str = tp_post = "N/A"
                
            f.write(f"| {orig} | {mast} | {lufs_pre} | {lufs_post} | {lra_str} | {tp_post} | {stat} |\n")
            
        f.write("\n---\n")
        f.write("*Generado automáticamente por el IAMDIEGOD Engine.*\n")

    # 6. Sincronizar con el Cerebro (ID System API)
    print("\n📡 Sincronizando con el Cerebro (ID System)...")
    
    # Trigger Escaneo
    scan_ok, scan_res = make_request("http://localhost:8000/scan")
    if scan_ok:
        print("  ✅ Escaneo iniciado en el backend de ID System.")
    else:
        print(f"  ⚠️ No se pudo disparar el escaneo: {scan_res}")

    # Trigger UMAP (Mapa 3D)
    map_ok, map_res = make_request("http://localhost:8000/map/generate")
    if map_ok:
        print("  ✅ Generación de mapa UMAP iniciada en el backend.")
    else:
        print(f"  ⚠️ No se pudo disparar la generación del mapa: {map_res}")

    print("\n🏁 Proceso finalizado exitosamente.\n")

if __name__ == "__main__":
    main()
