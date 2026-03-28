from src.engine import Game


def demo():
    g = Game(game_id="demo", owner_id="owner", settings=None)
    # join 9 players (demonstrate +1 capacity vs typical 8-person table)
    for i in range(9):
        uid = f"p{i}"
        g.join(uid, f"Player{i}")
    started = g.start()
    print("Started:", started)
    print("Roles:")
    for p in g.players.values():
        print(p.name, p.role_id)

    # simulate night choices: wolves choose first non-wolf
    night_choices = {}
    for pid, p in g.players.items():
        if p.role_id == 'werewolf':
            # pick someone who is not a wolf
            target = next((x.id for x in g.players.values() if x.role_id != 'werewolf' and x.alive), None)
            night_choices[pid] = target
    g.night_actions(night_choices)
    print('\nLogs:')
    for l in g.logs:
        print(l)


if __name__ == '__main__':
    demo()
