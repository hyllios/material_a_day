#!/usr/bin/env python
"""A Property a Month, no. 1: mass density over the PBE convex hull of Alexandria.

    tools/env.sh properties/2026-10-density/survey.py pull      # database -> data/survey/ (2 min)
    tools/env.sh properties/2026-10-density/survey.py analyse   # -> numbers.json, tables.json
    tools/env.sh properties/2026-10-density/survey.py figures   # -> fig*_light.png, fig*_dark.png

`pull` needs the database; the other two steps read only the cache and data/cod_raw.
Every number quoted in README.md is in numbers.json or tables.json.
"""
import glob, json, os, pathlib, pickle, re, subprocess, sys, warnings
import numpy as np, pandas as pd, scipy.sparse as sp
from scipy.sparse.linalg import lsqr

warnings.filterwarnings("ignore")
HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
CACHE = ROOT / "data" / "survey"
AMU = 1.66053907          # g/cm3 per (amu/A^3)
COLS = ["mat_id", "formula", "nsites", "spg", "volume", "e_above_hull", "e_phase_separation", "gap"]
METAL_GAP = 0.05          # eV; below this PBE gap a phase is counted as a metal
OPEN_F = set("Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Pa U Np Pu".split())
# a compound is named after the most electronegative of these that it contains
ANION = {"O": "oxide", "F": "fluoride", "Cl": "heavier halide", "Br": "heavier halide",
         "I": "heavier halide", "S": "chalcogenide", "Se": "chalcogenide", "Te": "chalcogenide",
         "N": "nitride", "P": "pnictide", "As": "pnictide", "Sb": "pnictide", "H": "hydride",
         "C": "carbide", "B": "boride", "Si": "silicide", "Ge": "germanide"}
NOBLE = {"He", "Ne", "Ar", "Kr", "Xe"}
IN_USE = ["NaI", "CsI", "Bi4Ge3O12", "Lu2SiO5", "Gd2O2S", "CdWO4", "LaBr3", "Lu2O3", "HfO2", "LuTaO4", "Pb", "W"]


# ---------------------------------------------------------------- pull
def pull():
    import db
    CACHE.mkdir(parents=True, exist_ok=True)
    conn = db.connect()

    def get(table, wheres, name):
        rows = []
        for w in wheres:                                   # chunks stay under the statement timeout
            rows += db.q(f"""select mat_id, formula, nsites, spg,
                (structure->'lattice'->>'volume')::float, e_above_hull, e_phase_separation,
                band_gap_ind from {table} where {w}""", conn=conn)[1]
        pd.DataFrame(rows, columns=COLS).to_pickle(CACHE / f"{name}.pkl")
        print(name, len(rows), flush=True)

    e = np.round(np.arange(0, 0.1001, 0.004), 3)
    get("energy_runs_pbe", ["e_above_hull <= 0"] +
        [f"e_above_hull > {a} and e_above_hull <= {b}" for a, b in zip(e[:-1], e[1:])], "pbe")
    s = [(1, 2), (3, 11), (12, 14), (15, 61), (62, 63), (64, 138), (139, 165), (166, 193),
         (194, 215), (216, 224), (225, 230)]
    get("energy_runs_ps", [f"spg between {a} and {b}" for a, b in s], "pbesol")
    get("energy_runs_pbe_mp", [f"e_above_hull <= 0 and spg between {a} and {b}" for a, b in s], "pbe_mp")
    # data/cod_raw holds entries with two to five elements; the elements come in one more request
    subprocess.run(["curl", "-sS", "-m", "600", "-A", "Mozilla/5.0", "-o", str(CACHE / "cod_elements.csv"),
                    "https://www.crystallography.net/cod/result?format=csv&strictmin=1&strictmax=1"], check=True)


# ---------------------------------------------------------------- shared
def load(name):
    """A table with mean atomic mass, volume per atom and density; and the atom-fraction matrix."""
    from pymatgen.core import Composition, Element
    els = [e.symbol for e in Element]
    idx = {e: i for i, e in enumerate(els)}
    mass = np.array([float(Element(e).atomic_mass) for e in els])
    df = pd.read_pickle(CACHE / f"{name}.pkl").sort_values("mat_id").reset_index(drop=True)   # the fit's split must not depend on row order
    uf = df.formula.unique()
    r, c, v = [], [], []
    for i, f in enumerate(uf):
        for el, x in Composition(f).fractional_composition.as_dict().items():
            r.append(i); c.append(idx[el]); v.append(x)
    X = sp.csr_matrix((v, (r, c)), shape=(len(uf), len(els)))[df.formula.map({f: i for i, f in enumerate(uf)}).values]
    df["mbar"] = X @ mass
    df["nel"] = np.asarray((X > 0).sum(1)).ravel()
    df["vat"] = df.volume / df.nsites
    df["rho"] = AMU * df.mbar / df.vat
    return df, X, els


