from __future__ import annotations
from typing import List, Optional
import discord
from discord import app_commands, ui
from discord.ext import commands
import importlib
WordWolfGame = None
try:
    # try package-relative import
    spec = importlib.util.find_spec('.wordwolf', package=__package__)
    if spec is not None:
        mod = importlib.import_module(f'{__package__}.wordwolf')
        WordWolfGame = getattr(mod, 'WordWolfGame', None)
    else:
        # try top-level
        mod = importlib.import_module('wordwolf')
        WordWolfGame = getattr(mod, 'WordWolfGame', None)
except Exception:
    WordWolfGame = None
import random
from .discord_bot import safe_interaction_send
from .i18n import msg

class WordWolfCog(commands.Cog):
    def __init__(self, bot: commands.Bot, storage=None):
        self.bot = bot
        self.storage = storage
        # store games by channel id
        self._games = {}
        # map designated VC id -> channel id for quick lookup
        self._vc_map = {}

    def get_game(self, channel_id: str):
        return self._games.get(channel_id)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        # detect joins/leaves for any designated VC used by WordWolf lobbies
        try:
            # left a VC
            if before and getattr(before, 'channel', None):
                cid = str(getattr(before.channel, 'id'))
                ch_id = self._vc_map.get(cid)
                if ch_id:
                    g = self._games.get(ch_id)
                    if g and str(member.id) in g.players:
                        # user left designated VC -> remove from players
                        try:
                            g.players = [p for p in g.players if p != str(member.id)]
                        except Exception:
                            try:
                                g.players.remove(str(member.id))
                            except Exception:
                                pass
                        # notify channel
                        try:
                            channel = self.bot.get_channel(int(ch_id))
                            if channel:
                                await channel.send(msg('ww_player_left_vc', name=member.display_name))
                        except Exception:
                            pass
            # joined a VC
            if after and getattr(after, 'channel', None):
                cid = str(getattr(after.channel, 'id'))
                ch_id = self._vc_map.get(cid)
                if ch_id:
                    g = self._games.get(ch_id)
                    if g and str(member.id) not in g.players:
                        # add to players
                        try:
                            g.players.append(str(member.id))
                        except Exception:
                            pass
                        # notify channel
                        try:
                            channel = self.bot.get_channel(int(ch_id))
                            if channel:
                                await channel.send(msg('ww_player_joined_vc', name=member.display_name))
                        except Exception:
                            pass
        except Exception:
            # ensure voice state listener never raises
            pass

    @app_commands.command(name='ww_word_create', description=msg('cmd_ww_word_create_description'))
    @app_commands.describe(voice_channel=msg('arg_voice_channel'), chooser=msg('arg_chooser'), owner_auto_joined=msg('arg_owner_auto_joined'))
    async def ww_word_create(self, interaction: discord.Interaction, voice_channel: Optional[discord.VoiceChannel] = None, chooser: Optional[discord.User] = None, owner_auto_joined: Optional[bool] = True):
        # only allow in guild channel
        channel = interaction.channel
        if channel is None or not isinstance(channel, discord.TextChannel):
            try:
                await safe_interaction_send(interaction, content=msg('cmd_guild_only'), ephemeral=True)
            except Exception:
                pass
            return

        # ensure no existing game in this channel
        if str(channel.id) in self._games:
            try:
                await safe_interaction_send(interaction, content=msg('ww_already_exists'), ephemeral=True)
            except Exception:
                pass
            return

        # create lobby object
        try:
            g = WordWolfGame(str(channel.id), str(interaction.user.id))
        except Exception:
            try:
                await safe_interaction_send(interaction, content=msg('ww_create_internal_error'), ephemeral=True)
            except Exception:
                pass
            return


        # optional designated voice channel: record and capture current members as initial participants
        try:
            if voice_channel:
                g._designated_vc_id = int(voice_channel.id)
                members = [m.id for m in voice_channel.members if not m.bot]
                # add current VC members as initial participants
                try:
                    g.add_players_from_voice_channel(members)
                except Exception:
                    # fallback: append
                    for m in members:
                        try:
                            sid = str(m)
                            if sid not in g.players:
                                g.players.append(sid)
                        except Exception:
                            pass
        except Exception:
            pass

        # owner auto join handling: if owner_auto_joined and owner is not chooser, add owner
        try:
            if owner_auto_joined:
                try:
                    if getattr(g, 'chooser_id', None) and str(interaction.user.id) == getattr(g, 'chooser_id'):
                        # owner is chooser -> do not auto-join
                        owner_auto_joined = False
                except Exception:
                    pass
                if owner_auto_joined:
                    try:
                        if str(interaction.user.id) not in g.players:
                            g.players.append(str(interaction.user.id))
                    except Exception:
                        pass
        except Exception:
            pass

        # optional chooser: record chooser and ensure chooser is not in participants
        try:
            if chooser:
                g.chooser_id = str(chooser.id)
                # remove chooser from participants if present
                try:
                    if g.chooser_id in g.players:
                        g.players = [p for p in g.players if p != g.chooser_id]
                except Exception:
                    pass
        except Exception:
            pass

        # persist in memory
        try:
            self._games[str(channel.id)] = g
            # send participant list now
            try:
                names = []
                for pid in getattr(g, 'players', []):
                    try:
                        user = await self.bot.fetch_user(int(pid))
                        names.append(user.display_name if user else pid)
                    except Exception:
                        names.append(pid)
                if names:
                    await safe_interaction_send(interaction, content=msg('ww_create_with_participants', owner=interaction.user.display_name, names=', '.join(names)), ephemeral=True)
                else:
                    await safe_interaction_send(interaction, content=msg('ww_create_no_participants', owner=interaction.user.display_name), ephemeral=True)
            except Exception:
                await safe_interaction_send(interaction, content=msg('ww_create_lobby_created'), ephemeral=True)
            # if designated VC set, register mapping
            try:
                if getattr(g, '_designated_vc_id', None):
                    self._vc_map[str(getattr(g, '_designated_vc_id'))] = str(channel.id)
            except Exception:
                pass
        except Exception:
            try:
                await safe_interaction_send(interaction, content=msg('ww_create_failed'), ephemeral=True)
            except Exception:
                pass



    @app_commands.command(name='ww_word_set', description=msg('cmd_ww_word_set_description'))
    @app_commands.describe(majority_word=msg('arg_majority_word'), minority_word=msg('arg_minority_word'))
    async def ww_word_set(self, interaction: discord.Interaction, majority_word: str, minority_word: str):
        # This command is intended to be used by the chosen chooser via DM or anywhere; it will set words for the stored game
        # Find game where chooser matches
        chooser_id = str(interaction.user.id)
        tgt = None
        for ch, g in list(self._games.items()):
            try:
                if g.chooser_id == chooser_id:
                    tgt = g
                    break
            except Exception:
                continue
        if not tgt:
            try:
                await safe_interaction_send(interaction, content=msg('ww_chooser_not_found'), ephemeral=True)
            except Exception:
                pass
            return
        try:
            tgt.major_word = majority_word
            tgt.minor_word = minority_word
            # Do not assign or DM players yet; /ww_word_start will perform assignment and DM
            try:
                await safe_interaction_send(interaction, content=msg('ww_words_set_ok'), ephemeral=True)
            except Exception:
                pass
        except Exception:
            try:
                await safe_interaction_send(interaction, content=msg('ww_words_set_error'), ephemeral=True)
            except Exception:
                pass

    @app_commands.command(name='ww_word_end', description=msg('cmd_ww_word_end_description'))
    async def ww_word_end(self, interaction: discord.Interaction):
        channel = interaction.channel
        if not channel:
            try:
                await safe_interaction_send(interaction, content=msg('create_failed_vc_required'), ephemeral=True)
            except Exception:
                pass
            return
        g = self._games.pop(str(channel.id), None)
        if not g:
            try:
                await safe_interaction_send(interaction, content=msg('ww_channel_no_active'), ephemeral=True)
            except Exception:
                pass
            return
        try:
            await safe_interaction_send(interaction, content=msg('ww_game_ended'), ephemeral=True)
        except Exception:
            pass

    @app_commands.command(name='ww_word_start', description=msg('cmd_ww_word_start_description'))
    @app_commands.describe(minority_howmany=msg('arg_minority_howmany'), vote_time=msg('arg_vote_time'), reversal_time=msg('arg_reversal_time'), allow_abstain=msg('arg_allow_abstain'))
    async def ww_word_start(self, interaction: discord.Interaction, minority_howmany: Optional[int] = 1, vote_time: Optional[int] = 30, reversal_time: Optional[int] = 10, allow_abstain: bool = True):
        # This command starts a stored lobby in this channel; merge VC joiners and begin
        channel = interaction.channel
        if channel is None or not isinstance(channel, discord.TextChannel):
            try:
                await safe_interaction_send(interaction, content='このコマンドはサーバー内のチャンネルで使用してください。', ephemeral=True)
            except Exception:
                pass
            return
        g = self._games.get(str(channel.id))
        if not g:
            try:
                await safe_interaction_send(interaction, content=msg('ww_no_lobby_in_channel'), ephemeral=True)
            except Exception:
                pass
            return
        # permission: only chooser (if set) or owner may start
        chooser_id = getattr(g, 'chooser_id', None)
        owner_id = getattr(g, 'owner_id', None)
        caller_id = str(interaction.user.id)
        allowed = False
        if chooser_id and caller_id == str(chooser_id):
            allowed = True
        elif not chooser_id and caller_id == str(owner_id):
            allowed = True
        elif chooser_id and caller_id == str(owner_id):
            # owner may still start if explicitly desired
            allowed = True
        if not allowed:
            try:
                await safe_interaction_send(interaction, content=msg('ww_start_not_allowed'), ephemeral=True)
            except Exception:
                pass
            return

        # merge VC joiners from designated VC if present
        try:
            did = getattr(g, '_designated_vc_id', None)
            if did:
                vc = self.bot.get_channel(int(did))
                if vc:
                    members = [m.id for m in vc.members if not m.bot]
                    for m in members:
                        sid = str(m)
                        if sid not in g.players:
                            g.players.append(sid)
        except Exception:
            pass
        # set minority and timeouts on game
        try:
            # set runtime abstain flag for this WW game
            try:
                g._runtime_allow_abstain = bool(allow_abstain)
            except Exception:
                pass
            g.pick_minority(minority_howmany)
        except Exception:
            g.pick_minority(1)
        try:
            g.vote_timeout = int(vote_time) if vote_time is not None else 30
            # Store fixed timeout for consistent behavior
            g._fixed_vote_timeout = g.vote_timeout
        except Exception:
            g.vote_timeout = 30
            g._fixed_vote_timeout = 30
        try:
            g.reversal_seconds = int(reversal_time) if reversal_time is not None else 10
        except Exception:
            g.reversal_seconds = 10
        # proceed to assign words and start (chooser may have already set words)
        try:
            # Ensure chooser is not treated as a participant: remove chooser from players if present
            try:
                chooser_id = getattr(g, 'chooser_id', None)
                if chooser_id is not None:
                    try:
                        g.players = [p for p in g.players if p != str(chooser_id)]
                    except Exception:
                        try:
                            if str(chooser_id) in g.players:
                                g.players.remove(str(chooser_id))
                        except Exception:
                            pass
            except Exception:
                pass

            if not getattr(g, 'major_word', None) or not getattr(g, 'minor_word', None):
                # pick random simple pair
                pairs = [('猫','犬'),('りんご','みかん'),('赤','青')]
                maj,minw = random.choice(pairs)
                g.major_word = maj
                g.minor_word = minw
            g.assign_words()
            # DM all players their word (skip chooser if present)
            failed = []
            try:
                chooser_id = getattr(g, 'chooser_id', None)
            except Exception:
                chooser_id = None
            for pid, word in list(g.assigned_words.items()):
                try:
                    if chooser_id is not None and str(pid) == str(chooser_id):
                        # skip sending word to chooser
                        continue
                    user = await self.bot.fetch_user(int(pid))
                    try:
                        await user.send(msg('ww_your_word', word=word))
                    except Exception:
                        failed.append(pid)
                except Exception:
                    failed.append(pid)
            try:
                await safe_interaction_send(interaction, content=msg('ww_start_game_dm_sent', count=len(g.players), failed=len(failed)), ephemeral=False)
            except Exception:
                pass
            # now call internal vote start
            try:
                await self._start_wordwolf_vote_internal(g, channel, interaction.user.id)
            except Exception:
                pass
        except Exception:
            try:
                await safe_interaction_send(interaction, content=msg('ww_start_game_failed'), ephemeral=True)
            except Exception:
                pass
        return

    async def _start_wordwolf_vote_internal(self, g, channel: discord.TextChannel, initiator_id: str):
        """Send vote UI to channel for the WordWolf game, collect votes and handle reversal chance."""
        try:
            # prepare pending votes
            g._pending_votes = {}
            # pre-seed pending votes so timeouts can be classified: '__invalid__' = non-responder invalid, '__abstain__' = explicit abstain
            try:
                allow_abstain = getattr(g, '_runtime_allow_abstain', True)
            except Exception:
                allow_abstain = True
            try:
                chooser_id = getattr(g, 'chooser_id', None)
                for pid in g.players:
                    # only seed for alive players
                    p = None
                    try:
                        p = g.players.get(pid) if isinstance(g.players, dict) else None
                    except Exception:
                        p = None
                    alive = True
                    try:
                        if p is not None:
                            alive = getattr(p, 'alive', True)
                    except Exception:
                        alive = True
                    # skip chooser (chooser does not participate)
                    try:
                        if chooser_id is not None and str(pid) == str(chooser_id):
                            continue
                    except Exception:
                        pass
                    if not alive:
                        continue
                    if allow_abstain:
                        g._pending_votes[str(pid)] = '__abstain__'
                    else:
                        g._pending_votes[str(pid)] = '__invalid__'
            except Exception:
                pass
            # build options excluding chooser and map id->name
            opts = []
            id_to_name = {}
            try:
                chooser_id = getattr(g, 'chooser_id', None)
            except Exception:
                chooser_id = None
            for pid in g.players:
                try:
                    # skip chooser when building name map
                    if chooser_id is not None and str(pid) == str(chooser_id):
                        continue
                    id_to_name[pid] = None
                    user = await self.bot.fetch_user(int(pid))
                    id_to_name[pid] = user.display_name if user else pid
                except Exception:
                    id_to_name[pid] = pid
            # exclude chooser if present
            choices = [discord.SelectOption(label=id_to_name[pid], value=pid) for pid in g.players if not (getattr(g, 'chooser_id', None) and pid == str(getattr(g, 'chooser_id')))]
            # add abstain option only if enabled at runtime
            try:
                allow_abstain = getattr(g, '_runtime_allow_abstain', True)
            except Exception:
                allow_abstain = True
            if allow_abstain:
                choices.append(discord.SelectOption(label=msg('vote_abstain_label'), value='__abstain__'))

            # chunk and batch similar to main implementation
            def chunk_options(opts_list, size=25):
                for i in range(0, len(opts_list), size):
                    yield opts_list[i:i+size]

            selects = list(chunk_options(choices, 25))
            batches = []
            for i in range(0, len(selects), 5):
                batches.append(selects[i:i+5])

            # create view class that records into WordWolfGame._pending_votes
            class WWVotingView(ui.View):
                def __init__(self, timeout: int, game):
                    super().__init__(timeout=timeout)
                    self.game = game

                class WWSelect(ui.Select):
                    def __init__(self, options, chunk_idx=0):
                        if chunk_idx == 0:
                            placeholder = msg('vote_placeholder_single')
                        else:
                            placeholder = msg('vote_placeholder_paged', page=chunk_idx+1)
                        super().__init__(placeholder=placeholder, min_values=1, max_values=1, options=options)

                    async def callback(self, interaction: discord.Interaction):
                        try:
                            await interaction.response.defer(ephemeral=True)
                        except Exception:
                            pass
                        user_id = str(interaction.user.id)
                        selected = self.values[0]
                        try:
                            # only allow alive players
                            if user_id not in self.view.game.players:
                                try:
                                                await interaction.followup.send(msg('you_are_not_participant'), ephemeral=True)
                                except Exception:
                                    pass
                                return
                            self.view.game._pending_votes[user_id] = selected
                            try:
                                await interaction.followup.send(msg('vote_received'), ephemeral=True)
                            except Exception:
                                pass
                        except Exception:
                            try:
                                if getattr(self.view.game, 'owner_id', None):
                                    pass
                            except Exception:
                                pass

                # add selects for each chunk
            views = []
            for batch in batches:
                v = WWVotingView(timeout=60, game=g)
                for idx, opts in enumerate(batch):
                    try:
                        v.add_item(WWVotingView.WWSelect(options=opts, chunk_idx=idx))
                    except Exception:
                        pass
                # send
                try:
                    await channel.send(msg('ww_vote_start_public'), view=v)
                except Exception:
                    try:
                        await channel.send(msg('ww_vote_start_public_view_fail'))
                    except Exception:
                        pass
                views.append(v)

            # wait for views to finish (use vote_timeout from g or default 30s)
            import asyncio as _asyncio
            try:
                await _asyncio.sleep( max(5, int(getattr(g, 'vote_timeout', 30))) )
            except Exception:
                pass

            # tally votes
            counts = g.tally_votes()
            if not counts:
                try:
                    await channel.send(msg('ww_no_votes'))
                except Exception:
                    pass
                return
            # find top candidate(s)
            top_votes = max(counts.values())
            top_candidates = [pid for pid, c in counts.items() if c == top_votes]
            if len(top_candidates) != 1:
                try:
                    await channel.send(msg('ww_no_lynch'))
                except Exception:
                    pass
                return
            lynched = top_candidates[0]
            # eliminate
            removed = g.eliminate(lynched)
            # announce
            try:
                name = id_to_name.get(lynched, lynched)
                await channel.send(msg('ww_lynched_announce', name=name))
            except Exception:
                pass

            # Determine if lynched was minority
            try:
                was_minority = lynched in getattr(g, 'minority_ids', set())
            except Exception:
                was_minority = False

            if not was_minority:
                # lynched was majority -> werewolf win
                try:
                    await channel.send(msg('ww_majority_lynched'))
                except Exception:
                    pass
                try:
                    # cleanup
                    if str(g.channel_id) in self._games:
                        del self._games[str(g.channel_id)]
                except Exception:
                    pass
                return

            # was minority -> reversal chance
            try:
                await channel.send(msg('ww_reversal_start_public'))
            except Exception:
                pass

            # Determine who should receive the reversal DM and who is allowed to press the buttons
            chooser_id = getattr(g, 'chooser_id', None)
            owner_id = getattr(g, 'owner_id', None)
            notifier_id = chooser_id or owner_id or initiator_id

            try:
                target_user = None
                try:
                    target_user = await self.bot.fetch_user(int(notifier_id))
                except Exception:
                    target_user = None
            except Exception:
                target_user = None

            class ReversalView(ui.View):
                def __init__(self, allowed_id: str, timeout: int = 60):
                    super().__init__(timeout=timeout)
                    self.allowed_id = str(allowed_id)
                    self.result = None

                async def interaction_check(self, inter: discord.Interaction) -> bool:
                    return str(inter.user.id) == str(self.allowed_id)

                @ui.button(label=msg('button_yes'), style=discord.ButtonStyle.success)
                async def yes(self, inter: discord.Interaction, button: ui.Button):
                    self.result = True
                    self.stop()
                    try:
                        await inter.response.send_message(msg('ww_reversal_werewolf_win'), ephemeral=True)
                    except Exception:
                        pass

                @ui.button(label=msg('button_no'), style=discord.ButtonStyle.danger)
                async def no(self, inter: discord.Interaction, button: ui.Button):
                    self.result = False
                    self.stop()
                    try:
                        await inter.response.send_message(msg('ww_reversal_citizen_win'), ephemeral=True)
                    except Exception:
                        pass

            # Send DM with Yes/No immediately when reversal time starts to the chooser (or owner/initiator fallback)
            rv_timeout = int(getattr(g, 'reversal_seconds', 10)) if getattr(g, 'reversal_seconds', None) is not None else 10
            rv = ReversalView(allowed_id=notifier_id, timeout=rv_timeout)
            if target_user:
                try:
                    await target_user.send(msg('ww_reversal_dm'), view=rv)
                except Exception:
                    try:
                        await channel.send(msg('ww_reversal_dm_failed', id=notifier_id))
                    except Exception:
                        pass
            else:
                try:
                    await channel.send(msg('ww_reversal_notify_not_found'))
                except Exception:
                    pass

            # Wait for the view to finish (timeout = reversal_seconds). If no button was pressed, send timeout message.
            try:
                await rv.wait()
            except Exception:
                pass

            # decide based on rv.result
            if getattr(rv, 'result', None) is True:
                try:
                    await channel.send(msg('ww_reversal_werewolf_win'))
                except Exception:
                    pass
            elif getattr(rv, 'result', None) is False:
                try:
                    await channel.send(msg('ww_reversal_citizen_win'))
                except Exception:
                    pass
            else:
                # timeout
                try:
                    await channel.send(msg('ww_reversal_timeout_public'))
                except Exception:
                    pass
                try:
                    # notify the chooser/owner as well
                    if target_user:
                        await target_user.send(msg('ww_reversal_timeout_dm'))
                except Exception:
                    pass
            try:
                if str(g.channel_id) in self._games:
                    del self._games[str(g.channel_id)]
            except Exception:
                pass
        except Exception:
            try:
                await channel.send(msg('ww_vote_processing_error'))
            except Exception:
                pass

    @app_commands.command(name='ww_word_end_vote', description='Force-end the current WordWolf vote in this channel and tally results (owner/chooser).')
    async def ww_word_end_vote(self, interaction: discord.Interaction):
        channel = interaction.channel
        if not channel or not isinstance(channel, discord.TextChannel):
            try:
                await safe_interaction_send(interaction, content=msg('create_failed_vc_required'), ephemeral=True)
            except Exception:
                pass
            return
        g = self._games.get(str(channel.id))
        if not g:
            try:
                await safe_interaction_send(interaction, content=msg('ww_no_active_vote'), ephemeral=True)
            except Exception:
                pass
            return
        # permission: chooser or owner
        chooser_id = getattr(g, 'chooser_id', None)
        owner_id = getattr(g, 'owner_id', None)
        caller_id = str(interaction.user.id)
        if chooser_id and caller_id != str(chooser_id) and caller_id != str(owner_id):
            try:
                await safe_interaction_send(interaction, content=msg('ww_start_not_allowed'), ephemeral=True)
            except Exception:
                pass
            return
        # Mark forced end and perform tally by calling internal vote handler's next steps.
        try:
            await safe_interaction_send(interaction, content=msg('ww_end_vote_confirmed'), ephemeral=True)
        except Exception:
            pass
        # We approximate force-end by invoking the same tally logic: stop views by clearing pending and calling tally
        try:
            # attempt to run the internal completion logic synchronously by reusing game's tally
            counts = g.tally_votes()
            # reuse same finishing sequence as in internal function: post counts and decide
            if not counts:
                try:
                    await channel.send(msg('ww_no_votes'))
                except Exception:
                    pass
                return
            top_votes = max(counts.values())
            top_candidates = [pid for pid, c in counts.items() if c == top_votes]
            if len(top_candidates) != 1:
                try:
                    await channel.send(msg('ww_no_lynch'))
                except Exception:
                    pass
                return
            lynched = top_candidates[0]
            removed = g.eliminate(lynched)
            try:
                name = lynched
                try:
                    user = await self.bot.fetch_user(int(lynched))
                    name = user.display_name if user else lynched
                except Exception:
                    pass
                await channel.send(msg('ww_lynched_announce', name=name))
            except Exception:
                pass
            try:
                if lynched in getattr(g, 'minority_ids', set()):
                    await channel.send(msg('ww_reversal_start_public'))
                else:
                    await channel.send(msg('ww_majority_lynched'))
                    if str(g.channel_id) in self._games:
                        del self._games[str(g.channel_id)]
            except Exception:
                pass
        except Exception:
            try:
                await safe_interaction_send(interaction, content=msg('internal_error_short', error='force_end_vote'), ephemeral=True)
            except Exception:
                pass

async def setup(bot: commands.Bot):
    cog = WordWolfCog(bot)
    await bot.add_cog(cog)
