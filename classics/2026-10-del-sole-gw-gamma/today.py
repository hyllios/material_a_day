#!/usr/bin/env python3
"""The 1988 Bechstedt–Del Sole gap correction, applied to the Alexandria hull.

    tools/env.sh classics/2026-10-del-sole-gw-gamma/today.py pull       # -> data/survey/today.pkl
    tools/env.sh classics/2026-10-del-sole-gw-gamma/today.py analyse    # -> today.json
    tools/env.sh classics/2026-10-del-sole-gw-gamma/today.py figures    # -> fig2_{light,dark}.png

F. Bechstedt and R. Del Sole, Phys. Rev. B 38, 7710 (1988), Eq. (26): the correction to an
LDA gap is a screened-exchange difference between the nonmetal and an electron gas of the
same density,

    Delta = (e^2/eps0) q_TF (2/pi) int_0^inf dt/(1+t^2) [(1-a)/2 f(q_TF r_A t) + (1+a)/2 f(q_TF r_B t)]^2
    f(t)  = (1 - 10/3 t^2 + t^4) / (1 + t^2)^6,     r = a_lat / (4 pi beta),

with q_TF the Thomas-Fermi wave number of the valence electrons, eps0 the electronic
dielectric constant, a the polarity and r the radius of the p orbitals at the top of the
valence band (beta = 1.7 for group IV). The paper derives it for the diamond and
zinc-blende structures. To apply it to every compound we take a = 0 and one radius from
the volume per atom (the zinc-blende relation a_lat = 2 V_atom^(1/3)), and count valence
electrons by group (1, 2, 12 -> 1, 2, 2; 13-18 -> 3-8), so only compounds of s- and
p-block elements (and Zn, Cd, Hg) are used. eps0 is Alexandria's PBE eps_infinity
(dielectric_pbe, with local fields). The comparison is with the MBJ minus PBE gap of the
same compound.
"""
import json, pathlib, pickle, sys
import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
CACHE = ROOT / "data" / "survey" / "today.pkl"
HA = 27.211386
BOHR = 0.529177
C = 7.62


def f(t):
    return (1 - 10 / 3 * t**2 + t**4) / (1 + t**2) ** 6


def delta(n, eps, rA, rB=None, a=0.0):
    """Eq. (26) in eV; n valence electrons per bohr^3, radii in bohr."""
    rB = rA if rB is None else rB
    kf = (3 * np.pi**2 * n) ** (1 / 3)
    q = np.sqrt(4 * kf / np.pi)
    t = np.linspace(0, 60, 60001)
    g = ((1 - a) / 2 * f(q * rA * t) + (1 + a) / 2 * f(q * rB * t)) ** 2 / (1 + t**2)
    return HA * q / eps * 2 / np.pi * np.trapz(g, t)


def check():
    """Table I of the paper, covalent part: C 1.60, Si 0.57, Ge 0.41, alpha-Sn 0.24 eV."""
    out = {}
    for el, a, eps, ref in (("C", 6.740, 5.7, 1.60), ("Si", 10.263, 12.0, 0.57), ("Ge", 10.691, 15.9, 0.41), ("Sn", 12.267, 27.0, 0.24)):
        out[el] = {"paper": ref, "here": round(delta(32 / a**3, eps, a / (4 * np.pi * 1.7)), 3)}
    return out


def valence(el):
    from pymatgen.core import Element
    e = Element(el)
    if e.group in (1, 2):
        return e.group
    if e.group == 12:
        return 2
    if 13 <= e.group <= 18 and el != "He":
        return e.group - 10
    return None


def pull():
    import db
    cols, rows = db.q("""select p.mat_id, p.formula, p.spg, p.band_gap_ind as pbe, m.band_gap_ind as mbj, d.eps_avg,
                                (p.structure->'lattice'->>'volume')::float as vol, p.structure->'sites' as sites
                         from energy_runs_pbe p join energy_runs_mbj m using(mat_id) join dielectric_pbe d using(mat_id)
                         where p.e_above_hull <= 0.0""", ())
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    pickle.dump((cols, rows), open(CACHE, "wb"))
    print(len(rows), "hull compounds with PBE, MBJ and eps_infinity")


def table():
    import pandas as pd
    cols, rows = pickle.load(open(CACHE, "rb"))
    d = pd.DataFrame(rows, columns=cols).sort_values("mat_id").reset_index(drop=True)
    els = d.sites.map(lambda s: [x["species"][0]["element"] for x in s])
    d["natoms"] = els.map(len)
    nv = els.map(lambda e: [valence(x) for x in e])
    d["sp"] = nv.map(lambda v: all(x is not None for x in v))
    d["nval"] = nv.map(lambda v: sum(v) if all(x is not None for x in v) else np.nan)
    d["eps"] = d.eps_avg.astype(float)
    d["vat"] = d.vol / d.natoms
    return d.drop(columns=["sites"])


