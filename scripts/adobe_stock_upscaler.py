#!/usr/bin/env python3
"""
Adobe Stock Image Upscaler & Quality Enhancer (Studio Edge & Anti-Aliasing Edition)
===================================================================================
Dirancang khusus untuk memperbaiki dan mencegah penolakan (rejection)
Adobe Stock Contributor berdasarkan pedoman:
"Quality Issues & Technical Standards"
(https://helpx.adobe.com/stock/contributor/content-moderation/quality-technical-standards-reasons-content-refusal.html)
"""

import os
import sys
import argparse
import math
from pathlib import Path

# Dependency Check
try:
    from PIL import Image, ImageFilter, ImageOps, ImageEnhance, ImageChops
    Image.MAX_IMAGE_PIXELS = 150_000_000
except ImportError:
    print("[ERROR] Pillow belum terpasang. Jalankan: pip install Pillow numpy")
    sys.exit(1)


class AdobeStockEnhancer:
    def __init__(self, scale=2.0, target_mp=16.0, denoise="medium", sharpen="medium", edge_smooth="medium", engine="classic"):
        self.scale = float(scale)
        self.target_mp = float(target_mp)
        self.denoise_level = denoise
        self.sharpen_level = sharpen
        self.edge_smooth_level = edge_smooth
        self.engine = engine

    def reduce_chroma_noise(self, img: Image.Image) -> Image.Image:
        """
        Denoise murni pada kanal Chroma (Cb & Cr pada YCbCr) tanpa merusak ketajaman detail
        maupun merusak kehalusan anti-aliasing pinggiran objek (anti-gerigi).
        """
        if self.denoise_level == "none":
            return img

        radius = {"low": 0.6, "medium": 1.0, "strong": 1.5}.get(self.denoise_level, 1.0)

        has_alpha = ("A" in img.mode or img.mode in ("RGBA", "LA"))
        if has_alpha:
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            r, g, b, a = img.split()
            rgb = Image.merge("RGB", (r, g, b))
        else:
            rgb = img.convert("RGB")
            a = None

        # Filter hanya kanal Cb & Cr (warna splotchy / chroma noise)
        ycbcr = rgb.convert("YCbCr")
        y, cb, cr = ycbcr.split()
        cb = cb.filter(ImageFilter.GaussianBlur(radius=radius))
        cr = cr.filter(ImageFilter.GaussianBlur(radius=radius))
        rgb_clean = Image.merge("YCbCr", (y, cb, cr)).convert("RGB")

        if a is not None:
            r2, g2, b2 = rgb_clean.split()
            return Image.merge("RGBA", (r2, g2, b2, a))
        return rgb_clean

    def calculate_target_size(self, width: int, height: int) -> tuple[int, int]:
        """
        Menghitung resolusi akhir:
        - Minimal 4.2 MP (agar tidak kena auto-reject < 4 MP)
        - Batas aman memori & Adobe Stock: Capped di maks 36 MP (~6500px)
        """
        current_mp = (width * height) / 1_000_000.0

        if self.scale > 1.0:
            target_w = int(width * self.scale)
            target_h = int(height * self.scale)
        else:
            ratio = math.sqrt(self.target_mp / max(current_mp, 0.1))
            target_w = int(width * ratio)
            target_h = int(height * ratio)

        target_mp = (target_w * target_h) / 1_000_000.0

        if target_mp < 4.2:
            scale_boost = math.sqrt(4.2 / target_mp)
            target_w = int(target_w * scale_boost)
            target_h = int(target_h * scale_boost)
            target_mp = 4.2

        if target_mp > 36.0:
            scale_down = math.sqrt(36.0 / target_mp)
            target_w = int(target_w * scale_down)
            target_h = int(target_h * scale_down)

        return target_w, target_h

    def smooth_object_edges(self, img: Image.Image) -> Image.Image:
        """
        Subpixel Anti-Aliasing & Studio Edge Smoothing:
        Meratakan dan menghaluskan pinggiran objek (cutout boundary) agar tidak terlihat bergerigi
        atau seperti potongan kasar, baik untuk gambar transparan (PNG) maupun solid (JPG).
        """
        if self.edge_smooth_level == "none":
            return img

        blur_radius = {"low": 0.6, "medium": 1.0, "strong": 1.5}.get(self.edge_smooth_level, 1.0)
        has_alpha = ("A" in img.mode or img.mode in ("RGBA", "LA"))

        if has_alpha:
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            r, g, b, a = img.split()

            # 1. Anti-aliasing halus pada kanal alpha untuk menghilangkan pixel-stepping / gigi gergaji
            a_smooth = a.filter(ImageFilter.GaussianBlur(radius=blur_radius))

            # 2. Defringe / de-halo: bersihkan color bleeding pada tepi alpha transisi
            alpha_edge = ImageChops.difference(a, a.filter(ImageFilter.MinFilter(3)))
            edge_blend_mask = alpha_edge.filter(ImageFilter.GaussianBlur(radius=blur_radius))

            rgb = Image.merge("RGB", (r, g, b))
            rgb_smooth = rgb.filter(ImageFilter.GaussianBlur(radius=blur_radius))
            rgb_final = Image.composite(rgb_smooth, rgb, edge_blend_mask)

            rf, gf, bf = rgb_final.split()
            return Image.merge("RGBA", (rf, gf, bf, a_smooth))
        else:
            # Solid RGB (JPG): Deteksi kontur siluet kontras tinggi dan haluskan subpixel-nya
            rgb = img.convert("RGB")
            gray = rgb.convert("L")
            edges = gray.filter(ImageFilter.FIND_EDGES)

            # Mask pinggiran siluet objek (potongan kasar / jagged boundary)
            edge_mask = edges.point(lambda p: 255 if p > 35 else 0)
            edge_mask = edge_mask.filter(ImageFilter.GaussianBlur(radius=blur_radius))

            # Haluskan subpixel hanya pada pinggiran objek
            smoothed = rgb.filter(ImageFilter.GaussianBlur(radius=blur_radius))
            return Image.composite(smoothed, rgb, edge_mask)

    def apply_smart_sharpening(self, img: Image.Image) -> Image.Image:
        """
        Penajaman adaptif: menajamkan tekstur & detail internal (kulit, kain, bulu, rambut, pola)
        namun meredam penajaman pada garis siluet luar agar pinggiran tidak bergerigi / berkabut halo.
        """
        if self.sharpen_level == "none":
            return img

        params = {
            "low": {"radius": 1.0, "percent": 50, "threshold": 4},
            "medium": {"radius": 1.2, "percent": 75, "threshold": 3},
            "strong": {"radius": 1.5, "percent": 100, "threshold": 2},
        }.get(self.sharpen_level, {"radius": 1.2, "percent": 75, "threshold": 3})

        has_alpha = ("A" in img.mode or img.mode in ("RGBA", "LA"))
        if has_alpha:
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            r, g, b, a = img.split()
            rgb = Image.merge("RGB", (r, g, b))
        else:
            rgb = img.convert("RGB")
            a = None

        # 1. Unsharp mask pada kanal warna
        sharpened_rgb = rgb.filter(ImageFilter.UnsharpMask(
            radius=params["radius"],
            percent=params["percent"],
            threshold=params["threshold"]
        ))

        # 2. Deteksi siluet tepi luar agar tidak terkena overshooting / halo effect
        gray = rgb.convert("L")
        silhouette_edges = gray.filter(ImageFilter.FIND_EDGES)
        outer_mask = silhouette_edges.point(lambda p: 255 if p > 45 else 0)
        outer_mask = outer_mask.filter(ImageFilter.GaussianBlur(radius=1.2))

        # 3. Lindungi garis pinggir siluet dari halo kasar, tetap tajamkan detail internal
        final_rgb = Image.composite(rgb, sharpened_rgb, outer_mask)

        if a is not None:
            r_fin, g_fin, b_fin = final_rgb.split()
            return Image.merge("RGBA", (r_fin, g_fin, b_fin, a))
        return final_rgb

    def upscale(self, input_path: str, output_path: str, target_format: str = "auto") -> dict:
        """
        Memproses 1 file gambar secara menyeluruh dengan Studio Edge & Anti-Aliasing.
        """
        out_path = Path(output_path)
        
        # Tentukan apakah target akhirnya PNG atau JPG
        if target_format == "png" or out_path.suffix.lower() == ".png":
            is_png = True
            out_ext = ".png"
        elif target_format in ("jpg", "jpeg") or out_path.suffix.lower() in (".jpg", ".jpeg"):
            is_png = False
            out_ext = ".jpg"
        else:
            # Auto: ikuti format file input
            input_ext = Path(input_path).suffix.lower()
            is_png = (input_ext == ".png")
            out_ext = ".png" if is_png else ".jpg"

        out_path = out_path.with_suffix(out_ext)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        with Image.open(input_path) as img:
            orig_w, orig_h = img.size
            orig_mp = (orig_w * orig_h) / 1_000_000.0
            has_alpha = ("A" in img.mode or img.mode in ("RGBA", "LA", "P"))

            # Pastikan mode warna terstandarisasi sebelum diproses
            if has_alpha:
                if img.mode != "RGBA":
                    img = img.convert("RGBA")
            else:
                if img.mode != "RGB":
                    img = img.convert("RGB")

            # Step 1: Chroma Denoise murni (tanpa merusak luminance & ketajaman garis)
            img = self.reduce_chroma_noise(img)

            # Step 2: Hitung resolusi target
            target_w, target_h = self.calculate_target_size(orig_w, orig_h)
            target_mp = (target_w * target_h) / 1_000_000.0

            # Step 3: Single-Pass High-Precision Lanczos Resampling
            img = img.resize((target_w, target_h), Image.Resampling.LANCZOS)

            # Step 4: Subpixel Anti-Aliasing & Studio Edge Smoothing
            img = self.smooth_object_edges(img)

            # Step 5: Adaptive Texture Sharpening (Anti-Halo pada siluet)
            img = self.apply_smart_sharpening(img)

            # Step 6: Export File (PNG atau JPEG)
            if is_png:
                img.save(
                    str(out_path),
                    format="PNG",
                    dpi=(300, 300),
                    optimize=True
                )
            else:
                # Jika diexport ke JPEG (Adobe Stock) dan input memiliki alpha:
                # Composite di atas background putih murni (255, 255, 255) pada resolusi tinggi
                # sehingga batas pinggiran objek menyatu sangat halus (anti-aliased) dengan latar putih.
                if "A" in img.mode or img.mode in ("RGBA", "LA"):
                    bg = Image.new("RGB", img.size, (255, 255, 255))
                    alpha_mask = img.split()[-1]
                    bg.paste(img.convert("RGB"), mask=alpha_mask)
                    final_jpeg_img = bg
                else:
                    final_jpeg_img = img.convert("RGB")

                final_jpeg_img.save(
                    str(out_path),
                    format="JPEG",
                    quality=98,
                    subsampling=0,       # 4:4:4 full chroma preservation
                    dpi=(300, 300),      # Standard print inspection
                    optimize=True,
                    progressive=False
                )

        final_size_mb = os.path.getsize(str(out_path)) / (1024 * 1024)

        return {
            "input": input_path,
            "output": str(out_path),
            "format": "PNG (Lossless)" if is_png else "JPEG (Adobe Stock 4:4:4)",
            "orig_res": f"{orig_w}x{orig_h} ({orig_mp:.2f} MP)",
            "final_res": f"{target_w}x{target_h} ({target_mp:.2f} MP)",
            "file_size": f"{final_size_mb:.2f} MB",
            "compliant": target_mp >= 4.0 and target_mp <= 100.0 and final_size_mb <= 45.0,
            "engine": "Studio Edge Pro (Lanczos + Subpixel Anti-Aliasing)"
        }


