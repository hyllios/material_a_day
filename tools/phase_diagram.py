#!/usr/bin/env python
"""The phase diagram around a compound, from the PBE hull, as a picture and a table.

    tools/env.sh tools/phase_diagram.py CsNdSnBr6 entries/2026-10-08-CsNdSnBr6
    tools/env.sh tools/phase_diagram.py X outdir --corners CsBr,NdBr3,SnBr2

What is drawn depends on how many elements the compound has:
  2  the formation-energy hull along the binary
  3  the ternary triangle with its tie lines
  4+ a pseudo-ternary section through the compound. The corners are three simpler hull
     phases (elements or binaries) that add up to it; of all such triples the one taken
     is the most honest section (fewest tie lines leaving its plane), then the fullest
     (CsBr-NdBr3-SnBr2 for CsNdSnBr6, not Br-CsSn-Nd). `--corners` overrides the choice.

A section shows only the phases that lie in its plane. It is a true pseudo-ternary only
if no tie line leaves the plane; the script checks every triangle against the full hull
and says so when one fails.

Writes phase_diagram_light.png, phase_diagram_dark.png (transparent, ink chosen for each
page colour) and phase_diagram.json, which is the table behind the picture.
"""
import argparse, itertools, json, pathlib, re, sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pymatgen.core import Composition
from scipy.spatial import ConvexHull
import db

TABLE = "energy_runs_pbe"
INK = {"light": dict(text="#0b0b0b", second="#52514e", line="#898781", mark="#52514e", accent="#2a78d6"),
       "dark": dict(text="#ffffff", second="#c3c2b7", line="#898781", mark="#c3c2b7", accent="#3987e5")}


def pretty(f):
    return re.sub(r"(\d+)", r"$_{\1}$", f)


def hull_phases(elements):
    """Lowest hull entry per composition in the chemical system, elements included."""
    _, rows = db.q(f"""select formula, min(e_form), (array_agg(mat_id order by e_form))[1]
                       from {TABLE} where elements <@ %s::varchar[] and e_above_hull <= 0
                       group by formula""", (list(elements),))
    out = {}
    for f, e, mid in rows:
        c = Composition(f)
        out[c.reduced_formula] = dict(comp=c.reduced_composition, e_form=float(e or 0.0), mat_id=mid)
    for el in elements:
        out.setdefault(el, dict(comp=Composition(el), e_form=0.0, mat_id=None))
    return out


def vec(comp, els):
    return np.array([comp[e] for e in els], float)


def coords(comp, corners, els):
    """Mole fractions of the corner compounds that make this composition, or None."""
    A = np.array([vec(c, els) for c in corners]).T
    x, res, rank, _ = np.linalg.lstsq(A, vec(comp, els), rcond=None)
    if np.abs(A @ x - vec(comp, els)).max() > 1e-6 or x.min() < -1e-8 or x.sum() <= 0:
        return None
    return np.clip(x, 0, None) / x.sum(), x.sum()


def choose_corners(target, phases, els, full):
    """Three element-or-binary corners spanning a plane through the target.

    Ranked by how honest the section is (fewest tie lines that leave the plane), then by
    how much it shows (most hull phases in the plane), then by the simplest corners.
    """
    simple = [f for f, p in phases.items() if len(p["comp"]) <= 2 and f != target.reduced_formula]
    best = None
    for trio in itertools.combinations(simple, 3):
        cs = [phases[f]["comp"] for f in trio]
        if np.linalg.matrix_rank(np.array([vec(c, els) for c in cs])) < 3:
            continue
        r = coords(target, cs, els)
        if r is None or r[0].min() < 1e-6:
            continue
        pts = section(target, phases, list(trio), els)
        try:
            tie = ties(pts, lower_hull(pts, 3))
        except Exception:
            continue
        broken = sum(t not in full for t in tie)
        score = (-broken, len(pts), -sum(len(c) for c in cs))
        if best is None or score > best[0]:
            best = (score, trio)
    return None if best is None else list(best[1])