def classify(X, els):
    from pymatgen.core import Element
    en = {e: Element(e).X for e in ANION}
    order = sorted(ANION, key=lambda e: -en[e])
    col = {e: i for i, e in enumerate(els)}
    has = {e: np.asarray(X[:, col[e]].todense()).ravel() > 0 for e in list(ANION) + list(NOBLE)}
    out = np.array(["intermetallic"] * X.shape[0], dtype=object)
    for e in NOBLE:
        out[has[e]] = "other"
    for e in reversed(order):                               # the most electronegative is written last
        out[has[e]] = ANION[e]
    out[np.asarray((X > 0).sum(1)).ravel() == 1] = "element"
    return out


def cod_cells():
    """Measured cells from the raw COD answers: density from the cell contents and the cell volume."""
    from pymatgen.core import Composition
    fs = [f for f in sorted(glob.glob(str(ROOT / "data" / "cod_raw" / "*.csv"))) if os.path.getsize(f) > 2000]
    fs.append(str(CACHE / "cod_elements.csv"))
    cod = pd.concat([pd.read_csv(f, comment="#", dtype=str, on_bad_lines="skip") for f in fs]).drop_duplicates("file")

    def comp(s):
        if not isinstance(s, str):
            return None
        s = s.strip("- ").strip()
        if not s or re.search(r"[.?()]", s):
            return None                                     # fractional occupancy
        try:
            c = Composition(re.sub(r"\bD(\d*)\b", r"H\1", s).replace(" ", ""))
            return c, float(c.weight)
        except Exception:
            return None

    rows = []
    for r in cod.itertuples():
        c, f = comp(r.cellformula), comp(r.formula)
        vol = pd.to_numeric(r.vol, errors="coerce")
        if c is None or f is None or not vol > 0 or c[0].reduced_formula != f[0].reduced_formula:
            continue
        rows.append((r.file, f[0].reduced_formula, pd.to_numeric(r.sgNumber, errors="coerce"),
                     AMU * c[1] / vol, pd.to_numeric(r.cellpressure, errors="coerce")))
    c = pd.DataFrame(rows, columns=["cod", "formula", "spg_x", "rho_x", "P"])
    # cellpressure is in kPa; cells denser than any known solid are errors of the record
    return c[~(c.P > 200) & (c.rho_x < 25)], len(cod)


def q(s, ps=(.01, .05, .25, .5, .75, .95, .99)):
    return {f"p{int(100 * p):02d}": round(float(s.quantile(p)), 4) for p in ps}


def top(d, col, n, big=True, cols=("mat_id", "formula", "spg", "rho", "gap", "cls")):
    t = (d.nlargest if big else d.nsmallest)(n, col)[list(cols)]
    return json.loads(t.round(3).to_json(orient="records"))


