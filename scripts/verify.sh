#!/usr/bin/env bash
# The repository's one verification command, where SeptaCore (SPEC-0013 IN-6) and a
# newcomer both look first. It is .gnomon/ci.sh, unchanged: the gate lives there, beside
# the gnomon surface it serves, and this file only makes it findable.
#
# Exit contract (SeptaCore SPEC-0012 DG-7): 0 passed, 1 a blocking step failed,
# 2 could not measure, 3 an advisory regressed.
exec bash "$(dirname "$0")/../.gnomon/ci.sh" "$@"
