#!/usr/bin/env python
"""A ray-traced picture of a structure for the post, on a transparent background.

    tools/env.sh tools/picture.py entries/<entry>/<formula>.cif
    tools/env.sh tools/picture.py X.cif --rep 2 2 1 --poly Nd,Sn --views top,side

Balls, bonds and coordination polyhedra, rendered with POV-Ray. Colours are the usual
element colours with the saturation raised; the legend carries the element symbol inside
its own disc, so the picture reads on a dark page and on a white one.

Bonds are drawn from each cation to the anions in its first shell (anything within
`--shell` times the shortest such distance). With no anion in the formula, bonds are
drawn between atoms closer than 1.15 times the sum of covalent radii and no polyhedra
are made; `--bonds Mo-Mo:2.6` draws only the named pairs, which is the way to show the
one motif that matters in an intermetallic. `--poly` names the centres that get a polyhedron; the default is every cation
with four to eight neighbours. A cation whose nearest anion is farther than `--bond-max`
(3.3 angstrom) is a counter-cation and is drawn as a bare ball. `--poly none` turns them off.
"""
import argparse, colorsys, itertools, pathlib, subprocess, tempfile
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from pymatgen.core import Structure, Element
from scipy.spatial import ConvexHull
from ase.data import atomic_numbers, covalent_radii
from ase.data.colors import jmol_colors

ANIONS = {"N", "O", "F", "P", "S", "Cl", "As", "Se", "Br", "Te", "I"}
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
EDGE = (0.55, 0.58, 0.62)            # a mid grey: visible on black and on white


def colour(sym):
    r, g, b = jmol_colors[atomic_numbers[sym]]
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    if s < 0.08:                                  # a grey element stays grey
        return (r, g, b)
    return colorsys.hls_to_rgb(h, min(max(l, 0.42), 0.60), max(s, 0.85))


def radius(sym, is_anion):
    return 0.42 if is_anion else 0.30 + 0.16 * covalent_radii[atomic_numbers[sym]]


def build(s, rep, shell, poly, bond_max, pairs=None):
    """Atoms inside the displayed block (faces included), bonds, polyhedra, cell edges."""
    anions = [e.symbol for e in s.composition.elements if e.symbol in ANIONS]
    if len(anions) > 1:      # P in a phosphate is a cation: keep only the hardest anions
        chi = {e: Element(e).X for e in anions}
        anions = [e for e in anions if chi[e] > max(chi.values()) - 0.9]
    lat, eps = s.lattice, 1e-3
    atoms = {}                                    # (site, image) -> (symbol, cartesian)
    for i, site in enumerate(s):
        base = site.frac_coords % 1.0
        for sh in itertools.product(*[range(0, rep[k] + 1) for k in range(3)]):
            f = base + sh
            if all(f[k] <= rep[k] + eps for k in range(3)):
                img = tuple(np.round(f - site.frac_coords).astype(int))
                atoms[(i,) + img] = (site.specie.symbol, lat.get_cartesian_coords(f))
    bonds, polys = [], []
    if pairs:                                     # explicit cutoffs: draw these bonds and nothing else
        for a, b in itertools.combinations(list(atoms), 2):
            cut = pairs.get(frozenset((atoms[a][0], atoms[b][0])))
            if cut and np.linalg.norm(atoms[a][1] - atoms[b][1]) < cut:
                bonds.append((a, b))
    elif anions:
        for k, (sym, xyz) in list(atoms.items()):
            if sym in anions:
                continue
            i, img = k[0], np.array(k[1:])
            nn = [n for n in s.get_neighbors(s[i], 4.2) if n.specie.symbol in anions]
            if not nn:
                continue
            d0 = min(n.nn_distance for n in nn)
            if d0 > bond_max:                     # a counter-cation: a ball, no sticks
                continue
            pts = []
            for n in nn:
                if n.nn_distance > shell * d0:
                    continue
                nimg = np.array(n.image).astype(int) + img
                nk = (n.index,) + tuple(int(x) for x in nimg)
                p = lat.get_cartesian_coords(s[n.index].frac_coords + nimg)
                atoms.setdefault(nk, (n.specie.symbol, p))        # complete the shell
                bonds.append((k, nk))
                pts.append(p)
            want = (sym in poly) if poly is not None else 4 <= len(pts) <= 8
            if want and len(pts) >= 4:
                polys.append((sym, np.array(pts)))
    else:
        for a, b in itertools.combinations(list(atoms), 2):
            d = np.linalg.norm(atoms[a][1] - atoms[b][1])
            if d < 1.15 * (covalent_radii[atomic_numbers[atoms[a][0]]]
                           + covalent_radii[atomic_numbers[atoms[b][0]]]):
                bonds.append((a, b))
    idx = list(itertools.product((0, 1), repeat=3))
    corner = [lat.get_cartesian_coords(np.array(c) * rep) for c in idx]
    cell = [(corner[a], corner[b]) for a, b in itertools.combinations(range(8), 2)
            if sum(x != y for x, y in zip(idx[a], idx[b])) == 1]
    return atoms, bonds, polys, cell, set(anions)


