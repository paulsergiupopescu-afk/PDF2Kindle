#!/usr/bin/env python3
"""Second-opinion OCR for build.py: two Tesseract readings of every page.

    python ocr_pass.py SCAN.pdf OUT_DIR

Writes pNNN.eng.tsv (English) and pNNN.ind.tsv (English + Punjabi + Hindi,
for the Gurmukhi and Devanagari in notes and glossary). Needs tesseract with
the eng, pan and hin language packs. Restartable: finished pages are skipped.
"""

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pymupdf

pdf, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)
n = len(pymupdf.open(pdf))


def job(i: int) -> int:
    png = f"{out}/p{i:03d}.png"
    if not os.path.exists(f"{out}/p{i:03d}.ind.tsv"):
        pymupdf.open(pdf)[i].get_pixmap(dpi=300, colorspace=pymupdf.csGRAY).save(png)
        env = dict(os.environ, OMP_THREAD_LIMIT="1")
        subprocess.run(["tesseract", png, f"{out}/p{i:03d}.eng", "-l", "eng", "--psm", "4", "tsv"],
                       capture_output=True, env=env)
        subprocess.run(["tesseract", png, f"{out}/p{i:03d}.ind", "-l", "eng+pan+hin", "--psm", "4", "tsv"],
                       capture_output=True, env=env)
        os.remove(png)
    return i


with ThreadPoolExecutor(os.cpu_count() or 4) as ex:
    for i in ex.map(job, range(n)):
        if i % 25 == 0:
            print(f"page {i}/{n}", flush=True)
print("done")
