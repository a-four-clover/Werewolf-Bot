import asyncio
import sys
import importlib.util
import os

# load module path for src.discord_bot
proj_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if proj_root not in sys.path:
    sys.path.insert(0, proj_root)

import discord

# For local unit testing, make discord.DMChannel a lightweight dummy so isinstance checks pass
try:
    discord.DMChannel = type('DMChannel', (), {})
except Exception:
    pass

from src.discord_bot import WerewolfCog
from src.engine import Game
from types import SimpleNamespace

class DummyUser:
    def __init__(self, uid, name, send_fail_once=False):
        self.id = int(uid)
        self.name = name
        self.display_name = name
        self._sent = []
        self._fail_once = send_fail_once

    async def send(self, content):
        print(f"DummyUser {self.id}.send called with: {content}")
        if self._fail_once and not self._sent:
            self._sent.append(('failed', content))
            self._fail_once = False
            print(f"DummyUser {self.id} simulating failure")
            raise Exception('simulated send failure')
        self._sent.append(('ok', content))
        print(f"DummyUser {self.id} send ok")
        return True

class DummyBot:
    def __init__(self):
        self._users = {}

    def register_user(self, user: DummyUser):
        self._users[user.id] = user

    async def fetch_user(self, uid: int):
        print(f"fetch_user called for {uid}")
        return self._users.get(uid)

class DummyStorage:
    def __init__(self, game: Game):
        self._games = {game.game_id: game}

async def main():
    g = Game(game_id='ch1', owner_id='1', settings=None)
    g.players = {
        '10': SimpleNamespace(id='10', name='A', alive=True, role_id='werewolf'),
        '20': SimpleNamespace(id='20', name='B', alive=True, role_id='werewolf'),
        '30': SimpleNamespace(id='30', name='C', alive=True, role_id='werewolf'),
    }
    bot = DummyBot()
    for uid in (10,20,30):
        bot.register_user(DummyUser(uid, f'U{uid}', send_fail_once=(uid==20)))
    storage = DummyStorage(g)
    cog = WerewolfCog(bot, storage=storage)

    g._wolf_group_members = ['10','20','30']
    from src.discord_bot import Phase
    g.phase = Phase.NIGHT

    class Msg:
        def __init__(self):
            self.author = SimpleNamespace(id=10, bot=False, name='A', display_name='A')
            # Use an actual instance of patched discord.DMChannel so isinstance check passes
            try:
                self.channel = discord.DMChannel()
            except Exception:
                self.channel = SimpleNamespace()
            self.content = 'hello wolves'
    msg = Msg()

    print('storage._games keys:', getattr(storage, '_games', {}).keys())
    print('g._wolf_group_members:', getattr(g, '_wolf_group_members', None))
    print('g.phase:', getattr(g, 'phase', None))
    print('g.players:', list(g.players.keys()))
    await cog.on_message(msg)

    print('--- sent lists ---')
    for uid, user in bot._users.items():
        print(uid, user._sent)
    print('failures:', getattr(g, '_wolf_dm_failures', None))
    print('errors:', getattr(g, '_wolf_dm_errors', None))

if __name__ == '__main__':
    asyncio.run(main())
