#!/usr/bin/env python
"""The image for the post: one view of the structure on a solid background.

    tools/env.sh tools/card.py entries/<entry> [--view top] [--light]

Social networks flatten transparent PNGs unpredictably, so this one is opaque. It renders
the chosen view (default: top, down the c axis) with tools/picture.py and writes
post_image.png on a dark background, or post_image_light.png on white with --light.
"""
import argparse, pathlib, subprocess, sys, tempfile
from PIL import Image

ap = argparse.ArgumentParser()
ap.add_argument("entry")
ap.add_argument("--view", default="top")
ap.add_argument("--light", action="store_true")
o = ap.parse_args()
entry = pathlib.Path(o.entry)
cif = next(entry.glob("*.cif"))
with tempfile.TemporaryDirectory() as tmp:
    png = pathlib.Path(tmp) / "view.png"
    subprocess.run([sys.executable, str(pathlib.Path(__file__).with_name("picture.py")), str(cif),
                    "--views", o.view, "--out", str(png)], check=True, capture_output=True)
    im = Image.open(png).convert("RGBA")
pad = 80
card = Image.new("RGBA", (im.width + 2 * pad, im.height + 2 * pad),
                 (255, 255, 255, 255) if o.light else (13, 17, 23, 255))
card.alpha_composite(im, (pad, pad))
dest = entry / ("post_image_light.png" if o.light else "post_image.png")
card.convert("RGB").save(dest)
print(dest, card.size)
