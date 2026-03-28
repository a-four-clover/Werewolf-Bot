from __future__ import annotations
from typing import Dict, List, Optional
import json
from pathlib import Path


def load_roles_json() -> Optional[Dict[str, Dict[str, str]]]:
    """Load roles definitions from roles/roles.json in project root or cwd.
    Returns a mapping role_id -> {name, faction} or None on failure.
    """
    try:
        this_dir = Path(__file__).resolve().parents[1]
    except Exception:
        this_dir = Path.cwd()

    paths = [this_dir / 'roles' / 'roles.json', Path.cwd() / 'roles' / 'roles.json']
    for p in paths:
        if p.exists():
            try:
                with p.open('r', encoding='utf-8-sig') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        out: Dict[str, Dict[str, str]] = {}
                        for item in data:
                            rid = item.get('id')
                            if not rid:
                                continue
                            out[rid] = {'name': item.get('name', rid), 'faction': item.get('faction', 'village')}
                        return out
                    elif isinstance(data, dict):
                        return data
            except Exception:
                return None
    return None


def roles_for_count(n: int, allow_third_faction: bool = True) -> List[str]:
    """Return a list of role ids to assign for a given player count.
    The list length should equal n. This function uses a simple heuristic:
    - at least 1 werewolf (n//4)
    - 1 seer if n>=3
    - 1 madman if allow_third_faction and n>=6
    - rest villagers
    """
    if n <= 0:
        return []

    # First, try loading a role_distribution.json (or template) from roles/ directory
    try:
        this_dir = Path(__file__).resolve().parents[1]
    except Exception:
        this_dir = Path.cwd()

    # Build candidate locations for role distribution files. Try multiple likely
    # bases: current working directory and parent directories relative to this file
    candidates = []
    try:
        candidates.append(Path.cwd())
    except Exception:
        pass
    # include this module's directory (repo root) first so files next to src are found
    try:
        candidates.append(this_dir)
    except Exception:
        pass
    # include this module's parents to handle different run contexts
    try:
        for p in this_dir.parents[:3]:
            candidates.append(p)
    except Exception:
        pass

    seen = set()
    for base in candidates:
        for name in ('roles/role_distribution.json', 'roles/role_distribution_template.json', 'role_distribution.json'):
            p = (base / name)
            if str(p) in seen:
                continue
            seen.add(str(p))
            if not p.exists():
                continue
            try:
                with p.open('r', encoding='utf-8-sig') as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        key = str(n)
                        # accept either dict or list entries so the shorthand list
                        # notation in role_distribution.json is supported
                        if key in data and isinstance(data[key], (dict, list)):
                            entry = data[key]
                            # Build counts mapping with flexible parsing
                            counts: Dict[str, int] = {}
                            try:
                                # entry may be dict or list
                                if isinstance(entry, list):
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
                                elif isinstance(entry, dict):
                                    for k, v in entry.items():
                                        try:
                                            rid = str(k)
                                            # support key like 'role:2'
                                            kcount = None
                                            if ':' in rid:
                                                try:
                                                    rid_parts = rid.rsplit(':', 1)
                                                    rid = rid_parts[0]
                                                    kcount = int(rid_parts[1])
                                                except Exception:
                                                    pass
                                            # determine count from value or key-suffix
                                            if v is None or v == '':
                                                if kcount is not None:
                                                    c = kcount
                                                else:
                                                    c = 1
                                            else:
                                                try:
                                                    c = int(v)
                                                except Exception:
                                                    try:
                                                        c = int(str(v))
                                                    except Exception:
                                                        c = 1
                                            counts[rid] = counts.get(rid, 0) + c
                                        except Exception:
                                            continue
                                # Now compute villager auto-fill if needed
                                out: List[str] = []
                                specified_villagers = counts.get('villager', None)
                                non_v_count = sum(c for role, c in counts.items() if role != 'villager')
                                if specified_villagers is None:
                                    vcount = max(0, n - non_v_count)
                                else:
                                    vcount = specified_villagers
                                # append non-villagers
                                for role, c in counts.items():
                                    if role == 'villager':
                                        continue
                                    for _ in range(c):
                                        out.append(role)
                                # append villagers
                                for _ in range(vcount):
                                    out.append('villager')
                            except Exception:
                                out = []
                            # If lengths mismatch, ignore this entry and fallback
                            if len(out) == n:
                                return out
            except Exception:
                # ignore malformed files and continue search
                continue

    # Fallback heuristic
    werewolves = max(1, n // 4)
    roles: List[str] = []
    # assign werewolves
    for _ in range(werewolves):
        roles.append('werewolf')
    # seer
    if n - len(roles) >= 1:
        roles.append('seer')
    # third faction
    if allow_third_faction and n >= 6 and n - len(roles) >= 1:
        roles.append('madman')
    # fill with villagers
    while len(roles) < n:
        roles.append('villager')
    return roles
