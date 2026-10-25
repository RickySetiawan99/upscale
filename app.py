#!/usr/bin/env python3
import os
import re
import sys
import time
import uuid
import shutil
import zipfile
import threading
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_file

BASE_DIR = Path(__file__).resolve().parent

# Add scripts directory to path to import AdobeStockEnhancer and metadata generator
sys.path.insert(0, str(BASE_DIR / 'scripts'))
sys.path.insert(0, '/home/ubuntu/projects/scripts')
try:
    from adobe_stock_upscaler import AdobeStockEnhancer
except ImportError:
    AdobeStockEnhancer = None

try:
    from metadata_generator import (
        analyze_image_metadata,
        generate_adobe_stock_csv,
        generate_vecteezy_csv
    )
except ImportError:
    analyze_image_metadata = None
    generate_adobe_stock_csv = None
    generate_vecteezy_csv = None

try:
    from vector_generator import convert_to_eps, PRESETS as VECTOR_PRESETS
except ImportError:
    convert_to_eps = None
    VECTOR_PRESETS = {}

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500 MB max upload total for batch
STORAGE_DIR = BASE_DIR / "storage"
STORAGE_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}
MAX_BATCH_FILES = 20
MAX_SINGLE_FILE_SIZE = 50 * 1024 * 1024  # 50 MB per file


@app.errorhandler(413)
def request_entity_too_large(error):
    return jsonify({
        "success": False,
        "error": "Total ukuran file upload melebihi batas server (Maks 500 MB). Silakan kurangi ukuran atau jumlah gambar."
    }), 413


def sanitize_filename(name: str) -> str:
    """Membersihkan nama file agar aman di filesystem namun mempertahankan nama asli."""
    stem = Path(name).stem
    # Pertahankan karakter alfanumerik, spasi, strip, dan underscore
    clean = re.sub(r'[^\w\s\-\.]', '', stem).strip()
    return clean if clean else "image"


# Tracking Batch Aktif: Mencegah auto-purge menghapus batch yang sedang dalam proses generate
ACTIVE_BATCHES = set()
BATCH_LOCK = threading.Lock()


def mark_batch_active(batch_id: str):
    with BATCH_LOCK:
        ACTIVE_BATCHES.add(batch_id)


def mark_batch_done(batch_id: str):
    with BATCH_LOCK:
        ACTIVE_BATCHES.discard(batch_id)
    touch_batch(batch_id)


BATCH_EXPIRY_SECONDS = 3600  # 1 jam penuh sejak generate selesai


def touch_batch(batch_id: str):
    """Memperbarui timestamp mtime folder batch agar countdown pembersihan di-reset dari aktivitas terakhir."""
    try:
        b_dir = STORAGE_DIR / Path(batch_id).name
        if b_dir.exists():
            now = time.time()
            os.utime(b_dir, (now, now))
    except Exception:
        pass


# Background Auto-Cleanup Thread: Menghapus folder/file batch yang sudah IDLE lebih dari 1 jam (3600 detik) setelah selesai
def cleanup_old_files():
    while True:
        try:
            now = time.time()
            for item in STORAGE_DIR.glob("*"):
                # 1. JANGAN PERNAH hapus batch yang sedang aktif diproses generate!
                with BATCH_LOCK:
                    if item.name in ACTIVE_BATCHES:
                        continue

                # 2. Hapus hanya jika batch sudah IDLE (tidak aktif & selesai) lebih dari 1 jam (3600 detik)
                if now - item.stat().st_mtime > BATCH_EXPIRY_SECONDS:
                    try:
                        if item.is_dir():
                            shutil.rmtree(item)
                            print(f"[CLEANUP] Deleted expired batch folder: {item.name}")
                        elif item.is_file():
                            item.unlink()
                            print(f"[CLEANUP] Deleted expired file: {item.name}")
                    except Exception as e:
                        print(f"[CLEANUP ERROR] {e}")
        except Exception as err:
            print(f"[CLEANUP LOOP ERROR] {err}")
        time.sleep(60)  # Cek tiap 1 menit


