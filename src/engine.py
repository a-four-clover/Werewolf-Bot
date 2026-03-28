
# --- engine.py (外部化対応版) ---
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum, auto
from typing import Dict, List, Optional
import random
import json
from pathlib import Path
from . import roles

class Phase(Enum):
    LOBBY = auto()
    NIGHT = auto()
    DAY = auto()
    VOTE = auto()
    RESOLUTION = auto()
    CHECK_WIN = auto()
    END = auto()
    CLOSED = auto()  # Force-closed by admin command

@dataclass
class Player:
    id: str
    name: str
    alive: bool = True
    role_id: Optional[str] = None
    oiled: bool = False

@dataclass
class Role:
    id: str
    name: str
    faction: str

@dataclass
class GameSettings:
    max_players: int = 15
    min_players: int = 4
    allow_third_faction: bool = True
    night_duration_sec: int = 120
    day_duration_sec: int = 180
    vote_duration_sec: int = 30
    # whether to enable lovers overlay assignment at game start
    lovers_enabled: bool = False

@dataclass
class Vote:
    from_id: str
    target_id: Optional[str]

class Game:
    def __init__(self, game_id: str, owner_id: str, settings: Optional[GameSettings] = None):
        print(f"DEBUG: Creating new Game instance for game_id={game_id}, owner_id={owner_id}")
        self.game_id = game_id
        self.owner_id = owner_id
        self.settings = settings or GameSettings()
        self.players: Dict[str, Player] = {}
        self.phase: Phase = Phase.LOBBY
        self.roles: Dict[str, Role] = {}
        self.votes: List[Vote] = []
        self.logs: List[str] = []
        # List of winner tokens from last check_win evaluation (e.g. ['werewolf','madman'])
        self.last_winners: List[str] = []
        # List of player ids who are considered winners from last check (includes dead/alive)
        self.last_winner_ids: List[str] = []
        # per-player private messages (these are intended to be delivered via DM by adapter)
        self.private_messages: Dict[str, List[str]] = {}
        # lovers mapping: player_id -> partner_id (both directions)
        self._lovers: Dict[str, str] = {}
        # Sage shields left per player id (sage can use up to 2 shields)
        self._sage_shields_left: Dict[str, int] = {}
        # track which sages used shield this night
        self._sage_shielded_this_night: set = set()
        # Evil Busker fake-death usage tracking
        # _busker_fake_used: set of pids who have consumed their fake-death (one-time)
        # _busker_fake_active: set of pids who are currently fake-dead (temporarily marked not alive)
        # _busker_fake_pending: set of pids who selected fake-death this night and will be processed
        # at end-of-night (so seer and other night actions can still target them during the night)
        self._busker_fake_used: set = set()
        self._busker_fake_pending: set = set()
        self._busker_fake_active: set = set()
        # per-busker fake-death usage counters (support >1 uses if configured)
        self._busker_fake_uses: Dict[str, int] = {}
        # _busker_revived_this_day: set of pids who were revived this day and should be excluded
        # from being targeted by certain actions on the revival turn (adapter can consult this)
        self._busker_revived_this_day: set = set()
        # Optional per-busker blocking counters (kept for backward compatibility but not used by game logic)
        # Note: Evil Busker does NOT block seer actions per current spec.
        self._busker_blocks_left: Dict[str, int] = {}
        # track last-protected target per knight to enforce no two-night repeat
        self._knight_prev_protect: Dict[str, Optional[str]] = {}
        self._ensure_default_roles()
        # track last lynched players (ids) from day resolution for special win checks
        self._last_lynched_ids: List[str] = []
        # track per-player guess uses (number of times guesser has used their ability)
        self._guess_uses: Dict[str, int] = {}
        # legacy set for compatibility/tests: mark players who have used guess at least once
        self._guess_used: set = set()
        # cached role-specific settings loaded from roles/role_settings.json when needed
        self._role_settings_cache: Optional[Dict[str, any]] = None

    def _push_private(self, player_id: str, msg: str):
        """Store a private message for a player; adapter is expected to deliver these via DM."""
        try:
            if player_id not in self.private_messages:
                self.private_messages[player_id] = []
            self.private_messages[player_id].append(msg)
        except Exception:
            # non-fatal — just log
            self.log(f"Failed to enqueue private message for {player_id}: {msg}")

    def _kill_player(self, player_id: str, reason: Optional[str] = None):
        """Kill a player if alive, queue private message, and if they are in a lovers pair, also kill their partner.
        Returns list of actually killed player ids in the order they died (primary first, partner second).
        """
        killed: List[str] = []
        try:
            p = self.players.get(player_id)
            if not p:
                return killed
            if not p.alive:
                return killed
            p.alive = False
            killed.append(player_id)
            # queue a dead dm for the player
            try:
                self._push_private(player_id, {'key': 'dead_dm', 'params': {}})
            except Exception:
                pass
            # if player is lovers-paired, kill partner as well (if alive)
            partner = self._lovers.get(player_id)
            if partner:
                partner_p = self.players.get(partner)
                if partner_p and partner_p.alive:
                    partner_p.alive = False
                    killed.append(partner)
                    # queue partner dead dm and lovers partner notification
                    try:
                        self._push_private(partner, {'key': 'lovers_partner_killed', 'params': {'by': p.name}})
                    except Exception:
                        pass
        except Exception as e:
            self.log(f"_kill_player failed for {player_id}: {e}")
        return killed

    # Guess usage helpers
    def _guess_limit_for_role(self, role_id: Optional[str]) -> int:
        """Return allowed number of guess uses for a role id. Defaults to 1.
        Attempts to read roles/role_settings.json in project roles directory and cache it.
        """
        default = 1
        try:
            # load cache if present
            if isinstance(self._role_settings_cache, dict):
                rs = self._role_settings_cache
            else:
                try:
                    this_dir = Path(__file__).resolve().parents[1]
                except Exception:
                    this_dir = Path.cwd()
                rs_path = this_dir / 'roles' / 'role_settings.json'
                if rs_path.exists():
                    try:
                        rs = json.loads(rs_path.read_text(encoding='utf-8-sig'))
                        if isinstance(rs, dict):
                            self._role_settings_cache = rs
                        else:
                            rs = {}
                    except Exception:
                        rs = {}
                else:
                    rs = {}
            if not role_id:
                return default
            if role_id == 'nice_guesser':
                key = 'nice_guesser_kills'
            elif role_id == 'evil_guesser':
                key = 'evil_guesser_kills'
            else:
                return default
            val = rs.get(key) if isinstance(rs, dict) else None
            if val is None or str(val).strip() == '':
                return default
            try:
                v = int(val)
                return max(0, v)
            except Exception:
                return default
        except Exception:
            return default

    def _busker_fake_limit(self) -> int:
        """Return allowed number of fake-death uses for Evil Busker. Defaults to 1.
        Reads roles/role_settings.json and caches it similarly to guess limits.
        """
        default = 1
        try:
            if isinstance(self._role_settings_cache, dict):
                rs = self._role_settings_cache
            else:
                try:
                    this_dir = Path(__file__).resolve().parents[1]
                except Exception:
                    this_dir = Path.cwd()
                rs_path = this_dir / 'roles' / 'role_settings.json'
                if rs_path.exists():
                    try:
                        rs = json.loads(rs_path.read_text(encoding='utf-8-sig'))
                        if isinstance(rs, dict):
                            self._role_settings_cache = rs
                        else:
                            rs = {}
                    except Exception:
                        rs = {}
                else:
                    rs = {}
            val = rs.get('evil_busker_fake_uses') if isinstance(rs, dict) else None
            if val is None or str(val).strip() == '':
                return default
            try:
                v = int(val)
                return max(0, v)
            except Exception:
                return default
        except Exception:
            return default

    def _guess_uses_get(self, player_id: str) -> int:
        try:
            return int(self._guess_uses.get(player_id, 0))
        except Exception:
            return 0

    def _guess_uses_inc(self, player_id: str) -> int:
        try:
            cur = int(self._guess_uses.get(player_id, 0))
        except Exception:
            cur = 0
        cur += 1
        try:
            self._guess_uses[player_id] = cur
        except Exception:
            pass
        try:
            # maintain legacy set for compatibility
            if getattr(self, '_guess_used', None) is None:
                self._guess_used = set()
            try:
                self._guess_used.add(player_id)
            except Exception:
                try:
                    s = getattr(self, '_guess_used', set())
                    s.add(player_id)
                    self._guess_used = s
                except Exception:
                    pass
        except Exception:
            pass
        return cur

    def log(self, msg: str):
        self.logs.append(msg)

    def _ensure_default_roles(self):
        # Try to load role definitions via roles module
        loaded = roles.load_roles_json()
        if loaded:
            self.roles = {}
            for rid, info in loaded.items():
                name = info.get('name', rid)
                faction = info.get('faction', 'village')
                self.roles[rid] = Role(id=rid, name=name, faction=faction)
            self.log(f"Loaded roles from JSON: {list(self.roles.keys())}")
            return

        # Fallback defaults
        self.roles = {
            "villager": Role(id="villager", name="Villager", faction="village"),
            "werewolf": Role(id="werewolf", name="Werewolf", faction="werewolf"),
            "seer": Role(id="seer", name="Seer", faction="village"),
            # madman (狂人)
            "madman": Role(id="madman", name="Madman", faction="madman"),
            # medium (霊媒師)
            "medium": Role(id="medium", name="Medium", faction="village"),
            # knight (騎士)
            "knight": Role(id="knight", name="Knight", faction="village"),
            # bakery (パン屋)
            "bakery": Role(id="bakery", name="Bakery", faction="village"),
            # jester (てるてる)
            "jester": Role(id="jester", name="Jester", faction="jester"),
            # new guesser roles
            "nice_guesser": Role(id="nice_guesser", name="Nice Guesser", faction="village"),
            "evil_guesser": Role(id="evil_guesser", name="Evil Guesser", faction="werewolf"),
        }
        self.log("Using builtin default roles")

    def _effective_role_id(self, player: Player) -> Optional[str]:
        """Return the underlying role id for a player, ignoring any overlay like 'lovers'.
        Currently we treat the role_id field as the authoritative role; lovers are an overlay mapping
        stored separately in self._lovers, so there's no special 'lovers' role_id. This helper exists
        for clarity and future changes.
        """
        if not player:
            return None
        return player.role_id

    def _load_roles_from_json(self) -> Optional[Dict[str, Dict[str, str]]]:
        # Candidate locations: project_root/roles/roles.json or cwd/roles/roles.json
        try:
            this_dir = Path(__file__).resolve().parents[1]
        except Exception:
            this_dir = Path.cwd()

        paths = [this_dir / 'roles' / 'roles.json', Path.cwd() / 'roles' / 'roles.json']
        for p in paths:
            if p.exists():
                try:
                    # use utf-8-sig to gracefully handle files with BOM
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
                except Exception as e:
                    # record error to logs and return None to fallback
                    self.log(f"Failed to load roles from {p}: {e}")
                    return None
        return None

    # Lobby management
    def join(self, user_id: str, display_name: str) -> bool:
        if self.phase != Phase.LOBBY:
            return False
        if user_id in self.players:
            return False
        if len(self.players) >= self.settings.max_players:
            return False
        self.players[user_id] = Player(id=user_id, name=display_name)
        self.log(f"{display_name} joined")
        return True

    def leave(self, user_id: str) -> bool:
        if user_id not in self.players:
            return False
        p = self.players.pop(user_id)
        self.log(f"{p.name} left")
        return True

    def start(self) -> bool:
        if self.phase != Phase.LOBBY:
            return False
        if len(self.players) < self.settings.min_players:
            return False
        self.assign_roles()
        self.phase = Phase.NIGHT
        self.log("Game started: NIGHT")
        return True

    def assign_roles(self):
        n = len(self.players)
        if n <= 0:
            return
        ids = list(self.players.keys())
        random.shuffle(ids)
        role_list = roles.roles_for_count(n, allow_third_faction=self.settings.allow_third_faction)

        assigned: Dict[str, str] = {}
        # Pop roles from role_list and assign
        for rid in role_list:
            if not ids:
                break
            pid = ids.pop()
            assigned[pid] = rid

        # If any players remain (unlikely), assign villagers
        for pid in ids:
            assigned[pid] = 'villager'

        for pid, role_id in assigned.items():
            self.players[pid].role_id = role_id
            # initialize sage shields for sages
            try:
                if role_id == 'sage':
                    # Read configured sage_limit from roles/role_settings.json if present.
                    sage_limit = 2
                    try:
                        this_dir = Path(__file__).resolve().parents[1]
                    except Exception:
                        this_dir = Path.cwd()
                    try:
                        rs_path = this_dir / 'roles' / 'role_settings.json'
                        if rs_path.exists():
                            try:
                                rs = json.loads(rs_path.read_text(encoding='utf-8-sig'))
                                if isinstance(rs, dict) and 'sage_limit' in rs:
                                    # coerce to int safely, fallback to default 2
                                    try:
                                        sval = rs.get('sage_limit')
                                        sage_limit = int(sval) if sval is not None and str(sval).strip() != '' else sage_limit
                                    except Exception:
                                        sage_limit = 2
                            except Exception:
                                pass
                    except Exception:
                        pass
                    # ensure non-negative integer
                    try:
                        sage_limit = max(0, int(sage_limit))
                    except Exception:
                        sage_limit = 2
                    self._sage_shields_left[pid] = sage_limit
            except Exception:
                pass
            # initialize fake-death uses for evil_busker
            try:
                if role_id == 'evil_busker':
                    # Read configured evil_busker_fake_uses from roles/role_settings.json if present.
                    busker_limit = 2
                    try:
                        this_dir = Path(__file__).resolve().parents[1]
                    except Exception:
                        this_dir = Path.cwd()
                    try:
                        rs_path = this_dir / 'roles' / 'role_settings.json'
                        if rs_path.exists():
                            try:
                                rs = json.loads(rs_path.read_text(encoding='utf-8-sig'))
                                if isinstance(rs, dict) and 'evil_busker_fake_uses' in rs:
                                    # coerce to int safely, fallback to default 2
                                    try:
                                        bval = rs.get('evil_busker_fake_uses')
                                        busker_limit = int(bval) if bval is not None and str(bval).strip() != '' else busker_limit
                                    except Exception:
                                        busker_limit = 2
                            except Exception:
                                pass
                    except Exception:
                        pass
                    # ensure non-negative integer
                    try:
                        busker_limit = max(0, int(busker_limit))
                    except Exception:
                        busker_limit = 2
                    # Initialize uses count to 0 (will increment on each use)
                    # The limit is loaded from config and checked during night_actions
                    self._busker_fake_uses[pid] = 0
            except Exception:
                pass

        # If multiple werewolves (by faction), let them know their teammates (private logs per wolf)
        wolf_ids = [pid for pid, p in self.players.items() if p.role_id and getattr(self.roles.get(p.role_id), 'faction', None) == 'werewolf']
        if len(wolf_ids) >= 2:
            wolf_names = [self.players[pid].name for pid in wolf_ids]
            for pid in wolf_ids:
                teammates = [n for i, n in enumerate(wolf_names) if wolf_ids[i] != pid]
                if teammates:
                    # push a structured private message for adapter to render via i18n
                    self._push_private(pid, {'key': 'wolf_teammates', 'params': {'names': ', '.join(teammates)}})
        # Optionally assign lovers overlay only if enabled in settings
        try:
            if getattr(self.settings, 'lovers_enabled', False):
                # select candidates whose underlying faction is village or werewolf
                candidates = []
                for pid, p in self.players.items():
                    rid = p.role_id
                    role = self.roles.get(rid) if rid else None
                    faction = getattr(role, 'faction', None) if role else None
                    if faction in ('village', 'werewolf'):
                        candidates.append(pid)
                if len(candidates) >= 2:
                    random.shuffle(candidates)
                    a, b = candidates[0], candidates[1]
                    self._lovers[a] = b
                    self._lovers[b] = a
                    try:
                        self._push_private(a, {'key': 'lovers_assigned', 'params': {'partner': self.players[b].name}})
                    except Exception:
                        pass
                    try:
                        self._push_private(b, {'key': 'lovers_assigned', 'params': {'partner': self.players[a].name}})
                    except Exception:
                        pass
        except Exception:
            pass
    # Phase progression (synchronous simplified)
    def night_actions(self, night_choices: Dict[str, Optional[str]]):
        if self.phase != Phase.NIGHT:
            raise RuntimeError("Not in NIGHT phase")

        wolf_votes: Dict[str, int] = {}
        # collect knight protections: knight_id -> protected_id
        knight_protects: Dict[str, Optional[str]] = {}
        for pid, choice in night_choices.items():
            if pid not in self.players:
                continue
            role = self.players[pid].role_id
            # determine role object and faction-aware wolf membership
            try:
                role_obj = self.roles.get(role) if role else None
            except Exception:
                role_obj = None
            # Count werewolf choices, but exclude the Evil Busker's fake-death sentinel
            if getattr(role_obj, 'faction', None) == 'werewolf' and choice and not (role == 'evil_busker' and choice == '__busker_fake__'):
                wolf_votes[choice] = wolf_votes.get(choice, 0) + 1
                # private: don't expose who wolves chose in public logs
                self.log(f"[PRIVATE] {self.players[pid].name} (wolf) chose {choice}")
            if role == "knight":
                # knight chooses one player to protect
                # Adapter is expected to exclude the previous night's protect target from selection;
                # here we accept the choice if present and let the adapter prevent repeats.
                if choice:
                    knight_protects[pid] = choice
                else:
                    knight_protects[pid] = None
            if role == "seer" and choice:
                target = self.players.get(choice)
                # Determine seer result based on the target's role faction.
                # For now, seer sees 'werewolf' if the target's role.faction == 'werewolf',
                # otherwise the seer sees 'village'. This means neutral roles like
                # 'madman' (faction='madman') will appear as village (白) to the seer.
                if target:
                    # use effective role id to ignore lovers overlay
                    eff_rid = self._effective_role_id(target)
                    target_role = self.roles.get(eff_rid) if eff_rid else None
                    try:
                        if eff_rid == 'jester' or (target_role and getattr(target_role, 'id', None) == 'jester'):
                            seer_result = 'village'
                        elif target_role and getattr(target_role, 'faction', None) == 'werewolf':
                            seer_result = 'werewolf'
                        else:
                            seer_result = 'village'
                    except Exception:
                        seer_result = 'village'
                else:
                    seer_result = 'unknown'
                # Decide whether the seer action is blocked by an Evil Busker (tests may set _busker_blocks_left)
                blocked = False
                try:
                    blocks = getattr(self, '_busker_blocks_left', {}) or {}
                    for bid, cnt in list(blocks.items()):
                        try:
                            if cnt and int(cnt) > 0:
                                # consume one block and suppress seer result
                                try:
                                    self._busker_blocks_left[bid] = max(0, int(cnt) - 1)
                                except Exception:
                                    pass
                                blocked = True
                                break
                        except Exception:
                            continue
                except Exception:
                    blocked = False

                # private: queue seer check result as a structured message so adapter can localize presentation
                target_name = target.name if target else choice
                if not blocked:
                    try:
                        self._push_private(pid, {'key': 'seer_result', 'params': {'target': target_name, 'result': seer_result}})
                    except Exception:
                        # fallback to older plain text if structured push fails
                        self._push_private(pid, f"Seer {self.players[pid].name} checked {target_name} -> {seer_result}")

            # Arsonist: douse a target with oil (cannot target self, dead, or already oiled)
            if role == 'arsonist' and choice:
                try:
                    target = self.players.get(choice)
                    if target and target.alive and choice != pid and not getattr(target, 'oiled', False):
                        try:
                            target.oiled = True
                        except Exception:
                            setattr(target, 'oiled', True)
                        try:
                            if not hasattr(self, 'private_messages'):
                                self.private_messages = {}
                            msgs = self.private_messages.get(target.id) or []
                            msgs.append({'key': 'oiled', 'params': {}})
                            self.private_messages[target.id] = msgs
                            self.log(f"[PRIVATE] {self.players[pid].name} (arsonist) oiled {target.name}")
                        except Exception:
                            pass
                except Exception:
                    pass

            # Sage: can use a shield (special sentinel '__shield__') up to configured times
            if role == 'sage' and choice:
                try:
                    if choice == '__shield__' and self._sage_shields_left.get(pid, 0) > 0:
                        try:
                            self._sage_shielded_this_night.add(pid)
                        except Exception:
                            self._sage_shielded_this_night = set(getattr(self, '_sage_shielded_this_night', set()) | {pid})
                        try:
                            self._sage_shields_left[pid] = max(0, self._sage_shields_left.get(pid, 0) - 1)
                        except Exception:
                            try:
                                self._sage_shields_left[pid] = 0
                            except Exception:
                                pass
                        try:
                            self._push_private(pid, {'key': 'sage_shield_used', 'params': {}})
                        except Exception:
                            pass
                except Exception:
                    pass
            # Evil Busker: can perform a one-time fake-death ('__fake_death__') which is
            # queued and processed at the end of the night. We record the use and add to
            # _busker_fake_pending but do NOT change alive here so seer/other actions can still
            # target the pre-fake-death state.
            if role == 'evil_busker' and choice:
                try:
                    if choice == '__fake_death__':
                        # consult configured limit (defaults to 1)
                        try:
                            limit = int(self._busker_fake_limit())
                        except Exception:
                            limit = 1
                        try:
                            cur = int(self._busker_fake_uses.get(pid, 0))
                        except Exception:
                            cur = 0
                        # only allow if current uses < configured limit
                        if cur < limit:
                            # increment per-busker counter
                            try:
                                self._busker_fake_uses[pid] = cur + 1
                            except Exception:
                                try:
                                    d = getattr(self, '_busker_fake_uses', {}) or {}
                                    d[pid] = cur + 1
                                    self._busker_fake_uses = d
                                except Exception:
                                    pass
                            # maintain legacy set for compatibility (mark as used at least once)
                            try:
                                self._busker_fake_used.add(pid)
                            except Exception:
                                try:
                                    self._busker_fake_used = set(getattr(self, '_busker_fake_used', set()) | {pid})
                                except Exception:
                                    pass
                            # add to pending set (process at end-of-night)
                            try:
                                self._busker_fake_pending.add(pid)
                            except Exception:
                                try:
                                    self._busker_fake_pending = set(getattr(self, '_busker_fake_pending', set()) | {pid})
                                except Exception:
                                    pass
                            # notify privately (ability used), include remaining uses
                            try:
                                remaining = max(0, limit - (cur + 1))
                                self._push_private(pid, {'key': 'busker_fake_used', 'params': {'remaining': remaining}})
                            except Exception:
                                pass
                        else:
                            # no uses left; ignore the request
                            pass
                except Exception:
                    pass

        # Build a set of protected ids for this night (only alive protections count)
        protected_ids = set()
        for kpid, target in knight_protects.items():
            if not target:
                continue
            tp = self.players.get(target)
            # cannot protect if target not found or not alive
            if tp and tp.alive:
                protected_ids.add(target)
                # record last protect for knight (for next night rule)
                self._knight_prev_protect[kpid] = target

        if wolf_votes:
            # Determine highest vote count
            max_votes = max(wolf_votes.values())
            top_targets = [tid for tid, cnt in wolf_votes.items() if cnt == max_votes]
            # If multiple top targets (tie), set a tie flag so adapter can trigger a revote
            if len(top_targets) > 1:
                # record tie candidates and do NOT kill anyone yet
                try:
                    self._wolf_tie = top_targets
                except Exception:
                    self._wolf_tie = top_targets
                self.log(f"Night: wolf votes tied among {top_targets}; requesting revote")
            else:
                # single highest target -> resolve normally
                target_id = top_targets[0]
                target = self.players.get(target_id)
                if target:
                    # FIRST: Check if target is a sage who used shield - this triggers BEFORE protection check
                    sage_reflected = False
                    try:
                        if target.role_id == 'sage' and target_id in getattr(self, '_sage_shielded_this_night', set()):
                            wolf_ids = [p.id for p in self.players.values() if p.alive and p.role_id and getattr(self.roles.get(p.role_id), 'faction', None) == 'werewolf']
                            if wolf_ids:
                                # Select random wolf for reflection, but check if they are protected
                                victim_wolf = random.choice(wolf_ids)
                                
                                # Check if the selected wolf is protected by a knight
                                if victim_wolf in protected_ids:
                                    self.log(f"Night: Sage's shield tried to reflect attack to {self.players[victim_wolf].name}, but they were protected by a knight")
                                    sage_reflected = False  # Reflection blocked by knight protection
                                else:
                                    killed = self._kill_player(victim_wolf, reason='sage_reflect')
                                    if killed:
                                        try:
                                            self.log(f"Night: Sage's shield reflected attack; wolf {self.players[killed[0]].name} died")
                                        except Exception:
                                            pass
                                        sage_reflected = True
                                    else:
                                        self.log("Night: Sage shield reflection failed")
                            else:
                                self.log("Night: Sage shield used but no wolves alive to reflect to")
                    except Exception:
                        pass
                    
                    # SECOND: Check if target is protected by knight
                    if target_id in protected_ids:
                        self.log(f"Night: {target.name} was attacked but protected by a knight")
                        if sage_reflected:
                            self.log(f"Night: Despite knight protection, sage's shield still reflected the attack")
                        # Target survives due to knight protection, but sage reflection already happened
                    else:
                        # Target not protected by knight
                        if sage_reflected:
                            # Sage reflection already handled, target still survives due to shield
                            self.log(f"Night: {target.name} survived the attack due to sage's shield")
                        else:
                            # Normal wolf kill (no sage shield, no knight protection)
                            try:
                                # use centralized kill so lovers pair deaths are handled
                                killed = self._kill_player(target_id, reason='wolf')
                            except Exception:
                                killed = []
                            if killed:
                                # log primary kill
                                try:
                                    self.log(f"Night: {self.players[killed[0]].name} was killed by wolves")
                                except Exception:
                                    pass
                                # if partner also killed, log them as well
                                if len(killed) > 1:
                                    try:
                                        self.log(f"Night: {self.players[killed[1]].name} died (lover pair)")
                                    except Exception:
                                        pass
            # Log private summary of wolf votes
            # Build a mapping from wolf name to their choice for private logs
            for pid, choice in night_choices.items():
                try:
                    p = self.players.get(pid)
                    if not p:
                        continue
                    robj = self.roles.get(p.role_id) if p.role_id else None
                    if getattr(robj, 'faction', None) == 'werewolf':
                        self.log(f"[PRIVATE] {p.name} voted for {choice}")
                except Exception:
                    # best-effort logging only
                    try:
                        if pid in self.players:
                            self.log(f"[PRIVATE] {self.players[pid].name} voted for {choice}")
                    except Exception:
                        pass
        # Only advance to DAY if there was no wolf tie requiring revote
        try:
            if getattr(self, '_wolf_tie', None):
                # remain in NIGHT until adapter resolves the tie and triggers next actions
                self.log("Night remains due to wolf tie; awaiting revote")
            else:
                self.phase = Phase.DAY
                self.log("Moved to DAY")
        except Exception:
            try:
                self.phase = Phase.DAY
                self.log("Moved to DAY")
            except Exception:
                pass

        # Process any queued busker fake-deaths at end-of-night. This should trigger
        # normal death handling (including lovers chain-death) so that other night
        # actions (seer checks, knight protects, etc.) which happened earlier in the
        # night saw the pre-fake-death state.
        try:
            pending = getattr(self, '_busker_fake_pending', set()) or set()
            for bpid in list(pending):
                try:
                    # kill the busker now (this will also kill lover partner if any)
                    killed = self._kill_player(bpid, reason='busker_fake')
                    if killed:
                        try:
                            self.log(f"Night: Busker {bpid} fake-death processed; marked dead")
                        except Exception:
                            pass
                    # mark as active fake-dead for potential revival at day end
                    try:
                        self._busker_fake_active.add(bpid)
                    except Exception:
                        self._busker_fake_active = set(getattr(self, '_busker_fake_active', set()) | {bpid})
                except Exception:
                    pass
            # clear pending set
            try:
                self._busker_fake_pending = set()
            except Exception:
                try:
                    self._busker_fake_pending.clear()
                except Exception:
                    pass
        except Exception:
            pass
        # clear any per-night sage shield markers
        try:
            self._sage_shielded_this_night = set()
        except Exception:
            try:
                self._sage_shielded_this_night.clear()
            except Exception:
                pass

    def start_day_vote(self):
        if self.phase != Phase.DAY:
            raise RuntimeError("Not in DAY phase")
        self.phase = Phase.VOTE
        self.votes = []
        self.log("Moved to VOTE")

    def cast_vote(self, from_id: str, target_id: Optional[str]) -> bool:
        if self.phase != Phase.VOTE:
            return False
        if from_id not in self.players:
            return False
        if not self.players[from_id].alive:
            return False
        self.votes = [v for v in self.votes if v.from_id != from_id]
        self.votes.append(Vote(from_id=from_id, target_id=target_id))
        self.log(f"{self.players[from_id].name} voted -> {target_id}")
        return True

    def resolve_votes(self):
        if self.phase != Phase.VOTE:
            raise RuntimeError("Not in VOTE phase")
            
        # CRITICAL: Check for adapter-level vote invalidation
        if getattr(self, '_vote_invalidated_by_guess', False):
            self.log("ENGINE: resolve_votes blocked - votes invalidated by guess action")
            return
        if getattr(self, '_emergency_vote_reset', False):
            self.log("ENGINE: resolve_votes blocked - emergency vote reset active")
            return
            
        tally: Dict[Optional[str], int] = {}
        for v in self.votes:
            tally[v.target_id] = tally.get(v.target_id, 0) + 1
        if not tally:
            self.log("No votes cast")
            self.phase = Phase.CHECK_WIN
            return
        # determine top vote count and candidates
        max_votes = max(tally.values())
        top_targets = [tid for tid, cnt in tally.items() if cnt == max_votes]
        # if multiple top targets (tie), set a day tie flag and do not lynch yet; adapter will handle revote
        if len(top_targets) > 1:
            try:
                self._day_tie = top_targets
            except Exception:
                self._day_tie = top_targets
            self.log(f"Day: votes tied among {top_targets}; requesting revote")
            # remain in VOTE phase until adapter resolves revote
            self.phase = Phase.VOTE
            return
        target_id = top_targets[0]
        if target_id is None:
            self.log("Abstain won -> no lynch")
        else:
            victim = self.players.get(target_id)
            if victim:
                killed = self._kill_player(victim.id, reason='lynch')
                if killed:
                    try:
                        self.log(f"Day: {self.players[killed[0]].name} was lynched")
                    except Exception:
                        pass
                    if len(killed) > 1:
                        try:
                            self.log(f"Day: {self.players[killed[1]].name} died (lover pair)")
                        except Exception:
                            pass
                # record this lynched player id for special win conditions (e.g., jester)
                try:
                    self._last_lynched_ids = [victim.id]
                except Exception:
                    try:
                        self._last_lynched_ids = [victim.id]
                    except Exception:
                        pass
                # Queue medium notifications: any medium should learn if the lynched player is werewolf or not
                try:
                    # determine lynch faction: werewolf -> 'werewolf', else 'village'
                    eff_rid = self._effective_role_id(victim)
                    role = self.roles.get(eff_rid) if eff_rid else None
                    if role and getattr(role, 'faction', None) == 'werewolf':
                        medium_result = 'werewolf'
                    else:
                        medium_result = 'village'
                    # enqueue message for all media (alive or dead? usually medium can be alive only; send to all with role)
                    for p in self.players.values():
                        if p.role_id == 'medium':
                            # store a structured private message intended to be delivered by adapter
                            try:
                                if not hasattr(self, 'private_messages'):
                                    self.private_messages = {}
                                msgs = self.private_messages.get(p.id) or []
                                # structured: adapter will localize 'result' to 白/黒
                                msgs.append({'key': 'medium_result', 'params': {'victim': victim.name, 'result': medium_result}})
                                self.private_messages[p.id] = msgs
                            except Exception:
                                pass
                except Exception:
                    pass
        self.phase = Phase.CHECK_WIN

    def check_win(self) -> Optional[str]:
        if self.phase != Phase.CHECK_WIN:
            raise RuntimeError("Not in CHECK_WIN phase")

        self.log(f"CHECK_WIN ENGINE START: Current phase={self.phase}, alive players={len([p for p in self.players.values() if p.alive])}")

        # helper: list of alive players
        alive_players = [p for p in self.players.values() if p.alive]

        # Build a simple accessor to get faction for a player (safe)
        def faction_of(player: Player) -> Optional[str]:
            rid = player.role_id
            if not rid:
                return None
            role = self.roles.get(rid)
            return role.faction if role and hasattr(role, 'faction') else None

        # Win condition base class and concrete implementations
        class WinCondition:
            """Base class for win conditions. Implement evaluate(game) -> Optional[str]
            Return a winner token string (e.g. 'village','werewolf','madman','fox','lovers') or None.
            """
            def evaluate(self, game: 'Game') -> Optional[str]:
                return None

        class FoxCondition(WinCondition):
            def evaluate(self, game: 'Game') -> Optional[str]:
                # fox wins if any fox alive and no werewolves alive
                fox_ids = [p for p in game.players.values() if p.alive and (p.role_id in ('fox', 'kitsune', '狐'))]
                wolves_alive = any(p.alive and p.role_id and getattr(game.roles.get(p.role_id), 'faction', None) == 'werewolf' for p in game.players.values())
                if fox_ids and not wolves_alive:
                    return 'fox'
                return None

        class LoversCondition(WinCondition):
            def evaluate(self, game: 'Game') -> Optional[str]:
                # lovers are stored as overlay mapping self._lovers: id -> partner_id (both directions)
                # Identify any pairs where both members are alive; lovers win only if exactly one such pair
                # exists and the only alive players are that pair.
                try:
                    pairs = set()
                    alive_lovers = set()
                    lm = getattr(game, '_lovers', {}) or {}
                    for a, b in lm.items():
                        try:
                            pa = game.players.get(a)
                            pb = game.players.get(b)
                            if pa and pb and pa.alive and pb.alive:
                                # normalize pair ordering
                                pair = tuple(sorted((a, b)))
                                pairs.add(pair)
                                alive_lovers.add(a)
                                alive_lovers.add(b)
                        except Exception:
                            continue
                    # Exactly one alive pair required
                    if len(pairs) != 1:
                        return None
                    # All alive players must be exactly the two lovers
                    alive_ids = {p.id for p in game.players.values() if p.alive}
                    if alive_ids == alive_lovers:
                        return 'lovers'
                except Exception:
                    return None
                return None

        class JesterCondition(WinCondition):
            def evaluate(self, game: 'Game') -> Optional[str]:
                # Jester wins if a jester role was lynched in the most recent day resolution
                try:
                    last_lynched = getattr(game, '_last_lynched_ids', []) or []
                    if not last_lynched:
                        return None
                    for lid in last_lynched:
                        p = game.players.get(lid)
                        if p and p.role_id == 'jester':
                            return 'jester'
                        # if player object missing, attempt to check role mapping (best-effort skipped)
                    return None
                except Exception:
                    return None

        class WerewolfCondition(WinCondition):
            def evaluate(self, game: 'Game') -> Optional[str]:
                # werewolves win if werewolf_count >= other non-werewolf alive count
                alive = [p for p in game.players.values() if p.alive]
                # count by role faction when possible
                werewolf_count = 0
                for p in alive:
                    try:
                        rid = p.role_id
                        role = game.roles.get(rid) if rid else None
                        if role and getattr(role, 'faction', None) == 'werewolf':
                            werewolf_count += 1
                    except Exception:
                        continue
                other_count = 0
                for p in alive:
                    try:
                        rid = p.role_id
                        role = game.roles.get(rid) if rid else None
                        if not (role and getattr(role, 'faction', None) == 'werewolf'):
                            other_count += 1
                    except Exception:
                        other_count += 1
                if werewolf_count >= other_count and werewolf_count > 0:
                    return 'werewolf'
                return None

        # Note: Neutral (third generic faction) removed; specific independent roles
        # like madman are handled as co-winners when werewolves win.

        class VillageCondition(WinCondition):
            def evaluate(self, game: 'Game') -> Optional[str]:
                # village wins if there are no werewolves alive
                wolves_alive = False
                has_village_alive = False
                for p in game.players.values():
                    try:
                        if not p.alive:
                            continue
                        rid = p.role_id
                        role = game.roles.get(rid) if rid else None
                        if role and getattr(role, 'faction', None) == 'werewolf':
                            wolves_alive = True
                            break
                        if role and getattr(role, 'faction', None) == 'village':
                            has_village_alive = True
                    except Exception:
                        continue
                # Only declare village victory when there are no werewolves alive and
                # at least one alive player belongs to the village faction. This avoids
                # spuriously declaring village when no roles are assigned or when the
                # state is ambiguous.
                if not wolves_alive and has_village_alive:
                    return 'village'
                return None

        class ArsonistCondition(WinCondition):
            def evaluate(self, game: 'Game') -> Optional[str]:
                # Arsonist wins if all non-arsonist alive players have been oiled (cumulative)
                try:
                    # require at least one living arsonist to be present for a valid win
                    arsonists_alive = [p for p in game.players.values() if p.alive and p.role_id == 'arsonist']
                    if not arsonists_alive:
                        return None
                    # consider only alive non-arsonist players
                    others = [p for p in game.players.values() if p.alive and p.role_id != 'arsonist']
                    if not others:
                        return None
                    # if every other alive player has oiled==True (or attribute present and True), arsonist wins
                    all_oiled = all(getattr(p, 'oiled', False) for p in others)
                    if all_oiled:
                        return 'arsonist'
                except Exception:
                    return None
                return None

        # Ordered list of conditions to evaluate; earlier items have precedence
        # Neutral/third generic faction removed; madman is treated as co-winner when werewolves win
        conditions: List[WinCondition] = [
            FoxCondition(),
            JesterCondition(),    # てるてる（ジェスター）
            ArsonistCondition(),  # 放火犯
            LoversCondition(),    # 恋人
            WerewolfCondition(),
            VillageCondition(),
        ]

        # Evaluate each condition in order and collect the first non-None result
        primary_result: Optional[str] = None
        for cond in conditions:
            try:
                res = cond.evaluate(self)
            except Exception:
                res = None
            if res:
                primary_result = res
                break

        # No win yet
        if not primary_result:
            # Check the context to decide next phase
            context = getattr(self, '_check_win_context', None)
            previous_phase = getattr(self, '_previous_phase_before_check_win', None)
            
            if previous_phase == Phase.VOTE:
                # If this is from guesser action, stay in VOTE; if from vote resolution, go to NIGHT
                if context == 'guesser_action':
                    self.log(f"CHECK_WIN ENGINE: No winner found, returning to VOTE phase for re-voting (session: {getattr(self, '_current_vote_session_id', 'unknown')})")
                    self.phase = Phase.VOTE
                    self.log("No win yet; continue in VOTE phase")
                else:
                    # Vote resolution completed, normal flow to NIGHT
                    self.log(f"CHECK_WIN ENGINE: No winner found, setting phase to NIGHT (session: {getattr(self, '_current_vote_session_id', 'unknown')})")
                    self.phase = Phase.NIGHT
                    self.log("No win yet; continue to NIGHT")
            else:
                self.log(f"CHECK_WIN ENGINE: No winner found, setting phase to NIGHT (session: {getattr(self, '_current_vote_session_id', 'unknown')})")
                self.phase = Phase.NIGHT
                self.log("No win yet; continue to NIGHT")
            # If any Evil Busker had used fake-death and is currently fake-dead, revive them now
            # (only when the game did not end). Upon revival, the busker may immediately perform
            # one extra attack: select a target and kill them (similar to a wolf attack).
            try:
                active = getattr(self, '_busker_fake_active', set()) or set()
                for bpid in list(active):
                    try:
                        busker = self.players.get(bpid)
                        if not busker:
                            continue
                        # revive the busker so they are present for the next day/night
                        try:
                            busker.alive = True
                        except Exception:
                            pass
                        # clear active flag
                        try:
                            self._busker_fake_active.discard(bpid)
                        except Exception:
                            pass
                        # mark as revived this day so adapters may exclude them from being targeted
                        try:
                            self._busker_revived_this_day.add(bpid)
                        except Exception:
                            self._busker_revived_this_day = set(getattr(self, '_busker_revived_this_day', set()) | {bpid})
                        # notify busker privately that they revived and should choose a target
                        try:
                            self._push_private(bpid, {'key': 'busker_revive_prompt', 'params': {}})
                        except Exception:
                            pass
                    except Exception:
                        pass
            except Exception:
                pass
            return None

        # At this point we have a primary_result; determine winners and possible co-winners
        self.phase = Phase.END
        self.last_winners = [primary_result]

        def _player_ids_for_token(token: str) -> List[str]:
            out: List[str] = []
            if token == 'village':
                for p in self.players.values():
                    if p.role_id and getattr(self.roles.get(p.role_id), 'faction', None) == 'village':
                        out.append(p.id)
                return out
            if token == 'werewolf':
                for p in self.players.values():
                    if p.role_id and getattr(self.roles.get(p.role_id), 'faction', None) == 'werewolf':
                        out.append(p.id)
                return out
            if token == 'madman':
                for p in self.players.values():
                    if p.role_id == 'madman' or (p.role_id and getattr(self.roles.get(p.role_id), 'faction', None) == 'madman'):
                        out.append(p.id)
                return out
            if token == 'lovers':
                # collect alive lover ids (only include both members of alive pairs)
                try:
                    lm = getattr(self, '_lovers', {}) or {}
                    seen_pairs = set()
                    for a, b in lm.items():
                        try:
                            pa = self.players.get(a)
                            pb = self.players.get(b)
                            if not pa or not pb:
                                continue
                            if not pa.alive or not pb.alive:
                                continue
                            pair = tuple(sorted((a, b)))
                            if pair in seen_pairs:
                                continue
                            seen_pairs.add(pair)
                            out.append(a)
                            out.append(b)
                        except Exception:
                            continue
                except Exception:
                    pass
                return out
            if token == 'fox':
                for p in self.players.values():
                    if p.role_id in ('fox', 'kitsune', '狐'):
                        out.append(p.id)
                return out
            if token == 'jester':
                for p in self.players.values():
                    if p.role_id == 'jester':
                        out.append(p.id)
                return out
            if token == 'arsonist':
                for p in self.players.values():
                    if p.role_id == 'arsonist' and p.alive:
                        out.append(p.id)
                return out
            return out

        # Base winner ids
        self.last_winner_ids = _player_ids_for_token(primary_result)

        # Note: Previously lovers would override village/werewolf wins if any alive
        # lovers pair existed. That behavior caused premature winners in scenarios
        # where other players remained alive. Lovers are handled explicitly by the
        # LoversCondition above (which requires the lovers to be the only alive
        # players). Therefore we no longer perform a blanket override here.
        # New rule: If the primary_result is village or werewolf, but there exists
        # at least one alive lovers pair, the lovers override and win. This implements
        # the requested "lovers hijack village/werewolf victory" rule.
        try:
            if primary_result in ('werewolf', 'village'):
                lm = getattr(self, '_lovers', {}) or {}
                # detect any alive pair (both members alive)
                for a, b in lm.items():
                    try:
                        pa = self.players.get(a)
                        pb = self.players.get(b)
                        if pa and pb and pa.alive and pb.alive:
                            # override winners to lovers
                            self.last_winners = ['lovers']
                            self.last_winner_ids = _player_ids_for_token('lovers')
                            self.log('Lovers override victory (hijack)')
                            return 'lovers'
                    except Exception:
                        continue
        except Exception:
            # if anything goes wrong, fall through to normal handling
            pass

        # If werewolf wins, madmen are co-winners
        if primary_result == 'werewolf':
            self.log('Werewolves win')
            madmen_exist = [p for p in self.players.values() if (p.role_id == 'madman' or (p.role_id and getattr(self.roles.get(p.role_id), 'faction', None) == 'madman'))]
            if madmen_exist:
                self.last_winners.append('madman')
                madman_ids = _player_ids_for_token('madman')
                for mid in madman_ids:
                    if mid not in self.last_winner_ids:
                        self.last_winner_ids.append(mid)
                self.log('Madman also wins (co-winner)')
            return 'werewolf'

        # If jester or arsonist wins, lovers must be losers (do not allow lovers to overwrite)
        if primary_result in ('jester', 'arsonist'):
            self.log(f"{primary_result} wins")
            return primary_result

        # If lovers condition is the primary result, accept it directly. The LoversCondition
        # already enforces that lovers are the only alive players for a valid lovers win.
        if primary_result == 'lovers':
            self.last_winner_ids = _player_ids_for_token('lovers')
            self.log('Lovers win')
            return 'lovers'

        # For village and fox and other tokens, default logging/return
        self.log(f"{primary_result} wins")
        return primary_result

    # Helpers
    def alive_players(self) -> List[Player]:
        return [p for p in self.players.values() if p.alive]

    def possible_arsonist_targets(self, arsonist_id: str) -> List[Player]:
        """Return candidates for arsonist target selection: alive players excluding self and already oiled."""
        out: List[Player] = []
        try:
            for p in self.players.values():
                if not p.alive:
                    continue
                if p.id == arsonist_id:
                    continue
                if getattr(p, 'oiled', False):
                    continue
                out.append(p)
        except Exception:
            pass
        return out

    def get_player_role(self, player_id: str) -> Optional[str]:
        p = self.players.get(player_id)
        return p.role_id if p else None

    def busker_perform_extra_attack(self, busker_id: str, target_id: str) -> List[str]:
        """Called when a revived Evil Busker selects their extra-attack target.
        Returns list of killed ids (may be empty). This enforces that only a busker
        who was revived this day may perform the extra attack; after performing it
        the revived marker is cleared.
        """
        out: List[str] = []
        try:
            if busker_id not in self.players:
                return out
            busker = self.players[busker_id]
            # busker must be alive and have been revived this day
            if not busker.alive:
                return out
            revived_set = getattr(self, '_busker_revived_this_day', set()) or set()
            if busker_id not in revived_set:
                return out
            # perform centralized kill to respect lovers behavior
            killed = self._kill_player(target_id, reason='busker_revenge')
            if killed:
                try:
                    self.log(f"Busker: {busker.name} performed extra-attack on {self.players[killed[0]].name}")
                except Exception:
                    pass
                out = killed
            # clear revived marker for busker
            try:
                self._busker_revived_this_day.discard(busker_id)
            except Exception:
                pass
        except Exception:
            pass
        return out

# --- end of engine.py
