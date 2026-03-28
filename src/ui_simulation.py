from src.engine import Game, GameSettings, Phase
import random


def run_simulation(num_players: int = 9, seed: int = 42):
    print(f"Starting UI simulation with {num_players} players (seed={seed})")
    random.seed(seed)
    g = Game(game_id='sim', owner_id='owner', settings=GameSettings(min_players=2, max_players=12))

    for i in range(num_players):
        g.join(f'p{i}', f'Player{i}')

    started = g.start()
    print(f"Started: {started}")

    # show assigned roles (DM in real bot)
    print("Roles:")
    for pid, p in g.players.items():
        print(f"{p.name} {p.role_id}")

    # Simulate night choices: wolves target lowest-index non-wolf
    alive = [p for p in g.players.values() if p.alive]
    wolf_ids = [p.id for p in alive if p.role_id == 'werewolf']
    print("\nLogs:")
    if wolf_ids:
        # choose a target that's not a wolf
        non_wolves = [p for p in alive if p.id not in wolf_ids]
        if non_wolves:
            target = non_wolves[0].id
            night_choices = {wid: target for wid in wolf_ids}
        else:
            night_choices = {}
    else:
        night_choices = {}

    # perform night actions
    g.night_actions(night_choices)

    for l in g.logs:
        print(l)

    # DAY: show vote UI (console) and simulate votes
    print("\n-- DAY: Voting simulation --")
    if g.phase != Phase.DAY:
        print(f"Expected DAY phase but got {g.phase}")
    g.start_day_vote()

    # simple voting strategy: first three alive (excluding dead) vote for the lowest-index alive player
    alive_now = [p for p in g.players.values() if p.alive]
    if not alive_now:
        print("No alive players to vote")
        return
    target = alive_now[0].id
    voters = [p.id for p in alive_now[1:4]]  # up to 3 voters
    for v in voters:
        g.cast_vote(v, target)

    g.resolve_votes()
    print("Post-vote logs:")
    for l in g.logs[-10:]:
        print(l)

    print("Alive players:")
    for p in g.players.values():
        print(f"- {p.name}: {'alive' if p.alive else 'dead'}")


def run_abstain_scenario(num_players: int = 4, seed: int = 123):
    print(f"\nRunning abstain scenario with {num_players} players (seed={seed})")
    random.seed(seed)
    g = Game(game_id='sim2', owner_id='owner', settings=GameSettings(min_players=2, max_players=12))
    for i in range(num_players):
        g.join(f'p{i}', f'Player{i}')
    g.start()
    # progress to day
    g.night_actions({})
    g.start_day_vote()

    # everyone abstains
    for pid in list(g.players.keys()):
        g.cast_vote(pid, None)

    g.resolve_votes()
    print("Logs:")
    for l in g.logs:
        print(l)
    print("Alive players after abstain:")
    for p in g.players.values():
        print(f"- {p.name}: {'alive' if p.alive else 'dead'}")


if __name__ == '__main__':
    run_simulation(9, seed=42)
    run_abstain_scenario(4, seed=123)