cleanup_thread = threading.Thread(target=cleanup_old_files, daemon=True)
cleanup_thread.start()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/upscale", methods=["POST"])
def upscale_endpoint():
    # Ambil list file yang diunggah
    uploaded_files = request.files.getlist("files")
    if not uploaded_files or len(uploaded_files) == 0:
        # Fallback jika form kirim single "file"
        single = request.files.get("file")
        if single:
            uploaded_files = [single]

    if not uploaded_files or uploaded_files[0].filename == "":
        return jsonify({"success": False, "error": "Tidak ada file yang dipilih"}), 400

    # Batasi maksimal 20 file per batch
    if len(uploaded_files) > MAX_BATCH_FILES:
        uploaded_files = uploaded_files[:MAX_BATCH_FILES]

    # Validasi ukuran tiap file tidak boleh melebihi 50 MB
    for f in uploaded_files:
        f.seek(0, os.SEEK_END)
        f_size = f.tell()
        f.seek(0)
        if f_size > MAX_SINGLE_FILE_SIZE:
            return jsonify({
                "success": False,
                "error": f"File '{f.filename}' ({f_size / (1024*1024):.1f} MB) melebihi batas 50 MB per gambar."
            }), 400

    # Parse parameters
    try:
        scale = float(request.form.get("scale", 2.0))
        target_mp = float(request.form.get("target_mp", 16.0))
        denoise = request.form.get("denoise", "medium")
        sharpen = request.form.get("sharpen", "medium")
        edge_smooth = request.form.get("edge_smooth", "medium")
        engine = request.form.get("engine", "classic")
        target_format = request.form.get("target_format", "auto").strip().lower()
        if target_format not in ("auto", "jpg", "png"):
            target_format = "auto"
        generate_meta = request.form.get("generate_metadata", "true").lower() in ("true", "1", "yes")
        generate_vector = request.form.get("generate_vector", "false").lower() in ("true", "1", "yes")
        vector_preset = request.form.get("vector_preset", "logo_icon")
    except ValueError:
        return jsonify({"success": False, "error": "Parameter opsi tidak valid"}), 400

    batch_id = uuid.uuid4().hex[:12]
    batch_dir = STORAGE_DIR / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    mark_batch_active(batch_id)

    enhancer = AdobeStockEnhancer(
        scale=scale,
        target_mp=target_mp,
        denoise=denoise,
        sharpen=sharpen,
        edge_smooth=edge_smooth,
        engine=engine
    )

    processed_list = []
    output_files_to_zip = []
    metadata_records = []

    try:
        # Proses satu per satu secara sekuensial agar hemat RAM di VPS
        for idx, f in enumerate(uploaded_files):
            # Refresh timestamp mtime tiap proses file baru agar timer tidak pernah kadaluarsa saat proses berjalan
            touch_batch(batch_id)

            ext = Path(f.filename).suffix.lower()
            if ext not in ALLOWED_EXTENSIONS:
                continue

            raw_base = sanitize_filename(f.filename)

            # Tentukan ekstensi output berdasarkan format target
            if target_format == "png":
                out_ext = ".png"
                file_target_fmt = "png"
            elif target_format in ("jpg", "jpeg"):
                out_ext = ".jpg"
                file_target_fmt = "jpg"
            else:  # auto: ikuti format input
                out_ext = ".png" if ext == ".png" else ".jpg"
                file_target_fmt = "auto"

            out_name = f"{raw_base}{out_ext}"
            
            # Cegah duplikasi nama jika ada 2 file dengan nama sama
            counter = 1
            while (batch_dir / out_name).exists():
                out_name = f"{raw_base}_{counter}{out_ext}"
                counter += 1

            temp_in = batch_dir / f"temp_{idx}{ext}"
            target_out = batch_dir / out_name

            try:
                f.save(str(temp_in))
                res = enhancer.upscale(str(temp_in), str(target_out), target_format=file_target_fmt)
                
                # Hapus input mentahan seketika untuk hemat storage
                if temp_in.exists():
                    temp_in.unlink()

                # Generate metadata via AI Vision jika opsi aktif
                meta_info = None
                if generate_meta and analyze_image_metadata:
                    try:
                        meta = analyze_image_metadata(str(target_out), out_name)
                        # Sesuaikan ekstensi filename di CSV: .eps jika opsi vektor aktif, atau out_name (.jpg/.png)
                        meta["filename"] = f"{Path(out_name).stem}.eps" if generate_vector else out_name
                        metadata_records.append(meta)
                        meta_info = {
                            "title_adobe": meta.get("title_adobe"),
                            "title_vecteezy": meta.get("title_vecteezy"),
                            "category_id": meta.get("category_adobe_id"),
                            "keywords_count": len(meta.get("keywords", []))
                        }
                    except Exception as meta_err:
                        print(f"[METADATA ERROR] {out_name}: {meta_err}")

                # Generate EPS Vector jika opsi aktif
                vector_info = None
                if generate_vector and convert_to_eps:
                    eps_stem = Path(out_name).stem
                    eps_name = f"{eps_stem}.eps"
                    eps_path = batch_dir / eps_name
                    try:
                        vres = convert_to_eps(str(target_out), str(eps_path), preset=vector_preset)
                        if vres.get("success"):
                            output_files_to_zip.append(eps_path)
                            vector_info = {
                                "eps_name": eps_name,
                                "eps_download_url": f"/download/{batch_id}/{eps_name}",
                                "eps_size": vres.get("file_size"),
                                "preset_name": vres.get("preset_name")
                            }
                    except Exception as ve:
                        print(f"[VECTOR ERROR] {eps_name}: {ve}")

                processed_list.append({
                    "original_name": f.filename,
                    "output_name": out_name,
                    "format": res.get("format", "PNG" if out_ext == ".png" else "JPEG"),
                    "download_url": f"/download/{batch_id}/{out_name}",
                    "orig_res": res["orig_res"],
                    "final_res": res["final_res"],
                    "file_size": res["file_size"],
                    "compliant": res["compliant"],
                    "metadata": meta_info,
                    "vector": vector_info
                })
                output_files_to_zip.append(target_out)
            except Exception as e:
                if temp_in.exists():
                    temp_in.unlink()
                print(f"[ERROR] Failed processing {f.filename}: {e}")

        if not processed_list:
            if batch_dir.exists():
                shutil.rmtree(batch_dir)
            return jsonify({"success": False, "error": "Tidak ada gambar yang berhasil diproses"}), 500

        # Buat file CSV Metadata resmi untuk Adobe Stock dan Vecteezy
        has_metadata = False
        if metadata_records and generate_adobe_stock_csv and generate_vecteezy_csv:
            try:
                adobe_csv = batch_dir / "adobe_stock_metadata.csv"
                vecteezy_csv = batch_dir / "vecteezy_metadata.csv"
                generate_adobe_stock_csv(metadata_records, str(adobe_csv))
                generate_vecteezy_csv(metadata_records, str(vecteezy_csv))
                has_metadata = True
                output_files_to_zip.append(adobe_csv)
                output_files_to_zip.append(vecteezy_csv)
            except Exception as csv_err:
                print(f"[CSV GENERATION ERROR] {csv_err}")

        # Buat file ZIP berisi semua gambar + CSV metadata yang sudah di-upscale
        zip_filename = f"NyobaAI_Batch_{batch_id}.zip"
        zip_path = batch_dir / zip_filename
        with zipfile.ZipFile(str(zip_path), 'w', zipfile.ZIP_DEFLATED) as zipf:
            for out_file in output_files_to_zip:
                if out_file.exists():
                    zipf.write(str(out_file), arcname=out_file.name)

        zip_size_mb = os.path.getsize(str(zip_path)) / (1024 * 1024)
        now_ts = time.time()

        result_payload = {
            "success": True,
            "batch_id": batch_id,
            "created_at": now_ts,
            "expires_at": now_ts + BATCH_EXPIRY_SECONDS,
            "total_processed": len(processed_list),
            "zip_filename": zip_filename,
            "zip_download_url": f"/download-zip/{batch_id}",
            "zip_size": f"{zip_size_mb:.2f} MB",
            "has_metadata": has_metadata,
            "has_vectors": any(item.get("vector") for item in processed_list),
            "adobe_csv_url": f"/download-csv/{batch_id}/adobe" if has_metadata else None,
            "vecteezy_csv_url": f"/download-csv/{batch_id}/vecteezy" if has_metadata else None,
            "files": processed_list,
            "metadata_summary": metadata_records if has_metadata else []
        }

        # Simpan metadata batch agar persisten selama 1 jam saat user refresh / akses riwayat
        try:
            with open(batch_dir / "meta.json", "w", encoding="utf-8") as f:
                json.dump(result_payload, f, ensure_ascii=False, indent=2)
        except Exception as json_err:
            print(f"[META JSON SAVE ERROR] {json_err}")

        return jsonify(result_payload)
    finally:
        # Selesai processing: lepas status aktif dan timer 1 jam baru mulai dihitung mundur
        mark_batch_done(batch_id)


