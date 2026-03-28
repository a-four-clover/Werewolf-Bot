from typing import List, Tuple
import discord


def build_guess_options(g) -> Tuple[List[discord.SelectOption], List[discord.SelectOption]]:
    """Return (alive_options, role_options) for the guess DM UI.

    alive_options: alive players excluding the actor (caller) should be filtered by caller by caller logic.
    role_options: all role ids present in g.roles (order preserved), labeled by display name.
    """
    alive_opts: List[discord.SelectOption] = []
    try:
        for p in g.players.values():
            try:
                alive_opts.append(discord.SelectOption(label=p.name, value=p.id))
            except Exception:
                continue
    except Exception:
        alive_opts = []

    role_opts: List[discord.SelectOption] = []
    try:
        role_ids = list(getattr(g, 'roles', {}).keys())
        for rid in role_ids:
            try:
                # Get role name from roles dict - roles can be dict or object
                role_info = g.roles.get(rid)
                if hasattr(role_info, 'name'):
                    rname = role_info.name
                elif isinstance(role_info, dict) and 'name' in role_info:
                    rname = role_info['name']
                else:
                    rname = str(rid)
                role_opts.append(discord.SelectOption(label=rname, value=rid))
            except Exception:
                continue
    except Exception:
        role_opts = []

    return alive_opts, role_opts
