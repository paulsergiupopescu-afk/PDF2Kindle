"""Cover and ornaments, drawn rather than scanned.

The cover is modelled on a cloth binding of the period: maroon buckram with
gold blocking -- a double rule frame, the chain border that runs round every
page of the 1960 edition, the ੴ, and the title in blackletter as on its
title page. The chain band is reused as the chapter ornament.
"""

from __future__ import annotations

import io
import math
import os
import random
from typing import Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont

MAROON = (118, 30, 34)
GOLD = (214, 178, 96)
GOLD_DARK = (158, 120, 52)


def _font(fonts_dir: Optional[str], names, size, wght=None):
    for n in names:
        for cand in (n, n.replace("[", "%5B").replace("]", "%5D")):
            p = os.path.join(fonts_dir or "", cand)
            if os.path.exists(p):
                f = ImageFont.truetype(p, size)
                if wght:
                    try:
                        f.set_variation_by_axes([wght] if "Devanagari" not in n else [100, wght])
                    except Exception:
                        pass
                return f
    return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", size)


def _chain(draw: ImageDraw.ImageDraw, x0, y0, x1, y1, link: float, colour, width: int, vertical=False):
    """A row of interlocking oval links between two points."""
    length = (y1 - y0) if vertical else (x1 - x0)
    n = max(1, int(length // link))
    step = length / n
    for k in range(n):
        c = (x0 + (y0 * 0)) if vertical else x0 + step * (k + 0.5)
        if vertical:
            cx, cy = x0, y0 + step * (k + 0.5)
            w, h = (link * 0.36, link * 0.62) if k % 2 == 0 else (link * 0.22, link * 0.62)
        else:
            cx, cy = c, y0
            w, h = (link * 0.62, link * 0.36) if k % 2 == 0 else (link * 0.62, link * 0.22)
        draw.ellipse([cx - w, cy - h, cx + w, cy + h], outline=colour, width=width)


def chain_band(width: int = 1200, height: int = 44) -> bytes:
    """Transparent PNG: a centred chain with a fleuron-like knot at its middle."""
    scale = 3
    W, H = width * scale, height * scale
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    col = (122, 70, 60, 255)
    mid = W / 2
    link = 26 * scale
    _chain(d, W * 0.08, H / 2, mid - 60 * scale, H / 2, link, col, int(2.2 * scale))
    _chain(d, mid + 60 * scale, H / 2, W * 0.92, H / 2, link, col, int(2.2 * scale))
    # central knot: a lozenge between two small rings
    r = 11 * scale
    d.polygon([(mid, H / 2 - r * 1.3), (mid + r * 1.3, H / 2), (mid, H / 2 + r * 1.3), (mid - r * 1.3, H / 2)],
              outline=col, width=int(2.4 * scale))
    d.ellipse([mid - r * 0.35, H / 2 - r * 0.35, mid + r * 0.35, H / 2 + r * 0.35], fill=col)
    for sgn in (-1, 1):
        cx = mid + sgn * 38 * scale
        d.ellipse([cx - 7 * scale, H / 2 - 7 * scale, cx + 7 * scale, H / 2 + 7 * scale], outline=col, width=int(2.2 * scale))
    img = img.resize((width, height), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def signature(pdf: str, page: int, rect) -> bytes:
    """A signature cut from the scan, as ink on a transparent ground."""
    import pymupdf
    pix = pymupdf.open(pdf)[page].get_pixmap(dpi=300, clip=pymupdf.Rect(*rect), colorspace=pymupdf.csGRAY)
    g = Image.frombytes("L", (pix.width, pix.height), pix.samples)
    alpha = g.point(lambda v: 255 - v).filter(ImageFilter.GaussianBlur(0.6))
    ink = Image.new("RGBA", g.size, (28, 36, 64, 255))
    ink.putalpha(alpha)
    buf = io.BytesIO()
    ink.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _cloth(W: int, H: int) -> Image.Image:
    """Maroon buckram: fine woven noise plus a soft vignette."""
    random.seed(7)
    base = Image.new("RGB", (W, H), MAROON)
    noise = Image.effect_noise((W, H), 22).convert("L")
    weave = Image.new("L", (W, H), 0)
    wd = ImageDraw.Draw(weave)
    for y in range(0, H, 3):
        wd.line([(0, y), (W, y)], fill=18)
    for x in range(0, W, 3):
        wd.line([(x, 0), (x, H)], fill=10)
    tex = Image.blend(noise, weave, 0.5).filter(ImageFilter.GaussianBlur(0.6))
    dark = Image.new("RGB", (W, H), (40, 6, 10))
    base = Image.composite(dark, base, tex.point(lambda v: int(v * 0.32)))
    vign = Image.new("L", (W, H), 0)
    vd = ImageDraw.Draw(vign)
    for k in range(60):
        a = int(110 * (1 - k / 60) ** 2)
        vd.rectangle([k * 6, k * 6, W - k * 6, H - k * 6], outline=255 - a)
    vign = vign.filter(ImageFilter.GaussianBlur(60))
    return Image.composite(base, Image.new("RGB", (W, H), (30, 4, 8)), vign)


def _centred(d: ImageDraw.ImageDraw, W: int, y: float, text: str, font, fill, spacing: float = 0):
    if spacing:
        widths = [d.textlength(ch, font=font) for ch in text]
        total = sum(widths) + spacing * (len(text) - 1)
        x = (W - total) / 2
        for ch, w in zip(text, widths):
            d.text((x, y), ch, font=font, fill=fill)
            x += w + spacing
        return
    w = d.textlength(text, font=font)
    d.text(((W - w) / 2, y), text, font=font, fill=fill)


def _gold(draw_fn, W: int, H: int, base: Image.Image) -> Image.Image:
    """Draw with a mask, then fill the mask with a gold gradient and a slight emboss."""
    mask = Image.new("L", (W, H), 0)
    draw_fn(ImageDraw.Draw(mask))
    grad = Image.new("RGB", (W, H))
    gd = ImageDraw.Draw(grad)
    for y in range(H):
        t = 0.5 + 0.5 * math.sin(y / H * math.pi * 3.2)
        c = tuple(int(GOLD_DARK[i] + (GOLD[i] - GOLD_DARK[i]) * t) for i in range(3))
        gd.line([(0, y), (W, y)], fill=c)
    shadow = Image.new("RGB", (W, H), (25, 3, 6))
    sm = mask.filter(ImageFilter.GaussianBlur(3)).point(lambda v: int(v * 0.7))
    base = Image.composite(shadow, base, Image.merge("L", [sm]).transform((W, H), Image.AFFINE, (1, 0, -3, 0, 1, -4)))
    return Image.composite(grad, base, mask)


def cover(fonts_dir: Optional[str], W: int = 1600, H: int = 2560) -> bytes:
    img = _cloth(W, H)
    frak = _font(fonts_dir, ["UnifrakturMaguntia-Book.ttf"], 210)
    frak_small = _font(fonts_dir, ["UnifrakturMaguntia-Book.ttf"], 150)
    gar = _font(fonts_dir, ["EBGaramond[wght].ttf"], 64, 500)
    gar_it = _font(fonts_dir, ["EBGaramond-Italic[wght].ttf"], 60, 400)
    gar_big = _font(fonts_dir, ["EBGaramond[wght].ttf"], 84, 600)
    gur = _font(fonts_dir, ["NotoSerifGurmukhi[wght].ttf"], 230, 500)
    orn = _font(fonts_dir, ["EBGaramond[wght].ttf"], 110, 400)

    def frame(d):
        m = 70
        d.rectangle([m, m, W - m, H - m], outline=255, width=10)
        d.rectangle([m + 26, m + 26, W - m - 26, H - m - 26], outline=255, width=4)
        i = m + 70
        _chain(d, i + 20, i, W - i - 20, i, 46, 255, 5)
        _chain(d, i + 20, H - i, W - i - 20, H - i, 46, 255, 5)
        _chain(d, i, i + 20, i, H - i - 20, 46, 255, 5, vertical=True)
        _chain(d, W - i, i + 20, W - i, H - i - 20, 46, 255, 5, vertical=True)
        for cx, cy in ((i, i), (W - i, i), (i, H - i), (W - i, H - i)):
            r = 22
            d.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)], fill=255)

    def text(d):
        _centred(d, W, 330, "ੴ", gur, 255)
        _centred(d, W, 720, "Sri", frak_small, 255)
        _centred(d, W, 900, "Guru Granth", frak, 255)
        _centred(d, W, 1130, "Sahib", frak, 255)
        _centred(d, W, 1420, "❦", orn, 255)
        _centred(d, W, 1610, "ENGLISH VERSION", gar, 255, spacing=14)
        _centred(d, W, 1700, "Volume I", gar_it, 255)
        y = 1880
        d.line([(W / 2 - 260, y), (W / 2 + 260, y)], fill=255, width=4)
        _centred(d, W, 1960, "Translated and annotated by", gar_it, 255)
        _centred(d, W, 2060, "DR. GOPAL SINGH", gar_big, 255, spacing=8)

    img = _gold(frame, W, H, img)
    img = _gold(text, W, H, img)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88, optimize=True, progressive=True)
    return buf.getvalue()


if __name__ == "__main__":
    import sys
    fd = sys.argv[1] if len(sys.argv) > 1 else None
    out = sys.argv[2] if len(sys.argv) > 2 else "cover.jpg"
    with open(out, "wb") as f:
        f.write(cover(fd))
    with open(os.path.splitext(out)[0] + "-chain.png", "wb") as f:
        f.write(chain_band())