@app.route("/download/<batch_id>/<filename>")
def download_single(batch_id, filename):
    touch_batch(batch_id)
    safe_batch = Path(batch_id).name
    safe_name = Path(filename).name
    file_path = STORAGE_DIR / safe_batch / safe_name
    if not file_path.exists() or not file_path.is_file():
        return "File tidak ditemukan atau telah kedaluwarsa.", 404

    ext = file_path.suffix.lower()
    if ext == ".png":
        mimetype = "image/png"
    elif ext == ".eps":
        mimetype = "application/postscript"
    else:
        mimetype = "image/jpeg"

    return send_file(
        str(file_path),
        as_attachment=True,
        download_name=safe_name,
        mimetype=mimetype
    )


@app.route("/download-zip/<batch_id>")
def download_zip(batch_id):
    touch_batch(batch_id)
    safe_batch = Path(batch_id).name
    batch_dir = STORAGE_DIR / safe_batch
    if not batch_dir.exists() or not batch_dir.is_dir():
        return "Batch tidak ditemukan atau telah kedaluwarsa.", 404

    zip_files = list(batch_dir.glob("*.zip"))
    if not zip_files:
        return "File ZIP tidak ditemukan.", 404

    return send_file(
        str(zip_files[0]),
        as_attachment=True,
        download_name=zip_files[0].name,
        mimetype="application/zip"
    )


