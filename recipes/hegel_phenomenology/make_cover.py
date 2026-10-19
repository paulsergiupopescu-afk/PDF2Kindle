"""Draw a typographic cover (1600 x 2560) for the study edition."""

from __future__ import annotations

import sys

from PIL import Image, ImageDraw, ImageFont

W, H = 1600, 2560
INK = (236, 229, 214)
DIM = (176, 166, 146)
GROUND = (28, 33, 43)

SERIF = "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf"
SERIF_I = "/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf"
SERIF_B = "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf"


def centered(d: ImageDraw.ImageDraw, y: int, text: str, font, fill) -> int:
    box = d.textbbox((0, 0), text, font=font)
    d.text(((W - (box[2] - box[0])) / 2, y), text, font=font, fill=fill)
    return y + (box[3] - box[1])


def spaced(text: str) -> str:
    return " ".join(text)


def main(out_path: str) -> None:
    img = Image.new("RGB", (W, H), GROUND)
    d = ImageDraw.Draw(img)
    # Frame
    d.rectangle([70, 70, W - 70, H - 70], outline=DIM, width=3)
    d.rectangle([92, 92, W - 92, H - 92], outline=DIM, width=1)

    f_author = ImageFont.truetype(SERIF, 66)
    f_title = ImageFont.truetype(SERIF_B, 150)
    f_title2 = ImageFont.truetype(SERIF, 112)
    f_part = ImageFont.truetype(SERIF_I, 74)
    f_small = ImageFont.truetype(SERIF, 54)
    f_tiny = ImageFont.truetype(SERIF_I, 50)

    y = 330
    y = centered(d, y, spaced("G. W. F. HEGEL"), f_author, DIM) + 170
    y = centered(d, y, "Phenomenology", f_title, INK) + 50
    y = centered(d, y, "of Spirit", f_title2, INK) + 120
    d.line([W / 2 - 230, y, W / 2 + 230, y], fill=DIM, width=3)
    y += 90
    y = centered(d, y, "Volume One", f_part, INK) + 60
    for line in ("Preface · Introduction", "Sensory Certainty · Perception", "¶1–131"):
        y = centered(d, y, line, f_small, DIM) + 34

    # Epigraph from ¶20, between two small diamonds.
    f_quote = ImageFont.truetype(SERIF_I, 84)
    f_cite = ImageFont.truetype(SERIF, 46)
    for cy in (1610, 1930):
        d.polygon([(W / 2, cy - 14), (W / 2 + 14, cy), (W / 2, cy + 14), (W / 2 - 14, cy)], fill=DIM)
    yq = centered(d, 1700, "“The true is the whole.”", f_quote, INK) + 40
    centered(d, yq, "Preface, ¶20", f_cite, DIM)

    y = 2130
    y = centered(d, y, "An Annotated Study Edition", f_tiny, INK) + 40
    centered(d, y, "translated by Michael Inwood", f_tiny, DIM)
    img.save(out_path, "JPEG", quality=88)
    print("cover ->", out_path)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "cover.jpg")
