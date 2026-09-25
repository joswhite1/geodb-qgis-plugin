#!/bin/bash
# Reproduce the plugins.qgis.org upload scan against what `git archive` ships.
# The site BLOCKS a version on ANY bandit or detect-secrets finding (one finding
# fails the whole check), and a blocked version cannot be unblocked — only a new
# upload rescans. Run this before packaging; both counts must be 0.
#
# Rule list copied from https://plugins.qgis.org/docs/security-scanning/rules
# (2026-09-25). Re-check that page if the site changes its enabled rules.
#
# Usage: bash scripts/security_scan.sh [ref]   (default: HEAD)
#        needs `bandit` and `detect-secrets` on PATH (pip install into a venv).
set -euo pipefail
REF="${1:-HEAD}"
T=B102,B103,B105,B106,B107,B111,B201,B202,B301,B302,B304,B305,B306,B307,B312,B321,B323,B401,B402,B412,B413,B501,B502,B503,B505,B506,B507,B601,B602,B604,B605,B609,B610,B611,B612,B613,B615,B701,B101,B104,B108,B110,B112,B113,B303,B308,B310,B311,B313,B314,B315,B316,B317,B318,B319,B320,B324,B403,B405,B406,B407,B408,B409,B504,B508,B509,B603,B606,B607,B608,B614,B702,B703,B704,B109,B404,B410,B411
DIR="$(mktemp -d)"
trap 'rm -rf "$DIR"' EXIT
git archive "$REF" | tar -x -C "$DIR"
echo "== bandit (site rule list) on $REF"
bandit -r "$DIR" -t "$T" -f json --quiet 2>/dev/null | python3 -c "
import json,sys
r=json.load(sys.stdin)['results']
print('findings:', len(r))
for x in r: print(' ', x['test_id'], x['filename'][len(sys.argv[1])+1:], x['line_number'], x['issue_text'])
" "$DIR"
echo "== detect-secrets"
(cd "$DIR" && detect-secrets scan --all-files --exclude-files 'metadata\.txt' --exclude-files '\.secrets\.baseline') | python3 -c "
import json,sys
r=json.load(sys.stdin)['results']
print('findings:', sum(len(v) for v in r.values()))
for f,v in r.items():
    for x in v: print(' ', f, x['line_number'], x['type'])
"