def v(p):
    return "<%.4f,%.4f,%.4f>" % tuple(p)


def scene(atoms, bonds, polys, cell, anions, direction, up):
    xyz = np.array([a[1] for a in atoms.values()])
    centre = (xyz.max(0) + xyz.min(0)) / 2
    direction = np.array(direction, float) / np.linalg.norm(direction)
    up = np.array(up, float)
    up = up - up.dot(direction) * direction
    up /= np.linalg.norm(up)
    right = np.cross(up, direction)
    rel = xyz - centre
    w = 2 * np.abs(rel @ right).max() + 2.2
    h = 2 * np.abs(rel @ up).max() + 2.2
    cam = centre - 60 * direction
    out = ["#version 3.7;", "global_settings { assumed_gamma 1.0 max_trace_level 12 }",
           "camera { orthographic location %s direction %s right %s up %s }"
           % (v(cam), v(direction), v(-right * w), v(up * h)),
           "light_source { %s color rgb 1.0 shadowless }" % v(cam + 40 * up + 30 * right),
           "light_source { %s color rgb 0.45 shadowless }" % v(cam - 30 * up - 40 * right),
           "#declare F = finish { ambient 0.22 diffuse 0.72 specular 0.45 roughness 0.02 }",
           "#declare G = finish { ambient 0.35 diffuse 0.6 specular 0.2 roughness 0.05 }"]
    for sym, pts in polys:
        c = colour(sym)
        try:
            hull = ConvexHull(pts)
        except Exception:
            continue
        out.append("mesh {")
        for t in hull.simplices:
            out.append(" triangle { %s, %s, %s }" % tuple(v(pts[i]) for i in t))
        out.append(" texture { pigment { color rgbt <%.3f,%.3f,%.3f,0.55> } finish { G } } }" % c)
        faces = {}
        for q, t in enumerate(hull.simplices):
            for a, b in itertools.combinations(sorted(t), 2):
                faces.setdefault((a, b), []).append(q)
        for (a, b), qs in faces.items():
            # an edge between two coplanar triangles is the diagonal of a flat face: skip it
            if len(qs) == 2 and hull.equations[qs[0]][:3].dot(hull.equations[qs[1]][:3]) > 0.999:
                continue
            out.append("cylinder { %s, %s, 0.035 pigment { color rgb <%.3f,%.3f,%.3f> } finish { G } }"
                       % ((v(pts[a]), v(pts[b])) + tuple(0.6 * x for x in c)))
    for a, b in bonds:
        (sa, pa), (sb, pb) = atoms[a], atoms[b]
        mid = (pa + pb) / 2
        for p, sy in ((pa, sa), (pb, sb)):
            out.append("cylinder { %s, %s, 0.085 pigment { color rgb <%.3f,%.3f,%.3f> } finish { F } }"
                       % ((v(p), v(mid)) + colour(sy)))
    for sym, p in atoms.values():
        out.append("sphere { %s, %.3f pigment { color rgb <%.3f,%.3f,%.3f> } finish { F } }"
                   % ((v(p), radius(sym, sym in anions)) + colour(sym)))
    for a, b in cell:
        out.append("cylinder { %s, %s, 0.03 pigment { color rgb <%.2f,%.2f,%.2f> } "
                   "finish { ambient 0.8 diffuse 0.2 } }" % ((v(a), v(b)) + EDGE))
    return "\n".join(out), w / h


