# NyobaAI Upscaler (Adobe Stock & Vecteezy Automation)

Web application built with Flask for batch image upscaling, vector tracing (potrace), and automatic metadata generation formatted for Adobe Stock and Vecteezy contributor uploads.

## Features
- **Batch Processing**: Up to 20 images per batch (max 50 MB / file, max 500 MB total).
- **Upscaling & Enhancement**: Sharpening, edge smoothing, noise reduction, and chroma 4:4:4 sRGB export.
- **Vector Tracing**: Automated EPS vector conversion via potrace with presets.
- **Stock Metadata Automation**: AI vision-assisted title, description, and keyword generation exported to ready-to-upload Adobe Stock & Vecteezy CSV files.
- **Auto Packaging**: All results packaged into a single downloadable ZIP.
- **Auto Purge**: Background worker to purge idle batches after 30 minutes.

## Requirements
- Python 3.10+
- `potrace` (for vector EPS generation)
- Dependencies: `pip install -r requirements.txt`

## Running Locally
```bash
python3 app.py
```
App runs by default on port `8105`.