# ---------------------------------------------------------------- analyse
def analyse():
    N, T = {}, {}
    df, X, els = load("pbe")
    hm = (df.e_above_hull <= 0).values
    h = df[hm].copy()
    Xh = X[np.where(hm)[0]]
    h["cls"] = classify(Xh, els)
    h["metal"] = h.gap < METAL_GAP
    lr, lm, lv = np.log(h.rho), np.log(h.mbar), np.log(h.vat)

    N["n_pbe_table_within_100meV"] = len(df)
    N["n_hull"] = len(h)
    N["rho"] = dict(q(h.rho), mean=round(h.rho.mean(), 3), geometric_mean=round(float(np.exp(lr.mean())), 3),
                    min=round(h.rho.min(), 3), max=round(h.rho.max(), 3))
    N["vat"], N["mbar"] = q(h.vat), q(h.mbar)
    N["nsites_median"] = float(h.nsites.median())
    N["metal_fraction"] = round(h.metal.mean(), 3)
    N["rho_median_metal"] = round(h[h.metal].rho.median(), 2)
    N["rho_median_nonmetal"] = round(h[~h.metal].rho.median(), 2)
    ionic = h.cls.isin(["oxide", "fluoride", "heavier halide", "chalcogenide"])
    N["metal_vs_gap"] = {k: {"n": int(len(d)), "rho": round(d.rho.median(), 2), "mbar": round(d.mbar.median(), 1),
                             "vat": round(d.vat.median(), 2), "share_O_F_halide_chalcogenide": round(ionic[d.index].mean(), 3)}
                         for k, d in (("no_gap", h[h.metal]), ("gap", h[~h.metal]))}
    N["metal_vs_gap"]["by_class"] = json.loads(h.groupby(["cls", "metal"]).agg(
        n=("rho", "size"), rho=("rho", "median"), mbar=("mbar", "median"), vat=("vat", "median")).round(2)
        .reset_index().to_json(orient="records"))
    N["by_nel"] = json.loads(h.groupby("nel").agg(n=("rho", "size"), median=("rho", "median")).round(2).to_json(orient="index"))
    N["threshold_meV"] = {str(int(1000 * t)): {"n": int((df.e_above_hull <= t).sum()),
                                                "median": round(df[df.e_above_hull <= t].rho.median(), 3),
                                                "p05": round(df[df.e_above_hull <= t].rho.quantile(.05), 3),
                                                "p95": round(df[df.e_above_hull <= t].rho.quantile(.95), 3)}
                          for t in (0, 0.025, 0.05, 0.1)}
    N["variance_log"] = {"rho": round(lr.var(), 4), "mass": round(lm.var(), 4), "volume": round(lv.var(), 4),
                         "covariance": round(float(np.cov(lm, lv)[0, 1]), 4),
                         "corr_mass_volume": round(float(np.corrcoef(lm, lv)[0, 1]), 3),
                         "corr_rho_mass": round(float(np.corrcoef(lr, lm)[0, 1]), 3),
                         "corr_rho_volume": round(float(np.corrcoef(lr, lv)[0, 1]), 3)}

    # classes
    g = h.groupby("cls")
    cl = pd.DataFrame({"n": g.size(), "share": g.size() / len(h), "p05": g.rho.quantile(.05), "p25": g.rho.quantile(.25),
                       "median": g.rho.median(), "p75": g.rho.quantile(.75), "p95": g.rho.quantile(.95),
                       "vat": g.vat.median(), "mbar": g.mbar.median(), "metal": g.metal.mean()}).sort_values("median")
    T["classes"] = json.loads(cl.round(3).to_json(orient="index"))

    # elements: median density of the hull phases that contain each
    Xc = Xh.tocsc()
    el = [(e, len(i), float(np.median(h.rho.values[i]))) for j, e in enumerate(els)
          for i in [Xc[:, j].nonzero()[0]] if len(i) >= 20]
    T["elements"] = {e: {"n": n, "median": round(m, 2)} for e, n, m in el}

    # additive atomic volumes: one volume per element, least squares on four fifths of the hull
    used = np.asarray((Xh > 0).sum(0)).ravel() > 0
    A, y = Xh[:, used], h.vat.values
    test = np.random.default_rng(0).random(len(h)) < 0.2
    v_el = lsqr(A[~test], y[~test])[0]
    h["rho_add"] = AMU * h.mbar / (A @ v_el)
    err = (h.rho_add / h.rho - 1)[test]
    N["additive"] = {"n_test": int(test.sum()), "median_abs_error": round(err.abs().median(), 4),
                     "mean_abs_error": round(err.abs().mean(), 4), "within_10pct": round((err.abs() < .1).mean(), 3),
                     "r2_volume_test": round(1 - ((A @ v_el - y)[test] ** 2).sum() / ((y[test] - y[test].mean()) ** 2).sum(), 3),
                     "median_abs_error_by_class": json.loads((h[test].groupby("cls").apply(
                         lambda d: (d.rho_add / d.rho - 1).abs().median())).round(3).to_json())}
    T["atomic_volumes"] = {e: round(float(v), 2) for e, v in zip(np.array(els)[used], v_el)}
    e1 = h[h.nel == 1]
    v1 = dict(zip([re.sub(r"\d", "", f) for f in e1.formula], e1.vat))
    T["elemental_volumes"] = {e: round(float(v), 2) for e, v in v1.items()}
    ve = np.array([v1.get(e, np.nan) for e in els])
    ok = (np.asarray(Xh[:, np.isnan(ve)].sum(1)).ravel() == 0) & (h.nel > 1).values
    h["v_rel"] = np.where(ok, h.vat.values / np.asarray(Xh @ np.nan_to_num(ve)).ravel(), np.nan)
    N["volume_vs_elements"] = dict(q(h.v_rel.dropna(), (.05, .5, .95)),
                                   by_class=json.loads(h.groupby("cls").v_rel.median().round(3).to_json()))

    # extremes
    comp = h[h.nel > 1]
    T["densest"] = top(h, "rho", 15)
    # phases made only of non-metals are mostly gases or liquids at room temperature: not ranked as light solids
    nonmetal = [els.index(e) for e in list(ANION) + list(NOBLE)]
    h["has_metal"] = np.asarray(Xh[:, nonmetal].sum(1)).ravel() < 1 - 1e-6
    comp = h[h.nel > 1]
    N["n_without_metal"] = int((~h.has_metal).sum())
    T["lightest"] = top(h[h.has_metal], "rho", 15, big=False)
    T["elements_by_density"] = top(e1, "rho", len(e1), cols=("mat_id", "formula", "spg", "nsites", "vat", "rho"))
    T["class_extremes"] = {k: {"densest": top(d, "rho", 4), "lightest": top(d[d.has_metal], "rho", 4, big=False)}
                           for k, d in comp.groupby("cls")}
    T["largest_volume"] = top(h, "vat", 6, cols=("mat_id", "formula", "vat", "rho"))
    T["smallest_volume"] = top(h, "vat", 6, big=False, cols=("mat_id", "formula", "vat", "rho"))
    nof = np.asarray(Xh[:, [els.index(e) for e in OPEN_F]].sum(1)).ravel() == 0
    h["open_f"] = ~nof
    T["gap_front"] = {str(g0): top(h[nof & (h.gap >= g0)], "rho", 4) for g0 in (0.5, 1, 2, 3, 4, 5, 6, 7)}
    N["n_gap_front_pool"] = int((nof & (h.gap >= 0.5)).sum())

    # polymorphs: phases within 100 meV of a hull phase of the same composition
    hr = h.set_index("formula").rho
    o = df[(df.e_above_hull > 0) & df.formula.isin(hr.index)].copy()
    o["dr"] = o.rho / o.formula.map(hr) - 1
    N["polymorphs"] = {"pairs": len(o), "compositions": int(o.formula.nunique()),
                       "less_dense": round((o.dr < 0).mean(), 3), "median_change": round(o.dr.median(), 4),
                       "hull_phase_is_densest": round((o.groupby("formula").dr.max() < 0).mean(), 3),
                       "bins_meV": {f"{int(1000 * a)}-{int(1000 * b)}": {
                           "n": int(len(s)), "less_dense": round((s.dr < 0).mean(), 3),
                           "median_change": round(s.dr.median(), 4), "p25": round(s.dr.quantile(.25), 4),
                           "p75": round(s.dr.quantile(.75), 4)}
                           for a, b in ((0, .01), (.01, .025), (.025, .05), (.05, .1))
                           for s in [o[(o.e_above_hull > a) & (o.e_above_hull <= b)]]}}

    # other functionals, same phase (same id, same number of atoms). PBEsol is energy_runs_ps;
    # energy_runs_scan holds SCAN single points on the same PBEsol cells and adds nothing for a density.
    sc, mp = load("pbesol")[0], load("pbe_mp")[0]
    for name, other in (("pbesol", sc), ("pbe_mp", mp)):
        m = h.merge(other[["mat_id", "vat", "nsites"]], on="mat_id", suffixes=("", "_o"))
        r = (m.vat / m.vat_o)[m.nsites == m.nsites_o]
        N[f"rho_{name}_over_pbe"] = dict(n=len(r), **q(r, (.05, .25, .5, .75, .95)))

    # calibration against measured cells
    c, n_cod = cod_cells()
    m = c.merge(h[["mat_id", "formula", "spg", "rho", "cls", "metal"]], on="formula")
    m["r"] = m.rho / m.rho_x
    same = m[m.spg_x == m.spg]
    k = same.groupby("mat_id").agg(formula=("formula", "first"), r=("r", "median"), n=("r", "size"),
                                   spread=("r", lambda x: x.max() / x.min() - 1), cls=("cls", "first"),
                                   metal=("metal", "first"), rho=("rho", "first"), rho_x=("rho_x", "median"))
    N["calibration"] = {"cod_records": n_cod, "cod_cells_usable": len(c), "cells_matched": len(same),
                        "compounds": len(k), "ratio": q(k.r), "pbe_lighter": round((k.r < 1).mean(), 3),
                        "within_2pct": round((abs(k.r - 1) < .02).mean(), 3),
                        "within_5pct": round((abs(k.r - 1) < .05).mean(), 3),
                        "within_10pct": round((abs(k.r - 1) < .10).mean(), 3),
                        "median_metal": round(k[k.metal].r.median(), 4),
                        "median_nonmetal": round(k[~k.metal].r.median(), 4),
                        "measured_spread": {"compounds_with_several_cells": int((k.n > 1).sum()),
                                            "median": round(k[k.n > 1].spread.median(), 4),
                                            "p90": round(k[k.n > 1].spread.quantile(.9), 4)}}
    kc = k.groupby("cls").r
    T["calibration_classes"] = json.loads(pd.DataFrame({"n": kc.size(), "p05": kc.quantile(.05), "median": kc.median(),
                                                        "p95": kc.quantile(.95)}).sort_values("median").round(3).to_json(orient="index"))
    ks = k.reset_index().merge(sc[["mat_id", "rho", "nsites"]].rename(columns={"rho": "rho_s"}), on="mat_id")
    ks["rs"] = ks.rho_s / ks.rho_x
    N["calibration_pbesol"] = {"compounds": len(ks), "pbe": q(ks.r, (.05, .25, .5, .75, .95)),
                               "pbesol": q(ks.rs, (.05, .25, .5, .75, .95)),
                               "median_abs_error_pbe": round(abs(ks.r - 1).median(), 4),
                               "median_abs_error_pbesol": round(abs(ks.rs - 1).median(), 4)}
    T["calibration_pbesol_classes"] = json.loads(ks.groupby("cls").agg(n=("r", "size"), pbe=("r", "median"), pbesol=("rs", "median")).round(3).to_json(orient="index"))
    ke = k[k.cls == "element"].sort_values("rho")
    T["calibration_elements"] = json.loads(ke[["formula", "rho", "rho_x", "r", "n"]].round(3).to_json(orient="records"))
    N["calibration_elements"] = {"n": len(ke), "ratio": q(ke.r, (.05, .25, .5, .75, .95))}
    T["calibration_outliers_light"] = json.loads(k.nsmallest(25, "r")[["formula", "r", "cls", "rho", "rho_x"]].round(3).to_json(orient="records"))

    # established scintillator hosts and shielding metals: every phase within 100 meV that has a measured cell
    from pymatgen.core import Composition
    ref = {}
    for name in IN_USE:
        f = Composition(name).reduced_formula
        x, cc = df[df.formula == f], c[c.formula == f]
        ref[name] = [{"spg": int(g), "meV_above_hull": round(1000 * r.e_above_hull, 1), "rho": round(r.rho, 2),
                      "gap": round(r.gap, 2), "cod_rho": sorted(cc[cc.spg_x == g].rho_x.round(2))}
                     for g in sorted(set(cc.spg_x.dropna()))
                     for r in [x[x.spg == g].sort_values("e_above_hull").head(1)] if len(r) for r in [r.iloc[0]]]
        hf = h[h.formula == f]
        ref[name].append({"hull_spg": int(hf.spg.iloc[0]) if len(hf) else None,
                          "hull_rho": round(hf.rho.iloc[0], 2) if len(hf) else None,
                          "cod_spg_without_phase": sorted(int(g) for g in set(cc.spg_x.dropna()) - set(x.spg))})
    T["in_use"] = ref
    wide = h[~h.open_f & (h.gap >= 3)]
    N["wide_gap_3eV"] = {"n": len(wide), "denser_than_CdWO4": int((wide.rho > h[h.formula == "CdWO4"].rho.iloc[0]).sum()),
                         "denser_than_8": int((wide.rho > 8).sum()), "denser_than_9": int((wide.rho > 9).sum())}
    T["wide_gap_3eV_densest"] = top(wide, "rho", 20)

    # hull phases with a different space group from every measured cell of the composition
    m["same"] = m.spg_x == m.spg
    a = m.groupby("formula").agg(any_same=("same", "any"), rmax=("r", "max"), rmin=("r", "min"),
                                 cls=("cls", "first"), rho=("rho", "first"), mat_id=("mat_id", "first"), spg=("spg", "first"))
    light = a[~a.any_same & (a.rmax < 0.8)]
    cc = c[c.formula.isin(light.index)].drop_duplicates(["formula", "spg_x"]).rename(columns={"spg_x": "spg"})
    j = cc.merge(df[df.formula.isin(light.index)][["formula", "spg", "rho", "e_above_hull"]], on=["formula", "spg"])
    j = j[(j.rho / j.rho_x > 0.85) & (j.rho / j.rho_x < 1.10)]
    de = 1000 * j.groupby("formula").e_above_hull.min()
    N["hull_vs_measured_structure"] = {
        "compositions_with_measured_cell": len(a), "hull_spg_measured": int(a.any_same.sum()),
        "hull_spg_not_measured": int((~a.any_same).sum()),
        "hull_lighter_than_every_cell_by_10pct": int((~a.any_same & (a.rmax < 0.9)).sum()),
        "hull_lighter_than_every_cell_by_20pct": len(light),
        "hull_denser_than_every_cell_by_10pct": int((~a.any_same & (a.rmin > 1.1)).sum()),
        "light_by_class": json.loads(light.cls.value_counts().to_json()),
        "measured_structure_found_within_100meV": len(de),
        "its_hull_distance_meV": {"p25": round(de.quantile(.25), 1), "median": round(de.median(), 1), "p75": round(de.quantile(.75), 1)}}
    T["hull_lighter_than_measured"] = json.loads(light.join(de.rename("measured_structure_meV")).sort_values("rmax")
                                                 .reset_index().round(3).to_json(orient="records"))

    json.dump(N, open(HERE / "numbers.json", "w"), indent=1)
    json.dump(T, open(HERE / "tables.json", "w"), indent=1)
    pickle.dump({"h": h, "k": k, "ks": ks, "o": o[["e_above_hull", "dr"]]}, open(CACHE / "analysed.pkl", "wb"))
    print(json.dumps(N, indent=1))


