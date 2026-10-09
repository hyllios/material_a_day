#!/usr/bin/env python
"""The image for the post: one view of the structure, with a margin.

    tools/env.sh tools/card.py entries/<entry> [--view top] [--light | --dark]

It renders the chosen view (default: top, down the c axis) with tools/picture.py and
writes post_image.png on a transparent background. A site that flattens transparent
PNGs chooses the background itself; --light (white) and --dark fix it instead, and the
result is then opaque.
"""
import argparse, pathlib, subprocess, sys, tempfile
from PIL import Image

ap = argparse.ArgumentParser()
ap.add_argument("entry")
ap.add_argument("--view", default="top")
ap.add_argument("--light", action="store_true", help="opaque, on white")
ap.add_argument("--dark", action="store_true", help="opaque, on the dark of a GitHub page")
ap.add_argument("--rep", nargs=3, default=None, help="block to show, passed to picture.py")
ap.add_argument("--bonds", default=None, help="passed to picture.py")
ap.add_argument("--poly", default=None, help="passed to picture.py")
o = ap.parse_args()
entry = pathlib.Path(o.entry)
cif = next(entry.glob("*.cif"))
with tempfile.TemporaryDirectory() as tmp:
    png = pathlib.Path(tmp) / "view.png"
    subprocess.run([sys.executable, str(pathlib.Path(__file__).with_name("picture.py")), str(cif),
                    "--views", o.view, "--out", str(png)] + (["--rep"] + o.rep if o.rep else [])
               + (["--bonds", o.bonds] if o.bonds else []) + (["--poly", o.poly] if o.poly else []), check=True, capture_output=True)
    im = Image.open(png).convert("RGBA")
pad = 80
card = Image.new("RGBA", (im.width + 2 * pad, im.height + 2 * pad),
                 (255, 255, 255, 255) if o.light else (13, 17, 23, 255) if o.dark else (0, 0, 0, 0))
card.alpha_composite(im, (pad, pad))
dest = entry / "post_image.png"
(card.convert("RGB") if o.light or o.dark else card).save(dest)
print(dest, card.size)
