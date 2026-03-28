"""Generate a Markdown table from roles/role_distribution.json

Usage: python tools/generate_role_table.py
"""
from pathlib import Path
import json
from collections import OrderedDict
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
DROLE = ROOT / 'roles' / 'role_distribution.json'
ROLES_JSON = ROOT / 'roles' / 'roles.json'
OUT = ROOT / 'roles' / 'role_distribution_table.md'

if not DROLE.exists():
    print(f"Role distribution file not found: {DROLE}")
    raise SystemExit(1)

with DROLE.open('r', encoding='utf-8') as f:
    data = json.load(f)

# Load role display names from roles.json (if present)
role_names = {}
role_abbr = {}
if ROLES_JSON.exists():
    try:
        with ROLES_JSON.open('r', encoding='utf-8-sig') as rf:
            rdata = json.load(rf)
            if isinstance(rdata, dict):
                for rid, info in rdata.items():
                    role_names[rid] = info.get('name', rid)
                    if isinstance(info, dict) and info.get('abbr'):
                        role_abbr[rid] = info.get('abbr')
            elif isinstance(rdata, list):
                for item in rdata:
                    if isinstance(item, dict) and item.get('id'):
                        role_names[item['id']] = item.get('name', item['id'])
                        if item.get('abbr'):
                            role_abbr[item['id']] = item.get('abbr')
    except Exception:
        pass


def expand_entry(entry):
    """Given an entry (dict or list), return an OrderedDict role_id->count."""
    counts = OrderedDict()
    if isinstance(entry, dict):
        for k, v in entry.items():
            rid = str(k)
            kcount = None
            if ':' in rid:
                try:
                    rid, s = rid.rsplit(':', 1)
                    kcount = int(s)
                except Exception:
                    pass
            if v is None or v == '':
                c = kcount if kcount is not None else 1
            else:
                try:
                    c = int(v)
                except Exception:
                    try:
                        c = int(str(v))
                    except Exception:
                        c = 1
            counts[rid] = counts.get(rid, 0) + c
    elif isinstance(entry, list):
        for tok in entry:
            try:
                if isinstance(tok, str) and ':' in tok:
                    rid, sc = tok.rsplit(':', 1)
                    try:
                        c = int(sc)
                    except Exception:
                        c = 1
                else:
                    rid = str(tok)
                    c = 1
                counts[rid] = counts.get(rid, 0) + c
            except Exception:
                continue
    return counts


# Build a sorted list of player counts
counts = sorted(int(k) for k in data.keys() if k.isdigit())

# Collect all role ids mentioned in any entry
all_roles = OrderedDict()
for k in counts:
    entry = data.get(str(k))
    if entry is None:
        continue
    ecounts = expand_entry(entry)
    for rid in ecounts.keys():
        all_roles[rid] = None

# Also include any roles explicitly listed in roles.json but not in distributions
for rid in role_names.keys():
    if rid not in all_roles:
        all_roles[rid] = None

all_roles = list(all_roles.keys())

# Build a matrix for table output: header + rows
def make_abbr(rid: str) -> str:
    if rid in role_abbr:
        return role_abbr[rid]
    # fallback: first 2 characters of Japanese name or role id
    name = role_names.get(rid, rid)
    return name[:2]

header = ['Players'] + [make_abbr(rid) for rid in all_roles]
rows = []
for c in counts:
    entry = data.get(str(c), {})
    ecounts = expand_entry(entry)
    specified_v = ecounts.get('villager')
    non_v_count = sum(v for r, v in ecounts.items() if r != 'villager')
    if specified_v is None:
        vcount = max(0, c - non_v_count)
    else:
        vcount = specified_v
    row = [str(c)]
    for rid in all_roles:
        if rid == 'villager':
            row.append(str(vcount))
        else:
            row.append(str(ecounts.get(rid, 0)))
    rows.append(row)

# compute column widths
cols = [header] + rows
def char_display_width(ch: str) -> int:
    # East Asian wide (W) and fullwidth (F) characters are typically width 2
    ea = unicodedata.east_asian_width(ch)
    if ea in ('F', 'W'):
        return 2
    # Combining marks have zero width
    if unicodedata.category(ch).startswith('M'):
        return 0
    return 1


def display_width(s: str) -> int:
    return sum(char_display_width(ch) for ch in str(s))


col_widths = [0] * len(header)
for r in cols:
    for i, cell in enumerate(r):
        l = display_width(cell)
        if l > col_widths[i]:
            col_widths[i] = l

# build markdown table with padded columns
table_lines = []
def pad(cell, w):
    s = str(cell)
    cur = display_width(s)
    if cur >= w:
        return s
    return s + ' ' * (w - cur)

table_lines.append('| ' + ' | '.join(pad(h, col_widths[i]) for i, h in enumerate(header)) + ' |')
table_lines.append('| ' + ' | '.join('-' * col_widths[i] for i in range(len(header))) + ' |')
for r in rows:
    table_lines.append('| ' + ' | '.join(pad(r[i], col_widths[i]) for i in range(len(header))) + ' |')

# append legend mapping abbr -> full name
legend_lines = []
legend_lines.append('\n**凡例**')
for rid in all_roles:
    ab = make_abbr(rid)
    full = role_names.get(rid, rid)
    legend_lines.append(f'- {ab}: {full} (`{rid}`)')

OUT.write_text('\n'.join(table_lines + legend_lines), encoding='utf-8-sig')
print(f'Wrote {OUT} (utf-8-sig)')
