import sys
from pathlib import Path
proj_root = Path(__file__).resolve().parents[1]
if str(proj_root) not in sys.path:
    sys.path.insert(0, str(proj_root))
from src.engine import Game, Player, Phase

# Simulate scenario where day_tie contains only one candidate

def simulate_single_candidate_revoter():
    g = Game(game_id='1', owner_id='o')
    # create players as dict expected by engine
    g.players = {}
    # three players: A (alive), B (alive), C (alive)
    g.players['A'] = Player(id='A', name='Alice', alive=True, role_id='villager')
    g.players['B'] = Player(id='B', name='Bob', alive=True, role_id='villager')
    g.players['C'] = Player(id='C', name='Charlie', alive=True, role_id='villager')
    # suppose initial vote produced a tie but now day_tie is a single candidate (edge case)
    g._day_tie = ['A']
    # set phase to CHECK_WIN (simulating after resolve) or VOTE? In code, day_tie handling occurs after resolve -> they detect day_tie and enter revote while in adapter.
    # Let's simulate adapter revote: reset pending votes
    g._pending_votes = {}
    # no pending votes (nobody voted)
    print('Before revote: phase=', g.phase, 'day_tie=', g._day_tie)
    # In the real flow resolve_votes expects to be called while in Phase.VOTE.
    g.phase = Phase.VOTE
    # Adapter would clear g._day_tie before calling resolve after collecting revote votes.
    g._day_tie = None
    try:
        g.resolve_votes()
    except Exception as e:
        print('resolve_votes raised', e)
    print('After resolve_votes: phase=', g.phase, 'day_tie=', getattr(g,'_day_tie',None), 'votes=', g.votes)
    # Now call check_win
    try:
        res = g.check_win()
    except Exception as e:
        res = f'err:{e}'
    print('check_win ->', res, 'phase=', g.phase)

if __name__ == "__main__":
    simulate_single_candidate_revoter()
