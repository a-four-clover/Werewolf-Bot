from typing import List, Tuple


def format_winner_loser_lines(g, winners_ids: List[str]) -> Tuple[List[str], List[str]]:
    """Return (winners_lines, losers_lines) where each is a list of strings ready to join with '\n'.

    Groups players by role according to g.roles insertion order. Players whose role id is not
    present in g.roles are collected into an 'その他' group.
    """
    try:
        role_order = list(getattr(g, 'roles', {}).keys())
    except Exception:
        role_order = []

    winners_map = {rid: [] for rid in role_order}
    losers_map = {rid: [] for rid in role_order}
    other_winners = []
    other_losers = []

    for pid, p in g.players.items():
        try:
            rid = p.role_id or 'unknown'
            robj = g.roles.get(rid) if rid and getattr(g, 'roles', None) else None
            rname = robj.name if robj and getattr(robj, 'name', None) else rid or '不明'
            line = f"・ {p.name} ({rname})"
            if pid in (winners_ids or []):
                if rid in winners_map:
                    winners_map[rid].append(line)
                else:
                    other_winners.append(line)
            else:
                if rid in losers_map:
                    losers_map[rid].append(line)
                else:
                    other_losers.append(line)
        except Exception:
            continue

    winners_lines: List[str] = []
    for rid in role_order:
        items = winners_map.get(rid, [])
        if items:
            name = g.roles.get(rid).name if g.roles.get(rid) else rid
            winners_lines.append(f"{name}:\n" + "\n".join(items))
    if other_winners:
        winners_lines.append("その他:\n" + "\n".join(other_winners))

    losers_lines: List[str] = []
    for rid in role_order:
        items = losers_map.get(rid, [])
        if items:
            name = g.roles.get(rid).name if g.roles.get(rid) else rid
            losers_lines.append(f"{name}:\n" + "\n".join(items))
    if other_losers:
        losers_lines.append("その他:\n" + "\n".join(other_losers))

    return winners_lines, losers_lines