@app.route("/download-csv/<batch_id>/<platform>")
def download_csv(batch_id, platform):
    touch_batch(batch_id)
    safe_batch = Path(batch_id).name
    batch_dir = STORAGE_DIR / safe_batch
    if not batch_dir.exists() or not batch_dir.is_dir():
        return "Batch tidak ditemukan atau telah kedaluwarsa.", 404

    plat = platform.lower()
    if plat == "adobe":
        csv_file = batch_dir / "adobe_stock_metadata.csv"
        dl_name = f"AdobeStock_Metadata_{safe_batch}.csv"
    elif plat == "vecteezy":
        csv_file = batch_dir / "vecteezy_metadata.csv"
        dl_name = f"Vecteezy_Metadata_{safe_batch}.csv"
    else:
        return "Platform tidak dikenali (pilih adobe atau vecteezy).", 400

    if not csv_file.exists():
        return "File CSV metadata tidak ditemukan.", 404

    return send_file(
        str(csv_file),
        as_attachment=True,
        download_name=dl_name,
        mimetype="text/csv"
    )


@app.route("/download-template/adobe")
def download_adobe_template():
    tpl_path = BASE_DIR / "templates" / "Sample_Adobe_Stock_CSV_upload.csv"
    if not tpl_path.exists():
        tpl_path = BASE_DIR / "sample_metadata" / "Sample_Adobe_Stock_CSV_upload.csv"
    if not tpl_path.exists():
        return "Template CSV tidak ditemukan.", 404
    return send_file(
        str(tpl_path),
        as_attachment=True,
        download_name="Sample_Adobe_Stock_CSV_upload.csv",
        mimetype="text/csv"
    )


@app.route("/download-template/vecteezy")
def download_vecteezy_template():
    tpl_path = BASE_DIR / "templates" / "Sample_Vecteezy_CSV_upload.csv"
    if not tpl_path.exists():
        tpl_path = BASE_DIR / "sample_metadata" / "resources-metadata-2026-10-25.csv"
    if not tpl_path.exists():
        return "Template CSV tidak ditemukan.", 404
    return send_file(
        str(tpl_path),
        as_attachment=True,
        download_name="resources-metadata-template.csv",
        mimetype="text/csv"
    )


@app.route("/api/delete-batch/<batch_id>", methods=["POST"])
def delete_batch(batch_id):
    safe_batch = Path(batch_id).name
    batch_dir = STORAGE_DIR / safe_batch
    if batch_dir.exists() and batch_dir.is_dir():
        try:
            shutil.rmtree(batch_dir)
            return jsonify({"success": True, "message": "Semua file batch berhasil dihapus dari server"})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
    return jsonify({"success": True, "message": "Batch sudah tidak ada"})