def render(pov, aspect, height):
    with tempfile.TemporaryDirectory() as tmp:
        f = pathlib.Path(tmp) / "s.pov"
        f.write_text(pov)
        png = f.with_suffix(".png")
        r = subprocess.run(["povray", f"+I{f}", f"+O{png}", f"+W{int(height * aspect)}", f"+H{height}",
                            "+UA", "+A0.1", "+AM2", "-D", "+FN"], capture_output=True, cwd=tmp)
        if not png.exists():
            raise SystemExit(r.stderr.decode()[-1500:])
        return Image.open(png).convert("RGBA").copy()


def legend(symbols, height=120):
    d, gap = int(height * 0.72), int(height * 0.35)
    im = Image.new("RGBA", (len(symbols) * (d + gap) + gap, height), (0, 0, 0, 0))
    dr = ImageDraw.Draw(im)
    font = ImageFont.truetype(FONT, int(d * 0.42))
    for k, sym in enumerate(symbols):
        c = colour(sym)
        x0, y0 = gap + k * (d + gap), (height - d) // 2
        dr.ellipse([x0, y0, x0 + d, y0 + d], fill=tuple(int(255 * x) for x in c) + (255,),
                   outline=tuple(int(255 * x) for x in EDGE) + (255,), width=3)
        lum = 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]
        dr.text((x0 + d / 2, y0 + d / 2), sym, font=font, anchor="mm",
                fill=(20, 20, 20, 255) if lum > 0.55 else (255, 255, 255, 255))
    return im


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cif")
    ap.add_argument("--rep", type=int, nargs=3, default=None)
    ap.add_argument("--poly", default=None, help="comma-separated centres, or 'none'")
    ap.add_argument("--shell", type=float, default=1.18)
    ap.add_argument("--bond-max", type=float, default=3.3,
                    help="cations whose nearest anion is farther than this get no bonds")
    ap.add_argument("--bonds", default=None,
                    help="explicit pair cutoffs in angstrom, e.g. Mo-Mo:2.6,Mo-Si:2.8; nothing else is drawn")
    ap.add_argument("--views", default="top,side")
    ap.add_argument("--height", type=int, default=1400)
    ap.add_argument("--out", default=None)
    o = ap.parse_args()
    cif = pathlib.Path(o.cif)
    s = Structure.from_file(cif)
    rep = o.rep or [max(1, round(15 / x)) for x in s.lattice.abc]
    poly = None if o.poly is None else ([] if o.poly == "none" else o.poly.split(","))
    pairs = None
    if o.bonds:
        pairs = {frozenset(k.split("-")): float(v) for k, v in (x.split(":") for x in o.bonds.split(","))}
    atoms, bonds, polys, cell, anions = build(s, rep, o.shell, poly, o.bond_max, pairs)
    a, b, c = s.lattice.matrix
    n = np.cross(a, b) / np.linalg.norm(np.cross(a, b))          # the layer normal
    inplane = np.cross(n, a) / np.linalg.norm(a)
    views = {"top": (-n, inplane),                               # down c
             "side": (-inplane - 0.10 * n, n),                   # along the layers, tipped a little
             "a": (-a, n), "b": (-b, n),
             "oblique": (-(a / np.linalg.norm(a) + 0.38 * inplane / np.linalg.norm(inplane) + 0.28 * n), n)}
    panels = []
    for name in o.views.split(","):
        pov, aspect = scene(atoms, bonds, polys, cell, anions, *views[name])
        panels.append(render(pov, aspect, o.height))
    order = sorted({sym for sym, _ in atoms.values()}, key=lambda e: (e in anions, Element(e).X))
    leg = legend(order)
    pad = 60
    W = sum(p.width for p in panels) + pad * (len(panels) - 1)
    out = Image.new("RGBA", (max(W, leg.width), o.height + leg.height + 20), (0, 0, 0, 0))
    x = (out.width - W) // 2
    for p in panels:
        out.alpha_composite(p, (x, 0))
        x += p.width + pad
    out.alpha_composite(leg, ((out.width - leg.width) // 2, o.height + 20))
    dest = pathlib.Path(o.out) if o.out else cif.with_name("structure.png")
    out.save(dest)
    print(dest, out.size, f"{len(atoms)} atoms, {len(bonds)} bonds, {len(polys)} polyhedra")


if __name__ == "__main__":
    main()
