#!/usr/bin/env python
"""A plot made on white paper, as two transparent pictures: one for a dark page, one for a light one.

    tools/env.sh tools/plot.py <run>/ph-bs.pdf entries/<entry>/phonons
    tools/env.sh tools/plot.py old_plot.png entries/<entry>/bands --dpi 200

Writes <stem>_dark.png and <stem>_light.png. The white of the page becomes transparent
(each pixel is split into an ink colour and a coverage, so line edges stay smooth). In the
dark version every ink is lightened by the same rule: black becomes white, a dark blue a
light blue, and the hue does not change. Embed the pair with a <picture> element, as for
the phase diagram.
"""
import argparse, pathlib, subprocess, tempfile
import numpy as np
from PIL import Image

ap = argparse.ArgumentParser()
ap.add_argument("source", help="a PDF (first page) or an image on a white background")
ap.add_argument("stem", help="output path without _dark.png / _light.png")
ap.add_argument("--dpi", type=int, default=150, help="resolution for a PDF")
o = ap.parse_args()

src = pathlib.Path(o.source)
with tempfile.TemporaryDirectory() as tmp:
    if src.suffix.lower() == ".pdf":
        subprocess.run(["pdftoppm", "-r", str(o.dpi), "-png", "-f", "1", "-singlefile", str(src),
                        str(pathlib.Path(tmp) / "page")], check=True)
        src = pathlib.Path(tmp) / "page.png"
    rgba = np.asarray(Image.open(src).convert("RGBA"), dtype=float) / 255
# flatten onto white first, so a picture that is already transparent is handled too
c = rgba[..., :3] * rgba[..., 3:] + (1 - rgba[..., 3:])
alpha = 1 - c.min(axis=2, keepdims=True)                    # how much ink covers the pixel
ink = np.where(alpha > 0, (c - (1 - alpha)) / np.maximum(alpha, 1e-6), 0)
light = ink + (1 - ink.max(axis=2, keepdims=True))          # lightness mirrored, hue kept
for name, colour in (("light", ink), ("dark", light)):
    out = np.concatenate([colour, alpha], axis=2)
    dest = pathlib.Path(f"{o.stem}_{name}.png")
    Image.fromarray((out * 255).round().astype("uint8"), "RGBA").save(dest)
    print(dest, out.shape[1], "x", out.shape[0])