def ties(pts, facets):
    return sorted({tuple(sorted((pts[a]["formula"], pts[b]["formula"])))
                   for f in facets for a, b in itertools.combinations(f, 2)})


def section(target, phases, corner_names, els):
    cs = [phases[f]["comp"] for f in corner_names]
    pts = []
    for f, p in phases.items():
        r = coords(p["comp"], cs, els)
        if r is None:
            continue
        x, scale = r
        # energy of one mole of corner mixture at this point, relative to the corners
        n_atoms = sum(x[i] * cs[i].num_atoms for i in range(len(cs)))
        dE = p["e_form"] * n_atoms - sum(x[i] * phases[corner_names[i]]["e_form"] * cs[i].num_atoms
                                         for i in range(len(cs)))
        pts.append(dict(formula=f, x=[round(float(v), 6) for v in x], dE_meV_per_atom=round(1000 * dE / n_atoms, 2),
                        _dE=dE, mat_id=p["mat_id"], target=(f == target.reduced_formula)))
    return pts


def lower_hull(pts, k):
    """Tie lines (k=3: triangles) of the section, from the lower hull of (x, dE)."""
    X = np.array([p["x"][1:] + [p["_dE"]] for p in pts])
    if len(pts) <= k:
        return [tuple(range(len(pts)))]
    hull = ConvexHull(X, qhull_options="QJ")
    return [tuple(sorted(s)) for s, eq in zip(hull.simplices, hull.equations) if eq[-2] < -1e-9]


def full_ties(phases, els):
    """Every pair of phases joined by a tie line on the full hull of the system."""
    names = list(phases)
    X = np.array([list(vec(phases[f]["comp"], els)[1:] / phases[f]["comp"].num_atoms) + [phases[f]["e_form"]]
                  for f in names])
    try:
        hull = ConvexHull(X)
    except Exception:
        hull = ConvexHull(X, qhull_options="QJ")
    pairs = set()
    for s, eq in zip(hull.simplices, hull.equations):
        if eq[-2] < -1e-9:
            pairs.update(tuple(sorted((names[a], names[b]))) for a, b in itertools.combinations(s, 2))
    return pairs


def xy(x):
    return np.array([x[1] + 0.5 * x[2], x[2] * np.sqrt(3) / 2])


def draw_ternary(pts, facets, corner_names, mode, dest):
    ink = INK[mode]
    fig, ax = plt.subplots(figsize=(7.2, 6.6), dpi=220)
    P = np.array([xy(p["x"]) for p in pts])
    edges = set()
    for f in facets:
        for a, b in itertools.combinations(f, 2):
            edges.add((a, b))
    for a, b in edges:
        ax.plot(*zip(P[a], P[b]), color=ink["line"], lw=1.0, zorder=1, solid_capstyle="round")
    centre = np.array([0.5, np.sqrt(3) / 6])
    for p, q in zip(pts, P):
        corner = max(p["x"]) > 1 - 1e-9
        if p["target"]:
            ax.scatter(*q, s=260, color=ink["accent"], edgecolor=ink["text"], linewidth=1.6, zorder=4, marker="*")
        else:
            ax.scatter(*q, s=46, color=ink["mark"], edgecolor="none", zorder=3)
        d = q - centre
        d = d / (np.linalg.norm(d) or 1)
        on_edge = min(p["x"]) < 1e-9
        off = d * (0.055 if on_edge else 0.0) + (np.array([0, 0.045]) if not on_edge else 0)
        ha = "center" if abs(d[0]) < 0.35 or not on_edge else ("left" if d[0] > 0 else "right")
        ax.text(*(q + off), pretty(p["formula"]), ha=ha, va="center", zorder=5,
                fontsize=13 if (corner or p["target"]) else 10.5,
                fontweight="bold" if p["target"] else "normal",
                color=ink["text"] if (corner or p["target"]) else ink["second"])
    ax.set_xlim(-0.2, 1.2)
    ax.set_ylim(-0.12, 1.0)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.savefig(dest, transparent=True, bbox_inches="tight")
    plt.close(fig)


