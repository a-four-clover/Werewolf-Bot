import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ''))

import discord
from src.discord_bot import WerewolfCog
from src.engine import Game, Player

class FakeResponse:
    def __init__(self):
        self._done = False
    async def defer(self, **kwargs):
        self._done = True
    async def send_message(self, *args, **kwargs):
        # record or print
        print('response.send_message called with:', args, kwargs)
    async def edit_message(self, *args, **kwargs):
        print('response.edit_message called with:', args, kwargs)

class FakeFollowup:
    async def send(self, *args, **kwargs):
        print('followup.send called with:', args, kwargs)

class FakeUser:
    def __init__(self, id):
        self.id = id

class FakeInteraction:
    def __init__(self, user_id):
        self.user = FakeUser(user_id)
        self.response = FakeResponse()
        self.followup = FakeFollowup()

async def main():
    # Setup game with arsonist
    g = Game(game_id='test', owner_id='owner')
    g.players = {}
    g.players['1'] = Player(id='1', name='Arsonist')
    g.players['2'] = Player(id='2', name='Bob')
    g.players['3'] = Player(id='3', name='Carol')
    g.players['1'].role_id = 'arsonist'
    g.players['2'].role_id = 'villager'
    g.players['3'].role_id = 'villager'
    g.phase = g.phase.NIGHT
    # initialize expected adapter-managed structures
    g._pending_night_choices = {}
    g._night_events = {}

    # Build options using engine helper to mirror adapter behavior
    options = []
    # Use possible_arsonist_targets to get Player objects
    helper = getattr(g, 'possible_arsonist_targets', None)
    candidates = helper('1') if callable(helper) else [p for p in g.players.values() if p.id != '1']
    for p in candidates:
        options.append(discord.SelectOption(label=p.name, value=p.id))

    # Create the view
    view = WerewolfCog.NightSelectView(timeout=30, game=g, player_id='1', options=options)
    # find the select item
    select = None
    for child in view.children:
        # child is a Select
        select = child
        break
    if select is None:
        print('No select found in view; abort')
        return

    # simulate user selecting Bob (id '2') -- set internal _values since `.values` has no setter
    try:
        select._values = ['2']
    except Exception:
        # fallback: try to set via object attribute
        try:
            object.__setattr__(select, 'values', ['2'])
        except Exception:
            pass
    inter = FakeInteraction(user_id=1)
    # call callback
    await select.callback(inter)

    # After callback, pending choice should be set
    print('g._pending_night_choices:', getattr(g, '_pending_night_choices', {}))

    # Execute night actions using pending choices
    g.night_actions(getattr(g, '_pending_night_choices', {}))

    # Check oil status and private_messages
    print('player 2 oiled flag:', g.players['2'].oiled)
    print('private_messages for 2:', g.private_messages.get('2'))

if __name__ == '__main__':
    asyncio.run(main())
