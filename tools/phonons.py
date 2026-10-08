#!/usr/bin/env python
"""What the group's phonon and electron-phonon calculations say about a compound.

    tools/env.sh tools/phonons.py agm002077195                 # by id
    tools/env.sh tools/phonons.py agm063329412 --formula CsNdSnBr6 --out entries/<entry>/phonons.json

The calculations (Quantum ESPRESSO, DFPT, PBEsol; the structure is re-relaxed in PBEsol, so
its cell is 1-2 % smaller than the PBE one in energy_runs_pbe) live in $MAD_PHONONS, default
~/nas-data-001/espresso/agm, one folder per structure under <batch>/{done,imag,semi}/, with
the summary read here in summary/:

  done   metal, dynamically stable: electron-phonon coupling lambda, the logarithmic average
         phonon frequency (K) and the Allen-Dynes Tc (K) for mu* = 0.10 ... 0.16
  imag   imaginary phonon frequencies were found: dynamically unstable in the harmonic
         approximation, so the structure as stored would distort. Not proof it does not exist
  semi   semiconductor or insulator: phonons only, no electron-phonon coupling

A structure is matched by its agm id. If the id is absent, every run with the same reduced
formula is listed as "same composition, other structure" with its space group, because the
ids in these folders are often older than the ones in energy_runs_pbe: compare the space
group and cell before quoting one. experiment.csv (measured Tc from SuperCon, mapped to agm
ids) is searched the same way.
"""
import argparse, csv, json, os, pathlib, re

ROOT = pathlib.Path(os.environ.get("MAD_PHONONS", pathlib.Path.home() / "nas-data-001/espresso/agm"))
SUM = ROOT / "summary"


def reduced(f):
    from pymatgen.core import Composition
    try:
        return Composition(f).reduced_formula
    except Exception:
        return None


def load():
    runs = {}
    for name, kind in (("tc_list_filtered.json", "done"), ("imag_list_filtered.json", "imag"),
                       ("semi_list_filtered.json", "semi")):
        f = SUM / name
        if f.exists():
            for mid, d in json.loads(f.read_text()).items():
                runs[mid] = dict(d, kind=kind)
    extra = json.loads((SUM / "mcmillan_extra.json").read_text()) if (SUM / "mcmillan_extra.json").exists() else {}
    for mid, d in extra.items():
        if mid in runs:
            runs[mid].update(d)
    return runs


def describe(mid, d):
    run = ROOT / d["run"]
    out = dict(mat_id=mid, kind=d["kind"], run=str(d["run"]))
    if d["kind"] == "done":
        out.update({"lambda": d.get("lambda"), "wlog_K": d.get("wlog"), "w2_K": d.get("w2av"),
                    "Tc_K_AllenDynes": {k.split("_")[-1]: v for k, v in d.items() if k.startswith("Tc_AD_")}
                                       or {"0.100": d.get("Tc")}})
    cif = run / "geo_opt.cif"
    if cif.exists():                              # stored in P1: find the symmetry again
        try:
            from pymatgen.core import Structure
            from pymatgen.symmetry.analyzer import SpacegroupAnalyzer
            s = Structure.from_file(cif)
            sga = SpacegroupAnalyzer(s, 0.01)
            c = sga.get_conventional_standard_structure()
            out["spacegroup"] = sga.get_space_group_number()
            out["conventional_cell"] = [round(float(x), 3) for x in c.lattice.abc + c.lattice.angles]
            out["nsites_primitive"] = len(s)
        except Exception as e:
            out["spacegroup"] = None
    dos = run / "matdyn.dos"
    if dos.exists():
        w = [float(l.split()[0]) for l in dos.read_text().splitlines()[1:] if l.split() and float(l.split()[1]) > 1e-6]
        if w:
            out["phonon_max_cm1"], out["phonon_min_cm1"] = round(max(w), 1), round(min(w), 1)
    if run.exists():
        have = {p.name for p in run.iterdir()}
        out["files"] = sorted(n for n in have if n in ("ph-bs.pdf", "el-bs.pdf", "matdyn.dos", "geo_opt.cif", "lambda",
                                                         "Eliashberg.dat", "a2F.dos10") or n.endswith(".pdf"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--formula", default=None)
    ap.add_argument("--out", default=None)
    o = ap.parse_args()
    if not SUM.exists():
        raise SystemExit(f"no phonon summary at {SUM}; set MAD_PHONONS")
    runs = load()
    res = dict(source=str(ROOT), method="Quantum ESPRESSO, DFPT, PBEsol", n_runs=len(runs), exact=[], same_composition=[], experiment=[])
    for mid in o.ids:
        if mid in runs:
            res["exact"].append(describe(mid, runs[mid]))
    want = reduced(o.formula) if o.formula else None
    if want:
        seen = {e["mat_id"] for e in res["exact"]}
        for mid, d in runs.items():
            name = pathlib.Path(d["run"]).name.rsplit("_agm", 1)[0]
            if mid not in seen and reduced(name) == want:
                res["same_composition"].append(describe(mid, d))
    exp = ROOT / "experiment.csv"
    if exp.exists():
        with exp.open() as fh:
            for r in csv.DictReader(fh):
                if r.get("agm_id") in o.ids or (want and reduced(r.get("formula", "")) == want):
                    res["experiment"].append({k: r[k] for k in ("formula", "spg", "tc", "agm_id", "title", "journal", "doi", "year") if k in r})
    txt = json.dumps(res, indent=1)
    if o.out:
        pathlib.Path(o.out).write_text(txt)
    print(txt)


if __name__ == "__main__":
    main()