def main():
    parser = argparse.ArgumentParser(
        description="Adobe Stock Image Upscaler & Quality Fixer (Studio Edge Pro Edition)"
    )
    parser.add_argument("-i", "--input", required=True, help="Path gambar input (jpg/png) atau folder gambar")
    parser.add_argument("-o", "--output", required=True, help="Path file output atau folder tujuan")
    parser.add_argument("-s", "--scale", type=float, default=2.0, help="Faktor perbesaran. Default: 2.0")
    parser.add_argument("-m", "--target-mp", type=float, default=16.0, help="Target Megapiksel minimal. Default: 16.0")
    parser.add_argument("--format", choices=["auto", "jpg", "png"], default="auto", help="Format output. Default: auto")
    parser.add_argument("--denoise", choices=["none", "low", "medium", "strong"], default="medium", help="Denoise. Default: medium")
    parser.add_argument("--sharpen", choices=["none", "low", "medium", "strong"], default="medium", help="Anti-halo sharpening. Default: medium")
    parser.add_argument("--edge-smooth", choices=["none", "low", "medium", "strong"], default="medium", help="Studio edge smoothing / anti-aliasing. Default: medium")
    parser.add_argument("--engine", choices=["auto", "classic"], default="classic", help="Engine. Default: classic")

    args = parser.parse_args()

    enhancer = AdobeStockEnhancer(
        scale=args.scale,
        target_mp=args.target_mp,
        denoise=args.denoise,
        sharpen=args.sharpen,
        edge_smooth=args.edge_smooth,
        engine=args.engine
    )

    in_path = Path(args.input)
    if in_path.is_file():
        files = [in_path]
        out_is_dir = Path(args.output).is_dir() or not Path(args.output).suffix
    elif in_path.is_dir():
        valid_exts = {".jpg", ".jpeg", ".png", ".webp"}
        files = [p for p in in_path.iterdir() if p.suffix.lower() in valid_exts]
        out_is_dir = True
    else:
        print(f"[ERROR] Input '{args.input}' tidak ditemukan.")
        sys.exit(1)

    if not files:
        print("[WARN] Tidak ditemukan gambar berformat jpg/jpeg/png/webp untuk diproses.")
        sys.exit(0)

    for idx, f in enumerate(files, 1):
        if out_is_dir:
            ext = ".png" if (args.format == "png" or (args.format == "auto" and f.suffix.lower() == ".png")) else ".jpg"
            out_file = Path(args.output) / f"{f.stem}{ext}"
        else:
            out_file = Path(args.output)

        print(f"[{idx}/{len(files)}] Memproses: {f.name} ...")
        res = enhancer.upscale(str(f), str(out_file), target_format=args.format)
        print(f"   ✓ Selesai    : {res['final_res']} | {res['file_size']} ({res['format']})")


if __name__ == "__main__":
    main()
