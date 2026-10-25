#!/usr/bin/env python3
"""
Stock Vector Generator & Converter (EPS 3.0 PostScript Edition)
==============================================================
Mengonversi gambar raster (JPG/PNG) menjadi kurva vektor Bezier asli format EPS
sesuai spesifikasi Adobe Stock & Vecteezy Contributor.
Dilengkapi pre-scale pintar agar proses vector tracing 5x lebih cepat dan hemat RAM.
"""

import os
import sys
from pathlib import Path
from PIL import Image

try:
    import vtracer
    import cairosvg
except ImportError:
    print("[ERROR] vtracer atau cairosvg belum terpasang.")
    sys.exit(1)


PRESETS = {
    "logo_icon": {
        "name": "Logo & Flat Icon",
        "desc": "Palet warna minimalis, garis tegas, anchor point sedikit, file EPS ringan",
        "filter_speckle": 10,
        "color_precision": 5,
        "layer_difference": 24,
        "corner_threshold": 60,
        "length_threshold": 4.0,
        "path_precision": 2,
    },
    "sticker_silhouette": {
        "name": "Sticker & Silhouette",
        "desc": "Kontur tebal, siluet rata, anti-gerigi, sangat cocok untuk stiker & badge",
        "filter_speckle": 8,
        "color_precision": 6,
        "layer_difference": 20,
        "corner_threshold": 50,
        "length_threshold": 4.0,
        "path_precision": 2,
    },
    "illustration": {
        "name": "Detailed Illustration",
        "desc": "Detail bertingkat, shading kurva halus, palet warna seimbang",
        "filter_speckle": 4,
        "color_precision": 7,
        "layer_difference": 16,
        "corner_threshold": 60,
        "length_threshold": 3.5,
        "path_precision": 2,
    },
    "complex": {
        "name": "Complex Graphic / Scenery",
        "desc": "Spektrum warna kaya untuk gambar pemandangan atau grafis multi-layer",
        "filter_speckle": 2,
        "color_precision": 8,
        "layer_difference": 10,
        "corner_threshold": 70,
        "length_threshold": 3.0,
        "path_precision": 2,
    }
}


def convert_to_eps(input_path: str, output_eps_path: str, preset="logo_icon", max_trace_dim=1600) -> dict:
    """
    Mengonversi 1 gambar raster menjadi file vektor EPS (Adobe Stock & Vecteezy standard).
    Secara otomatis mengoptimalkan input ke resolusi vektor ideal (max 1600px) agar:
    1. Tracing 5x lebih cepat (2-3 detik vs 15-20 detik).
    2. Bebas dari Linux swap thrashing (menghemat ratusan MB RAM).
    3. Ukuran EPS ideal (500KB - 1.5MB) tanpa anchor point bloating.
    """
    p_config = PRESETS.get(preset, PRESETS["logo_icon"])
    out_eps = Path(output_eps_path).with_suffix(".eps")
    out_eps.parent.mkdir(parents=True, exist_ok=True)
    temp_svg = out_eps.with_suffix(".temp.svg")
    temp_trace_img = None

    try:
        trace_source = str(input_path)

        # Pre-scaling cerdas jika gambar raksasa (misal upscaled 4K/36MP)
        with Image.open(input_path) as img:
            w, h = img.size
            if max(w, h) > max_trace_dim:
                scale_ratio = max_trace_dim / float(max(w, h))
                new_w = max(1, int(w * scale_ratio))
                new_h = max(1, int(h * scale_ratio))
                temp_trace_img = out_eps.with_suffix(".trace_tmp.png")
                # Gunakan LANCZOS untuk mempertahankan kehalusan outline
                resized_img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                resized_img.save(temp_trace_img, format="PNG")
                trace_source = str(temp_trace_img)

        # Step 1: Trace raster ke SVG kurva Bezier spline menggunakan vtracer
        vtracer.convert_image_to_svg_py(
            trace_source,
            str(temp_svg),
            colormode="color",
            hierarchical="stacked",
            mode="spline",
            filter_speckle=p_config["filter_speckle"],
            color_precision=p_config["color_precision"],
            layer_difference=p_config["layer_difference"],
            corner_threshold=p_config["corner_threshold"],
            length_threshold=p_config["length_threshold"],
            max_iterations=10,
            splice_threshold=45,
            path_precision=p_config["path_precision"]
        )

        # Step 2: Konversi SVG ke PostScript EPS level 3.0 menggunakan Cairo
        cairosvg.svg2eps(url=str(temp_svg), write_to=str(out_eps))

        file_size_mb = os.path.getsize(str(out_eps)) / (1024 * 1024)

        return {
            "success": True,
            "output_eps": str(out_eps),
            "filename": out_eps.name,
            "preset": preset,
            "preset_name": p_config["name"],
            "file_size": f"{file_size_mb:.2f} MB" if file_size_mb >= 0.01 else f"{os.path.getsize(str(out_eps))/1024:.1f} KB"
        }

    except Exception as e:
        print(f"[VECTOR ERROR] Gagal konversi {input_path} ke EPS: {e}")
        return {
            "success": False,
            "error": str(e)
        }
    finally:
        # Selalu bersihkan file sementara agar hemat disk
        if temp_svg.exists():
            try:
                temp_svg.unlink()
            except Exception:
                pass
        if temp_trace_img and temp_trace_img.exists():
            try:
                temp_trace_img.unlink()
            except Exception:
                pass


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 vector_generator.py <input_image> <output_eps> [preset]")
        print("Presets: logo_icon, sticker_silhouette, illustration, complex")
        sys.exit(1)

    in_f = sys.argv[1]
    out_f = sys.argv[2]
    pr = sys.argv[3] if len(sys.argv) > 3 else "logo_icon"
    result = convert_to_eps(in_f, out_f, preset=pr)
    print(f"Result: {result}")
