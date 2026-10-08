#!/usr/bin/env python
"""Everything an entry needs from the relaxed structure, written into the entry folder.

    tools/env.sh tools/structure.py agm063329412 entries/2026-10-08-CsNdSnBr6

Writes <formula>.cif (symmetrised conventional cell) and structure.json: cell, density,
volume per formula unit, the coordination shell of every inequivalent site, the shortest
cation-cation distances, and the strongest Cu K-alpha powder lines. PBE cells run
0.5-2 % long, so measured lines sit at slightly higher angle than these.
"""
import json, pathlib, sys
import numpy as np
import db
from pymatgen.core import Structure
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer
from pymatgen.analysis.diffraction.xrd import XRDCalculator
from pymatgen.analysis.local_env import CrystalNN

mat_id, outdir = sys.argv[1], pathlib.Path(sys.argv[2])
table = sys.argv[3] if len(sys.argv) > 3 else "energy_runs_pbe"
outdir.mkdir(parents=True, exist_ok=True)
_, r = db.q(f"select structure, formula, band_gap_ind, band_gap_dir, total_mag, e_form, "
            f"e_above_hull, e_phase_separation from {table} where mat_id=%s", (mat_id,))
d = r[0][0]
s = Structure.from_dict(json.loads(d) if isinstance(d, str) else d)
sga = SpacegroupAnalyzer(s, 0.01)
s = sga.get_conventional_standard_structure()
sga = SpacegroupAnalyzer(s, 0.01)
sym = sga.get_symmetrized_structure()
formula = s.composition.reduced_formula
nfu = s.composition.get_reduced_composition_and_factor()[1]
s.to(filename=str(outdir / f"{formula}.cif"), symprec=0.01)

sites = []
for group, wy in zip(sym.equivalent_indices, sym.wyckoff_symbols):
    i = group[0]
    try:
        nn = CrystalNN().get_nn_info(s, i)
        shell = sorted((round(float(s[i].distance(n["site"])), 3), n["site"].specie.symbol) for n in nn)
    except Exception:
        shell = []
    sites.append(dict(element=s[i].specie.symbol, wyckoff=wy,
                      frac=[round(float(x), 4) for x in s[i].frac_coords],
                      cn=len(shell), shell=shell))
p = XRDCalculator("CuKa").get_pattern(s, two_theta_range=(5, 70))
lines = sorted(zip(p.y, p.x, p.hkls), reverse=True)[:15]
out = dict(mat_id=mat_id, table=table, formula=formula, spacegroup=sga.get_space_group_symbol(),
           number=sga.get_space_group_number(), Z=int(nfu),
           cell=[round(float(x), 4) for x in s.lattice.abc + s.lattice.angles],
           volume_per_fu=round(s.volume / nfu, 2), density=round(float(s.density), 3),
           sites=sites,
           xrd_CuKa=[dict(two_theta=round(float(x), 2), I=round(float(y), 1),
                          hkl="".join(str(k) for k in h[0]["hkl"]))
                     for y, x, h in sorted(lines, key=lambda t: t[1])],
           db=dict(gap_ind=r[0][2], gap_dir=r[0][3], total_mag=r[0][4], e_form=r[0][5],
                   e_above_hull_meV=None if r[0][6] is None else 1000 * r[0][6],
                   depth_meV=None if r[0][7] is None else -1000 * r[0][7]))
(outdir / "structure.json").write_text(json.dumps(out, indent=1))
print(json.dumps(out, indent=1))
