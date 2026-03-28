import sys
from pathlib import Path
try:
    # Ensure parent project root is on sys.path so we can import the src package
    proj_root = Path(__file__).resolve().parents[1]
    if str(proj_root) not in sys.path:
        sys.path.insert(0, str(proj_root))
    import src.engine as engine
    Game = engine.Game
    Player = engine.Player
except Exception:
    # fallback: try relative import (best-effort)
    from engine import Game, Player


def scenario_only_lovers():
    g = Game(game_id='1', owner_id='o')
    # create two players
    g.players = {}
    g.players['A'] = Player(id='A', name='Alice', alive=True, role_id='villager')
    g.players['B'] = Player(id='B', name='Bob', alive=True, role_id='villager')
    # others dead
    g.players['C'] = Player(id='C', name='Charlie', alive=False, role_id='werewolf')
    # set lovers overlay
    g._lovers = {'A':'B', 'B':'A'}
    # set phase to CHECK_WIN
    g.phase = engine.Phase.CHECK_WIN
    res = g.check_win()
    print('scenario_only_lovers ->', res, 'last_winners=', g.last_winners, 'last_winner_ids=', g.last_winner_ids)

def scenario_lovers_and_werewolf():
    g = Game(game_id='1', owner_id='o')
    g.players = {}
    g.players['A'] = Player(id='A', name='Alice', alive=True, role_id='villager')
    g.players['B'] = Player(id='B', name='Bob', alive=True, role_id='werewolf')
    g.players['C'] = Player(id='C', name='Charlie', alive=False, role_id='villager')
    g._lovers = {'A':'B', 'B':'A'}
    g.phase = engine.Phase.CHECK_WIN
    res = g.check_win()
    print('scenario_lovers_and_werewolf ->', res, 'last_winners=', g.last_winners, 'last_winner_ids=', g.last_winner_ids)


def scenario_lovers_plus_extra_alive():
    g = Game(game_id='1', owner_id='o')
    g.players = {}
    g.players['A'] = Player(id='A', name='Alice', alive=True, role_id='villager')
    g.players['B'] = Player(id='B', name='Bob', alive=True, role_id='villager')
    g.players['C'] = Player(id='C', name='Charlie', alive=True, role_id='villager')
    g._lovers = {'A':'B', 'B':'A'}
    g.phase = engine.Phase.CHECK_WIN
    res = g.check_win()
    print('scenario_lovers_plus_extra_alive ->', res, 'last_winners=', g.last_winners, 'last_winner_ids=', g.last_winner_ids)


def scenario_werewolf_majority_with_lovers():
    # Create scenario where werewolves would normally win, but a lovers pair is alive
    g = Game(game_id='1', owner_id='o')
    g.players = {}
    # lovers A and B (both alive)
    g.players['A'] = Player(id='A', name='Alice', alive=True, role_id='villager')
    g.players['B'] = Player(id='B', name='Bob', alive=True, role_id='villager')
    # two werewolves
    g.players['C'] = Player(id='C', name='Charlie', alive=True, role_id='werewolf')
    g.players['D'] = Player(id='D', name='Diana', alive=True, role_id='werewolf')
    # no other players
    g._lovers = {'A':'B', 'B':'A'}
    g.phase = engine.Phase.CHECK_WIN
    res = g.check_win()
    print('scenario_werewolf_majority_with_lovers ->', res, 'last_winners=', g.last_winners, 'last_winner_ids=', g.last_winner_ids)


if __name__ == '__main__':
    scenario_only_lovers()
    scenario_lovers_and_werewolf()
    scenario_lovers_plus_extra_alive()
    scenario_werewolf_majority_with_lovers()