# ---------------------------------------------------------------- figures
BLUE, ORANGE, INK, MUTED = "#2a78d6", "#eb6834", "#0b0b0b", "#898781"


def figures():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap, LogNorm
    from pymatgen.core import Element
    plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False,
                         "axes.edgecolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED,
                         "axes.labelcolor": INK, "xtick.labelcolor": INK, "ytick.labelcolor": INK,
                         "axes.linewidth": 0.6, "xtick.major.width": 0.6, "ytick.major.width": 0.6,
                         "xtick.minor.width": 0.4, "ytick.minor.width": 0.4, "legend.frameon": False,
                         "savefig.dpi": 200, "figure.dpi": 200})
    seq = LinearSegmentedColormap.from_list("blue", ["#ffffff", "#cde2fb", "#6da7ec", "#256abf", "#0d366b"])
    D = pickle.load(open(CACHE / "analysed.pkl", "rb"))
    h, k, ks, o = D["h"], D["k"], D["ks"], D["o"]
    T = json.load(open(HERE / "tables.json"))
    df = pd.read_pickle(CACHE / "pbe.pkl")

    def save(fig, name):
        tmp = CACHE / f"{name}.png"
        fig.savefig(tmp, facecolor="white", **({"bbox_inches": "tight", "pad_inches": 0.05} if name[:4] in ("fig3", "fig4") else {}))
        plt.close(fig)
        subprocess.run([sys.executable, str(ROOT / "tools" / "plot.py"), str(tmp), str(HERE / name)], check=True)

    def logticks(ax, axis, vals):
        getattr(ax, f"set_{axis}scale")("log")
        getattr(ax, f"set_{axis}ticks")(vals)
        getattr(ax, f"set_{axis}ticklabels")([f"{v:g}" for v in vals])
        getattr(ax, f"{axis}axis").set_minor_formatter(matplotlib.ticker.NullFormatter())

    # 1 distribution
    fig, ax = plt.subplots(figsize=(7.2, 3.4))
    bins = np.logspace(np.log10(0.4), np.log10(25), 90)
    for d, c, l in ((h[h.metal].rho, BLUE, "no PBE gap (metals)"),
                    (h[~h.metal].rho, ORANGE, "PBE gap (semiconductors and insulators)")):
        ax.hist(d, bins=bins, color=c, alpha=0.45, linewidth=0, label=l)
        ax.hist(d, bins=bins, color=c, histtype="step", linewidth=1.1)
    logticks(ax, "x", [0.5, 1, 2, 5, 10, 20])
    med = h.rho.median()
    ax.axvline(med, color=INK, lw=0.8)
    ax.text(med * 1.03, ax.get_ylim()[1] * 0.97, f"median of all {med:.1f}", va="top", color=INK)
    ax.set_xlabel("density (g cm$^{-3}$, PBE)"); ax.set_ylabel("phases per bin")
    ax.legend(loc="upper left")
    fig.tight_layout(); save(fig, "fig1_distribution")

    # 2 mass against volume
    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    hb = ax.hexbin(h.mbar, h.vat, gridsize=85, xscale="log", yscale="log", bins="log", cmap=seq, mincnt=1, linewidths=0)
    xs = np.array([0.9, 260])
    for r in (0.5, 1, 2, 5, 10, 20):
        ax.plot(xs, AMU * xs / r, color=MUTED, lw=0.5, ls=(0, (4, 3)), zorder=0)
        x = min(240, 118 * r / AMU / 1.07)
        ax.text(x, AMU * x / r * 1.04, f"{r:g}", color=MUTED, fontsize=7.5, rotation=38, ha="right", va="bottom")
    lab = {"Li": "Li", "Mg(BH4)2": "Mg(BH$_4$)$_2$", "Cs": "Cs", "Os": "Os", "Be": "Be", "C": "graphite",
           "LiH": "LiH", "Pu": "Pu", "NiH": "NiH"}
    for f, t in lab.items():
        r = h[h.formula == f].iloc[0]
        ax.plot(r.mbar, r.vat, "o", ms=4, mfc="white", mec=INK, mew=0.9)
        left = f in ("Pu", "Cs")
        ax.annotate(t, (r.mbar, r.vat), xytext=(-5 if left else 5, 4), textcoords="offset points", fontsize=8,
                    color=INK, ha="right" if left else "left")
    logticks(ax, "x", [1, 2, 5, 10, 20, 50, 100, 200]); logticks(ax, "y", [5, 10, 20, 50, 100])
    ax.set_xlim(0.9, 260); ax.set_ylim(4.5, 130)
    ax.set_xlabel("mean atomic mass (u)"); ax.set_ylabel("volume per atom (Å$^3$)")
    ax.text(0.99, 0.02, "dashed: lines of equal density (g cm$^{-3}$)", transform=ax.transAxes, ha="right", color=MUTED, fontsize=8)
    cb = fig.colorbar(hb, ax=ax, pad=0.02, shrink=0.7); cb.set_label("phases per cell"); cb.outline.set_visible(False)
    fig.tight_layout(); save(fig, "fig2_mass_volume")

    # 3 periodic table
    E = T["elements"]
    vals = np.array([v["median"] for v in E.values()])
    norm = matplotlib.colors.Normalize(vals.min(), vals.max())
    fig, ax = plt.subplots(figsize=(7.4, 4.5))
    for s, v in E.items():
        e = Element(s)
        x, y = e.group, e.row
        if e.is_lanthanoid or e.is_actinoid:
            x, y = e.Z - (57 if e.is_lanthanoid else 89) + 3, e.row + 2.4
        ax.add_patch(plt.Rectangle((x, -y - 0.42), 0.94, 0.26, color=seq(0.12 + 0.88 * norm(v["median"])), lw=0))
        ax.add_patch(plt.Rectangle((x, -y - 0.42), 0.94, 0.94, fill=False, ec=MUTED, lw=0.4))
        ax.text(x + 0.47, -y + 0.32, s, ha="center", va="center", fontsize=7.5, color=INK, weight="bold")
        ax.text(x + 0.47, -y + 0.0, f"{v['median']:.1f}", ha="center", va="center", fontsize=6.5, color=INK)
    ax.set_xlim(0.9, 19.1); ax.set_ylim(-10.1, -0.3); ax.set_aspect("equal"); ax.axis("off")
    cax = fig.add_axes([0.27, 0.80, 0.34, 0.025])
    cb = fig.colorbar(matplotlib.cm.ScalarMappable(norm, LinearSegmentedColormap.from_list("b", [seq(0.12), seq(1.0)])), cax=cax, orientation="horizontal")
    cb.set_label("median density (g cm$^{-3}$)", fontsize=7.5)
    cb.outline.set_visible(False); cb.ax.tick_params(labelsize=7)
    save(fig, "fig4_elements")

    # volume per atom in the element and in its compounds
    V, V1 = T["atomic_volumes"], T["elemental_volumes"]
    div = LinearSegmentedColormap.from_list("div", [BLUE, "#f0efec", ORANGE])
    dn = matplotlib.colors.Normalize(-1, 1)                 # log2 of the ratio, a factor of two each way
    fig, ax = plt.subplots(figsize=(9.4, 6.3))

    def cell(x, y, sym, v1, vf):
        if v1 and vf:
            ax.add_patch(plt.Rectangle((x, -y - 0.42), 0.94, 0.22, color=div(dn(np.clip(np.log2(vf / v1), -1, 1))), lw=0))
        ax.add_patch(plt.Rectangle((x, -y - 0.42), 0.94, 0.94, fill=False, ec=MUTED, lw=0.4))
        ax.text(x + 0.47, -y + 0.35, sym, ha="center", va="center", fontsize=7, color=INK, weight="bold")
        ax.text(x + 0.47, -y + 0.13, v1 if isinstance(v1, str) else f"{v1:.1f}" if v1 else "–", ha="center", va="center", fontsize=5.8, color=MUTED)
        ax.text(x + 0.47, -y - 0.08, vf if isinstance(vf, str) else f"{vf:.1f}", ha="center", va="center", fontsize=5.8, color=INK)

    for s in E:
        e = Element(s)
        x, y = e.group, e.row
        if e.is_lanthanoid or e.is_actinoid:
            x, y = e.Z - (57 if e.is_lanthanoid else 89) + 3, e.row + 2.4
        cell(x, y, s, V1.get(s), V[s])
    ax.add_patch(plt.Rectangle((3.2, -1.92), 4.4, 0.94, fill=False, ec=MUTED, lw=0.4))
    ax.text(5.4, -1.15, "symbol", ha="center", va="center", fontsize=7, color=INK, weight="bold")
    ax.text(5.4, -1.37, "Å$^3$ per atom in the element", ha="center", va="center", fontsize=5.8, color=MUTED)
    ax.text(5.4, -1.58, "Å$^3$ per atom fitted in compounds", ha="center", va="center", fontsize=5.8, color=INK)
    ax.set_xlim(0.9, 19.1); ax.set_ylim(-10.1, -0.3); ax.set_aspect("equal"); ax.axis("off")
    cax = fig.add_axes([0.46, 0.76, 0.2, 0.018])
    cb = fig.colorbar(matplotlib.cm.ScalarMappable(dn, div), cax=cax, orientation="horizontal", ticks=[-1, 0, 1])
    cb.ax.set_xticklabels(["half", "same", "double"], fontsize=6.5)
    cb.set_label("volume in compounds relative to the element", fontsize=6.5); cb.outline.set_visible(False)
    cb.ax.xaxis.set_label_position("top")
    save(fig, "fig3_atomic_volumes")

    # classes, and the additive model
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(7.4, 3.7), gridspec_kw={"width_ratios": [1.25, 1]})
    C = {c: v for c, v in T["classes"].items() if c not in ("element", "other")}
    for i, (c, v) in enumerate(C.items()):
        a1.plot([v["p05"], v["p95"]], [i, i], color=BLUE, lw=1.2, solid_capstyle="round")
        a1.plot([v["p25"], v["p75"]], [i, i], color=BLUE, lw=5, solid_capstyle="round")
        a1.plot(v["median"], i, "o", ms=4.5, mfc="white", mec=INK, mew=0.9)
    a1.set_yticks(range(len(C))); a1.set_yticklabels([f"{c}  ({v['n']:,})" for c, v in C.items()])
    a1.tick_params(axis="y", length=0); a1.spines["left"].set_visible(False)
    a1.set_xlabel("density (g cm$^{-3}$, PBE)"); a1.set_xlim(0, 16); a1.grid(axis="x", color="#e4e3df", lw=0.5); a1.set_axisbelow(True)
    a1.set_title("a  by chemistry: median, middle half, 5–95 %", loc="left", fontsize=9)
    hb = a2.hexbin(h.rho_add, h.rho, gridsize=70, xscale="log", yscale="log", bins="log", cmap=seq, mincnt=1, linewidths=0,
                   extent=(np.log10(0.4), np.log10(25), np.log10(0.4), np.log10(25)))
    a2.plot([0.4, 25], [0.4, 25], color=INK, lw=0.6)
    logticks(a2, "x", [0.5, 1, 2, 5, 10, 20]); logticks(a2, "y", [0.5, 1, 2, 5, 10, 20])
    a2.set_xlim(0.4, 25); a2.set_ylim(0.4, 25)
    a2.set_xlabel("density from one volume per element"); a2.set_ylabel("PBE density (g cm$^{-3}$)")
    a2.set_title("b  predicted from the formula alone", loc="left", fontsize=9)
    fig.tight_layout(); save(fig, "fig5_classes_additive")

    # 5 calibration
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(7.4, 3.5), gridspec_kw={"width_ratios": [1, 1.1]})
    b = np.linspace(0.8, 1.12, 81)
    a1.hist(100 * (ks.r - 1), bins=100 * (b - 1), color=BLUE, alpha=0.85, label="PBE", linewidth=0)
    a1.hist(100 * (ks.rs - 1), bins=100 * (b - 1), color=ORANGE, alpha=0.75, label="PBEsol", linewidth=0)
    a1.axvline(0, color=INK, lw=0.7)
    a1.set_xlabel("computed density relative to measured (%)"); a1.set_ylabel("compounds per bin"); a1.legend(loc="upper left")
    a1.set_title(f"a  the same {len(ks):,} compounds", loc="left", fontsize=9)
    R = {c: v for c, v in T["calibration_pbesol_classes"].items() if c not in ("element", "other")}
    R = dict(sorted(R.items(), key=lambda kv: kv[1]["pbe"]))
    for i, (c, v) in enumerate(R.items()):
        a2.plot([100 * (v["pbe"] - 1), 100 * (v["pbesol"] - 1)], [i, i], color=MUTED, lw=0.6, zorder=0)
        a2.plot(100 * (v["pbe"] - 1), i, "o", ms=6, color=BLUE, mec="white", mew=0.8, label="PBE" if i == 0 else None)
        a2.plot(100 * (v["pbesol"] - 1), i, "s", ms=5.5, color=ORANGE, mec="white", mew=0.8, label="PBEsol" if i == 0 else None)
    a2.axvline(0, color=INK, lw=0.7)
    a2.set_yticks(range(len(R))); a2.set_yticklabels([f"{c}  ({v['n']:,})" for c, v in R.items()])
    a2.tick_params(axis="y", length=0); a2.spines["left"].set_visible(False)
    a2.set_xlabel("median error in density (%)"); a2.legend(loc="upper left")
    a2.set_title("b  by chemistry", loc="left", fontsize=9)
    fig.tight_layout(); save(fig, "fig7_calibration")

    # 6 density against gap
    s = h[~h.open_f & (h.gap >= METAL_GAP)]
    fig, ax = plt.subplots(figsize=(7.2, 3.9))
    hb = ax.hexbin(s.gap, s.rho, gridsize=(75, 45), bins="log", cmap=seq, mincnt=1, linewidths=0)
    edges = np.arange(0, 9.01, 0.25)
    env = [s[s.gap >= g].rho.max() for g in edges if (s.gap >= g).any()]
    ax.step(edges[:len(env)], env, where="post", color=INK, lw=0.9)
    for f, t, dx, dy in (("TaGeIr", "TaGeIr", 6, 2), ("ThO2", "ThO$_2$", 6, 3), ("Lu2O3", "Lu$_2$O$_3$", -6, -12), ("AcF3", "AcF$_3$", 6, 3),
                         ("LiLuF4", "LiLuF$_4$", 6, 3), ("Hf2N2O", "Hf$_2$N$_2$O", 6, 3)):
        r = s[s.formula == f].iloc[0]
        ax.plot(r.gap, r.rho, "o", ms=4, mfc="white", mec=INK, mew=0.9)
        ax.annotate(t, (r.gap, r.rho), xytext=(dx, dy), textcoords="offset points", fontsize=8, color=INK)
    ax.plot([], [], "o", ms=4, mfc="white", mec=INK, mew=0.9, label="hull phase, PBE density")
    # hosts in use, and monoclinic HfO2: the measured density at the PBE gap of the measured structure
    for f, g, t, dx, dy in (("NaI", 225, "NaI", 6, -3), ("CsI", 221, "CsI", 6, -3), ("LaBr3", 176, "LaBr$_3$", -32, -3),
                            ("Bi4Ge3O12", 220, "Bi$_4$Ge$_3$O$_{12}$", -58, -11), ("CdWO4", 13, "CdWO$_4$", -40, -3),
                            ("Lu2SiO5", 14, "Lu$_2$SiO$_5$", -30, -13), ("HfO2", 14, "HfO$_2$ (monoclinic)", -12, 7)):
        r = next(x for x in T["in_use"][f] if x.get("spg") == g)
        y = float(np.median(r["cod_rho"]))
        ax.plot(r["gap"], y, "s", ms=5, color=ORANGE, mec="white", mew=0.8, zorder=5)
        ax.annotate(t, (r["gap"], y), xytext=(dx, dy), textcoords="offset points", fontsize=8, color=INK, zorder=5)
    ax.plot([], [], "s", ms=5, color=ORANGE, mec="white", mew=0.8, label="measured density (COD)")
    ax.legend(loc="upper right", fontsize=8)
    ax.set_xlim(0, 9.2); ax.set_ylim(0, 15)
    ax.set_xlabel("PBE band gap (eV)"); ax.set_ylabel("density (g cm$^{-3}$)")
    cb = fig.colorbar(hb, ax=ax, pad=0.02, shrink=0.75); cb.set_label("phases per cell"); cb.outline.set_visible(False)
    fig.tight_layout(); save(fig, "fig6_gap")


if __name__ == "__main__":
    {"pull": pull, "analyse": analyse, "figures": figures}[sys.argv[1]]()
