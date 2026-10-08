#!/bin/bash
# Run a tool with the interpreter and paths this series needs.
#   tools/env.sh tools/structure.py agm063329412 entries/2026-10-08-CsNdSnBr6
# RG_ROOT is the reaction_graph checkout (database credentials live in its .env);
# MAD_PY is a python with numpy>=2, pymatgen, psycopg2.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export RG_ROOT="${RG_ROOT:-/aims_data/miguel/nas-data001/claude/reaction_graph}"
export MAD_PY="${MAD_PY:-$HOME/nas-data-001/software/progs/venv/grace/bin/python}"
export PYTHONPATH="$RG_ROOT/analysis:$RG_ROOT/cuillere-src:$RG_ROOT/agmq-src:$PYTHONPATH"
cd "$HERE" && exec "$MAD_PY" "$@"
