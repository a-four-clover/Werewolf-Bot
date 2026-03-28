import sys
from pathlib import Path
proj_root = Path(__file__).resolve().parents[1]
if str(proj_root) not in sys.path:
    sys.path.insert(0, str(proj_root))
import src.wordwolf as ww


def scenario_abstain_counts():
    g = ww.WordWolfGame('1', 'o')
    # players A,B,C
    g.players = ['A','B','C']
    # minority is C
    g.minority_ids = {'C'}
    # pre-seed pending votes like cog does
    g._pending_votes = {'A':'__abstain__', 'B':'C', 'C':'B'}
    print('pending:', g._pending_votes)
    counts = g.tally_votes()
    print('counts:', counts)
    # show what main code would do
    if not counts:
        print('No votes')
        return
    top_votes = max(counts.values())
    top_candidates = [pid for pid, c in counts.items() if c == top_votes]
    print('top_candidates:', top_candidates)
    if len(top_candidates) != 1:
        print('No lynch (tie)')
        return
    lynched = top_candidates[0]
    print('lynched:', lynched)


if __name__ == '__main__':
    scenario_abstain_counts()
