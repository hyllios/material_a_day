# LinkedIn draft — A Property a Month no. 1, density

**A Property a Month #1: density**

Alongside one material a day, one property a month: a single number, computed for every stable compound in our database, and what the whole distribution says. We start with the simplest one. How dense is a typical material?

🔹 The census: 166,133 compounds on the PBE convex hull of the Alexandria database. Median 7.2 g/cm³, about the density of zinc. Nine in ten lie between 3.1 and 13.0.
🔹 What sets it: mass, not packing. Volume per atom varies by a factor of three, mean atomic mass by a factor of five. Heavy atoms are also large atoms, and that cancels half of the spread.
🔹 A formula is nearly enough: give each element one volume, and the density of a compound comes out with a median error of 5 %.
🔹 The extremes: osmium and iridium at 22 g/cm³, and a porous magnesium borohydride at 0.55. The nominal record holder is plutonium, but PBE makes it 11 % too dense; in the measured structures osmium wins.
🔹 The check: for 8,543 compounds with a measured crystal structure, PBE is 3.4 % too light and PBEsol 1.6 % too dense. For chlorides, bromides and iodides PBE is 10 % off.

Full analysis with seven figures and the script: https://github.com/hyllios/material_a_day/tree/main/properties/2026-10-density

Written with Claude (Anthropic) on top of our database, and reviewed by me. Corrections welcome.

#MaterialsScience #DFT #AIforScience

---

**Image:** `fig2_mass_volume_light.png` — every stable compound by atomic mass and volume.

**Notes for review**
- About 200 words in the body.
- The link assumes the folder is published under `properties/`.