def analyse():
    d = table()
    keep = d[d.sp & (d.pbe > 0.5) & (d.eps > 1) & (d.eps < 50)].copy()
    n = keep.nval / (keep.vol / BOHR**3)
    r = 2 * (keep.vat / BOHR**3) ** (1 / 3) / (4 * np.pi * 1.7)
    keep["bd"] = [delta(ni, ei, ri) for ni, ei, ri in zip(n, keep.eps, r)]
    keep["dm"] = keep.mbj - keep.pbe
    res = keep.dm - keep.bd
    N = {"check_table_I": check(), "hull_with_pbe_mbj_eps": len(d), "used": len(keep),
         "median_bd": round(keep.bd.median(), 3), "median_mbj_minus_pbe": round(keep.dm.median(), 3),
         "pearson": round(np.corrcoef(keep.bd, keep.dm)[0, 1], 3),
         "spearman": round(keep[["bd", "dm"]].rank().corr().iloc[0, 1], 3),
         "median_abs_diff": round(res.abs().median(), 3),
         "within_0.5_eV": round((res.abs() < 0.5).mean(), 3),
         "slope_through_origin": round(float((keep.bd * keep.dm).sum() / (keep.bd**2).sum()), 3),
         "pearson_with_1_over_eps": round(np.corrcoef(1 / keep.eps, keep.dm)[0, 1], 3)}
    zb = keep[keep.spg.isin([216, 227, 186])]
    N["tetrahedral_binaries"] = json.loads(zb[zb.formula.str.count(r"[A-Z]") <= 2][["formula", "spg", "pbe", "mbj", "eps", "bd"]]
                                         .round(2).sort_values("bd").to_json(orient="records"))
    (HERE / "today.json").write_text(json.dumps(N, indent=1))
    keep[["mat_id", "formula", "spg", "pbe", "mbj", "eps", "bd", "dm"]].to_pickle(CACHE.with_name("today_used.pkl"))
    print(json.dumps({k: v for k, v in N.items() if k != "tetrahedral_binaries"}, indent=1))


def figures():
    import subprocess, tempfile
    import pandas as pd, matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    k = pd.read_pickle(CACHE.with_name("today_used.pkl"))
    N = json.loads((HERE / "today.json").read_text())
    ink, muted, blue = "#0b0b0b", "#898781", "#2a78d6"
    plt.rcParams.update({"font.size": 10, "axes.edgecolor": muted, "axes.labelcolor": ink,
                         "xtick.color": muted, "ytick.color": muted, "text.color": ink})
    fig, ax = plt.subplots(figsize=(5.2, 4.6))
    hb = ax.hexbin(k.bd, k.dm, gridsize=45, extent=(0, 4, 0, 4), mincnt=1, cmap="Blues", bins="log", linewidths=0.2)
    ax.plot([0, 4], [0, 4], color=ink, lw=1, ls="--")
    for r in N["tetrahedral_binaries"]:
        if r["formula"] in ("Si", "C", "SiC", "GaN", "ZnO", "AlN", "ZnS", "BN", "AlP", "GaP", "ZnSe"):
            ax.annotate(r["formula"], (r["bd"], r["mbj"] - r["pbe"]), fontsize=7.5, color=ink,
                        xytext={"ZnSe": (-28, 2), "ZnS": (3, 4)}.get(r["formula"], (3, 3)), textcoords="offset points")
            ax.plot(r["bd"], r["mbj"] - r["pbe"], "o", ms=3.5, mfc="none", mec=ink, mew=0.8)
    ax.set_xlim(0, 4); ax.set_ylim(0, 4)
    ax.set_xlabel("Bechstedt–Del Sole 1988 correction (eV)")
    ax.set_ylabel("MBJ gap − PBE gap in Alexandria (eV)")
    ax.spines[["top", "right"]].set_visible(False)
    cb = fig.colorbar(hb, ax=ax, shrink=0.8, pad=0.02)
    cb.set_label("compounds", color=ink); cb.outline.set_visible(False)
    fig.tight_layout()
    with tempfile.TemporaryDirectory() as t:
        fig.savefig(f"{t}/f.png", dpi=200, bbox_inches="tight", facecolor="white")
        subprocess.run([sys.executable, str(ROOT / "tools" / "plot.py"), f"{t}/f.png", str(HERE / "fig2")], check=True)


if __name__ == "__main__":
    {"pull": pull, "analyse": analyse, "figures": figures, "check": lambda: print(check())}[sys.argv[1]]()
