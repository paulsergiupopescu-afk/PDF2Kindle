"""Local web app: FastAPI backend + static TypeScript/React frontend.

Run with ``pdf2kindle serve`` and open http://127.0.0.1:8000.
"""

from __future__ import annotations

import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Dict

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .convert import ConvertOptions, convert_pdf

_ROOT = Path(__file__).resolve().parent.parent
_DIST = _ROOT / "web" / "dist"
_WORK = Path(tempfile.gettempdir()) / "pdf2kindle"
_WORK.mkdir(parents=True, exist_ok=True)

# job id -> (epub path, download filename, stats dict)
_JOBS: Dict[str, dict] = {}

app = FastAPI(title="pdf2kindle", version="0.1.0")


def _safe_stem(name: str) -> str:
    stem = os.path.splitext(os.path.basename(name))[0]
    stem = re.sub(r"[^\w\-. ]+", "_", stem).strip() or "book"
    return stem


@app.post("/api/convert")
async def api_convert(
    file: UploadFile = File(...),
    title: str = Form(""),
    author: str = Form(""),
    lang: str = Form("en"),
    ocr: str = Form("auto"),
    ocr_lang: str = Form("eng"),
    profile: str = Form("academic"),
) -> JSONResponse:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Please upload a .pdf file.")

    job_id = uuid.uuid4().hex
    job_dir = _WORK / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    stem = _safe_stem(file.filename)
    in_path = job_dir / f"{stem}.pdf"
    out_path = job_dir / f"{stem}.epub"

    data = await file.read()
    in_path.write_bytes(data)

    opts = ConvertOptions(
        title=title, author=author, language=lang, ocr=ocr, ocr_lang=ocr_lang, profile=profile
    )
    try:
        result = convert_pdf(str(in_path), str(out_path), opts)
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"Conversion failed: {exc}")

    stats = {
        "id": job_id,
        "filename": f"{stem}.epub",
        "title": result.title,
        "author": result.author,
        "pages": result.pages,
        "chapters": result.chapters,
        "footnotes": result.footnotes,
        "images": result.images,
        "ocr_pages": result.ocr_pages,
        "warnings": result.warnings,
        "download_url": f"/api/download/{job_id}",
    }
    _JOBS[job_id] = {"path": str(out_path), "filename": f"{stem}.epub", "stats": stats}
    return JSONResponse(stats)


@app.get("/api/download/{job_id}")
async def api_download(job_id: str) -> FileResponse:
    job = _JOBS.get(job_id)
    if not job or not os.path.isfile(job["path"]):
        raise HTTPException(status_code=404, detail="Result not found or expired.")
    return FileResponse(
        job["path"],
        media_type="application/epub+zip",
        filename=job["filename"],
    )


@app.get("/api/health")
async def api_health() -> dict:
    from . import ocr as ocr_mod

    return {"status": "ok", "ocr_available": ocr_mod.is_available()}


_FALLBACK_HTML = """<!doctype html><html><head><meta charset="utf-8">
<title>pdf2kindle</title></head><body style="font-family:sans-serif;max-width:640px;margin:3rem auto">
<h1>pdf2kindle</h1>
<p>The web frontend has not been built yet. Build it with:</p>
<pre>cd web &amp;&amp; npm install &amp;&amp; npm run build</pre>
<p>The API is running: <code>POST /api/convert</code> accepts a PDF upload.</p>
</body></html>"""


if _DIST.is_dir() and (_DIST / "index.html").is_file():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="static")
else:  # pragma: no cover
    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return _FALLBACK_HTML


def run(host: str = "127.0.0.1", port: int = 8000) -> None:
    import uvicorn

    print(f"pdf2kindle web app running at http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")
