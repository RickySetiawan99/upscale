#!/usr/bin/env python3
"""
Stock Contributor Metadata Generator (Adobe Stock & Vecteezy Standard Edition)
==============================================================================
Menghasilkan metadata terstruktur dan file CSV siap submit untuk:
1. Adobe Stock (Header: Filename,Title,Keywords,Category,Releases)
2. Vecteezy (Header: Filename,Title,Description,Keywords)

Menggunakan 9Router Vision Gateway dengan fallback cerdas berbasis nama file.
"""

import os
import io
import csv
import json
import base64
import urllib.request
from pathlib import Path
from PIL import Image

NINEROUTER_URL = os.environ.get("NINEROUTER_URL", "http://127.0.0.1:20128")
NINEROUTER_KEY = os.environ.get("NINEROUTER_KEY", "sk-7c2aef2c551a9489-8ytq37-923cf85b")
MODEL_NAME = "gemini/gemini-3.5-flash-lite"


def make_thumbnail_b64(image_path: str, max_size=384) -> str:
    """Membuat thumbnail ringan dalam format JPEG base64 untuk analisis AI vision yang cepat & hemat bandwidth."""
    with Image.open(image_path) as img:
        # Konversi ke RGB jika ada transparansi
        if img.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            alpha = img.split()[-1] if len(img.split()) == 4 else None
            if alpha:
                bg.paste(img, mask=alpha)
                img = bg
            else:
                img = img.convert("RGB")
        elif img.mode != "RGB":
            img = img.convert("RGB")

        img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        return base64.b64encode(buf.getvalue()).decode("utf-8")


def fallback_metadata(filename: str) -> dict:
    """Fallback otomatis jika koneksi AI vision sedang sibuk / timeout."""
    stem = Path(filename).stem.replace("_", " ").replace("-", " ")
    title = stem.title()
    words = [w for w in stem.split() if len(w) > 2]
    base_keywords = [
        "stock", "illustration", "graphic", "isolated", "vector", "design",
        "concept", "modern", "background", "artwork", "digital", "high quality"
    ]
    all_keywords = list(dict.fromkeys(words + base_keywords))[:35]
    
    return {
        "title_adobe": f"{title} Illustration"[:70],
        "title_vecteezy": f"{title} Vector Graphic"[:100],
        "description": f"High quality commercial graphic asset depicting {title.lower()} on clean background.",
        "category_adobe_id": 8,  # Graphic Resources
        "keywords": all_keywords
    }


def analyze_image_metadata(image_path: str, filename: str) -> dict:
    """
    Menganalisis gambar menggunakan AI vision dan menghasilkan metadata spesifik
    sesuai standar komersial Adobe Stock dan Vecteezy.
    """
    try:
        b64_data = make_thumbnail_b64(image_path)
    except Exception as e:
        print(f"[METADATA] Gagal membuat thumbnail {filename}: {e}")
        return fallback_metadata(filename)

    prompt = """Analyze this image and generate commercial stock metadata for Adobe Stock and Vecteezy.
Return strict JSON with this exact schema:
{
  "title_adobe": "Concise descriptive title, max 70 chars, plain text in English",
  "title_vecteezy": "Title Case concise title, 3-8 words in English",
  "description": "Factual 1-2 sentence description of subject, style, lighting, and composition in English",
  "category_adobe_id": 8,
  "keywords": ["keyword1", "keyword2", ..., "keyword35"]
}
Requirements:
- keywords: 30 to 45 relevant English keywords, ordered by importance (subject, action, concept, style, colors, mood).
- category_adobe_id: Pick exactly ONE numeric ID (1-21) matching Adobe Stock:
  1: Animals, 2: Buildings/Architecture, 3: Business, 4: Drinks, 5: Environment, 6: States of Mind, 7: Food,
  8: Graphic Resources, 9: Hobbies/Leisure, 10: Industry, 11: Landscapes, 12: Lifestyle, 13: People,
  14: Plants/Flowers, 15: Culture/Religion, 16: Science, 17: Social Issues, 18: Sports, 19: Technology, 20: Transport, 21: Travel.
Return ONLY valid raw JSON."""

    payload = {
        "model": MODEL_NAME,
        "stream": False,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_data}"}}
                ]
            }
        ],
        "max_tokens": 800,
        "temperature": 0.2
    }

    try:
        req = urllib.request.Request(
            f"{NINEROUTER_URL}/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {NINEROUTER_KEY}"
            }
        )

        with urllib.request.urlopen(req, timeout=18) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            raw = data["choices"][0]["message"]["content"].strip()

            # Bersihkan blok markdown ```json jika ada
            if raw.startswith("```"):
                parts = raw.split("```")
                raw = parts[1]
                if raw.startswith("json"):
                    raw = raw[4:]

            parsed = json.loads(raw.strip())
            
            # Validasi batasan Adobe Stock & Vecteezy
            title_adobe = str(parsed.get("title_adobe", fallback_metadata(filename)["title_adobe"])).strip()
            if len(title_adobe) > 70:
                title_adobe = title_adobe[:67].rsplit(" ", 1)[0]

            title_vecteezy = str(parsed.get("title_vecteezy", title_adobe)).strip()[:100]
            description = str(parsed.get("description", "")).strip()
            if not description:
                description = f"High quality commercial graphic asset depicting {title_adobe}."

            category_id = int(parsed.get("category_adobe_id", 8))
            if category_id < 1 or category_id > 21:
                category_id = 8

            keywords = parsed.get("keywords", [])
            if not isinstance(keywords, list) or len(keywords) < 5:
                keywords = fallback_metadata(filename)["keywords"]
            else:
                keywords = [str(k).strip().lower() for k in keywords if k]
                # Filter deduplikasi mempertahankan urutan prioritas
                keywords = list(dict.fromkeys(keywords))[:45]

            return {
                "title_adobe": title_adobe,
                "title_vecteezy": title_vecteezy,
                "description": description,
                "category_adobe_id": category_id,
                "keywords": keywords
            }

    except Exception as err:
        print(f"[METADATA] AI Vision error for {filename}: {err}. Menggunakan fallback cerdas.")
        return fallback_metadata(filename)


def generate_adobe_stock_csv(records: list[dict], output_path: str):
    """
    Template Resmi Adobe Stock:
    Filename,Title,Keywords,Category,Releases
    """
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(["Filename", "Title", "Keywords", "Category", "Releases"])
        for r in records:
            kw_str = ", ".join(r.get("keywords", []))
            writer.writerow([
                r.get("filename", ""),
                r.get("title_adobe", ""),
                kw_str,
                r.get("category_adobe_id", 8),
                ""  # Releases (optional)
            ])


def generate_vecteezy_csv(records: list[dict], output_path: str, license_type=""):
    """
    Template Resmi Vecteezy Contributor (Format Terbaru):
    Filename,Title,Description,Keywords,License,Id
    """
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(["Filename", "Title", "Description", "Keywords", "License", "Id"])
        for r in records:
            kw_str = ", ".join(r.get("keywords", []))
            writer.writerow([
                r.get("filename", ""),
                r.get("title_vecteezy", ""),
                r.get("description", ""),
                kw_str,
                license_type,  # Free / Pro / Empty (opsional)
                ""             # Id kosong untuk submission aset baru
            ])
