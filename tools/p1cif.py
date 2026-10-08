#!/usr/bin/env python
"""Print a compact P1 CIF of the primitive cell, for pasting into tugasp_predict / tugaff_relax.

    tools/env.sh tools/p1cif.py entries/<entry>/<formula>.cif
"""
import sys
from pymatgen.core import Structure
s = Structure.from_file(sys.argv[1]).get_primitive_structure()
l = s.lattice
print("data_x\n_symmetry_space_group_name_H-M 'P 1'")
for k, v in zip(("length_a", "length_b", "length_c", "angle_alpha", "angle_beta", "angle_gamma"), l.abc + l.angles):
    print(f"_cell_{k} {v:.5f}")
print("loop_\n_symmetry_equiv_pos_as_xyz\n'x, y, z'\nloop_\n_atom_site_type_symbol\n_atom_site_label\n_atom_site_fract_x\n_atom_site_fract_y\n_atom_site_fract_z")
for i, x in enumerate(s):
    print(f"{x.specie.symbol} {x.specie.symbol}{i} {x.frac_coords[0]:.5f} {x.frac_coords[1]:.5f} {x.frac_coords[2]:.5f}")