@app.route("/api/history")
def get_batch_history():
    now = time.time()
    history = []
    for item in STORAGE_DIR.glob("*"):
        if not item.is_dir():
            continue

        mtime = item.stat().st_mtime
        elapsed = now - mtime
        if elapsed > BATCH_EXPIRY_SECONDS:
            continue

        meta_file = item / "meta.json"
        remaining_seconds = max(0, int(BATCH_EXPIRY_SECONDS - elapsed))
        remaining_mins = max(1, int(remaining_seconds // 60))

        if meta_file.exists():
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                    created_at = meta.get("created_at", mtime)
                    c_elapsed = now - created_at
                    if c_elapsed <= BATCH_EXPIRY_SECONDS:
                        meta["remaining_seconds"] = max(0, int(BATCH_EXPIRY_SECONDS - c_elapsed))
                        meta["remaining_mins"] = max(1, int(meta["remaining_seconds"] // 60))
                        history.append(meta)
                        continue
            except Exception:
                pass

        # Fallback jika folder belum punya meta.json
        zip_files = list(item.glob("*.zip"))
        images = list(item.glob("*.jpg")) + list(item.glob("*.png")) + list(item.glob("*.jpeg"))
        if zip_files or images:
            zip_size = f"{zip_files[0].stat().st_size / (1024*1024):.2f} MB" if zip_files else "0 MB"
            history.append({
                "batch_id": item.name,
                "created_at": mtime,
                "total_processed": len(images),
                "zip_download_url": f"/download-zip/{item.name}" if zip_files else None,
                "zip_size": zip_size,
                "has_metadata": (item / "adobe_stock_metadata.csv").exists(),
                "has_vectors": len(list(item.glob("*.eps"))) > 0,
                "remaining_seconds": remaining_seconds,
                "remaining_mins": remaining_mins,
            })

    history.sort(key=lambda x: x.get("created_at", 0), reverse=True)
    return jsonify({"success": True, "batches": history, "total": len(history)})


@app.route("/api/batch-info/<batch_id>")
def get_batch_info(batch_id):
    safe_batch = Path(batch_id).name
    batch_dir = STORAGE_DIR / safe_batch
    if not batch_dir.exists() or not batch_dir.is_dir():
        return jsonify({"success": False, "error": "Batch tidak ditemukan atau telah kedaluwarsa (lebih dari 1 jam)."}), 404

    now = time.time()
    mtime = batch_dir.stat().st_mtime
    elapsed = now - mtime
    if elapsed > BATCH_EXPIRY_SECONDS:
        return jsonify({"success": False, "error": "Batch telah kedaluwarsa (melebihi batas 1 jam)."}), 404

    remaining_seconds = max(0, int(BATCH_EXPIRY_SECONDS - elapsed))
    remaining_mins = max(1, int(remaining_seconds // 60))

    meta_file = batch_dir / "meta.json"
    if meta_file.exists():
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                created_at = data.get("created_at", mtime)
                c_elapsed = now - created_at
                data["remaining_seconds"] = max(0, int(BATCH_EXPIRY_SECONDS - c_elapsed))
                data["remaining_mins"] = max(1, int(data["remaining_seconds"] // 60))
                data["success"] = True
                return jsonify(data)
        except Exception:
            pass

    touch_batch(batch_id)

    zip_files = list(batch_dir.glob("*.zip"))
    zip_download_url = f"/download-zip/{safe_batch}" if zip_files else None
    zip_size = f"{zip_files[0].stat().st_size / (1024*1024):.2f} MB" if zip_files else "0 MB"

    has_adobe = (batch_dir / "adobe_stock_metadata.csv").exists()
    has_vecteezy = (batch_dir / "vecteezy_metadata.csv").exists()

    files = []
    png_jpg_files = sorted(list(batch_dir.glob("*.png")) + list(batch_dir.glob("*.jpg")) + list(batch_dir.glob("*.jpeg")))
    for img_p in png_jpg_files:
        eps_p = batch_dir / f"{img_p.stem}.eps"
        vector_info = None
        if eps_p.exists():
            vector_info = {
                "eps_name": eps_p.name,
                "eps_download_url": f"/download/{safe_batch}/{eps_p.name}",
                "eps_size": f"{eps_p.stat().st_size / (1024*1024):.2f} MB" if eps_p.stat().st_size >= 1024*1024 else f"{eps_p.stat().st_size / 1024:.1f} KB"
            }

        files.append({
            "output_name": img_p.name,
            "download_url": f"/download/{safe_batch}/{img_p.name}",
            "file_size": f"{img_p.stat().st_size / (1024*1024):.2f} MB",
            "final_res": "4K UHD (Adobe Compliant)",
            "vector": vector_info
        })

    return jsonify({
        "success": True,
        "batch_id": safe_batch,
        "total_processed": len(files),
        "zip_download_url": zip_download_url,
        "zip_size": zip_size,
        "has_metadata": has_adobe or has_vecteezy,
        "adobe_csv_url": f"/download-csv/{safe_batch}/adobe" if has_adobe else None,
        "vecteezy_csv_url": f"/download-csv/{safe_batch}/vecteezy" if has_vecteezy else None,
        "files": files,
        "remaining_seconds": remaining_seconds,
        "remaining_mins": remaining_mins
    })


@app.route("/health")
def health():
    return jsonify({
        "status": "healthy",
        "service": "NyobaAI Upscaler (Adobe Stock Bulk Engine)",
        "timestamp": int(time.time()),
        "active_batches": len(list(STORAGE_DIR.glob("*")))
    })


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8105, debug=False)
