#!/usr/bin/env python
"""Every computed property Alexandria holds for a structure, in one JSON.

    tools/env.sh tools/properties.py agm063329412 [agm... more ids, e.g. an f-free analogue]
    tools/env.sh tools/properties.py agm063329412 agm029293084 --out entries/<entry>/properties.json

Per id, whatever exists of:
  gaps        band gap, indirect and direct, in every table that has the structure (PBE, MBJ, SCAN)
  dielectric  dielectric_pbe: electronic (clamped-ion) dielectric tensor with and without local
              fields, its average, the refractive index, Born effective charges per element, the
              clamped-ion piezoelectric tensor. No ionic contribution: this is eps_infinity, not
              the static constant.
  transport   el_transport_pbe / _mbj: band edges, density-of-states masses, the branch-point
              energy above the valence-band maximum, and on a grid of temperature and doping the
              Seebeck coefficient, sigma/tau, the power factor/tau and the conductivity mass, all
              in the constant-relaxation-time approximation.

Magnetic and open-4f compounds are often missing from the dielectric table, and their PBE
transport numbers describe the misplaced 4f bands (masses of tens to hundreds of electron masses).
Pass the Y, La or Lu analogue as a second id and quote that instead.
"""
import argparse, json, sys
import numpy as np
import db

HA = 27.211386


def one(sql, args):
    cols, rows = db.q(sql, args)
    return dict(zip(cols, rows[0])) if rows else None


def gaps(mid):
    out = {}
    for t in ("energy_runs_pbe", "energy_runs_mbj", "energy_runs_scan"):
        try:
            r = one(f"select formula, spg, band_gap_ind, band_gap_dir, total_mag, dos_ef from {t} where mat_id=%s", (mid,))
        except Exception:
            r = None
        if r:
            out[t.replace("energy_runs_", "")] = r
    return out


def dielectric(mid, symbols):
    r = one("select * from dielectric_pbe where mat_id=%s", (mid,))
    if not r:
        return None
    eps = np.array(r["eps_electronic"])
    born = np.array(r["born"])
    per = {}
    for s, z in zip(symbols, born):
        per.setdefault(s, []).append(np.linalg.eigvalsh((z + z.T) / 2))
    return dict(eps_inf_tensor=np.round(eps, 3).tolist(),
                eps_inf_principal=np.round(np.linalg.eigvalsh(eps), 3).tolist(),
                eps_inf_without_local_fields=np.round(np.linalg.eigvalsh(np.array(r["eps_ipa"])), 3).tolist(),
                eps_inf_avg=round(r["eps_avg"], 3), refractive_index=round(r["refractive_index"], 3),
                born_principal={s: dict(min=round(float(np.min(v)), 2), max=round(float(np.max(v)), 2),
                                        mean_trace_third=round(float(np.mean([x.mean() for x in v])), 2))
                                for s, v in per.items()},
                piezo_clamped_ion_max=round(float(np.abs(np.array(r["piezo_electronic"])).max()), 4),
                asr_violation=r["asr_violation"])


def transport(mid, flavour):
    r = one(f"select * from el_transport_{flavour} where mat_id=%s", (mid,))
    if not r:
        return None
    cols, rows = db.q(f"""select t_k, carrier, n_target, s_mean_uvk, s_uvk, sigma_tau_mean, pf_tau, m_cond, bipolar_risk
                          from el_transport_{flavour}_grid where mat_id=%s order by carrier, t_k, n_target""", (mid,))
    grid = [dict(zip(cols, x)) for x in rows]
    for g in grid:
        g["s_uvk"] = None if g["s_uvk"] is None else [round(v, 1) for v in g["s_uvk"]]
    rd = lambda x, n: None if x is None else round(x, n)          # any of these can be missing
    return dict(gap_from_edges_eV=None if None in (r["cbm_ha"], r["vbm_ha"]) else round((r["cbm_ha"] - r["vbm_ha"]) * HA, 3),
                m_dos_electron=rd(r["m_dos_e"], 2), m_dos_hole=rd(r["m_dos_h"], 2),
                branch_point_minus_vbm_eV=rd(r["bpe_minus_vbm_ev"], 3), bpe_window_ok=r["bpe_window_ok"],
                nkpts_irr=r["nkpts_irr"], grid=grid)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="+")
    ap.add_argument("--out", default=None)
    o = ap.parse_args()
    res = {}
    for mid in o.ids:
        r = one("select formula, structure from energy_runs_pbe where mat_id=%s", (mid,))
        if not r:
            res[mid] = None
            continue
        st = r["structure"] if isinstance(r["structure"], dict) else json.loads(r["structure"])
        symbols = [s["species"][0]["element"] for s in st["sites"]]
        res[mid] = dict(formula=r["formula"], gaps=gaps(mid), dielectric=dielectric(mid, symbols),
                        transport_pbe=transport(mid, "pbe"), transport_mbj=transport(mid, "mbj"))
    txt = json.dumps(res, indent=1, default=float)
    if o.out:
        open(o.out, "w").write(txt)
    # a short summary on the terminal
    for mid, r in res.items():
        if not r:
            print(mid, "not in energy_runs_pbe"); continue
        print(f"\n{mid} {r['formula']}")
        print("  gaps:", {k: (v["band_gap_ind"], v["band_gap_dir"]) for k, v in r["gaps"].items()})
        d = r["dielectric"]
        print("  dielectric:", None if not d else {k: d[k] for k in ("eps_inf_principal", "eps_inf_avg", "refractive_index", "born_principal", "piezo_clamped_ion_max")})
        for fl in ("transport_pbe", "transport_mbj"):
            t = r[fl]
            if not t:
                print(f"  {fl}: None"); continue
            print(f"  {fl}:", {k: t[k] for k in ("gap_from_edges_eV", "m_dos_electron", "m_dos_hole", "branch_point_minus_vbm_eV", "bpe_window_ok")})
            for g in t["grid"]:
                if g["t_k"] == 300 and g["n_target"] in (1e19, 1e20):
                    print(f"     {g['carrier']} n={g['n_target']:.0e} T=300  S={g['s_mean_uvk']} uV/K {g['s_uvk']}  sigma/tau={g['sigma_tau_mean']}  PF/tau={g['pf_tau']}  m_cond={g['m_cond']}")


if __name__ == "__main__":
    main()
