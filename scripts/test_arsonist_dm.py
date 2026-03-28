# Simple runner to reproduce arsonist night DM enqueue behavior without Discord runtime
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ''))

from src.engine import Game, GameSettings, Player

async def main():
    g = Game(game_id='test', owner_id='owner')
    # create 3 players: arsonist + two villagers
    g.players = {}
    g.players['1'] = Player(id='1', name='Alice')
    g.players['2'] = Player(id='2', name='Bob')
    g.players['3'] = Player(id='3', name='Carol')
    # assign roles
    g.players['1'].role_id = 'arsonist'
    g.players['2'].role_id = 'villager'
    g.players['3'].role_id = 'villager'
    g.phase = g.phase.NIGHT
    # simulate pending choices: arsonist chooses player 2
    pending = {'1': '2'}
    g.night_actions(pending)
    # check private_messages for oiled DM and player's oiled flag
    print('player 2 oiled flag:', g.players['2'].oiled)
    print('private_messages for 2:', g.private_messages.get('2'))

if __name__ == '__main__':
    asyncio.run(main())