def draw_binary(pts, corner_names, mode, dest):
    ink = INK[mode]
    fig, ax = plt.subplots(figsize=(7.2, 4.4), dpi=220)
    pts = sorted(pts, key=lambda p: p["x"][1])
    xs = [p["x"][1] for p in pts]
    ys = [p["dE_meV_per_atom"] / 1000 for p in pts]
    ax.plot(xs, ys, color=ink["line"], lw=2, zorder=1)
    for p, x, y in zip(pts, xs, ys):
        if p["target"]:
            ax.scatter(x, y, s=260, marker="*", color=ink["accent"], edgecolor=ink["text"], linewidth=1.6, zorder=4)
        else:
            ax.scatter(x, y, s=46, color=ink["mark"], zorder=3)
        ax.annotate(pretty(p["formula"]), (x, y), xytext=(0, -14), textcoords="offset points", ha="center",
                    va="top", fontsize=13 if p["target"] else 10.5, fontweight="bold" if p["target"] else "normal",
                    color=ink["text"] if p["target"] else ink["second"])
    ax.set_xlabel(f"fraction of {corner_names[1]}", color=ink["second"], fontsize=11)
    ax.set_ylabel("formation energy (eV/atom)", color=ink["second"], fontsize=11)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(ink["line"])
    ax.tick_params(colors=ink["second"])
    ax.margins(x=0.06, y=0.25)
    fig.savefig(dest, transparent=True, bbox_inches="tight")
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("formula")
    ap.add_argument("outdir")
    ap.add_argument("--corners", default=None)
    o = ap.parse_args()
    target = Composition(o.formula).reduced_composition
    els = sorted(e.symbol for e in target.elements)
    phases = hull_phases(els)
    if target.reduced_formula not in phases:
        sys.exit(f"{target.reduced_formula} is not on the {TABLE} hull")
    if o.corners:
        corner_names = [Composition(c).reduced_formula for c in o.corners.split(",")]
    elif len(els) <= 3:
        corner_names = els
    else:
        corner_names = choose_corners(target, phases, els, full_ties(phases, els))
        if corner_names is None:
            sys.exit("no pseudo-ternary section through this compound with element or binary corners; "
                     "name three corners with --corners, or describe the neighbours in a table")
    k = len(corner_names)
    pts = section(target, phases, corner_names, els)
    facets = lower_hull(pts, k)
    on = sorted({i for f in facets for i in f})
    hidden = [p["formula"] for i, p in enumerate(pts) if i not in on]
    tie = ties(pts, facets)
    # a section is a true pseudo-ternary only if each of its tie lines is one on the full hull
    full = set() if len(els) <= 3 else full_ties(phases, els)
    broken = [] if len(els) <= 3 else [t for t in tie if t not in full]
    note = "; ".join(x for x in (
        "dropped from the section hull: " + ", ".join(hidden) if hidden else "",
        "tie lines of the section that are not tie lines of the full hull (the equilibrium leaves the "
        "plane there): " + ", ".join("-".join(t) for t in broken) if broken else "") if x)
    out = pathlib.Path(o.outdir)
    for mode in ("light", "dark"):
        dest = out / f"phase_diagram_{mode}.png"
        if k == 3:
            draw_ternary(pts, facets, corner_names, mode, dest)
        else:
            draw_binary(pts, corner_names, mode, dest)
    neighbours = sorted({b if a == target.reduced_formula else a for a, b in tie if target.reduced_formula in (a, b)})
    for p in pts:
        p.pop("_dE")
    res = dict(target=target.reduced_formula, table=TABLE, corners=corner_names, n_system_hull_phases=len(phases),
               phases=sorted(pts, key=lambda p: p["x"], reverse=True), tie_lines=tie,
               target_neighbours=neighbours, note=note)
    (out / "phase_diagram.json").write_text(json.dumps(res, indent=1))
    print(json.dumps({k2: res[k2] for k2 in ("corners", "n_system_hull_phases", "target_neighbours", "note")}, indent=1))
    print("in section:", ", ".join(p["formula"] for p in res["phases"]))


if __name__ == "__main__":
    main()
