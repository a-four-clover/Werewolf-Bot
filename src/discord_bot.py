from __future__ import annotations
import asyncio
import os
import traceback
import logging
from typing import Dict, List, Optional
from datetime import datetime, timezone, timedelta

import discord
from discord import app_commands, ui
from discord.ext import commands

from .utils import safe_interaction_send, _ack_interaction, _format_private_message_for_send, _safe_display_name
from .storage import InMemoryStorage, UserStats  
from .engine import Game, GameSettings, Phase, Vote
from .i18n import msg
from .win_formatter import format_winner_loser_lines
from .views import (
    ConfirmEndView, ConfirmEndVoteView, SageActionView, NightSelectView, VotingView,
    BuskerNightView, BuskerFakeDeathView, StatsRecordConfirmView
)
import json
from pathlib import Path


class WerewolfCog(commands.Cog):
    def __init__(self, bot: commands.Bot, storage=None, start_watcher: bool = True):
        self.bot = bot
        self.storage = storage or InMemoryStorage()
        # Set cog reference in storage for bot access
        try:
            self.storage._cog_ref = self
        except Exception:
            pass
        # map message_id -> channel_id for messages that accept reaction-join
        self._reaction_join_messages: Dict[int, str] = {}

    def _get_jst_now(self) -> datetime:
        """Get current time in Japan Standard Time (JST)."""
        jst = timezone(timedelta(hours=9))
        return datetime.now(jst)

    def _get_jst_timestamp(self) -> datetime:
        """Get current timestamp for Discord embeds in JST."""
        return self._get_jst_now()

    async def _reload_roles(self):
        """Reload roles definitions and update all active games' g.roles mapping.

        Uses src.roles.load_roles_json() to get latest roles data, then updates each stored Game.roles.
        """
        try:
            from . import roles as roles_module
            loaded = roles_module.load_roles_json()
        except Exception:
            loaded = None
        if not loaded:
            # nothing to update
            return
        # convert to expected format used by Game (engine._load_roles_from_json does conversion)
        # We'll call Game._load_roles_from_json indirectly: create a roles dict mapping role_id->object
        try:
            # For each active game in storage, replace its roles mapping by calling engine helper
            for g in list(getattr(self.storage, '_games', {}).values()):
                try:
                    # engine.Game has attribute roles which is a dict of id->role object; use its loader
                    # attempt to reuse Game._load_roles_from_json if present
                    if hasattr(g, '_load_roles_from_json'):
                        try:
                            g._load_roles_from_json()
                            continue
                        except Exception:
                            pass
                    # fallback: assign raw loaded dict
                    try:
                        g.roles = loaded
                    except Exception:
                        pass
                except Exception:
                    continue
        except Exception:
            pass

    @app_commands.command(name='ww_reload', description=msg('cmd_reload_description'))
    async def ww_reload(self, interaction: discord.Interaction):
        # permission: guild administrators or bot/application owner
        is_admin = False
        try:
            member = None
            if interaction.guild:
                try:
                    member = interaction.guild.get_member(interaction.user.id) or await interaction.guild.fetch_member(interaction.user.id)
                except Exception:
                    member = None
            if member and member.guild_permissions.administrator:
                is_admin = True
        except Exception:
            is_admin = False

        if not is_admin:
            # check bot/application owner
            try:
                info = await self.bot.application_info()
                if str(info.owner.id) == str(interaction.user.id):
                    is_admin = True
            except Exception as e:
                logging.getLogger(__name__).warning(f"Failed to check bot owner: {e}")

        if not is_admin:
            try:
                await interaction.response.send_message(msg('cmd_reload_admin_only'), ephemeral=True)
            except Exception as e:
                logging.getLogger(__name__).error(f"Failed to send admin-only message: {e}")
            return

        try:
            await interaction.response.defer(ephemeral=True)
        except Exception as e:
            logging.getLogger(__name__).warning(f"Failed to defer reload command: {e}")

        try:
            await self._reload_roles()
            try:
                await interaction.followup.send(msg('reload_success'), ephemeral=True)
            except Exception:
                try:
                    await interaction.response.send_message(msg('reload_success'), ephemeral=True)
                except Exception as e:
                    logging.getLogger(__name__).error(f"Failed to send reload success message: {e}")
        except Exception as e:
            logging.getLogger(__name__).error(f"Role reload failed: {e}")
            try:
                await interaction.followup.send(msg('reload_failed', error=e), ephemeral=True)
            except Exception:
                try:
                    await interaction.response.send_message(msg('internal_error_short', error=str(e)), ephemeral=True)
                except Exception as send_e:
                    logging.getLogger(__name__).error(f"Failed to send reload error message: {send_e}")

    def _winner_display_name(self, g: Game, token: str) -> str:
        """Map a winner token to a human-friendly display name.
        Prefer role display name from g.roles when known; otherwise return the token.
        """
        try:
            # If token corresponds to a role id, use the role's name
            r = g.roles.get(token) if getattr(g, 'roles', None) else None
            if r and getattr(r, 'name', None):
                return r.name
        except Exception:
            pass
        # Fallback: return the raw token
        return token

    def _create_game_status_embed(self, g: Game) -> discord.Embed:
        """Create a comprehensive game status embed for the pinned status panel."""
        # Choose color based on current phase
        phase_colors = {
            Phase.LOBBY: 0x7289DA,      # Discord blurple
            Phase.NIGHT: 0x2F3136,      # Dark gray (night)
            Phase.DAY: 0xFFD700,        # Gold (day)
            Phase.VOTE: 0xFF6B6B,       # Red (voting)
            Phase.CHECK_WIN: 0x43B581,  # Green (checking)
            Phase.END: 0x747F8D,        # Gray (ended)
            Phase.CLOSED: 0x99AAB5      # Light gray (closed)
        }
        
        # Phase icons
        phase_icons = {
            Phase.LOBBY: '⏳',
            Phase.NIGHT: '🌙',
            Phase.DAY: '☀️',
            Phase.VOTE: '🗳️',
            Phase.CHECK_WIN: '🔍',
            Phase.END: '🏁',
            Phase.CLOSED: '🔒'
        }
        
        color = phase_colors.get(g.phase, 0x7289DA)
        icon = phase_icons.get(g.phase, '❓')
        
        embed = discord.Embed(
            title=msg('status_embed_title'),
            color=color
        )
        
        # Current phase
        embed.add_field(
            name=msg('status_current_phase', icon=icon),
            value=f'**{g.phase.name}**',
            inline=True
        )
        
        # Alive players count and names
        alive_players = [p for p in g.players.values() if p.alive]
        dead_players = [p for p in g.players.values() if not p.alive]
        
        alive_names = '\n'.join([f'• {p.name}' for p in alive_players[:8]])
        if len(alive_players) > 8:
            alive_names += '\n' + msg('status_others_count', count=len(alive_players)-8)
        
        embed.add_field(
            name=msg('status_alive_players', count=len(alive_players)),
            value=alive_names if alive_names else msg('status_no_players'),
            inline=True
        )
        
        # Dead players if any
        if dead_players:
            dead_names = '\n'.join([f'• ~~{p.name}~~' for p in dead_players[:5]])
            if len(dead_players) > 5:
                dead_names += '\n' + msg('status_others_count', count=len(dead_players)-5)
            
            embed.add_field(
                name=msg('status_dead_players', count=len(dead_players)),
                value=dead_names,
                inline=True
            )
        
        # Game settings info for lobby phase
        if g.phase == Phase.LOBBY:
            settings_info = msg('status_max_min_players', max=g.settings.max_players, min=g.settings.min_players)
            if hasattr(g.settings, 'lovers_enabled') and g.settings.lovers_enabled:
                settings_info += '\n' + msg('status_lovers_enabled')
            embed.add_field(
                name=msg('status_settings'),
                value=settings_info,
                inline=True
            )
        
        # Timestamp
        embed.set_footer(text=msg('status_last_updated', time=self._get_jst_now().strftime('%H:%M:%S JST')))
        
        return embed

    def _cleanup_game(self, g: Game):
        """Attempt to delete or clear a finished game similar to /ww_close behavior."""
        try:
            if hasattr(self.storage, 'delete_game'):
                try:
                    self.storage.delete_game(str(g.game_id))
                    return
                except Exception:
                    pass
        except Exception:
            pass

    def _fully_stop_game(self, g: Game):
        """Perform a best-effort full shutdown of per-game background tasks, views,
        and persistent storage so the game is completely stopped/removed.

        This helper is idempotent and swallows exceptions to avoid blocking the
        calling command handler.
        """
        try:
            # IMMEDIATELY force game to END state to prevent any new operations
            try:
                g.phase = Phase.END
            except Exception:
                pass
            
            # Cancel ALL known background tasks stored on the game
            task_attrs = [
                '_day_vote_reminder_task', '_wolf_30s_task', '_day_vote_task', 
                '_night_task', '_night_sequence_task', '_resolve_worker_task',
                '_vote_timeout_task', '_night_timeout_task'
            ]
            
            for attr in task_attrs:
                try:
                    t = getattr(g, attr, None)
                    if t:
                        if hasattr(t, 'cancel'):
                            try:
                                t.cancel()
                            except Exception:
                                pass
                        if hasattr(t, 'done') and not t.done():
                            try:
                                t.cancel()
                            except Exception:
                                pass
                        try:
                            setattr(g, attr, None)
                        except Exception:
                            pass
                except Exception:
                    pass
            
            # Reset ALL critical game state flags to prevent zombie operations
            flag_attrs = [
                '_night_sequence_started', '_vote_finalized', '_revote_in_progress',
                '_day_vote_in_progress', '_vote_resolution_in_progress',
                '_night_actions_completed', '_game_ended', '_creating_vote_ui',
                '_vote_invalidated_by_guess', '_in_re_vote_after_guess', '_emergency_vote_reset'
            ]
            
            for attr in flag_attrs:
                try:
                    setattr(g, attr, False)
                except Exception:
                    pass
            
            # Release ALL locks
            lock_attrs = ['_guess_lock', '_vote_resolution_lock', '_night_lock']
            for attr in lock_attrs:
                try:
                    lock = getattr(g, attr, None)
                    if lock and hasattr(lock, 'locked') and lock.locked():
                        try:
                            lock.release()
                        except Exception:
                            pass
                    try:
                        setattr(g, attr, None)
                    except Exception:
                        pass
                except Exception:
                    pass

            # Stop ALL active UI views forcefully
            try:
                av = getattr(g, '_active_vote_views', None) or []
                for v in list(av):
                    try:
                        if hasattr(v, 'stop'):
                            v.stop()
                    except Exception:
                        pass
                try:
                    g._active_vote_views = []
                except Exception:
                    pass
            except Exception:
                pass

            # Clear ALL pending data structures
            data_attrs = [
                '_pending_votes', '_pending_night_choices', '_day_vote_messages',
                'private_messages', 'votes'
            ]
            
            for attr in data_attrs:
                try:
                    setattr(g, attr, {} if attr != 'votes' else [])
                except Exception:
                    pass

            # Signal ALL pending events to unblock any waiting code
            try:
                evs = getattr(g, '_night_events', {}) or {}
                for k, ev in list(evs.items()):
                    try:
                        if hasattr(ev, 'set'):
                            ev.set()
                    except Exception:
                        pass
                try:
                    g._night_events = {}
                except Exception:
                    pass
            except Exception:
                pass

            # Clear resolve queue to stop worker
            try:
                queue = getattr(g, '_resolve_queue', None)
                if queue:
                    try:
                        # Put a special stop signal
                        queue.put_nowait(None)
                    except Exception:
                        pass
                try:
                    g._resolve_queue = None
                except Exception:
                    pass
            except Exception:
                pass

            # best-effort: ask storage to delete the game record if possible
            try:
                if hasattr(self.storage, 'delete_game'):
                    try:
                        self.storage.delete_game(str(g.game_id))
                        return
                    except Exception:
                        pass
            except Exception:
                pass

            # fallback: clear players and mark ended then persist
            try:
                g.players = {}
                g.phase = Phase.END
                try:
                    self.storage.save_game(g)
                except Exception:
                    pass
            except Exception:
                pass
        except Exception:
            # log unexpected error during full stop so operators can investigate
            logging.getLogger(__name__).exception('_fully_stop_game: unexpected error during main stop')
        
        # Extended cleanup for channels and messages
        try:
            try:
                wid = getattr(g, '_wolf_channel_id', None)
                if wid:
                    try:
                        # schedule async deletion of the channel; do not await here
                        try:
                            self.bot.loop.create_task(self._async_delete_wolf_channel(int(wid)))
                        except Exception:
                            try:
                                import asyncio
                                asyncio.create_task(self._async_delete_wolf_channel(int(wid)))
                            except Exception:
                                pass
                    except Exception:
                        pass
            except Exception:
                pass
            # schedule async cleanup of any lingering vote messages (to remove components or delete messages)
            try:
                msgs = getattr(g, '_day_vote_messages', None) or []
                if msgs:
                    try:
                        self.bot.loop.create_task(self._async_cleanup_vote_messages(g))
                    except Exception:
                        try:
                            import asyncio
                            asyncio.create_task(self._async_cleanup_vote_messages(g))
                        except Exception:
                            pass
            except Exception:
                pass
            
            # Final state reset
            try:
                g.players = {}
                g.phase = Phase.END
                self.storage.save_game(g)
            except Exception:
                pass
        except Exception:
            logging.getLogger(__name__).exception('_fully_stop_game: unexpected error during fallback cleanup')

    async def _async_delete_wolf_channel(self, wid: int):
        """Async helper to delete a wolf channel by id. Runs in background."""
        try:
            ch = self.bot.get_channel(wid)
            if ch:
                try:
                    await ch.delete(reason='cleanup werewolf channel')
                    return
                except Exception:
                    logging.getLogger(__name__).exception('_async_delete_wolf_channel: failed to delete cached channel')
            # if not in cache, try fetch
            try:
                ch = await self.bot.fetch_channel(wid)
                try:
                    await ch.delete(reason='cleanup werewolf channel')
                except Exception:
                    logging.getLogger(__name__).exception('_async_delete_wolf_channel: failed to delete fetched channel')
            except Exception:
                logging.getLogger(__name__).exception('_async_delete_wolf_channel: failed to fetch channel')
        except Exception:
            logging.getLogger(__name__).exception('_async_delete_wolf_channel: unexpected error')

    async def _async_cleanup_vote_messages(self, g: Game):
        """Async helper: attempt to remove components from voting messages or delete them entirely.
        This runs in background and tolerates failures.
        """
        try:
            msgs = getattr(g, '_day_vote_messages', None) or []
            logger = logging.getLogger(__name__)
            for item in list(msgs):
                try:
                    # item may be dict or tuple
                    if isinstance(item, dict):
                        chid = item.get('channel_id')
                        mid = item.get('message_id')
                    elif isinstance(item, (list, tuple)):
                        chid, mid = item[0], item[1]
                    else:
                        chid = None
                        mid = None
                    if not chid or not mid:
                        logger.debug(f"Skipping invalid vote message record: {item}")
                        continue
                    try:
                        ch = self.bot.get_channel(int(chid))
                    except Exception:
                        ch = None
                    if not ch:
                        try:
                            ch = await self.bot.fetch_channel(int(chid))
                        except Exception as e:
                            logger.warning(f"Failed to fetch channel {chid} for vote cleanup: {e}")
                            ch = None
                    if not ch:
                        continue
                    try:
                        msg_obj = await ch.fetch_message(int(mid))
                    except Exception as e:
                        logger.warning(f"Failed to fetch message {mid} in channel {chid}: {e}")
                        msg_obj = None
                    if not msg_obj:
                        continue
                    # Try to edit to remove components/views first
                    try:
                        await msg_obj.edit(view=None)
                        logger.info(f"Removed components from vote message {mid} in channel {chid}")
                    except Exception as e:
                        logger.warning(f"Failed to edit vote message {mid} in channel {chid}: {e}; attempting delete")
                        try:
                            await msg_obj.delete()
                            logger.info(f"Deleted vote message {mid} in channel {chid}")
                        except Exception as e2:
                            logger.error(f"Failed to delete vote message {mid} in channel {chid}: {e2}")
                except Exception:
                    logger.exception("Error while processing a stored vote message record")
            # clear the list now that we've attempted cleanup
            try:
                g._day_vote_messages = []
            except Exception:
                logger.exception("Failed to clear g._day_vote_messages after cleanup attempt")
        except Exception:
            logging.getLogger(__name__).exception('_async_cleanup_vote_messages: unexpected error')

    def _find_game_from_channel_or_thread(self, channel_or_thread_id: int):
        """Find game from either main channel ID or thread ID.
        
        This method supports finding games when commands are executed in:
        1. The main channel where the game was created
        2. The game's dedicated thread
        """
        # First, try to load by the given channel/thread ID directly
        try:
            g = self.storage.load_game(str(channel_or_thread_id))
            if g:
                return g
        except Exception:
            pass
        
        # If not found, check if this is a thread and find the game by thread ID
        try:
            # Iterate through all games to find one with matching thread ID
            if hasattr(self.storage, 'list_games'):
                for game in self.storage.list_games():
                    try:
                        # Check if this game has a thread ID that matches
                        if (hasattr(game, '_game_thread_id') and 
                            str(game._game_thread_id) == str(channel_or_thread_id)):
                            return game
                        # Also check if the thread ID matches the game ID (main channel)
                        if str(game.game_id) == str(channel_or_thread_id):
                            return game
                    except Exception:
                        continue
        except Exception:
            pass
        
        # Alternative approach: check if this channel is a thread and get parent channel
        try:
            # If we have bot access, try to get channel info
            channel = self.bot.get_channel(channel_or_thread_id)
            if channel and hasattr(channel, 'parent_id') and channel.parent_id:
                # This is a thread, try loading game by parent channel ID
                try:
                    g = self.storage.load_game(str(channel.parent_id))
                    if g:
                        return g
                except Exception:
                    pass
        except Exception:
            pass
        
        return None
    #     prev_count = len(g.players)
    #     ok = g.join(str(interaction.user.id), interaction.user.display_name)
    #     self.storage.save_game(g)
    #     # announce join to the channel (public), not ephemeral
    #     try:
    #         await interaction.response.send_message(f"Joined: {ok}")
    #     except Exception:
    #         # fallback to channel send
    #         try:
    #             ch = interaction.channel
    #             if ch:
    #                 await ch.send(f"Joined: {ok}")
    #         except Exception:
    #             pass
    #
    #     # If we just reached the minimum player threshold, announce in channel once
    #     try:
    #         new_count = len(g.players)
    #         if prev_count < g.settings.min_players <= new_count:
    #             # announce to the channel (not ephemeral)
    #             try:
    #                 ch = interaction.channel
    #                 if ch:
    #                     await ch.send(f"Lobby has reached minimum players ({new_count}). Owner can start the game with /ww_start.")
    #             except Exception:
    #                 pass
    #     except Exception:
    #         pass

    @app_commands.command(name='ww_show_logs', description=msg('cmd_show_logs_description'))
    async def ww_show_logs(self, interaction: discord.Interaction):
        channel = interaction.channel
        if channel is None:
            await interaction.response.send_message(msg('show_logs_no_channel'), ephemeral=True)
            return
        # load game by channel or thread
        g = self._find_game_from_channel_or_thread(int(channel.id))
        if not g:
            await interaction.response.send_message(msg('show_logs_no_game'), ephemeral=True)
            return
        # owner check
        uid = str(interaction.user.id)
        if uid != g.owner_id:
            await interaction.response.send_message(msg('show_logs_not_owner'), ephemeral=True)
            return

        # Defer before building log report
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            pass

        # build report
        try:
            logs = getattr(g, 'logs', []) or []
            last_logs = logs[-100:]  # 増加：50 -> 100
            members = getattr(g, '_wolf_group_members', None)
            failures = getattr(g, '_wolf_dm_failures', None)
            errors = getattr(g, '_wolf_dm_errors', None)

            parts = []
            parts.append(msg('show_logs_logs_header', count=len(last_logs)))
            for l in last_logs:
                parts.append(f'- {l}')
            parts.append('')
            parts.append(msg('show_logs_wolf_group_members', members=members))
            parts.append(msg('show_logs_wolf_dm_failures', failures=failures))
            parts.append(msg('show_logs_wolf_dm_errors', errors=errors))

            content = '\n'.join(parts)
            
            # Discordの制限に合わせて複数メッセージに分割
            max_length = 1900
            if len(content) > max_length:
                # 複数のチャンクに分割
                chunks = []
                lines = content.split('\n')
                current_chunk = []
                current_length = 0
                
                for line in lines:
                    line_length = len(line) + 1  # +1 for newline
                    if current_length + line_length > max_length and current_chunk:
                        chunks.append('\n'.join(current_chunk))
                        current_chunk = [line]
                        current_length = line_length
                    else:
                        current_chunk.append(line)
                        current_length += line_length
                
                if current_chunk:
                    chunks.append('\n'.join(current_chunk))
                
                # 最初のチャンクを送信
                try:
                    await interaction.followup.send(chunks[0], ephemeral=True)
                except Exception:
                    try:
                        await interaction.response.send_message(chunks[0], ephemeral=True)
                    except Exception:
                        pass
                
                # 残りのチャンクを送信
                for chunk in chunks[1:]:
                    try:
                        await interaction.followup.send(chunk, ephemeral=True)
                    except Exception:
                        pass
            else:
                try:
                    await interaction.followup.send(content, ephemeral=True)
                except Exception:
                    try:
                        await interaction.response.send_message(content, ephemeral=True)
                    except Exception:
                        pass
        except Exception as e:
            logging.getLogger(__name__).error(f"ww_show_logs error: {e}")
            logging.getLogger(__name__).error(f"Traceback: {traceback.format_exc()}")
            try:
                try:
                    await interaction.followup.send(msg('log_fetch_error', error=str(e)), ephemeral=True)
                except Exception:
                    try:
                        await interaction.response.send_message(msg('log_fetch_error', error=str(e)), ephemeral=True)
                    except Exception:
                        pass
            except Exception:
                pass

    @app_commands.command(name='ww_start', description=msg('cmd_start_description'))
    @app_commands.describe(
        night_timeout=msg('arg_night_timeout'),
        day_vote_timeout=msg('arg_day_vote_timeout'),
        allow_abstain=msg('arg_allow_abstain'),
        enable_lovers=msg('arg_enable_lovers'),
        max_players=msg('param_max_players_description'),
        min_players=msg('param_min_players_description')
    )
    async def start(self, interaction: discord.Interaction, 
                   night_timeout: Optional[int] = None, 
                   day_vote_timeout: Optional[int] = None, 
                   allow_abstain: Optional[bool] = None, 
                   enable_lovers: Optional[bool] = None,
                   max_players: int = 15,
                   min_players: int = 4):
        """Start a werewolf game with voice chat participants."""
        
        # Load game settings from JSON to use as defaults when parameters are None
        saved_settings = None
        try:
            repo_root = Path(__file__).resolve().parents[1]
            settings_path = repo_root / 'roles' / 'game_settings.json'
            if settings_path.exists():
                try:
                    saved_settings = json.loads(settings_path.read_text(encoding='utf-8'))
                    logging.getLogger(__name__).info(f"Loaded game_settings.json for /ww_start defaults: {saved_settings}")
                except Exception as e:
                    logging.getLogger(__name__).warning(f"Failed to parse game_settings.json: {e}")
        except Exception as e:
            logging.getLogger(__name__).warning(f"Failed to load game_settings.json: {e}")
        
        # Apply saved settings as defaults when command parameters are None
        if saved_settings and isinstance(saved_settings, dict):
            # Set timeout defaults from saved settings
            if night_timeout is None and 'night_minutes' in saved_settings:
                try:
                    raw = saved_settings.get('night_minutes')
                    v = int(raw) if raw is not None and str(raw).strip() != '' else 0
                    night_timeout = (v * 60) if v > 0 else None
                    logging.getLogger(__name__).info(f"Using saved night_timeout: {night_timeout}s (from {v} minutes)")
                except Exception:
                    pass
                    
            if day_vote_timeout is None and 'vote_minutes' in saved_settings:
                try:
                    raw = saved_settings.get('vote_minutes')
                    v = int(raw) if raw is not None and str(raw).strip() != '' else 0
                    day_vote_timeout = (v * 60) if v > 0 else None
                    logging.getLogger(__name__).info(f"Using saved day_vote_timeout: {day_vote_timeout}s (from {v} minutes)")
                except Exception:
                    pass
                    
            # Set other defaults from saved settings
            if enable_lovers is None and 'lovers' in saved_settings:
                try:
                    enable_lovers = bool(saved_settings.get('lovers'))
                    logging.getLogger(__name__).info(f"Using saved enable_lovers: {enable_lovers}")
                except Exception:
                    pass
                    
            # Note: allow_abstain defaults are typically handled elsewhere, 
            # but can be added here if needed in the future
        
        # Defer immediately to avoid 3-second timeout
        try:
            await interaction.response.defer(ephemeral=False)
        except Exception:
            pass
        
        # Check for existing active game
        existing = self.storage.load_game(str(interaction.channel_id))
        if existing and getattr(existing, 'phase', None) not in (None, Phase.END, Phase.CLOSED):
            try:
                await interaction.followup.send(msg('start_existing_game'), ephemeral=True)
            except Exception:
                pass
            return
        
        # Get voice channel and participants
        try:
            voice_participants = await self._get_voice_chat_participants(interaction)
            if not voice_participants:
                try:
                    await interaction.followup.send(msg('start_no_voice'), ephemeral=True)
                except Exception:
                    pass
                return
            
            if len(voice_participants) < min_players:
                try:
                    await interaction.followup.send(
                        msg('start_insufficient_players', min_players=min_players, current_players=len(voice_participants)), 
                        ephemeral=True
                    )
                except Exception:
                    pass
                return
                
            if len(voice_participants) > max_players:
                try:
                    await interaction.followup.send(
                        msg('start_too_many_players', max_players=max_players, current_players=len(voice_participants)), 
                        ephemeral=True
                    )
                except Exception:
                    pass
                return
                
        except Exception as e:
            logging.getLogger(__name__).error(f"Failed to get voice participants: {e}")
            try:
                await interaction.followup.send(msg('start_voice_error'), ephemeral=True)
            except Exception:
                pass
            return
        
        # Create and start game
        try:
            await self._create_and_start_game_with_participants(
                interaction, voice_participants, night_timeout, day_vote_timeout, 
                allow_abstain, enable_lovers, max_players, min_players
            )
        except Exception as e:
            logging.getLogger(__name__).error(f"Failed to create and start game: {e}")
            try:
                await interaction.followup.send(msg('start_creation_failed'), ephemeral=True)
            except Exception:
                pass

    # If explicit arguments were provided to /ww_start, treat them as per-game overrides
        try:
            if enable_lovers is not None and hasattr(g, 'settings'):
                g.settings.lovers_enabled = bool(enable_lovers)
            logging.getLogger(__name__).info(f"/ww_start override: enable_lovers -> {enable_lovers}")
        except Exception:
            pass
        ok = g.start()
        # Reset night counter and sequence flag at game start
        try:
            g._night_count = 0
            g._night_sequence_started = False
        except Exception:
            pass
        self.storage.save_game(g)
        # Apply runtime overrides immediately so subsequent UI/embeds reflect them
        try:
            try:
                # night_timeout is already set from saved settings if None, so just use the value
                g._runtime_night_timeout = int(night_timeout) if night_timeout is not None else None
            except Exception:
                g._runtime_night_timeout = None
            try:
                # day_vote_timeout is already set from saved settings if None, so just use the value
                g._runtime_day_vote_timeout = int(day_vote_timeout) if day_vote_timeout is not None else None
                # Store the original timeout for resetting after guesser actions
                g._original_day_vote_timeout = g._runtime_day_vote_timeout
                
                # CRITICAL: Load fixed vote timeout from game_settings.json to prevent /ww_end_vote issues
                g._fixed_vote_timeout = None
                try:
                    import json
                    import os
                    settings_path = os.path.join(os.path.dirname(__file__), '..', 'roles', 'game_settings.json')
                    if os.path.exists(settings_path):
                        with open(settings_path, 'r', encoding='utf-8') as f:
                            settings = json.load(f)
                        vote_minutes = settings.get('vote_minutes', 3)  # Default 3 minutes
                        g._fixed_vote_timeout = int(vote_minutes * 60) if vote_minutes > 0 else None
                        g.log(f"GAME INIT: Loaded fixed vote timeout from settings: {vote_minutes} minutes = {g._fixed_vote_timeout}s")
                    else:
                        g._fixed_vote_timeout = 180  # Default 3 minutes in seconds
                        g.log(f"GAME INIT: Using default fixed vote timeout: {g._fixed_vote_timeout}s")
                except Exception as e:
                    g._fixed_vote_timeout = 180  # Fallback to 3 minutes
                    g.log(f"GAME INIT: Failed to load vote timeout from settings, using default: {g._fixed_vote_timeout}s - Error: {e}")
                
                # CRITICAL: Clear all forced end states to prevent issues
                g._forced_end_vote = False
                g._emergency_vote_reset = False
                g._vote_invalidated_by_guess = False
                g._vote_finalized = False
                g._in_re_vote_after_guess = False
                g._next_turn_reset_vote_time = False
                # Clear guesser session tracking to prevent dead announcement issues
                g._current_vote_session_id = None
                g._guesser_session_id = None
                g.log("GAME INIT: Cleared all forced end and guesser states")
                
                g.log(f"GAME INIT: Set original day vote timeout to {g._original_day_vote_timeout}s, fixed timeout to {g._fixed_vote_timeout}s")
            except Exception:
                g._runtime_day_vote_timeout = None
                g._original_day_vote_timeout = None
                g._fixed_vote_timeout = 180  # Default fallback
            try:
                if allow_abstain is not None:
                    g._runtime_allow_abstain = bool(allow_abstain)
                else:
                    if saved_settings and isinstance(saved_settings, dict) and 'allow_abstain' in saved_settings:
                        try:
                            g._runtime_allow_abstain = bool(saved_settings.get('allow_abstain'))
                        except Exception:
                            g._runtime_allow_abstain = True
                    else:
                        g._runtime_allow_abstain = True
            except Exception:
                pass
        except Exception:
            # best-effort only; ignore failures here and continue
            pass
        if not ok:
            await interaction.response.send_message(msg('start_failed'), ephemeral=True)
            return
        # defer response to avoid "application did not respond" if DMing takes time
        try:
            await interaction.response.defer()
        except Exception:
            # if defer fails, continue and attempt to send via response later
            pass

        # DM roles to players (may take time)
        failed_role_dms: List[str] = []
        for pid, p in g.players.items():
            try:
                user = await self.bot.fetch_user(int(pid))
                role_id = g.get_player_role(pid)
                # Prefer localized/display name from g.roles if available
                try:
                    robj = g.roles.get(role_id) if role_id and getattr(g, 'roles', None) else None
                    if robj and getattr(robj, 'name', None):
                        display_role = robj.name
                    else:
                        display_role = role_id or 'unknown'
                except Exception:
                    display_role = role_id or 'unknown'
                await user.send(msg('role_dm', role=display_role))
                # If this player is a guesser, provide an extra hint about /ww_guess usage
                try:
                    if role_id in ('nice_guesser', 'evil_guesser'):
                        try:
                            # Get the configured limit for this role
                            limit = 1  # default
                            try:
                                limit = g._guess_limit_for_role(role_id) if hasattr(g, '_guess_limit_for_role') else 1
                            except Exception:
                                limit = 1
                            await user.send(msg('guess_role_dm_hint', limit=limit))
                        except Exception:
                            pass
                except Exception:
                    pass
            except Exception:
                # record DM failure for later public notification
                failed_role_dms.append(pid)

        # Reminders for designated VC are sent at creation time via /ww_create; start focuses on DMing roles and launching night sequence.
        # Send VC join reminders now (players typically join VC at ww_join timing; remind at start to be safe)
        try:
            guild = interaction.guild
            for pid, p in g.players.items():
                try:
                    member = None
                    if guild:
                        try:
                            member = guild.get_member(int(pid)) or await guild.fetch_member(int(pid))
                        except Exception:
                            member = None
                    in_designated_vc = False
                    if member and getattr(member, 'voice', None) and getattr(member.voice, 'channel', None):
                        vc = member.voice.channel
                        try:
                            designated = getattr(g, '_designated_vc_id', None)
                            if designated is None:
                                in_designated_vc = True
                            else:
                                in_designated_vc = (vc.id == designated)
                        except Exception:
                            in_designated_vc = True
                    if not in_designated_vc:
                        try:
                            user = await self.bot.fetch_user(int(pid))
                            if getattr(g, '_designated_vc_id', None):
                                try:
                                    ch = await self.bot.fetch_channel(int(g._designated_vc_id))
                                    vc_name = ch.name if ch and hasattr(ch, 'name') else '指定されたボイスチャンネル'
                                except Exception:
                                    vc_name = '指定されたボイスチャンネル'
                                await user.send(msg('vc_reminder_designated', vc_name=vc_name))
                            else:
                                await user.send(msg('vc_reminder_generic'))
                        except Exception:
                            pass
                except Exception:
                    pass
        except Exception:
            pass

        # Deliver any engine-generated private messages (one-time queued messages)
        failed_private_dms: List[str] = []
        try:
            for pid, msgs in list(getattr(g, 'private_messages', {}).items()):
                try:
                    user = await self.bot.fetch_user(int(pid))
                    for m in msgs:
                        try:
                            # Skip queued seer results to avoid duplicate DMs; seer gets immediate feedback in interaction
                            try:
                                if isinstance(m, dict) and m.get('key') in ('seer_result', 'seer_result_followup'):
                                    delivered = getattr(g, '_seer_results_delivered', set())
                                    # if this seer result has already been delivered via RunButton, skip it
                                    if str(pid) in delivered:
                                        continue
                            except Exception:
                                pass
                            rendered = _format_private_message_for_send(m)
                            await user.send(rendered)
                        except Exception:
                            # record failure but continue
                            if pid not in failed_private_dms:
                                failed_private_dms.append(pid)
                        except Exception:
                            # record failure but continue
                            if pid not in failed_private_dms:
                                failed_private_dms.append(pid)
                except Exception:
                    if pid not in failed_private_dms:
                        failed_private_dms.append(pid)
            # clear private messages after attempting delivery
            g.private_messages = {}
        except Exception:
            pass

        # If any DM failures occurred during role/private DMing, notify the channel so staff can act
        try:
            failures: List[str] = []
            for pid in set(failed_role_dms + failed_private_dms):
                p = g.players.get(pid)
                if p:
                    failures.append(p.name)
                else:
                    failures.append(pid)
            if failures:
                names = ", ".join(failures)
                try:
                    await interaction.followup.send(msg('dm_failed_notice', names=names))
                except Exception:
                    try:
                        ch = interaction.channel
                        if ch:
                            await ch.send(msg('dm_failed_notice', names=names))
                    except Exception:
                        pass
        except Exception:
            pass

        # send followup since we deferred earlier
        try:
            # Build participant list and role distribution (counts only)
            try:
                participant_names = [p.name for p in g.players.values()]
                total = len(participant_names)
                # count roles by role_id
                role_counts: Dict[str, int] = {}
                for p in g.players.values():
                    rid = p.role_id or 'unknown'
                    role_counts[rid] = role_counts.get(rid, 0) + 1

                # Map role ids to display names where possible
                lines = []
                lines.append(f"Players ({total}): {', '.join(participant_names)}")
                lines.append("")
                lines.append("Role distribution (counts):")
                # Use role ordering from g.roles if available
                try:
                    role_order = list(getattr(g, 'roles', {}).keys())
                except Exception:
                    role_order = []
                # append counts in that order, fall back to any remaining roles
                appended = set()
                for rid in role_order:
                    cnt = role_counts.get(rid, 0)
                    # skip roles with zero count
                    if not cnt:
                        continue
                    rname = g.roles.get(rid).name if g.roles.get(rid) else rid
                    lines.append(f"- {rname}: {cnt}")
                    appended.add(rid)
                for rid, cnt in sorted(role_counts.items(), key=lambda x: x[0]):
                    if rid in appended:
                        continue
                    # skip zero-count roles
                    if not cnt:
                        continue
                    rname = g.roles.get(rid).name if g.roles.get(rid) else rid
                    lines.append(f"- {rname}: {cnt}")
                summary = "\n".join(lines)
            except Exception:
                summary = "Game started. (failed to build participant summary)"

            # Removed: Game start notification to main channel for simplified UI
        except Exception:
            # Removed: Fallback game start message for simplified UI
            pass

        # Create game thread and status panel
        try:
            await self._create_game_thread_and_status(g, interaction)
        except Exception as e:
            logging.getLogger(__name__).error(f"Failed to create game thread: {e}")
            # Continue without thread if creation fails

        # Kick off night sequence task (non-blocking) and save reference for cancellation
        try:
            # apply runtime override for night timeout and day vote timeout if provided
            try:
                if night_timeout is not None:
                    g._runtime_night_timeout = int(night_timeout)
                else:
                    # explicit None => infinite
                    g._runtime_night_timeout = None
                if day_vote_timeout is not None:
                    g._runtime_day_vote_timeout = int(day_vote_timeout)
                    # Update original timeout when explicitly changed
                    g._original_day_vote_timeout = int(day_vote_timeout)
                    # CRITICAL: Also update fixed timeout to maintain consistency
                    g._fixed_vote_timeout = int(day_vote_timeout)
                    g.log(f"CONFIG UPDATE: Updated all vote timeouts to {day_vote_timeout}s")
                else:
                    g._runtime_day_vote_timeout = None
                    g._original_day_vote_timeout = None
                    # Keep fixed timeout as-is for None values to prevent infinite votes
                # set abstain allowance flag: allow_abstain True -> allow abstain
                try:
                    g._runtime_allow_abstain = bool(allow_abstain)
                except Exception:
                    pass
                # day_discussion argument removed; discussion time is merged into vote timeout
            except Exception:
                pass
            task = self.bot.loop.create_task(self._run_night_sequence(g, int(interaction.channel_id)))
            g._night_sequence_task = task
        except Exception:
            # fallback to asyncio.create_task
            try:
                if night_timeout is not None:
                    g._runtime_night_timeout = int(night_timeout)
            except Exception:
                pass
            task = asyncio.create_task(self._run_night_sequence(g, int(interaction.channel_id)))
            try:
                g._night_sequence_task = task
            except Exception:
                pass

    @app_commands.command(name='ww_close', description=msg('cmd_close_description'))
    async def close(self, interaction: discord.Interaction):
        # Defer immediately to avoid 3-second timeout
        try:
            await interaction.response.defer(ephemeral=False)
        except Exception:
            # If defer fails, continue anyway
            pass
        
        g = self._find_game_from_channel_or_thread(interaction.channel_id)
        # If there's no game, report no lobby
        if not g:
            try:
                await interaction.followup.send(msg('no_lobby_in_channel'), ephemeral=True)
            except Exception:
                try:
                    await interaction.response.send_message(msg('no_lobby_in_channel'), ephemeral=True)
                except Exception:
                    pass
            return
        
        # Owner permission check
        if str(interaction.user.id) != g.owner_id:
            try:
                await interaction.followup.send(msg('only_owner_close'), ephemeral=True)
            except Exception:
                try:
                    await interaction.response.send_message(msg('only_owner_close'), ephemeral=True)
                except Exception:
                    pass
            return

        # FORCE GAME SHUTDOWN: Ensure game is stopped regardless of current state
        success = False
        try:
            # CRITICAL: Force the game phase to CLOSED immediately to prevent any further operations
            original_phase = g.phase
            try:
                g.phase = Phase.CLOSED
                g.log(f"ww_close: FORCED phase transition from {original_phase} to CLOSED")
            except Exception as e:
                g.log(f"ww_close: Failed to set CLOSED phase: {e}")
                # Fallback to END if CLOSED fails
                try:
                    g.phase = Phase.END
                    g.log(f"ww_close: Fallback to END phase")
                except Exception:
                    pass
            
            # Force-save the CLOSED state immediately
            try:
                self.storage.save_game(g)
                g.log(f"ww_close: Saved CLOSED state to storage")
            except Exception as e:
                g.log(f"ww_close: Failed to save CLOSED state: {e}")
            
            # Cancel all tasks BEFORE deleting from storage to prevent zombie operations
            try:
                self._fully_stop_game(g)
            except Exception:
                logging.getLogger(__name__).exception('ww_close: failed to fully stop game before deletion')

            # Unmute all players first
            try:
                guild = interaction.guild
                if guild and getattr(g, 'players', None):
                    for pid, p in list(g.players.items()):
                        try:
                            member = guild.get_member(int(pid)) or await guild.fetch_member(int(pid))
                            if member:
                                await member.edit(mute=False)
                        except Exception:
                            try:
                                g.log(f"Failed to unmute {pid} during ww_close")
                            except Exception:
                                pass
            except Exception:
                try:
                    g.log("Error while attempting to unmute participants during ww_close")
                except Exception:
                    pass

            # Try multiple methods to ensure deletion
            deletion_attempted = False
            
            # Method 1: Use storage delete API if present
            if hasattr(self.storage, 'delete_game'):
                try:
                    self.storage.delete_game(str(interaction.channel_id))
                    deletion_attempted = True
                    success = True
                except Exception as e:
                    logging.getLogger(__name__).warning(f"Failed to delete game via storage API: {e}")

            # Method 2: If storage doesn't support delete or it failed, clear the game manually
            if not deletion_attempted:
                try:
                    # Clear all game data and mark as ended
                    g.players = {}
                    g.phase = Phase.END
                    # Clear all pending data
                    for attr in ('_pending_votes', '_pending_night_choices', '_night_events', 
                               '_active_vote_views', '_wolf_channel_id', '_day_vote_messages'):
                        try:
                            if hasattr(g, attr):
                                if attr == '_night_events':
                                    # Signal all events before clearing
                                    events = getattr(g, attr, {}) or {}
                                    for ev in events.values():
                                        try:
                                            if hasattr(ev, 'set'):
                                                ev.set()
                                        except Exception:
                                            pass
                                setattr(g, attr, {} if 'events' in attr or 'votes' in attr or 'choices' in attr else [])
                        except Exception:
                            pass
                    self.storage.save_game(g)
                    success = True
                except Exception as e:
                    logging.getLogger(__name__).warning(f"Failed to manually clear game: {e}")

            # Method 3: If all else fails, remove from storage directly
            if not success:
                try:
                    if hasattr(self.storage, '_games') and str(interaction.channel_id) in self.storage._games:
                        del self.storage._games[str(interaction.channel_id)]
                        success = True
                except Exception as e:
                    logging.getLogger(__name__).warning(f"Failed to delete from storage._games: {e}")

        except Exception as e:
            logging.getLogger(__name__).error(f"Critical error in ww_close: {e}")

        # Always report success to the user - the game should be stopped by now
        try:
            message = msg('lobby_closed_removed') if success else msg('game_force_ended_cleanup_partial')
            try:
                await interaction.followup.send(message)
            except Exception:
                try:
                    await interaction.response.send_message(message)
                except Exception:
                    try:
                        ch = interaction.channel
                        if ch:
                            await ch.send(message)
                    except Exception:
                        pass
        except Exception:
            logging.getLogger(__name__).error("Failed to send close confirmation message")

        # Final attempt: ensure the game is completely removed from memory
        try:
            if hasattr(self.storage, '_games') and str(interaction.channel_id) in self.storage._games:
                del self.storage._games[str(interaction.channel_id)]
        except Exception:
            pass

    @app_commands.command(name='ww_status', description=msg('cmd_status_description'))
    async def status(self, interaction: discord.Interaction):
        g = self._find_game_from_channel_or_thread(interaction.channel_id)
        if not g:
            await interaction.response.send_message(msg('no_lobby_in_channel'), ephemeral=True)
            return
        # Defer before iterating players
        try:
            await interaction.response.defer(ephemeral=False)
        except Exception:
            pass
        lines = [f"Phase: {g.phase.name}", "Players:"]
        for p in g.players.values():
            lines.append(f"- {p.name} ({'alive' if p.alive else 'dead'})")
        try:
            await interaction.followup.send("\n".join(lines))
        except Exception:
            try:
                await interaction.response.send_message("\n".join(lines))
            except Exception:
                pass

    @app_commands.command(name='ww_end_night', description=msg('cmd_end_night_description'))
    async def end_night(self, interaction: discord.Interaction):
        """Admin-only: end the NIGHT phase early.
        If some players have not completed night actions, show an ephemeral OK/Cancel confirmation to the invoker.
        On OK, force-night-end: run night_actions with current pending choices, advance to CHECK_WIN, DM incomplete players that the night was forcibly ended.
        """
        g = self._find_game_from_channel_or_thread(interaction.channel_id)
        if not g:
            await interaction.response.send_message(msg('no_lobby_in_channel'), ephemeral=True)
            return

        # Defer before permission check and pending determination
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            pass

        # permission: only guild administrators (manage_guild) may execute
        is_admin = False
        try:
            member = None
            if interaction.guild_id:
                guild = interaction.guild
                if guild:
                    try:
                        member = guild.get_member(interaction.user.id) or await guild.fetch_member(interaction.user.id)
                    except Exception:
                        member = None
            if member and member.guild_permissions.administrator:
                is_admin = True
        except Exception:
            is_admin = False

        if not is_admin:
            try:
                await interaction.followup.send(msg('start_only_owner'), ephemeral=True)
            except Exception:
                try:
                    await interaction.response.send_message(msg('start_only_owner'), ephemeral=True)
                except Exception:
                    pass
            return

        # Only valid during NIGHT
        if g.phase != Phase.NIGHT:
            try:
                await interaction.followup.send(msg('internal_error_short', error='Not in NIGHT phase'), ephemeral=True)
            except Exception:
                try:
                    await interaction.response.send_message(msg('internal_error_short', error='Not in NIGHT phase'), ephemeral=True)
                except Exception:
                    pass
            return

        # Determine players who haven't completed night actions
        pending = []
        try:
            pending_choices = getattr(g, '_pending_night_choices', {}) or {}
            for pid, p in g.players.items():
                if not p.alive:
                    continue
                # Treat absence from pending_choices or empty selection as not-completed
                if str(pid) not in pending_choices or not pending_choices.get(str(pid)):
                    pending.append(pid)
        except Exception:
            pending = []

        # If there are pending, show ephemeral confirmation with OK / Cancel buttons
        if pending:
            view = ConfirmEndView(owner_id=interaction.user.id)
            # Send ephemeral confirmation only to the invoker (owner) with OK/Cancel view.
            # If ephemeral sending fails (e.g., permissions), fall back to a public channel announcement.
            sent_ephemeral = False
            try:
                await interaction.followup.send(msg('confirm_end_night'), ephemeral=True, view=view)
                sent_ephemeral = True
            except Exception:
                # try response if followup fails
                try:
                    await interaction.response.send_message(msg('confirm_end_night'), ephemeral=True, view=view)
                    sent_ephemeral = True
                except Exception:
                    sent_ephemeral = False

            if not sent_ephemeral:
                # fallback: publicly announce the pending forced-night end so all players see the prompt
                try:
                    ch = interaction.channel
                    if ch:
                        try:
                            await ch.send(msg('confirm_end_night'))
                        except Exception:
                            pass
                except Exception:
                    pass

            # wait for the invoker to press a button or timeout
            await view.wait()
            if not getattr(view, 'result', False):
                # canceled or timed out
                return

        # Proceed to force night end
        try:
            # run night actions with current pending choices (no-op for missing)
            try:
                g.night_actions(getattr(g, '_pending_night_choices', {}))
            except Exception as e:
                try:
                    g.log(f"Force night end: night_actions error: {e}")
                except Exception:
                    pass

            # Advance and run win check robustly: if no winner, ensure we move to DAY (not back to NIGHT)
            winner = None
            try:
                # Temporarily set phase to CHECK_WIN so engine.check_win can run its logic
                prev_phase = getattr(g, 'phase', None)
                try:
                    g.phase = Phase.CHECK_WIN
                    winner = g.check_win()
                finally:
                    # If no winner, ensure we transition to DAY (night_actions likely set DAY already)
                    if not winner:
                        try:
                            g.phase = Phase.DAY
                            g.log('Forced end: moved to DAY')
                        except Exception:
                            pass
            except Exception:
                try:
                    # best-effort: ensure DAY if check failed
                    g.phase = Phase.DAY
                except Exception:
                    pass

            # Notify players who had not completed actions via DM
            try:
                for pid in pending:
                    try:
                        uid = int(pid)
                        user = None
                        try:
                            user = await self.bot.fetch_user(uid)
                        except Exception:
                            try:
                                user = self.bot.get_user(uid)
                            except Exception:
                                user = None
                        if user:
                            try:
                                await user.send(msg('night_forced_dm'))
                            except Exception:
                                pass
                    except Exception:
                        pass
            except Exception:
                pass

            self.storage.save_game(g)
            # send a confirmation in channel (public)
            try:
                ch = interaction.channel
                if ch:
                    await ch.send(msg('left_lobby_result', result='Night forced to end'))
            except Exception:
                # best-effort only: log the failure to announce
                try:
                    g.log('Failed to send forced-night confirmation to channel')
                except Exception:
                    pass

            # Unmute alive players (permission-guarded) so day conversation can proceed
            try:
                await self._unmute_all_participants(g, interaction.channel, only_alive=True)
            except Exception:
                try:
                    g.log('Failed to unmute participants after forced night end')
                except Exception:
                    pass

            # Immediately start the day vote UI so forced end behaves like a normal night->day transition
            try:
                # ALWAYS use the fixed vote timeout from game settings to prevent /ww_end_vote issues
                vote_timeout = getattr(g, '_fixed_vote_timeout', None)
                g.log(f"FORCED NIGHT END: Using fixed vote timeout: {vote_timeout}s")
                
                # CRITICAL: Clear guesser re-vote flag when starting new day to show death announcements
                g._in_re_vote_after_guess = False
                g.log("FORCED NIGHT END: Cleared re-vote flag for new day")
                
                ch = interaction.channel
                if ch:
                    if vote_timeout is None:
                        await self._start_day_vote_channel(g, ch, None)
                    else:
                        await self._start_day_vote_channel(g, ch, int(vote_timeout))
            except Exception:
                try:
                    g.log('Failed to start day vote UI after forced night end')
                except Exception:
                    pass
        except Exception as e:
            try:
                await interaction.response.send_message(msg('internal_error_short', error=str(e)), ephemeral=True)
            except Exception:
                pass

    @app_commands.command(name='ww_pause', description=msg('cmd_pause_description'))
    async def ww_pause(self, interaction: discord.Interaction):
        # Find the game in this channel
        channel = interaction.channel
        if channel is None:
            await interaction.response.send_message('コマンドはチャンネル内で実行してください。', ephemeral=True)
            return
        # Defer before game search and storage iteration
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            pass
        # load game by channel or thread ID
        g = self._find_game_from_channel_or_thread(int(channel.id))
        # Fallback: iterate games if helper method fails
        if not g:
            for game in self.storage.list_games():
                try:
                    # Check both game channel and thread
                    if (getattr(game, '_channel_id', None) == int(channel.id) or
                        getattr(game, '_game_thread_id', None) == int(channel.id) or
                        str(game.game_id) == str(channel.id)):
                        g = game
                        break
                except Exception:
                    continue
        if not g:
            try:
                await interaction.followup.send('このチャンネルで動作するゲームが見つかりません。', ephemeral=True)
            except Exception:
                try:
                    await interaction.response.send_message('このチャンネルで動作するゲームが見つかりません。', ephemeral=True)
                except Exception:
                    pass
            return
        # Only owner or staff may pause
        uid = str(interaction.user.id)
        if uid != g.owner_id and not interaction.user.guild_permissions.manage_guild:
            try:
                await interaction.followup.send('オーナーまたは権限のあるユーザのみ実行できます。', ephemeral=True)
            except Exception:
                try:
                    await interaction.response.send_message('オーナーまたは権限のあるユーザのみ実行できます。', ephemeral=True)
                except Exception:
                    pass
            return
        # Set paused flag and clear pause_event so waiters block
        try:
            g._paused = True
            # ensure pause_event exists and is cleared
            pe = getattr(g, '_pause_event', None)
            if pe is None:
                g._pause_event = asyncio.Event()
                pe = g._pause_event
            try:
                pe.clear()
            except Exception:
                pass
        except Exception:
            pass
        try:
            await interaction.followup.send(msg('game_paused'), ephemeral=True)
        except Exception:
            try:
                await interaction.response.send_message(msg('game_paused'), ephemeral=True)
            except Exception:
                pass

    @app_commands.command(name='ww_resume', description=msg('cmd_resume_description'))
    async def ww_resume(self, interaction: discord.Interaction):
        channel = interaction.channel
        if channel is None:
            await interaction.response.send_message('コマンドはチャンネル内で実行してください。', ephemeral=True)
            return
        # Defer before game search and storage iteration
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            pass
        g = self._find_game_from_channel_or_thread(int(channel.id))
        if not g:
            try:
                await interaction.followup.send('このチャンネルで動作するゲームが見つかりません。', ephemeral=True)
            except Exception:
                try:
                    await interaction.response.send_message('このチャンネルで動作するゲームが見つかりません。', ephemeral=True)
                except Exception:
                    pass
            return
        uid = str(interaction.user.id)
        if uid != g.owner_id and not interaction.user.guild_permissions.manage_guild:
            try:
                await interaction.followup.send('オーナーまたは権限のあるユーザのみ実行できます。', ephemeral=True)
            except Exception:
                try:
                    await interaction.response.send_message('オーナーまたは権限のあるユーザのみ実行できます。', ephemeral=True)
                except Exception:
                    pass
            return
        try:
            g._paused = False
            pe = getattr(g, '_pause_event', None)
            if pe is None:
                g._pause_event = asyncio.Event()
                pe = g._pause_event
            try:
                pe.set()
            except Exception:
                pass
        except Exception:
            pass
        try:
            await interaction.response.send_message(msg('game_resumed'), ephemeral=True)
        except Exception:
            pass

    @app_commands.command(name='ww_end_vote', description=msg('cmd_end_vote_description'))
    async def ww_end_vote(self, interaction: discord.Interaction):
        """Owner-only command to end an active DAY/VOTE early. Shows a confirmation (OK/Cancel) similar to /ww_end_night."""
        # Defer immediately to avoid 3-second timeout
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            # If defer fails, continue anyway
            pass
        
        channel = interaction.channel
        if channel is None:
            try:
                await interaction.followup.send(msg('command_channel_only'), ephemeral=True)
            except Exception:
                try:
                    await interaction.response.send_message(msg('command_channel_only'), ephemeral=True)
                except Exception:
                    pass
            return
        g = self._find_game_from_channel_or_thread(int(channel.id))
        if not g:
            try:
                await interaction.followup.send(msg('no_game_found'), ephemeral=True)
            except Exception:
                pass
            return

        uid = str(interaction.user.id)
        if uid != g.owner_id:
            try:
                await interaction.followup.send(msg('owner_only'), ephemeral=True)
            except Exception:
                pass
            return

        # Only valid when in VOTE phase
        if g.phase != Phase.VOTE:
            try:
                await interaction.followup.send(msg('not_vote_phase'), ephemeral=True)
            except Exception:
                pass
            return

        # Determine players who haven't recorded pending votes
        # Normalize IDs to strings because UI callbacks and other code use string ids.
        alive_ids = [str(p.id) for p in g.players.values() if p.alive]
        raw_pending = getattr(g, '_pending_votes', {}) or {}
        pending = {}
        try:
            for k, v in raw_pending.items():
                try:
                    pending[str(k)] = v
                except Exception:
                    pending[k] = v
        except Exception:
            pending = raw_pending or {}

        # Treat '__invalid__' or None as not-yet-voted for the owner confirmation
        not_voted = [pid for pid in alive_ids if pid not in pending or pending.get(pid) is None or pending.get(pid) == '__invalid__']

        # If there are non-voters, show ephemeral confirmation with OK/Cancel
        if not_voted:
            # Send warning DM to non-voters before showing confirmation
            for pid in not_voted:
                try:
                    user = await self.bot.fetch_user(int(pid))
                    try:
                        await user.send(msg('vote_ending_soon_dm'))
                    except Exception:
                        # Ignore DM failures for warning message
                        pass
                except Exception:
                    pass

            view = ConfirmEndVoteView(owner_id=interaction.user.id)
            try:
                await interaction.followup.send(msg('confirm_end_vote'), ephemeral=True, view=view)
            except Exception:
                try:
                    # fallback in case defer failed
                    await interaction.response.send_message(msg('confirm_end_vote'), ephemeral=True, view=view)
                except Exception:
                    try:
                        await interaction.followup.send(msg('internal_error_short', error='confirmation_failed'), ephemeral=True)
                    except Exception:
                        pass
                    return

            await view.wait()
            if not getattr(view, 'result', False):
                # canceled
                return

            # on OK: mark non-responders as invalid so resolution treats them as timeout/nop
            try:
                for pid in not_voted:
                    try:
                        pending[str(pid)] = '__invalid__'
                    except Exception:
                        try:
                            pending[pid] = '__invalid__'
                        except Exception:
                            pass
                # store normalized pending back on the game
                try:
                    g._pending_votes = pending
                except Exception:
                    # fallback: try to set original attr directly
                    try:
                        setattr(g, '_pending_votes', pending)
                    except Exception:
                        pass
            except Exception:
                pass
            # Notify non-responders by DM about forced invalidation
            # Skip DM notification if we're in a re-vote after guess (will restart voting)
            try:
                in_re_vote_after_guess = getattr(g, '_in_re_vote_after_guess', False)
                if not in_re_vote_after_guess:
                    failed_private_dms: List[str] = []
                    for pid in not_voted:
                        try:
                            user = await self.bot.fetch_user(int(pid))
                            try:
                                await user.send(msg('vote_invalid_dm'))
                            except Exception:
                                if pid not in failed_private_dms:
                                    failed_private_dms.append(pid)
                        except Exception:
                            if pid not in failed_private_dms:
                                failed_private_dms.append(pid)

                # If there were DM failures, notify the operator in-channel or via followup
                if failed_private_dms:
                    failures = []
                    for pid in failed_private_dms:
                        p = g.players.get(pid)
                        failures.append(p.name if p else pid)
                    names = ", ".join(failures)
                    try:
                        # prefer ephemeral followup to the invoking interaction
                        try:
                            await interaction.followup.send(msg('dm_failed_notice', names=names), ephemeral=True)
                        except Exception:
                            ch = interaction.channel
                            if ch:
                                await ch.send(msg('dm_failed_notice', names=names))
                    except Exception:
                        pass
            except Exception:
                pass

        # Stop any active vote views and allow main loop to proceed to resolution
        try:
            # Check both old and new view tracking attributes
            active_old = getattr(g, '_active_vote_views', []) or []
            active_new = getattr(g, '_active_voting_views', []) or []
            all_active = active_old + active_new
            g.log(f"FORCED END VOTE: Found {len(active_old)} old views, {len(active_new)} new views (total: {len(all_active)})")
            
            for v in list(all_active):
                try:
                    g.log(f"FORCED END VOTE: Stopping voting view {type(v).__name__}")
                    v.stop()
                    # Also mark view as disabled to prevent further interactions
                    try:
                        v._forced_disabled = True
                        # Disable all children (selects/buttons)
                        for item in v.children:
                            try:
                                item.disabled = True
                                g.log(f"FORCED END VOTE: Disabled UI component {type(item).__name__}")
                            except Exception:
                                pass
                    except Exception:
                        pass
                    g.log(f"FORCED END VOTE: Successfully stopped and disabled voting view")
                except Exception as e:
                    g.log(f"FORCED END VOTE: Error stopping view: {e}")
            
            # Clear both tracking lists
            try:
                g._active_vote_views = []
                g._active_voting_views = []
                g.log("FORCED END VOTE: Cleared all active view lists")
            except Exception:
                pass
                
            # Update the voting message to show it's ended
            try:
                voting_msg_id = getattr(g, '_voting_message_id', None)
                g.log(f"FORCED END VOTE: Retrieved voting message ID: {voting_msg_id}")
                if voting_msg_id:
                    # Create "ended" embed
                    ended_embed = discord.Embed(
                        title="🛑 投票終了",
                        description="管理者により投票が強制終了されました。\n結果を集計中です...",
                        color=0xFF0000,
                        timestamp=self._get_jst_timestamp()
                    )
                    
                    # Update the voting message
                    try:
                        # Get the game thread using the stored thread ID
                        thread_id = getattr(g, '_game_thread_id', None)
                        g.log(f"FORCED END VOTE: Thread ID from game: {thread_id}")
                        
                        if thread_id:
                            try:
                                thread = self.bot.get_channel(int(thread_id))
                                if not thread:
                                    # Try to fetch it if not in cache
                                    thread = await self.bot.fetch_channel(int(thread_id))
                                g.log(f"FORCED END VOTE: Got game thread: {thread}")
                                
                                if thread:
                                    try:
                                        voting_msg = await thread.fetch_message(voting_msg_id)
                                        g.log(f"FORCED END VOTE: Fetched voting message: {voting_msg.id}")
                                        await voting_msg.edit(embed=ended_embed, view=None)
                                        g.log("FORCED END VOTE: Successfully updated voting message to show ended state")
                                    except discord.NotFound:
                                        g.log(f"FORCED END VOTE: Voting message {voting_msg_id} not found in thread")
                                    except discord.HTTPException as e:
                                        g.log(f"FORCED END VOTE: HTTP error updating voting message: {e}")
                                    except Exception as e:
                                        g.log(f"FORCED END VOTE: Unexpected error updating voting message: {e}")
                                else:
                                    g.log("FORCED END VOTE: Could not get game thread from thread ID")
                            except Exception as e:
                                g.log(f"FORCED END VOTE: Error fetching thread: {e}")
                        else:
                            g.log("FORCED END VOTE: No thread ID found in game object")
                    except Exception as e:
                        g.log(f"FORCED END VOTE: Failed to update voting message: {e}")
                else:
                    g.log("FORCED END VOTE: No voting message ID found, cannot update message")
            except Exception:
                pass
        except Exception:
            pass

        # After owner confirmed, resolve votes immediately (use shared helper)
        try:
            g.log("FORCED END VOTE: Admin requested immediate vote resolution")
            # Set a flag to indicate this is a forced end by admin
            g._forced_end_vote = True
            
            # Clear any blocking flags that might prevent resolution
            try:
                g._emergency_vote_reset = False
                g._vote_invalidated_by_guess = False
                g.log("FORCED END VOTE: Cleared blocking flags")
            except Exception:
                pass
            
            # Log current game state for debugging
            try:
                g.log(f"FORCED END VOTE: Current game state - phase: {g.phase}, players: {len(g.players)}, alive: {len([p for p in g.players.values() if p.alive])}")
                g.log(f"FORCED END VOTE: Current pending votes: {getattr(g, '_pending_votes', {})}")
                g.log(f"FORCED END VOTE: Current engine votes: {len(g.votes)}")
                g.log(f"FORCED END VOTE: About to call _resolve_pending_votes")
            except Exception:
                pass
                
            await self._resolve_pending_votes(g, interaction.channel)
            g.log("FORCED END VOTE: Resolution completed successfully")
        except Exception as e:
            try:
                g.log(f"FORCED END VOTE: Error during resolution: {e}, attempting fallback")
                # If normal resolution fails, try direct fallback 
                g._forced_end_vote = True
                
                # Clear blocking flags for fallback too
                try:
                    g._emergency_vote_reset = False
                    g._vote_invalidated_by_guess = False
                except Exception:
                    pass
                    
                await self._do_resolve_pending_votes(g, interaction.channel)
            except Exception as e2:
                try:
                    g.log(f"FORCED END VOTE: Fallback also failed: {e2}")
                    self.storage.save_game(g)
                except Exception:
                    pass

    @app_commands.command(name='ww_guess', description=msg('cmd_guess_description'))
    async def ww_guess(self, interaction: discord.Interaction):
        """DM-only guess/kill for guesser roles. Implemented as a simple DM flow."""
        # Only accept in DM
        if not isinstance(interaction.channel, discord.DMChannel):
            await interaction.response.send_message(msg('guess_not_allowed_phase'), ephemeral=True)
            return

        # Defer early to avoid "This interaction failed" when processing may take time
        await interaction.response.defer(ephemeral=True)

        # delegate to handler to keep implementation testable
        try:
            await self._handle_guess_command(interaction)
        except Exception as e:
            # Log the actual error for debugging
            error_details = f"Guess command error: {e}\n{traceback.format_exc()}"
            logging.getLogger(__name__).error(error_details)
            # Show user-friendly error but also provide debug info in console
            await interaction.followup.send(f"Debug: {str(e)[:100]}...", ephemeral=True)


    async def _create_game_thread_and_status(self, g: Game, interaction: discord.Interaction):
        """Create a dedicated thread for the game and set up the status panel."""
        channel = interaction.channel
        
        # Create thread for this game with unique timestamp-based identifier
        import datetime
        now = datetime.datetime.now()
        thread_identifier = now.strftime("%m%d_%H%M")
        thread_name = msg('werewolf_game_thread', identifier=thread_identifier)
        thread = await channel.create_thread(
            name=thread_name,
            type=discord.ChannelType.public_thread,
            reason=msg('werewolf_thread_reason')
        )
        
        g._game_thread_id = thread.id
        
        # Add all players to the thread
        for pid in g.players:
            try:
                user = await self.bot.fetch_user(int(pid))
                await thread.add_user(user)
            except Exception as e:
                logging.getLogger(__name__).warning(f"Failed to add user {pid} to thread: {e}")
        
        # Create and pin status panel in thread
        status_embed = self._create_game_status_embed(g)
        status_msg = await thread.send(embed=status_embed)
        await status_msg.pin()
        g._status_message_id = status_msg.id
        
        # Removed: Welcome embed to thread for simplified UI
        
        # Removed: Thread creation notification to main channel for simplified UI
        
        self.storage.save_game(g)
    
    async def _update_status_panel(self, g: Game):
        """Update the pinned status panel in the game thread."""
        if not hasattr(g, '_game_thread_id') or not hasattr(g, '_status_message_id'):
            return
            
        try:
            thread = self.bot.get_channel(int(g._game_thread_id))
            if not thread:
                thread = await self.bot.fetch_channel(int(g._game_thread_id))
            
            if thread:
                status_msg = await thread.fetch_message(int(g._status_message_id))
                updated_embed = self._create_game_status_embed(g)
                await status_msg.edit(embed=updated_embed)
        except Exception as e:
            logging.getLogger(__name__).warning(f"Failed to update status panel: {e}")
    
    async def _send_to_game_thread(self, g: Game, content: str = None, embed: discord.Embed = None, view: discord.ui.View = None, allow_game_end: bool = False):
        """Send a message to the game's dedicated thread."""
        # Don't send messages if game has ended, unless explicitly allowed (for game end messages)
        if not allow_game_end and g.phase in (Phase.END, Phase.CLOSED):
            g.log(f"Blocked _send_to_game_thread - game phase is {g.phase}")
            return None
            
        if not hasattr(g, '_game_thread_id'):
            g.log("No game thread ID found")
            return None
            
        try:
            thread_id = int(g._game_thread_id)
            thread = self.bot.get_channel(thread_id)
            if not thread:
                g.log(f"Thread {thread_id} not in cache, fetching...")
                thread = await self.bot.fetch_channel(thread_id)
            
            if thread:
                g.log(f"Sending message to thread {thread_id} (type: {type(thread).__name__})")
                message = await thread.send(content=content, embed=embed, view=view)
                return message
            else:
                g.log(f"Could not find thread {thread_id}")
                return None
        except Exception as e:
            g.log(f"Failed to send to game thread {getattr(g, '_game_thread_id', 'unknown')}: {e}")
            return None
    
    def _create_voting_status_embed(self, g: Game) -> discord.Embed:
        """Create an embed showing current voting status with countdown."""
        embed = discord.Embed(
            title="🗳️ 投票進行状況",
            color=0xFF6B6B,
            timestamp=self._get_jst_timestamp()
        )
        
        # Get current voting data
        pending_votes = getattr(g, '_pending_votes', {}) or {}
        alive_players = [p for p in g.players.values() if p.alive]
        
        # Debug logging to help identify false positive voting status
        try:
            current_session = getattr(g, '_current_vote_session_id', 'unknown')
            g.log(f"DEBUG: Checking voting status for session {current_session}")
            g.log(f"DEBUG: pending_votes keys: {list(pending_votes.keys())}")
            for pid, vote in pending_votes.items():
                g.log(f"DEBUG: Player {pid} has vote: '{vote}' (type: {type(vote)})")
        except Exception:
            pass
        
        # Separate voted and not voted players
        voted = []
        not_voted = []
        
        for p in alive_players:
            pid_str = str(p.id)
            # Check if player has made a valid vote (not invalid/unset/empty)
            is_voted = (
                pid_str in pending_votes and 
                pending_votes[pid_str] and 
                pending_votes[pid_str] not in ['__invalid__', '__unset__', 'invalid', None, '']
            )
            
            if is_voted:
                voted.append(f"✅ {p.name}")
                try:
                    vote_value = pending_votes[pid_str]
                    g.log(f"DEBUG: Player {p.name} ({pid_str}) voted for: '{vote_value}'")
                except Exception:
                    pass
            else:
                not_voted.append(f"⏳ {p.name}")
                try:
                    vote_value = pending_votes.get(pid_str, 'NOT_FOUND')
                    g.log(f"DEBUG: Player {p.name} ({pid_str}) not voted - pending_votes entry: '{vote_value}'")
                except Exception:
                    pass
        
        # Summary section with voting completion
        summary_text = f"**投票完了 ({len(voted)}/{len(alive_players)})**"
        if voted:
            summary_text += "\n" + "\n".join(voted)
        embed.add_field(name="\u200b", value=summary_text, inline=False)
        
        # Waiting section
        if not_voted:
            waiting_text = "**投票待ち**\n" + "\n".join(not_voted)
            embed.add_field(name="\u200b", value=waiting_text, inline=False)
        
        # Countdown section with large text
        timeout = getattr(g, '_runtime_day_vote_timeout', None)
        vote_started = getattr(g, '_day_vote_started_at', None)
        forced_end = getattr(g, '_forced_end_vote', False)
        
        if forced_end:
            countdown_text = "**投票残り時間**\n🛑 **管理者により強制終了されました** 🛑"
        elif timeout and vote_started:
            import time
            elapsed = time.time() - vote_started
            remaining = max(0, int(timeout - elapsed))
            
            # Round down to 10-second intervals
            remaining_rounded = (remaining // 10) * 10
            
            countdown_text = f"**投票残り時間**\n残り **{remaining_rounded}秒** / {timeout}秒"
            if remaining < 40:
                countdown_text = f"⚠️ **投票残り時間**\n⚠️ 残り **{remaining_rounded}秒** / {timeout}秒 ⚠️"
        else:
            countdown_text = "**投票残り時間**\n⏰ 時間制限なし"
        
        embed.add_field(name="\u200b", value=countdown_text, inline=False)
        
        return embed
    
    async def _announce_phase_change(self, g: Game, new_phase: Phase):
        """Announce phase changes in the game thread with appropriate embeds."""
        if new_phase == Phase.NIGHT:
            embed = discord.Embed(
                title="🌙 夜のターンが始まりました",
                description="各役職は夜行動を行ってください。DMをご確認ください。",
                color=0x2F3136
            )
        
        elif new_phase == Phase.DAY:
            embed = discord.Embed(
                title="☀️ 朝が来ました",
                description="昼の会議を開始してください。",
                color=0xFFD700
            )
        
        elif new_phase == Phase.VOTE:
            # Skip sending "投票時間です" embed as requested
            # The voting status embed will be sent separately with countdown
            return
        
        elif new_phase == Phase.END:
            embed = discord.Embed(
                title="🏁 ゲーム終了！",
                description="ゲームが終了しました。お疲れさまでした！",
                color=0x43B581
            )
        
        else:
            # Generic phase change
            embed = discord.Embed(
                title=f"⚙️ フェーズ変更",
                description=f"現在のフェーズ: **{new_phase.name}**",
                color=0x7289DA
            )
        
        await self._send_to_game_thread(g, embed=embed)
        await self._update_status_panel(g)
    
    async def _start_enhanced_voting_in_thread(self, g: Game, timeout: Optional[int] = None):
        """Start enhanced voting UI within the game thread."""
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"Starting enhanced voting in thread for game {g.game_id}")
        
        from .views import VotingView
        
        # Check if thread exists
        if not hasattr(g, '_game_thread_id'):
            logger.error("No game thread ID found")
            raise Exception("No game thread ID found")
        
        # Create voting options
        alive_players = [p for p in g.players.values() if p.alive]
        logger.info(f"Found {len(alive_players)} alive players")
        options = []
        
        for p in alive_players:
            options.append(discord.SelectOption(
                label=p.name,
                value=str(p.id),
                description=msg('survivor', name=p.name)
            ))
        
        # Add abstain option if allowed
        allow_abstain = getattr(g, '_runtime_allow_abstain', True)
        if allow_abstain:
            options.append(discord.SelectOption(
                label=msg('abstain_label'),
                value="abstain",
                description=msg('abstain_description'),
                emoji="⚖️"
            ))
        
        logger.info(f"Created {len(options)} voting options")
        
        # Create voting view
        timeout_value = timeout if timeout and timeout > 0 else 3600  # 1 hour default
        voting_view = self.VotingView(timeout=timeout_value, game=g, channel=None, options=[options])
        
        # Log timeout configuration
        try:
            g.log(f"ENHANCED VOTING SETUP: Created VotingView with timeout={timeout_value}s (requested={timeout})")
        except Exception:
            pass
        
        # Track active voting views for forced termination
        if not hasattr(g, '_active_voting_views'):
            g._active_voting_views = []
        g._active_voting_views.append(voting_view)
        logger.info(f"Created voting view and added to active list (total: {len(g._active_voting_views)})")
        
        # Create voting status embed
        voting_embed = self._create_voting_status_embed(g)
        logger.info("Created voting embed")
        
        # Send to game thread
        logger.info(f"Sending voting UI to thread {g._game_thread_id}")
        voting_msg = await self._send_to_game_thread(g, embed=voting_embed, view=voting_view)
        
        if voting_msg:
            logger.info(f"Successfully sent voting message {voting_msg.id}")
            g._voting_message_id = voting_msg.id
            voting_view.message_id = voting_msg.id
            logger.info(f"VOTING SETUP: Saved voting message ID: {voting_msg.id}")
            
            # Start periodic update task for countdown
            if timeout and timeout > 0:
                asyncio.create_task(self._update_voting_countdown_task(g, voting_msg, timeout))
        else:
            logger.error("Failed to send voting message - _send_to_game_thread returned None")
            raise Exception("Failed to send voting message to thread")
        
        # Update status panel to reflect voting phase
        await self._update_status_panel(g)
        logger.info("Updated status panel")
        
        return voting_msg
    
    async def _update_voting_countdown_task(self, g: Game, voting_msg: discord.Message, total_timeout: int):
        """Periodically update voting countdown every 10 seconds."""
        import asyncio
        import logging
        logger = logging.getLogger(__name__)
        
        vote_started = getattr(g, '_day_vote_started_at', None)
        if not vote_started:
            return
            
        try:
            while g.phase == Phase.VOTE:
                import time
                elapsed = time.time() - vote_started
                remaining = max(0, int(total_timeout - elapsed))
                
                # Stop updating if forced end vote is active
                if getattr(g, '_forced_end_vote', False):
                    logger.info("Voting countdown stopped due to forced end vote")
                    break
                
                # Stop updating if time is up or game phase changed
                if remaining <= 0 or g.phase != Phase.VOTE:
                    break
                
                # Update embed with current countdown
                try:
                    updated_embed = self._create_voting_status_embed(g)
                    await voting_msg.edit(embed=updated_embed)
                    logger.info(f"Updated voting countdown: {remaining}s remaining")
                except Exception as e:
                    logger.warning(f"Failed to update voting countdown: {e}")
                    break
                
                # Wait 10 seconds before next update
                await asyncio.sleep(10)
                
        except Exception as e:
            logger.error(f"Error in voting countdown task: {e}")
    
    async def _start_enhanced_revote_in_thread(self, g: Game, options: List[discord.SelectOption], timeout: Optional[int] = None):
        """Start enhanced revote UI within the game thread with progress tracking."""
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"Starting enhanced revote in thread for game {g.game_id}")
        
        # Check if thread exists
        if not hasattr(g, '_game_thread_id'):
            logger.error("No game thread ID found")
            raise Exception("No game thread ID found")
        
        logger.info(f"Created {len(options)} revote options")
        
        # Create voting view - wrap options in a list since VotingView expects list of lists
        # Use the discord_bot.py VotingView class, not the one from views module
        voting_view = self.VotingView(timeout=timeout or 300, game=g, channel=None, options=[options])
        
        # Track active voting views for forced termination
        if not hasattr(g, '_active_voting_views'):
            g._active_voting_views = []
        g._active_voting_views.append(voting_view)
        logger.info(f"Created revote voting view and added to active list (total: {len(g._active_voting_views)})")
        
        # Create revote status embed
        revote_embed = self._create_revote_status_embed(g, options)
        logger.info("Created revote embed")
        
        # Send to game thread
        logger.info(f"Sending revote UI to thread {g._game_thread_id}")
        voting_msg = await self._send_to_game_thread(g, embed=revote_embed, view=voting_view)
        
        if voting_msg:
            logger.info(f"Successfully sent revote message {voting_msg.id}")
            g._voting_message_id = voting_msg.id
            g._active_vote_views = getattr(g, '_active_vote_views', []) + [voting_view]
            
            # Start periodic update task for countdown
            if timeout and timeout > 0:
                countdown_task = asyncio.create_task(self._update_revote_countdown_task(g, voting_msg, timeout, options))
                logger.info(f"REVOTE COUNTDOWN: Started countdown task for {timeout} seconds")
                # Store task reference for potential cancellation
                g._revote_countdown_task = countdown_task
            else:
                logger.warning(f"REVOTE COUNTDOWN: No timeout specified ({timeout}), countdown task not started")
        else:
            logger.error("Failed to send revote message - _send_to_game_thread returned None")
            raise Exception("Failed to send revote message to thread")
        
        # Update status panel to reflect revote phase
        await self._update_status_panel(g)
        logger.info("Updated status panel for revote")
        
        return voting_msg

    def _create_revote_status_embed(self, g: Game, tie_options: List[discord.SelectOption]) -> discord.Embed:
        """Create an embed showing current revote status."""
        return self._create_revote_status_embed_with_countdown(g, tie_options, None)
    
    def _create_revote_status_embed_with_countdown(self, g: Game, tie_options: List[discord.SelectOption], remaining_seconds: Optional[int] = None) -> discord.Embed:
        """Create an embed showing current revote status with countdown."""
        # Create title with countdown if provided
        if remaining_seconds is not None:
            title = f"🔄 再投票進行状況 (残り {remaining_seconds}秒)"
        else:
            title = "🔄 再投票進行状況"
            
        embed = discord.Embed(
            title=title,
            description="同数票のため、以下の候補者で再投票を行います。",
            color=0xFF9500,
            timestamp=self._get_jst_timestamp()
        )
        
        # Show tied candidates
        candidate_names = []
        for option in tie_options:
            if option.value != '__abstain__':
                # Find player name from game
                try:
                    player = g.players.get(option.value)
                    if player:
                        candidate_names.append(f"• **{player.name}**")
                    else:
                        candidate_names.append(f"• {option.label}")
                except Exception:
                    candidate_names.append(f"• {option.label}")
            else:
                candidate_names.append(f"• {option.label}")
        
        if candidate_names:
            embed.add_field(name="再投票対象", value="\n".join(candidate_names), inline=False)
        
        # Get current voting data
        pending_votes = getattr(g, '_pending_votes', {}) or {}
        alive_players = [p for p in g.players.values() if p.alive]
        
        # Debug logging for revote status
        try:
            g.log(f"DEBUG REVOTE: pending_votes keys: {list(pending_votes.keys())}")
            for pid, vote in pending_votes.items():
                g.log(f"DEBUG REVOTE: Player {pid} has vote: '{vote}' (type: {type(vote)})")
        except Exception:
            pass
        
        # Separate voted and not voted players
        voted = []
        not_voted = []
        
        for p in alive_players:
            pid_str = str(p.id)
            # Check if player has made a valid vote (not invalid/unset/empty)
            is_voted = (
                pid_str in pending_votes and 
                pending_votes[pid_str] and 
                pending_votes[pid_str] not in ['__invalid__', '__unset__', 'invalid', None, '']
            )
            
            if is_voted:
                voted.append(f"✅ {p.name}")
                try:
                    vote_value = pending_votes[pid_str]
                    g.log(f"DEBUG REVOTE: Player {p.name} ({pid_str}) voted for: '{vote_value}'")
                except Exception:
                    pass
            else:
                not_voted.append(f"⏳ {p.name}")
                try:
                    vote_value = pending_votes.get(pid_str, 'NOT_FOUND')
                    g.log(f"DEBUG REVOTE: Player {p.name} ({pid_str}) not voted - pending_votes entry: '{vote_value}'")
                except Exception:
                    pass
        
        # Summary section with voting completion
        summary_text = f"**投票完了 ({len(voted)}/{len(alive_players)})**"
        if voted:
            summary_text += "\n" + "\n".join(voted)
        embed.add_field(name="\u200b", value=summary_text, inline=False)
        
        # Waiting section
        if not_voted:
            waiting_text = "**投票待ち**\n" + "\n".join(not_voted)
            embed.add_field(name="\u200b", value=waiting_text, inline=False)
        
        # Countdown section
        timeout = getattr(g, '_runtime_day_vote_timeout', None)
        vote_started = getattr(g, '_day_vote_started_at', None)
        
        if timeout and vote_started:
            import time
            elapsed = time.time() - vote_started
            remaining = max(0, int(timeout - elapsed))
            
            # Round down to 10-second intervals
            remaining_rounded = (remaining // 10) * 10
            
            countdown_text = f"**再投票残り時間**\n残り **{remaining_rounded}秒** / {timeout}秒"
            if remaining < 40:
                countdown_text = f"⚠️ **再投票残り時間**\n⚠️ 残り **{remaining_rounded}秒** / {timeout}秒 ⚠️"
        else:
            countdown_text = "**再投票残り時間**\n⏰ 時間制限なし"
        
        embed.add_field(name="\u200b", value=countdown_text, inline=False)
        
        return embed

    async def _update_revote_countdown_task(self, g: Game, voting_msg: discord.Message, total_timeout: int, tie_options: List[discord.SelectOption]):
        """Periodically update revote countdown every 10 seconds."""
        import asyncio
        import logging
        logger = logging.getLogger(__name__)
        
        vote_started = getattr(g, '_day_vote_started_at', None)
        if not vote_started:
            logger.warning("REVOTE COUNTDOWN: No vote start time found for revote countdown")
            return
            
        logger.info(f"REVOTE COUNTDOWN: Task started - total_timeout={total_timeout}s, vote_started={vote_started}")
        
        # Track if we've sent the 30-second warning
        thirty_sec_warning_sent = False
        update_count = 0
        
        try:
            while g.phase == Phase.VOTE:
                update_count += 1
                import time
                elapsed = time.time() - vote_started
                remaining = max(0, int(total_timeout - elapsed))
                
                logger.info(f"REVOTE COUNTDOWN: Update #{update_count} - elapsed={elapsed:.1f}s, remaining={remaining}s, phase={g.phase}")
                
                # Stop updating if forced end vote is active
                if getattr(g, '_forced_end_vote', False):
                    logger.info("REVOTE COUNTDOWN: Stopped due to forced end vote")
                    break
                
                # Stop updating if time is up or game phase changed
                if remaining <= 0 or g.phase != Phase.VOTE:
                    logger.info(f"REVOTE COUNTDOWN: Stopped - remaining={remaining}, phase={g.phase}")
                    break
                
                # Send 30-second warning if needed
                if remaining <= 30 and not thirty_sec_warning_sent:
                    thirty_sec_warning_sent = True
                    try:
                        from .i18n import msg
                        warning_msg = msg('vote_30_seconds_warning')
                        await self._send_to_game_thread(g, warning_msg)
                        logger.info("REVOTE COUNTDOWN: Sent 30-second warning for revote")
                    except Exception as e:
                        logger.warning(f"REVOTE COUNTDOWN: Failed to send 30-second warning for revote: {e}")
                
                # Update embed with current countdown
                try:
                    updated_embed = self._create_revote_status_embed_with_countdown(g, tie_options, remaining)
                    await voting_msg.edit(embed=updated_embed)
                    logger.info(f"REVOTE COUNTDOWN: Updated revote UI - {remaining}s remaining")
                except Exception as e:
                    logger.warning(f"REVOTE COUNTDOWN: Failed to update revote UI: {e}")
                    break
                
                # Wait 10 seconds before next update
                logger.info("REVOTE COUNTDOWN: Waiting 10 seconds before next update")
                await asyncio.sleep(10)
                
        except Exception as e:
            logger.error(f"REVOTE COUNTDOWN: Task error: {e}")
        
        logger.info(f"REVOTE COUNTDOWN: Task completed after {update_count} updates")
    
    async def _get_voice_chat_participants(self, interaction: discord.Interaction):
        """Get voice channel participants for the user who invoked the command."""
        if not interaction.guild:
            return []
            
        # Get the member who invoked the command
        try:
            member = interaction.guild.get_member(interaction.user.id)
            if not member:
                member = await interaction.guild.fetch_member(interaction.user.id)
        except Exception:
            return []
        
        # Check if the member is in a voice channel
        if not member.voice or not member.voice.channel:
            return []
        
        voice_channel = member.voice.channel
        
        # Get all members in the voice channel (excluding bots)
        participants = []
        for vc_member in voice_channel.members:
            if not vc_member.bot:  # Exclude bots
                participants.append(vc_member)
        
        return participants
    
    async def _create_and_start_game_with_participants(self, interaction: discord.Interaction, participants: list, 
                                                      night_timeout: Optional[int], day_vote_timeout: Optional[int],
                                                      allow_abstain: Optional[bool], enable_lovers: Optional[bool],
                                                      max_players: int, min_players: int):
        """Create a game with voice chat participants and start immediately."""
        # Create game settings
        settings = GameSettings(min_players=min_players, max_players=max_players)
        g = Game(game_id=str(interaction.channel_id), owner_id=str(interaction.user.id), settings=settings)
        g._bot = self.bot
        
        # Add all voice participants to the game
        for member in participants:
            display_name = getattr(member, 'display_name', None) or getattr(member, 'name', str(member.id))
            g.join(str(member.id), display_name)
        
        # Store voice channel ID
        voice_channel = participants[0].voice.channel if participants and participants[0].voice else None
        if voice_channel:
            g._designated_vc_id = int(voice_channel.id)
        
        # Load saved global game settings
        await self._load_game_settings(g)
        
        # Apply command-line overrides for timeout values (use saved settings as defaults)
        if night_timeout is not None:
            g._runtime_night_timeout = night_timeout
        else:
            # Use saved setting if available
            g._runtime_night_timeout = getattr(g.settings, 'night_duration_sec', None)
            
        if day_vote_timeout is not None:
            g._runtime_day_vote_timeout = day_vote_timeout
        else:
            # Use saved setting if available  
            g._runtime_day_vote_timeout = getattr(g.settings, 'vote_duration_sec', None)
        
        # Apply command-line overrides for other settings
        if enable_lovers is not None and hasattr(g, 'settings'):
            g.settings.lovers_enabled = bool(enable_lovers)
        
        # Start the game
        ok = g.start()
        if not ok:
            raise Exception(msg('game_start_failed_insufficient'))
        
        # Reset night counter and sequence flag
        try:
            g._night_count = 0
            g._night_sequence_started = False
        except Exception:
            pass
        
        # Apply runtime overrides
        try:
            g._runtime_night_timeout = int(night_timeout) if night_timeout is not None else None
            g._runtime_day_vote_timeout = int(day_vote_timeout) if day_vote_timeout is not None else None
            
            if allow_abstain is not None:
                g._runtime_allow_abstain = bool(allow_abstain)
            else:
                g._runtime_allow_abstain = True
        except Exception:
            pass
        
        self.storage.save_game(g)
        
        # Create game thread and status panel
        try:
            await self._create_game_thread_and_status(g, interaction)
        except Exception as e:
            logging.getLogger(__name__).error(f"Failed to create game thread: {e}")
        
        # Send role DMs to players
        failed_role_dms = await self._send_role_dms(g)
        
        # Send VC join reminders
        await self._send_vc_reminders(g, interaction.guild)
        
        # Deliver private messages
        failed_private_dms = await self._deliver_private_messages(g)
        
        # Report DM failures if any
        await self._report_dm_failures(g, interaction, failed_role_dms, failed_private_dms)
        
        # Send start confirmation
        await self._send_start_confirmation(g, interaction)
        
        # Start night sequence
        try:
            task = self.bot.loop.create_task(self._run_night_sequence(g, int(interaction.channel_id)))
            g._night_sequence_task = task
        except Exception:
            try:
                task = asyncio.create_task(self._run_night_sequence(g, int(interaction.channel_id)))
                g._night_sequence_task = task
            except Exception:
                pass
    
    async def _load_game_settings(self, g: Game):
        """Load saved game settings from JSON files."""
        # Load saved global game settings from roles/game_settings.json (if present)
        saved_settings = None
        try:
            repo_root = Path(__file__).resolve().parents[1]
            settings_path = repo_root / 'roles' / 'game_settings.json'
            if settings_path.exists():
                try:
                    saved_settings = json.loads(settings_path.read_text(encoding='utf-8'))
                    logging.getLogger(__name__).info(f"Loaded saved game settings: {saved_settings}")
                except Exception as e:
                    logging.getLogger(__name__).warning(f"Failed to parse game_settings.json: {e}")
        except Exception:
            pass
        
        # Apply saved settings
        try:
            if saved_settings and isinstance(saved_settings, dict) and hasattr(g, 'settings'):
                if 'lovers' in saved_settings:
                    g.settings.lovers_enabled = bool(saved_settings.get('lovers'))
                
                # Set timeout settings
                for setting_key, game_attr in [('day_minutes', 'day_duration_sec'),
                                               ('night_minutes', 'night_duration_sec'),
                                               ('vote_minutes', 'vote_duration_sec')]:
                    if setting_key in saved_settings:
                        try:
                            raw = saved_settings.get(setting_key)
                            v = int(raw) if raw is not None and str(raw).strip() != '' else 0
                            setattr(g.settings, game_attr, (v * 60) if v > 0 else None)
                        except Exception:
                            pass
        except Exception:
            pass
    
    async def _send_role_dms(self, g: Game) -> list:
        """Send role DMs to all players. Returns list of failed player IDs."""
        failed_role_dms = []
        for pid, p in g.players.items():
            try:
                user = await self.bot.fetch_user(int(pid))
                role_id = g.get_player_role(pid)
                
                # Get display name for role
                try:
                    robj = g.roles.get(role_id) if role_id and getattr(g, 'roles', None) else None
                    display_role = robj.name if robj and getattr(robj, 'name', None) else (role_id or 'unknown')
                except Exception:
                    display_role = role_id or 'unknown'
                
                await user.send(msg('role_dm', role=display_role))
                
                # Send guesser hint if applicable
                try:
                    if role_id in ('nice_guesser', 'evil_guesser'):
                        limit = g._guess_limit_for_role(role_id) if hasattr(g, '_guess_limit_for_role') else 1
                        await user.send(msg('guess_role_dm_hint', limit=limit))
                except Exception:
                    pass
                    
            except Exception:
                failed_role_dms.append(pid)
        
        return failed_role_dms
    
    async def _send_vc_reminders(self, g: Game, guild: discord.Guild):
        """Send voice channel join reminders to players."""
        try:
            for pid, p in g.players.items():
                try:
                    member = guild.get_member(int(pid)) or await guild.fetch_member(int(pid))
                    in_designated_vc = False
                    
                    if member and getattr(member, 'voice', None) and getattr(member.voice, 'channel', None):
                        vc = member.voice.channel
                        designated = getattr(g, '_designated_vc_id', None)
                        in_designated_vc = (designated is None) or (vc.id == designated)
                    
                    if not in_designated_vc:
                        user = await self.bot.fetch_user(int(pid))
                        if getattr(g, '_designated_vc_id', None):
                            try:
                                ch = await self.bot.fetch_channel(int(g._designated_vc_id))
                                vc_name = ch.name if ch and hasattr(ch, 'name') else '指定されたボイスチャンネル'
                            except Exception:
                                vc_name = '指定されたボイスチャンネル'
                            await user.send(msg('vc_reminder_designated', vc_name=vc_name))
                        else:
                            await user.send(msg('vc_reminder_generic'))
                except Exception:
                    pass
        except Exception:
            pass
    
    async def _deliver_private_messages(self, g: Game) -> list:
        """Deliver any queued private messages. Returns list of failed player IDs."""
        failed_private_dms = []
        try:
            for pid, msgs in list(getattr(g, 'private_messages', {}).items()):
                try:
                    user = await self.bot.fetch_user(int(pid))
                    for m in msgs:
                        try:
                            # Skip seer results to avoid duplicates
                            if isinstance(m, dict) and m.get('key') in ('seer_result', 'seer_result_followup'):
                                delivered = getattr(g, '_seer_results_delivered', set())
                                if str(pid) in delivered:
                                    continue
                            rendered = _format_private_message_for_send(m)
                            await user.send(rendered)
                        except Exception:
                            if pid not in failed_private_dms:
                                failed_private_dms.append(pid)
                except Exception:
                    if pid not in failed_private_dms:
                        failed_private_dms.append(pid)
            
            # Clear private messages after delivery attempt
            g.private_messages = {}
        except Exception:
            pass
        
        return failed_private_dms
    
    async def _send_death_notifications(self, g: Game, dead_ids: List[str]) -> List[str]:
        """Send death DM notifications to players who have died. Returns list of failed player IDs."""
        failed_dead_dms: List[str] = []
        try:
            for did in dead_ids or []:
                try:
                    p = g.players.get(did)
                    if not p:
                        continue
                    user = await self.bot.fetch_user(int(did))
                    try:
                        await user.send(msg('dead_dm'))
                    except Exception:
                        failed_dead_dms.append(did)
                except Exception:
                    failed_dead_dms.append(did)
        except Exception:
            pass
        
        return failed_dead_dms
    
    async def _report_dm_failures(self, g: Game, interaction: discord.Interaction, failed_role_dms: list, failed_private_dms: list):
        """Report any DM delivery failures to the channel."""
        try:
            failures = []
            for pid in set(failed_role_dms + failed_private_dms):
                p = g.players.get(pid)
                failures.append(p.name if p else pid)
            
            if failures:
                names = ", ".join(failures)
                try:
                    await interaction.followup.send(msg('dm_failed_notice', names=names))
                except Exception:
                    try:
                        ch = interaction.channel
                        if ch:
                            await ch.send(msg('dm_failed_notice', names=names))
                    except Exception:
                        pass
        except Exception:
            pass
    
    async def _send_start_confirmation(self, g: Game, interaction: discord.Interaction):
        """Send game start confirmation with participant summary to game thread."""
        try:
            participant_names = [p.name for p in g.players.values()]
            total = len(participant_names)
            
            # Count roles by role_id
            role_counts = {}
            for p in g.players.values():
                rid = p.role_id or 'unknown'
                role_counts[rid] = role_counts.get(rid, 0) + 1
            
            # Create enhanced embed with better design
            emb = discord.Embed(
                title=msg('enhanced_game_start_title'),
                description=msg('enhanced_game_start_description', total=total),
                color=0xFF6B35  # Vibrant orange color
            )
            
            # Add participants with better formatting
            if total <= 10:
                # For smaller groups, show all names
                participant_list = "、".join([f"**{name}**" for name in participant_names])
            else:
                # For larger groups, show first 8 + count
                shown_names = participant_names[:8]
                participant_list = "、".join([f"**{name}**" for name in shown_names])
                participant_list += "\n" + msg('participant_others', count=total-8)
            
            emb.add_field(
                name=msg('participant_list_title'),
                value=participant_list if participant_list else "(なし)",
                inline=False
            )
            
            # Enhanced role distribution with icons and better grouping
            faction_roles = {
                msg('faction_werewolf'): [],
                msg('faction_village'): [],
                msg('faction_neutral'): [],
                msg('faction_other'): []
            }
            
            try:
                role_order = list(getattr(g, 'roles', {}).keys())
            except Exception:
                role_order = []
            
            for rid in role_order:
                cnt = role_counts.get(rid, 0)
                if cnt > 0:
                    robj = g.roles.get(rid)
                    rname = robj.name if robj else rid
                    role_text = f"**{rname}**: {cnt}人"
                    
                    # Categorize by faction
                    if robj and hasattr(robj, 'faction'):
                        faction = robj.faction
                        if faction == 'werewolf':
                            faction_roles[msg('faction_werewolf')].append(role_text)
                        elif faction in ['citizen', 'village']:  # Accept both 'citizen' and 'village'
                            faction_roles[msg('faction_village')].append(role_text)
                        elif faction in ['third', 'neutral', 'jester']:  # Accept multiple third-party faction names
                            faction_roles[msg('faction_neutral')].append(role_text)
                        elif faction == 'madman':
                            faction_roles[msg('faction_werewolf')].append(role_text)  # Madman goes with werewolf
                        else:
                            faction_roles[msg('faction_other')].append(role_text)
                    else:
                        # Default categorization based on role ID
                        if rid in ['werewolf', 'madman', 'evil_guesser']:
                            faction_roles[msg('faction_werewolf')].append(role_text)
                        elif rid in ['lovers']:
                            faction_roles[msg('faction_neutral')].append(role_text)
                        else:
                            faction_roles[msg('faction_village')].append(role_text)
            
            # Add remaining roles that might not be in role_order
            for rid in sorted(role_counts.keys()):
                if rid not in role_order:
                    cnt = role_counts.get(rid, 0)
                    if cnt > 0:
                        robj = g.roles.get(rid)
                        rname = robj.name if robj else rid
                        role_text = f"**{rname}**: {cnt}人"
                        faction_roles[msg('faction_other')].append(role_text)
            
            # Add faction fields to embed
            for faction_name, roles_list in faction_roles.items():
                if roles_list:
                    emb.add_field(
                        name=faction_name,
                        value="\n".join(roles_list),
                        inline=True
                    )
            
            # Add game settings in a compact format
            settings_lines = []
            try:
                lovers_enabled = getattr(g.settings, 'lovers_enabled', False)
                allow_abstain = getattr(g, '_runtime_allow_abstain', True)
                night_timeout = getattr(g, '_runtime_night_timeout', None)
                vote_timeout = getattr(g, '_runtime_day_vote_timeout', None)
                
                lovers_status = msg('setting_enabled') if lovers_enabled else msg('setting_disabled')
                abstain_status = msg('setting_possible') if allow_abstain else msg('setting_impossible')
                
                settings_lines.append(msg('setting_lovers', status=lovers_status))
                settings_lines.append(msg('setting_abstain', status=abstain_status))
                
                if night_timeout:
                    timeout_text = msg('setting_seconds', seconds=night_timeout)
                else:
                    timeout_text = msg('setting_no_limit')
                settings_lines.append(msg('setting_night_timeout', timeout=timeout_text))
                    
                if vote_timeout:
                    timeout_text = msg('setting_seconds', seconds=vote_timeout)
                else:
                    timeout_text = msg('setting_no_limit')
                settings_lines.append(msg('setting_vote_timeout', timeout=timeout_text))
                
            except Exception:
                settings_lines = [msg('setting_failed')]
            
            emb.add_field(
                name=msg('game_settings_title'),
                value="\n".join(settings_lines),
                inline=True
            )
            
            # Add helpful footer
            emb.set_footer(
                text=msg('enhanced_footer_detailed'),
                icon_url=None
            )
            
            # Send to game thread and pin the start message
            start_msg = await self._send_to_game_thread(g, embed=emb)
            if start_msg:
                try:
                    await start_msg.pin()
                except Exception as pin_e:
                    logging.getLogger(__name__).warning(f"Failed to pin start message: {pin_e}")
            
            # Also send a simple confirmation to main channel
            main_channel_msg = discord.Embed(
                title=msg('main_channel_start_title'),
                description=msg('main_channel_start_description', total=total),
                color=0x43B581
            )
            
            # Add thread mention if available
            if hasattr(g, '_game_thread_id'):
                try:
                    thread = self.bot.get_channel(int(g._game_thread_id))
                    if thread:
                        main_channel_msg.add_field(
                            name=msg('main_channel_thread_field'),
                            value=f"{thread.mention}",
                            inline=False
                        )
                except Exception:
                    pass
            
            await interaction.followup.send(embed=main_channel_msg)
            
        except Exception as e:
            logging.getLogger(__name__).error(f"Failed to send start confirmation: {e}")
            try:
                # Fallback: simple message to thread
                await self._send_to_game_thread(g, content=msg('generic_game_started'))
                await interaction.followup.send(msg('generic_game_started'))
            except Exception:
                pass
    
    async def _announce_phase_change(self, g: Game, new_phase: Phase):
        """Announce phase changes in the game thread with appropriate embeds."""
        if new_phase == Phase.NIGHT:
            embed = discord.Embed(
                title="🌙 夜のターンが始まりました",
                description="各役職は夜行動を行ってください。DMをご確認ください。",
                color=0x2F3136
            )
        
        elif new_phase == Phase.DAY:
            embed = discord.Embed(
                title="☀️ 朝が来ました",
                description="昼の会議を開始してください。",
                color=0xFFD700
            )
        
        elif new_phase == Phase.VOTE:
            # Skip sending "投票時間です" embed as requested
            # The voting status embed will be sent separately with countdown
            return
        
        elif new_phase == Phase.END:
            embed = discord.Embed(
                title=msg('game_ended_title'),
                description=msg('game_ended_description'),
                color=0x43B581
            )
        
        else:
            # Generic phase change
            embed = discord.Embed(
                title=f"フェーズ変更",
                description=f"現在のフェーズ: **{new_phase.name}**",
                color=0x7289DA
            )
        
        await self._send_to_game_thread(g, embed=embed)
        await self._update_status_panel(g)

    async def _setup_voice_channel(self, g, interaction, voice_channel):
        """Setup voice channel and auto-join members. Returns list of auto-joined names."""
        if voice_channel:
            g._designated_vc_id = int(voice_channel.id)
            return []
            
        # Use creator's current VC
        guild = interaction.guild
        if not guild:
            raise Exception("No voice channel specified and no guild context")
            
        creator_member = guild.get_member(int(interaction.user.id))
        if not creator_member:
            creator_member = await guild.fetch_member(int(interaction.user.id))
            
        if not (creator_member and creator_member.voice and creator_member.voice.channel):
            raise Exception("Creator not in voice channel and no voice channel specified")
            
        # Set voice channel and auto-join members
        g._designated_vc_id = int(creator_member.voice.channel.id)
        auto_joined_names = []
        
        for mem in creator_member.voice.channel.members:
            if mem.bot:
                continue
            uid = str(mem.id)
            if uid not in g.players:
                g.join(uid, _safe_display_name(mem))
                auto_joined_names.append(_safe_display_name(mem))
                
        return auto_joined_names

    async def _send_create_success(self, interaction, g, max_players, owner_display, auto_joined_names, auto_join_owner):
        """Send appropriate success message"""
        if auto_join_owner:
            if auto_joined_names:
                await interaction.followup.send(msg('create_success_auto_join_list', 
                    max_players=max_players, names=', '.join(auto_joined_names), owner=owner_display))
            else:
                await interaction.followup.send(msg('create_success_auto_join', 
                    max_players=max_players, owner=owner_display))
        else:
            await interaction.followup.send(msg('create_success_no_auto', 
                max_players=max_players, owner=owner_display))

    def _record_guess_usage(self, g, uid):
        """Record that a player used their guess"""
        if hasattr(g, '_guess_uses_inc'):
            try:
                g._guess_uses_inc(uid)
                return
            except Exception:
                pass
                
        # Fallback to direct dict management
        if not hasattr(g, '_guess_uses'):
            g._guess_uses = {}
        g._guess_uses[uid] = g._guess_uses.get(uid, 0) + 1
        
        if not hasattr(g, '_guess_used'):
            g._guess_used = set()
        g._guess_used.add(uid)

    def _cleanup_dead_player_votes(self, g, killed):
        """Clean up votes from/to dead players"""
        original_count = len(g.votes)
        
        # Remove votes FROM and TO dead players
        g.votes = [v for v in g.votes if v.from_id not in killed and v.target_id not in killed]
        
        # Clean pending votes 
        for dead_id in killed:
            g._pending_votes.pop(dead_id, None)
        
        g.log(f"GUESS: Cleaned votes {original_count} -> {len(g.votes)}")
        self.storage.save_game(g)

    async def _validate_guess_player(self, g, uid, interaction):
        """Validate that player can make guesses. Returns player object or None if invalid."""
        p = g.players.get(uid)
        if not p or not p.alive:
            await interaction.followup.send(msg('guess_dead_cannot'), ephemeral=True)
            return None
        if p.role_id not in ('nice_guesser', 'evil_guesser'):
            await interaction.followup.send(msg('guess_not_allowed_phase'), ephemeral=True)
            return None
        return p

    async def _validate_guess_timing(self, g, interaction):
        """Validate timing constraints for guessing. Returns True if valid."""
        if getattr(g, 'phase', None) not in (Phase.DAY, Phase.VOTE):
            await interaction.followup.send(msg('guess_not_allowed_phase'), ephemeral=True)
            return False
            
        vote_timeout = getattr(g, '_runtime_day_vote_timeout', None)
        if vote_timeout is None:
            return True  # No time limit
            
        vote_started = getattr(g, '_day_vote_started_at', None)
        if vote_started is None:
            await interaction.followup.send(msg('guess_not_allowed_phase'), ephemeral=True)
            return False
            
        import time
        remaining = int(vote_timeout) - int(time.time() - float(vote_started))
        if remaining < 30:
            await interaction.followup.send(msg('guess_not_allowed_phase'), ephemeral=True)
            return False
        return True

    def _invalidate_voting_system(self, g):
        """Invalidate all voting system state when a guess occurs"""
        import time
        import uuid
        
        # Generate new session ID
        old_session = getattr(g, '_current_vote_session_id', 'unknown')
        new_session = f"post_guess_vote_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        g._current_vote_session_id = new_session
        
        g.log(f"GUESS VOTE INVALIDATION: {old_session} -> {new_session}")
        
        # Set flags and clear state
        g._vote_invalidated_by_guess = True
        g._emergency_vote_reset = True
        g.votes = []
        g._pending_votes = {}
        g._vote_finalized = True
        g._revote_in_progress = False
        
        # Cancel background tasks
        for attr in ['_vote_timeout_task', '_night_timeout_task', '_resolve_worker_task']:
            task = getattr(g, attr, None)
            if task and hasattr(task, 'cancel') and not task.done():
                task.cancel()
            if hasattr(g, attr):
                delattr(g, attr)
        
        # Clear resolve queue
        resolve_queue = getattr(g, '_resolve_queue', None)
        if resolve_queue:
            while not resolve_queue.empty():
                try:
                    resolve_queue.get_nowait()
                    resolve_queue.task_done()
                except:
                    break
        
        # Stop vote views
        for view in getattr(g, '_active_vote_views', []):
            view._invalidated_for_guess = True
            if hasattr(view, 'stop'):
                view.stop()
        g._active_vote_views = []
        
        # Save state
        self.storage.save_game(g)

    async def _handle_guess_command(self, interaction: discord.Interaction):
        """Shared implementation of the /ww_guess DM flow. Separated for clarity and testability."""
        uid = str(interaction.user.id)
        # locate the game where the user participates
        games = list(getattr(self.storage, '_games', {}).values())

        g = None
        for gg in games:
            if uid in gg.players:
                g = gg
                break

        if not g:
            await interaction.followup.send(msg('no_lobby_in_channel'), ephemeral=True)
            return

        # Validate player eligibility
        p = await self._validate_guess_player(g, uid, interaction)
        if p is None:  # validation failed and response already sent
            return

        # Validate timing and phase
        if not await self._validate_guess_timing(g, interaction):
            return

        # Build guess options and validate usage limits
        from .guess_helpers import build_guess_options
        all_alive_opts, role_opts = build_guess_options(g)
        
        # Remove lovers overlay from role choices
        lovers_label = msg('start_option_lovers')
        role_opts = [o for o in role_opts if str(getattr(o, 'value', '')).lower() != 'lovers' and lovers_label not in getattr(o, 'label', '')]
        
        alive_opts = [opt for opt in all_alive_opts if opt.value != uid]
        if not alive_opts:
            await interaction.followup.send(msg('guess_not_allowed_phase'), ephemeral=True)
            return

        # Check usage limits
        allowed = g._guess_limit_for_role(p.role_id) if hasattr(g, '_guess_limit_for_role') else 1
        used = g._guess_uses_get(uid) if hasattr(g, '_guess_uses_get') else int(getattr(g, '_guess_uses', {}).get(uid, 0))
        if used >= (allowed or 1):
            await interaction.followup.send(msg('guess_only_once', limit=allowed), ephemeral=True)
            return

        # Ensure game state is initialized
        if getattr(g, '_guess_lock', None) is None:
            import asyncio
            g._guess_lock = asyncio.Lock()
        if getattr(g, '_guess_used', None) is None:
            g._guess_used = set()

        class _GuesserView(ui.View):
            def __init__(self):
                super().__init__(timeout=120)
                self.player: Optional[str] = None
                self.role: Optional[str] = None
                self.game: Optional[Game] = None

            @ui.select(custom_id='guess_select_player', placeholder=msg('guess_dm_header_alive_list'), min_values=1, max_values=1, options=alive_opts)
            async def select_player(self, inter: discord.Interaction, select: ui.Select):
                self.player = select.values[0]
                # Convert player ID to display name
                try:
                    player_obj = self.game.players.get(self.player)
                    target_name = player_obj.name if player_obj else self.player
                except Exception:
                    target_name = self.player
                content = msg('night_choice_registered', target=target_name)
                await _ack_interaction(inter, content=content, ephemeral=True)

            @ui.select(custom_id='guess_select_role', placeholder=msg('guess_dm_header_roles_list'), min_values=1, max_values=1, options=role_opts)
            async def select_role(self, inter: discord.Interaction, select: ui.Select):
                self.role = select.values[0]
                # Convert role ID to Japanese display name
                try:
                    role_obj = self.game.roles.get(self.role)
                    target_name = role_obj.name if role_obj else self.role
                except Exception:
                    target_name = self.role
                content = msg('night_choice_registered', target=target_name)
                await _ack_interaction(inter, content=content, ephemeral=True)

            @ui.button(label=msg('execute_button_label'), style=discord.ButtonStyle.danger)
            async def submit(self, inter: discord.Interaction, button: ui.Button):
                await _ack_interaction(inter, content=msg('execute_button'), ephemeral=True)
                self.stop()

            @ui.button(label='キャンセル', style=discord.ButtonStyle.secondary)
            async def cancel(self, inter: discord.Interaction, button: ui.Button):
                await inter.response.send_message(msg('guess_command_dm_cancelled'), ephemeral=True)
                self.stop()

        # Send guess view
        view = _GuesserView()
        view.game = g
        await interaction.followup.send(msg('guess_dm_header_alive_list'), view=view, ephemeral=True)

        # Acquire lock and wait for view completion
        import asyncio
        lock = getattr(g, '_guess_lock', None) 
        
        async with lock if lock else asyncio.Lock():
            view_completed = await self._wait_view_with_pause(view, 120, g)
        
        # Check view completion and extract choices
        if not view_completed or not getattr(view, 'player') or not getattr(view, 'role'):
            await interaction.followup.send(msg('guess_command_dm_cancelled'), ephemeral=True)
            return
            
        chosen_player = view.player
        chosen_role = view.role
        
        # IMMEDIATE STEP: Destroy old voting system upon guess execution
        self._invalidate_voting_system(g)
        
        # Verify game still exists and is in valid phase
        try:
            g_check = self.storage.load_game(str(g.game_id))
            if not g_check or g_check.phase not in (Phase.DAY, Phase.VOTE):
                try:
                    await interaction.followup.send(msg('guess_not_allowed_phase'), ephemeral=True)
                except Exception:
                    pass
                return
            # Update game reference to latest state
            g = g_check
            p = g.players.get(uid)
            if not p or not p.alive:
                try:
                    await interaction.followup.send(msg('guess_dead_cannot'), ephemeral=True)
                except Exception:
                    pass
                return
        except Exception:
            try:
                await interaction.followup.send(msg('guess_internal_error'), ephemeral=True)
            except Exception:
                pass
            return

        try:
            try:
                # re-check allowed uses (in case concurrent usage occurred)
                try:
                    allowed = g._guess_limit_for_role(p.role_id) if hasattr(g, '_guess_limit_for_role') else 1
                except Exception:
                    allowed = 1
                try:
                    used = g._guess_uses_get(uid) if hasattr(g, '_guess_uses_get') else int(getattr(g, '_guess_uses', {}).get(uid, 0))
                except Exception:
                    used = int(getattr(g, '_guess_uses', {}).get(uid, 0))
                if used >= (allowed or 1):
                    try:
                        remaining = max(0, allowed - used)
                        await interaction.followup.send(msg('guess_already_used', limit=allowed, remaining=remaining), ephemeral=True)
                    except Exception:
                        pass
                    return
            except Exception:
                pass

            victim = g.players.get(chosen_player)
            if not victim or not victim.alive:
                try:
                    await interaction.followup.send(msg('guess_target_not_alive'), ephemeral=True)
                except Exception:
                    pass
                return

            success = False
            killed = []
            if victim.role_id == chosen_role:
                success = True
                try:
                    killed = g._kill_player(victim.id, reason='guess') or []
                    names = ','.join([g.players[k].name for k in killed if k in g.players])
                    try:
                        g.log(f"Guesser {p.name} ({uid}) succeeded and killed {names}")
                    except Exception:
                        pass
                    # Send death notification DM to killed players
                    try:
                        await self._send_death_notifications(g, killed)
                    except Exception:
                        pass
                    if not killed:
                        try:
                            await interaction.followup.send(msg('guess_internal_error'), ephemeral=True)
                        except Exception:
                            pass
                except Exception:
                    pass
            else:
                success = False
                try:
                    killed = g._kill_player(p.id, reason='guess_failed') or []
                    names = ','.join([g.players[k].name for k in killed if k in g.players])
                    try:
                        g.log(f"Guesser {p.name} ({uid}) failed and died: {names}")
                    except Exception:
                        pass
                    # Send death notification DM to killed players (the guesser themselves)
                    try:
                        await self._send_death_notifications(g, killed)
                    except Exception:
                        pass
                    if not killed:
                        try:
                            await interaction.followup.send(msg('guess_internal_error'), ephemeral=True)
                        except Exception:
                            pass
                except Exception:
                    pass

            # Record the guess usage
            import asyncio
            async with g._guess_lock if g._guess_lock else asyncio.Lock():
                self._record_guess_usage(g, uid)
                self.storage.save_game(g)

            # Clean up votes involving dead players  
            if killed:
                self._cleanup_dead_player_votes(g, killed)

        except Exception as e:
            g.log(f"Exception during guess eval for {uid}: {e}")
            await interaction.followup.send(msg('guess_internal_error'), ephemeral=True)
            return

        # Note: Do not send immediate guess result here - it will be handled 
        # in the unified message below after win evaluation

        try:
            try:
                guild = None
                ch = self.bot.get_channel(int(g.game_id))
                if ch:
                    guild = getattr(ch, 'guild', None)
                if guild:
                    bot_member = guild.get_member(self.bot.user.id) or await guild.fetch_member(self.bot.user.id)
                    if bot_member and bot_member.guild_permissions.mute_members:
                        # Mute newly killed players (killed list) when available. This handles
                        # lovers and other engine-side cascading deaths. Fallback to muting
                        # the victim if they are dead, or the guesser if they died.
                        try:
                            targets = killed if killed else []
                        except Exception:
                            targets = []
                        try:
                            # If no killed list but victim is dead, include victim
                            if not targets and victim and not getattr(victim, 'alive', True):
                                targets = [victim.id]
                        except Exception:
                            pass
                        try:
                            # If nothing else, but the guesser died, mute the guesser
                            if not targets and not getattr(p, 'alive', True):
                                targets = [p.id]
                        except Exception:
                            pass

                        for tid in targets:
                            try:
                                member = guild.get_member(int(tid)) or await guild.fetch_member(int(tid))
                                if member and getattr(member, 'voice', None) and getattr(member.voice, 'channel', None):
                                    await member.edit(mute=True)
                            except Exception:
                                pass
            except Exception:
                pass

            try:
                self.storage.save_game(g)
            except Exception:
                pass

            try:
                # Get channel for unified messaging
                ch = self.bot.get_channel(int(g.game_id))
            except Exception:
                ch = None
                
            # STEP 1: Force terminate all voting processes and create emergency replacement
            try:
                # Emergency shutdown: Force the game into an intermediate state
                # that completely bypasses all existing vote processing
                g._emergency_vote_reset = True
                g._vote_invalidated_by_guess = True
                
                # Generate completely new vote session ID
                import time
                import uuid
                emergency_session_id = f"emergency_vote_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
                g._current_vote_session_id = emergency_session_id
                
                # Force clear ALL vote-related state immediately
                g.votes = []
                g._pending_votes = {}
                g._vote_finalized = True  # Force finalized to block any pending operations
                g._revote_in_progress = False
                
                # Emergency stop all active vote views with extreme prejudice
                old_views = getattr(g, '_active_vote_views', None) or []
                for ov in list(old_views):
                    try:
                        # Mark with multiple invalidation flags
                        ov._invalidated_for_guess = True
                        ov._emergency_invalidated = True
                        ov._old_session = True
                        # Try multiple stop methods
                        if hasattr(ov, 'stop'):
                            ov.stop()
                        if hasattr(ov, 'clear_items'):
                            ov.clear_items()
                    except Exception:
                        pass
                g._active_vote_views = []
                
                # Force save to persist the emergency state
                try:
                    self.storage.save_game(g)
                except Exception:
                    pass
                
                g.log(f"EMERGENCY: Forced termination of all vote processes, created emergency session {emergency_session_id}")
                
                # Mark that we will be in re-voting phase (not initial guesser execution)
                # This flag prevents guesser-triggered vote resolution from showing results
                g._in_re_vote_after_guess = True
                g.log(f"EMERGENCY: Set re-vote flag for session {emergency_session_id}")
            except Exception as e:
                try:
                    g.log(f"EMERGENCY: Failed emergency vote termination: {e}")
                except Exception:
                    pass
                    
            # STEP 2: Run CHECK_WIN
            try:
                original_phase = g.phase
                g._previous_phase_before_check_win = original_phase  # Store for engine reference
                g._check_win_context = 'guesser_action'  # Mark as guesser-triggered check_win
                g.phase = Phase.CHECK_WIN
                winner = g.check_win()
                if winner:
                    # Game ended - announce death and let _evaluate_and_handle_win handle the rest
                    try:
                        if ch:
                            if killed:
                                name = g.players[killed[0]].name
                            else:
                                name = victim.name if success else p.name
                            await self._send_to_game_thread(g, content=msg('guess_success_public', name=name))
                    except Exception:
                        pass
                    # Continue to _evaluate_and_handle_win
                else:
                    # Game continues - respect the phase set by engine
                    g.log(f"CHECK_WIN completed - game continues, current phase: {g.phase}")
                    # Note: Don't override the phase - engine.check_win() already set the appropriate phase
            except Exception as e:
                try:
                    g.log(f"CHECK_WIN failed: {e}")
                    # Fallback to original phase
                    g.phase = original_phase
                    winner = None
                except Exception:
                    winner = None
                    
            # Handle win evaluation
            try:
                # Clear invalidation flags before win evaluation when game ends
                if winner:
                    g._vote_invalidated_by_guess = False
                    g._emergency_vote_reset = False
                    g.log("GUESS: Cleared invalidation flags for win evaluation")
                    
                handled = await self._evaluate_and_handle_win(g, ch)
                if handled:
                    return
            except Exception:
                pass
                
            # STEP 3: If game continues, restart voting with 30-second bonus
            if not winner:
                try:
                    if ch:
                        # Determine victim name
                        try:
                            if killed:
                                name = g.players[killed[0]].name
                            else:
                                name = victim.name if success else p.name
                        except Exception:
                            name = victim.name if success else p.name
                            
                        # Calculate remaining time and add 30 seconds
                        import time
                        st = getattr(g, '_day_vote_started_at', None)
                        tot = getattr(g, '_runtime_day_vote_timeout', None)
                        if st and tot:
                            rem = max(5, int(tot) - int(time.time() - float(st)))
                            rem = rem + 30  # Add 30 seconds bonus
                            rem = max(rem, 60)  # Ensure minimum 60 seconds for stable voting
                            # Update vote start time and timeout
                            g._day_vote_started_at = time.time()
                            g._runtime_day_vote_timeout = rem
                            # Send restart message
                            g.log(f"GUESS RECOVERY: Calculated voting time - rem={rem}, original_timeout={tot}")
                            await self._send_to_game_thread(g, content=msg('guess_vote_restart_with_time', name=name, seconds=rem))
                        else:
                            rem = None
                            g.log("GUESS RECOVERY: No time limits - voting without timeout")
                            await ch.send(msg('guess_vote_restart', name=name))
                        
                        # Wait a brief moment to ensure background tasks are fully cancelled
                        g.log("GUESS RECOVERY: Waiting 0.1s for background task cancellation...")
                        await asyncio.sleep(0.1)
                        
                        # Clear invalidation flags ONLY after ensuring background tasks are stopped
                        g.log(f"GUESS RECOVERY: Clearing flags - before: vote_invalidated={getattr(g, '_vote_invalidated_by_guess', False)}, emergency_reset={getattr(g, '_emergency_vote_reset', False)}, finalized={getattr(g, '_vote_finalized', False)}")
                        g._vote_invalidated_by_guess = False
                        g._emergency_vote_reset = False
                        g._vote_finalized = False  # Reset to allow new voting
                        # Ensure the new vote session ID is current
                        current_session = getattr(g, '_current_vote_session_id', None)
                        g.log(f"GUESS RECOVERY: Cleared all invalidation flags - restarting fresh vote with session {current_session}")
                        g.log(f"GUESS RECOVERY: Flags after clear: vote_invalidated={getattr(g, '_vote_invalidated_by_guess', False)}, emergency_reset={getattr(g, '_emergency_vote_reset', False)}, finalized={getattr(g, '_vote_finalized', False)}")
                        
                        # Log background worker state after flag clearing
                        try:
                            worker_task = getattr(g, '_resolve_worker_task', None)
                            queue = getattr(g, '_resolve_queue', None) 
                            queue_size = queue.qsize() if queue else 0
                            g.log(f"GUESS RECOVERY: Background state after flag clear - worker_task={worker_task}, queue_size={queue_size}")
                        except Exception:
                            pass
                        
                        # Restart vote UI (but stay in VOTE phase)
                        g.log(f"GUESS RECOVERY: About to restart voting UI with session {getattr(g, '_current_vote_session_id', 'unknown')}")
                        # Mark that we are creating new voting UI (not processing completion)
                        g._creating_vote_ui = True
                        # Mark that we are in a re-vote after guess
                        g._in_re_vote_after_guess = True
                        # Use fixed timeout for consistency instead of remaining time
                        fixed_timeout = getattr(g, '_fixed_vote_timeout', None)
                        g.log(f"GUESS RECOVERY: Using fixed vote timeout: {fixed_timeout}s (instead of remaining: {rem}s)")
                        try:
                            await self._start_day_vote_channel(g, ch, fixed_timeout)
                        except Exception as e:
                            g.log(f"GUESS RECOVERY: Error during vote UI restart: {e}")
                            g.log(f"GUESS RECOVERY: Traceback: {traceback.format_exc()}")
                        finally:
                            # Ensure the flag is always cleared
                            g._creating_vote_ui = False
                            g.log(f"GUESS RECOVERY: Cleared _creating_vote_ui flag")
                        g.log(f"GUESS RECOVERY: Completed restart of voting UI")
                        
                        # Log final state for re-vote verification
                        try:
                            g.log(f"RE-VOTE READY: vote_invalidated={getattr(g, '_vote_invalidated_by_guess', False)}, in_re_vote={getattr(g, '_in_re_vote_after_guess', False)}, session={getattr(g, '_current_vote_session_id', 'unknown')}")
                        except Exception:
                            pass
                            
                        # Start a background task to ensure vote resolution happens after a brief delay
                        async def _ensure_vote_resolution():
                            try:
                                await asyncio.sleep(2)  # Brief delay to ensure UI is fully ready
                                g.log("GUESS RECOVERY: Starting post-recovery vote resolution check")
                                # Check if votes need to be resolved
                                current_timeout = getattr(g, '_runtime_day_vote_timeout', None)
                                vote_started = getattr(g, '_day_vote_started_at', None)
                                if current_timeout and vote_started:
                                    import time
                                    elapsed = time.time() - vote_started
                                    remaining = current_timeout - elapsed
                                    g.log(f"GUESS RECOVERY: Vote timing - elapsed={elapsed:.1f}, remaining={remaining:.1f}")
                                    # If timeout has passed or will pass soon, trigger resolution
                                    if remaining <= 0:
                                        g.log("GUESS RECOVERY: Vote timeout passed, triggering immediate resolution")
                                        await self._resolve_pending_votes(g, ch, wait=False)
                            except Exception as e:
                                g.log(f"GUESS RECOVERY: Error in vote resolution check: {e}")
                                
                        asyncio.create_task(_ensure_vote_resolution())
                        
                except Exception as e:
                    try:
                        g.log(f"Failed to restart voting: {e}")
                    except Exception:
                        pass

        except Exception:
            pass

        try:
            self.storage.save_game(g)
        except Exception:
            pass

    async def _evaluate_and_handle_win(self, g: Game, channel: Optional[discord.TextChannel] = None) -> Optional[str]:
        """Run g.check_win(), and if a winner is returned perform end-of-game
        announcements, persistence, unmute, and cleanup. Returns the winner token
        (truthy) if a winner was found, otherwise None.
        """
        try:
            g.log(f"_evaluate_and_handle_win START: phase={g.phase}, session={getattr(g, '_current_vote_session_id', 'unknown')}")
        except Exception:
            pass
            
        # CRITICAL: Check if game was force-closed
        if g.phase == Phase.CLOSED:
            g.log("Win evaluation blocked: game force-closed")
            return None
            
        # CRITICAL: Check for no survivors (should end game)
        alive_count = len([p for p in g.players.values() if p.alive])
        if alive_count == 0:
            g.log("Win evaluation: No survivors left, ending game")
            try:
                g.phase = Phase.END
                self.storage.save_game(g)
                # Send only to game thread
                await self._send_to_game_thread(g, content="ゲームが終了しました。生存者がいません。", allow_game_end=True)
            except Exception as e:
                g.log(f"Failed to end game with no survivors: {e}")
            return "no_survivors"
            
        try:
            # Check for vote invalidation during vote processing (not for guess-induced wins)
            if getattr(g, '_emergency_vote_reset', False) and g.phase not in (Phase.END, Phase.CLOSED, Phase.CHECK_WIN):
                try:
                    g.log('Blocking win evaluation - emergency vote reset during active voting')
                except Exception:
                    pass
                return None
            
            # Allow win evaluation even with vote invalidation if game has already ended or in check_win phase
            # This ensures guess-induced wins are properly processed
            
            # ensure channel object if possible
            if channel is None:
                try:
                    channel = self.bot.get_channel(int(g.game_id))
                except Exception:
                    channel = None
        except Exception:
            channel = None

        try:
            try:
                # If phase is not CHECK_WIN (e.g., called from guess during VOTE/DAY),
                # temporarily set it to CHECK_WIN for check_win() to run, then restore if no winner
                original_phase = g.phase
                if g.phase != Phase.CHECK_WIN:
                    g.phase = Phase.CHECK_WIN
                winner = g.check_win()
                if not winner and original_phase in (Phase.DAY, Phase.VOTE):
                    # Restore original phase if no winner and we're in a voting context
                    g.phase = original_phase
            except Exception as e:
                try:
                    g.log(f"check_win failed: {e}")
                except Exception:
                    pass
                winner = None

            if not winner:
                # No winner yet - check if we transitioned to NIGHT phase and need to start night sequence
                if g.phase == Phase.NIGHT:
                    try:
                        g.log(f"PHASE TRANSITION: Game transitioned to NIGHT after vote resolution (session: {getattr(g, '_current_vote_session_id', 'unknown')}); starting night sequence")
                        # Reset night sequence flag to ensure we can start fresh
                        g._night_sequence_started = False
                        if not getattr(g, '_night_sequence_started', False):
                            g.log(f"PHASE TRANSITION: About to schedule night sequence for channel {channel.id if channel else 'None'}")
                            try:
                                if channel:
                                    self.bot.loop.create_task(self._run_night_sequence(g, int(channel.id)))
                                else:
                                    self.bot.loop.create_task(self._run_night_sequence(g, int(g.game_id)))
                                g.log(f"PHASE TRANSITION: Successfully scheduled night sequence")
                            except Exception as e:
                                try:
                                    if channel:
                                        asyncio.create_task(self._run_night_sequence(g, int(channel.id)))
                                    else:
                                        asyncio.create_task(self._run_night_sequence(g, int(g.game_id)))
                                    g.log(f"PHASE TRANSITION: Successfully scheduled night sequence (fallback)")
                                except Exception as e2:
                                    g.log(f"PHASE TRANSITION: Failed to schedule night sequence: {e}, {e2}")
                        else:
                            g.log("Night sequence already started; skipping duplicate call")
                    except Exception as e:
                        g.log(f"PHASE TRANSITION: Error starting night sequence: {e}")
                return None

            # announce dead players (if any) and winners
            # Only announce night deaths if this win check was triggered by completing a night phase
            try:
                ln = getattr(g, '_last_night_dead', []) or []
                previous_phase = getattr(g, '_previous_phase_before_check_win', None)
                check_win_context = getattr(g, '_check_win_context', None)
                
                # Only announce night deaths if:
                # 1. The previous phase was NIGHT (natural night->day transition), OR
                # 2. The check was triggered by night actions completion
                should_announce_night_deaths = (
                    previous_phase == Phase.NIGHT or 
                    check_win_context == 'night_actions_complete'
                )
                
                if should_announce_night_deaths:
                    # Send only to game thread
                    try:
                        if ln:
                            await self._send_to_game_thread(g, content=msg('dead_players_public', names=", ".join(ln)))
                        else:
                            await self._send_to_game_thread(g, content=msg('dead_players_public_none'))
                    except Exception:
                        pass
                else:
                    g.log(f"Skipping night death announcement - previous_phase={previous_phase}, context={check_win_context}")
            except Exception:
                pass

            try:
                winners_ids = getattr(g, 'last_winner_ids', []) or []
                
                # /ww_closeでの強制終了の場合は統計更新しない
                should_update_stats = (g.phase != Phase.CLOSED)
                
                # 正常終了の場合は確認ダイアログを表示してから統計更新
                all_player_ids = list(g.players.keys())
                if should_update_stats and winners_ids and all_player_ids:
                    # ゲーム結果表示後に確認ダイアログを表示するため、一旦結果を保存
                    setattr(g, '_pending_stats_update', {
                        'all_player_ids': all_player_ids,
                        'winner_ids': winners_ids
                    })
                    g.log(f"Pending statistics update for {len(all_player_ids)} players, {len(winners_ids)} winners")
                
                winners_lines, losers_lines = format_winner_loser_lines(g, winners_ids)
                # annotate lovers with a heart symbol next to their username
                try:
                    def mark_lovers_on_lines(lines):
                        out = []
                        for line in lines:
                            try:
                                marked = line
                                for pid, p in g.players.items():
                                    if p.name and p.name in line:
                                        if pid in getattr(g, '_lovers', {}):
                                            marked = marked.replace(p.name, f"{p.name} ♥")
                                            break
                                out.append(marked)
                            except Exception:
                                out.append(line)
                        return out
                    winners_lines = mark_lovers_on_lines(winners_lines)
                    losers_lines = mark_lovers_on_lines(losers_lines)
                except Exception:
                    pass
                if channel:
                    emb = discord.Embed(
                        title=f"🏆 {msg('game_ended_embed_title')}", 
                        colour=0x8B0000,
                        timestamp=self._get_jst_timestamp()
                    )
                    try:
                        labels = msg('game_ended_fields')
                        winner_label = f"{labels[0] if labels and len(labels) > 0 else 'Winners'}"
                        loser_label = f"{labels[1] if labels and len(labels) > 1 else 'Losers'}"
                    except Exception:
                        winner_label = 'Winners'
                        loser_label = 'Losers'
                        
                    if winners_lines:
                        formatted_winners = []
                        for line in winners_lines:
                            # Add victory icon to each winner
                            formatted_winners.append(f"{line}")
                        emb.add_field(name=winner_label, value="\n".join(formatted_winners), inline=False)
                    else:
                        emb.add_field(name=winner_label, value='勝者なし', inline=False)
                        
                    if losers_lines:
                        formatted_losers = []
                        for line in losers_lines:
                            # Add defeat icon to each loser
                            formatted_losers.append(f"{line}")
                        emb.add_field(name=loser_label, value="\n".join(formatted_losers), inline=False)
                    else:
                        emb.add_field(name=loser_label, value='敗者なし', inline=False)
                        
                    emb.set_footer(text=f"ゲーム終了時刻: {self._get_jst_now().strftime('%Y-%m-%d %H:%M:%S JST')}")
                    
                    # Send to channel and thread, avoiding duplication
                    channel_sent = False
                    thread_sent = False
                    
                    try:
                        # Check if channel and thread are the same to avoid duplicate
                        thread_id = getattr(g, '_game_thread_id', None)
                        same_as_thread = thread_id and str(channel.id) == str(thread_id)
                        
                        if not same_as_thread:
                            await channel.send(embed=emb)
                            channel_sent = True
                            g.log(f"Game end embed sent to channel {channel.id}")
                        
                        # Send to thread if different from channel or if channel send failed
                        if thread_id:
                            thread_embed_sent = await self._send_to_game_thread(g, embed=emb, allow_game_end=True)
                            if thread_embed_sent:
                                thread_sent = True
                                g.log(f"Game end embed sent to thread {thread_id}")
                        
                        # If channel and thread are the same, send only once to thread
                        if same_as_thread and not thread_sent:
                            thread_embed_sent = await self._send_to_game_thread(g, embed=emb, allow_game_end=True)
                            if thread_embed_sent:
                                thread_sent = True
                                channel_sent = True  # Consider it sent to both
                                g.log(f"Game end embed sent to unified channel/thread {channel.id}")
                        
                    except Exception as e:
                        g.log(f"Error sending game end embed: {e}")
                        # Fallback to text message if embed fails
                        try:
                            fallback_msg = msg('game_ended_winner', winner=self._winner_display_name(g, winner))
                            if not channel_sent:
                                await channel.send(fallback_msg)
                                g.log("Game end fallback message sent to channel")
                            if not thread_sent and thread_id:
                                await self._send_to_game_thread(g, content=fallback_msg, allow_game_end=True)
                                g.log("Game end fallback message sent to thread")
                        except Exception as fallback_e:
                            g.log(f"Error sending fallback message: {fallback_e}")
            except Exception:
                pass

            # Send victory/defeat DM notifications to all players
            try:
                await self._send_victory_defeat_dms(g, winners_ids)
            except Exception as e:
                try:
                    g.log(f'Failed to send victory/defeat DM notifications: {e}')
                except Exception:
                    pass
            
            # 統計記録確認ダイアログを表示（正常終了且つプレイヤーがいる場合のみ）
            pending_stats = getattr(g, '_pending_stats_update', None)
            if pending_stats and g.phase != Phase.CLOSED:
                try:
                    await self._show_stats_record_confirmation(g, channel, pending_stats)
                except Exception as e:
                    g.log(f'Failed to show stats confirmation dialog: {e}')
                    # エラーの場合はデフォルトで統計を記録
                    try:
                        self.storage.update_game_results(
                            pending_stats['all_player_ids'], 
                            pending_stats['winner_ids']
                        )
                        g.log(f"Stats updated as fallback: {len(pending_stats['all_player_ids'])} players")
                    except Exception as fallback_e:
                        g.log(f"Failed to update stats as fallback: {fallback_e}")

            try:
                self.storage.save_game(g)
            except Exception:
                pass

            try:
                await self._unmute_all_participants(g, channel)
            except Exception:
                try:
                    g.log('Failed to unmute participants at game end')
                except Exception:
                    pass

            # Cancel all background tasks to prevent post-game messages
            try:
                # Cancel reminder tasks
                reminder_task = getattr(g, '_day_vote_reminder_task', None)
                if reminder_task and not reminder_task.done():
                    reminder_task.cancel()
                    g.log("Cancelled day vote reminder task")
                
                # Cancel any other game-related tasks
                worker_task = getattr(g, '_resolve_worker_task', None)
                if worker_task and not worker_task.done():
                    worker_task.cancel()
                    g.log("Cancelled resolve worker task")
                    
                # Clear task references
                g._day_vote_reminder_task = None
                g._resolve_worker_task = None
                
                g.log("Cancelled all background tasks for game end")
            except Exception as e:
                g.log(f"Failed to cancel background tasks: {e}")

            # Send victory/defeat DM notifications to all players
            try:
                await self._send_victory_defeat_dms(g, winners_ids)
            except Exception as e:
                try:
                    g.log(f'Failed to send victory/defeat DM notifications: {e}')
                except Exception:
                    pass

            try:
                self._cleanup_game(g)
            except Exception:
                pass

            return winner
        except Exception:
            return None

    async def _show_stats_record_confirmation(self, g: Game, channel: Optional[discord.TextChannel], pending_stats: dict):
        """統計記録の確認ダイアログを表示"""
        try:
            if not channel:
                return
            
            owner_id = int(g.owner_id)
            view = StatsRecordConfirmView(owner_id=owner_id, timeout=300.0)
            
            # メッセージを送信
            confirmation_msg = msg('stats_confirm_question', 
                                    owner=f"<@{owner_id}>", 
                                    players=len(pending_stats['all_player_ids']),
                                    winners=len(pending_stats['winner_ids']))
            
            message = await self._send_to_game_thread(
                g, 
                content=confirmation_msg,
                view=view,
                allow_game_end=True
            )
            
            if not message:
                # スレッドへの送信に失敗した場合、メインチャンネルに送信
                message = await channel.send(
                    content=confirmation_msg,
                    view=view
                )
            
            # ユーザーの選択を待機
            await view.wait()
            
            # 結果に応じて統計を更新
            if view.result is True:
                try:
                    self.storage.update_game_results(
                        pending_stats['all_player_ids'], 
                        pending_stats['winner_ids']
                    )
                    g.log(f"Stats recorded: {len(pending_stats['all_player_ids'])} players, {len(pending_stats['winner_ids'])} winners")
                    
                    # 統計記録成功後に参加者全員にDMで統計情報を送信
                    await self._send_stats_dms(g, pending_stats['all_player_ids'])
                    
                except Exception as e:
                    g.log(f"Failed to record stats: {e}")
            elif view.result is False:
                g.log("Stats recording skipped by owner choice")
            else:
                g.log("Stats recording timed out - not recorded")
                
        except Exception as e:
            g.log(f"Error in stats confirmation dialog: {e}")
            # エラー時はデフォルトで記録しない
            
        # 処理完了後にpending_statsを削除
        try:
            delattr(g, '_pending_stats_update')
        except Exception:
            pass

    async def _send_stats_dms(self, g: Game, all_player_ids: list[str]):
        """参加者全員に統計情報をDMで送信"""
        try:
            for player_id in all_player_ids:
                try:
                    user = self.bot.get_user(int(player_id))
                    if not user:
                        try:
                            user = await self.bot.fetch_user(int(player_id))
                        except Exception:
                            continue
                    
                    if not user:
                        continue
                    
                    # ユーザーの統計を取得
                    stats = self.storage.load_user_stats(player_id)
                    
                    # 統計情報を含むEmbedを作成
                    embed = discord.Embed(
                        title=msg('stats_dm_title'),
                        color=0x3498db,
                        timestamp=self._get_jst_timestamp()
                    )
                    
                    embed.add_field(
                        name=msg('stats_dm_total_games', total=stats.total_games),
                        value='\u200b',  # 非表示文字
                        inline=False
                    )
                    
                    embed.add_field(
                        name=msg('stats_dm_total_wins', wins=stats.total_wins),
                        value='\u200b',
                        inline=False
                    )
                    
                    embed.add_field(
                        name=msg('stats_dm_win_rate', rate=stats.win_rate * 100),
                        value='\u200b',
                        inline=False
                    )
                    
                    embed.set_footer(text=msg('stats_dm_footer'))
                    
                    # DMで送信
                    await user.send(embed=embed)
                    
                    g.log(f"Sent stats DM to {user.display_name} ({player_id})")
                    
                except Exception as e:
                    g.log(f"Failed to send stats DM to {player_id}: {e}")
        
        except Exception as e:
            g.log(f"Error in _send_stats_dms: {e}")

    async def _send_victory_defeat_dms(self, g: Game, winners_ids: List[str]):
        """Send victory/defeat DM notifications to all players."""
        try:
            for player_id, player in g.players.items():
                try:
                    user = self.bot.get_user(int(player_id))
                    if not user:
                        try:
                            user = await self.bot.fetch_user(int(player_id))
                        except Exception:
                            continue
                    
                    if not user:
                        continue
                    
                    # Determine if this player won or lost
                    is_winner = str(player_id) in (winners_ids or [])
                    
                    if is_winner:
                        # Send victory message
                        embed = discord.Embed(
                            title=msg('dm_game_victory_title'),
                            description=msg('dm_game_victory_message'),
                            color=0x00FF00,  # Green
                            timestamp=self._get_jst_timestamp()
                        )
                        embed.add_field(
                            name='あなたの役職',
                            value=f'{player.role} ({player.side if hasattr(player, "side") else "不明"})',
                            inline=False
                        )
                    else:
                        # Send defeat message
                        embed = discord.Embed(
                            title=msg('dm_game_defeat_title'),
                            description=msg('dm_game_defeat_message'),
                            color=0xFF0000,  # Red
                            timestamp=self._get_jst_timestamp()
                        )
                        embed.add_field(
                            name='あなたの役職',
                            value=f'{player.role} ({player.side if hasattr(player, "side") else "不明"})',
                            inline=False
                        )
                    
                    # Add game result information
                    try:
                        winner_name = self._winner_display_name(g, getattr(g, '_last_winner_token', None) or 'unknown')
                        embed.add_field(
                            name='ゲーム結果',
                            value=f'勝利陣営: {winner_name}',
                            inline=False
                        )
                    except Exception:
                        pass
                    
                    embed.set_footer(text=f"ゲーム終了時刻: {self._get_jst_now().strftime('%Y-%m-%d %H:%M:%S JST')}")
                    
                    try:
                        await user.send(embed=embed)
                        g.log(f'Sent {"victory" if is_winner else "defeat"} DM to {player.name} ({player_id})')
                    except discord.Forbidden:
                        g.log(f'Cannot send DM to {player.name} ({player_id}) - DMs disabled')
                    except Exception as e:
                        g.log(f'Failed to send DM to {player.name} ({player_id}): {e}')
                
                except Exception as e:
                    try:
                        g.log(f'Error processing DM for player {player_id}: {e}')
                    except Exception:
                        pass
        
        except Exception as e:
            try:
                g.log(f'Error in _send_victory_defeat_dms: {e}')
            except Exception:
                pass

    @app_commands.command(name='ww_execute', description='DM: 夜の選択を実行（実行ボタンの代替）')
    async def ww_execute(self, interaction: discord.Interaction):
        """DM-only command that acts like pressing the 夜の実行ボタン for the caller.

        This is intended for users who don't see the Execute button in the in-channel UI.
        It will set the per-player night event so the engine proceeds when everyone has executed.
        """
        # Only accept in DM
        if not isinstance(interaction.channel, discord.DMChannel):
            try:
                await safe_interaction_send(interaction, content='このコマンドはDMでのみ使用できます。', ephemeral=True)
            except Exception:
                pass
            return

        uid = str(interaction.user.id)
        # find the game where the user participates
        try:
            games = list(getattr(self.storage, '_games', {}).values())
        except Exception:
            games = []

        g = None
        for gg in games:
            try:
                if uid in gg.players:
                    g = gg
                    break
            except Exception:
                continue

        if not g:
            try:
                await safe_interaction_send(interaction, content=msg('no_lobby_in_channel'), ephemeral=True)
            except Exception:
                pass
            return

        # Ensure per-game pending choices exist
        pending = getattr(g, '_pending_night_choices', None)
        if pending is None:
            try:
                await safe_interaction_send(interaction, content='このゲームでは夜の選択が利用できません。', ephemeral=True)
            except Exception:
                pass
            return

        # Check for a staged selection — try both string and int keys to be robust
        sel = None
        try:
            if isinstance(pending, dict):
                sel = pending.get(uid)
                if sel is None:
                    try:
                        sel = pending.get(int(uid))
                    except Exception:
                        pass
        except Exception:
            sel = None

        if not sel:
            try:
                await safe_interaction_send(interaction, content=msg('no_selection'), ephemeral=True)
            except Exception:
                pass
            return

        # Signal the per-player event if present
        # Try to find the per-player event using str or int keys
        try:
            evs = getattr(g, '_night_events', {}) or {}
            ev = evs.get(uid)
            if ev is None:
                try:
                    ev = evs.get(int(uid))
                except Exception:
                    ev = None

            did_set = False
            if ev:
                try:
                    ev.set()
                    did_set = True
                except Exception:
                    did_set = False

            if did_set:
                try:
                    await safe_interaction_send(interaction, content='夜の選択を確定しました。', ephemeral=True)
                except Exception:
                    pass
                return
            else:
                try:
                    await safe_interaction_send(interaction, content=msg('execute_failed'), ephemeral=True)
                except Exception:
                    pass
                return
        except Exception:
            try:
                await safe_interaction_send(interaction, content=msg('execute_failed'), ephemeral=True)
            except Exception:
                pass
            return

        # Handle guesser commands via delegation to _handle_guess_command
        try:
            await self._handle_guess_command(interaction)
        except Exception:
            try:
                await safe_interaction_send(interaction, content=msg('guess_internal_error'), ephemeral=True)
            except Exception:
                pass


    #         if payload.user_id == self.bot.user.id:
    #             return
    #         # fetch display name if possible
    #         display_name = None
    #         try:
    #             user = await self.bot.fetch_user(int(user_id))
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """Auto-join: when a user joins a game's designated VC, add them to the lobby automatically.

        This scans storage._games if available (fallback for InMemoryStorage). If storage
        does not expose a listing API, the event is ignored.
        """
        try:
            # ignore bots
            if member.bot:
                return

            # joined may be None when the user left a VC; do not return early
            # because we need to handle leave events as well as joins.
            joined = getattr(after, 'channel', None)

            # try to enumerate games from storage if possible
            games = []
            try:
                if hasattr(self.storage, '_games'):
                    games = list(getattr(self.storage, '_games').values())
                else:
                    # no enumeration API available; cannot auto-join
                    return
            except Exception:
                return

            for g in games:
                try:
                    designated = getattr(g, '_designated_vc_id', None)
                    if not designated:
                        continue
                    # compare as ints where possible
                    try:
                        if int(designated) != int(joined.id):
                            continue
                    except Exception:
                        continue

                    # only auto-join while in LOBBY phase
                    if getattr(g, 'phase', None) != Phase.LOBBY:
                        continue

                    uid = str(member.id)
                    # If the user left the VC (before is in VC, after is None or different), handle leave
                    left_vc = getattr(before, 'channel', None)
                    joined_vc = getattr(after, 'channel', None)
                    if left_vc and (not joined_vc or left_vc.id != joined_vc.id):
                        # User left a VC. If leaving the designated VC:
                        if int(designated) == int(left_vc.id):
                            # If game still in LOBBY, remove them
                            try:
                                if getattr(g, 'phase', None) == Phase.LOBBY:
                                    if uid in g.players:
                                        g.leave(uid)
                                        self.storage.save_game(g)
                                        try:
                                            ch = self.bot.get_channel(int(g.game_id))
                                            if ch:
                                                # use a more specific message when removing due to VC leave
                                                await ch.send(msg('left_lobby_vc_removed', name=member.display_name))
                                        except Exception:
                                            pass
                                    continue
                            except Exception:
                                pass
                            # If game has started, send a rejoin reminder
                            try:
                                if getattr(g, 'phase', None) != Phase.LOBBY:
                                    try:
                                        ch = self.bot.get_channel(int(g.game_id))
                                        if ch:
                                            await ch.send(f"{member.display_name} {msg('vc_left_request_rejoin')}")
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                    # If joined the designated VC and not yet in players, auto-join
                    if joined_vc and int(designated) == int(joined_vc.id):
                        if uid in g.players:
                            continue
                        try:
                            g.join(uid, member.display_name)
                            self.storage.save_game(g)
                            try:
                                ch = self.bot.get_channel(int(g.game_id))
                                if ch:
                                    await ch.send(f"{member.display_name} {msg('joined_via_voice')}")
                            except Exception:
                                pass
                        except Exception:
                            pass
                except Exception:
                    # per-game failure should not stop others
                    continue
        except Exception:
            # ignore listener-level failures
            return

    # --- Night action handling ---
    async def _run_night_sequence(self, g: Game, channel_id: int):
        """Send night action DMs, collect choices (within timeout), execute night_actions, and post results.

        This implementation preserves the original behavior but fixes indentation and control flow.
        It: delivers queued medium messages, mutes participants, notifies wolves, sends night prompts,
        waits (pause-aware), executes night_actions, delivers private messages, checks for win,
        announces minimally to channel, unmutes, and starts the day flow (including wolf-revote timeout handling).
        """
        # Mark that night sequence has started to prevent duplicate calls
        try:
            # Check if game was force-closed
            if g.phase == Phase.CLOSED:
                g.log('Night sequence aborted: game force-closed')
                return
            if getattr(g, '_night_sequence_started', False):
                try:
                    g.log('Night sequence already started; skipping duplicate call')
                except Exception:
                    pass
                return
            g._night_sequence_started = True
        except Exception:
            pass

        # prepare storage fields
        g._pending_night_choices = {}
        g._night_events = {}
        
        # Double-check for force-close after field preparation
        if g.phase == Phase.CLOSED:
            g.log('Night sequence aborted after field setup: game force-closed')
            return
            
        # Check for no survivors (should end game)
        alive_count = len([p for p in g.players.values() if p.alive])
        if alive_count == 0:
            g.log('Night sequence aborted: no survivors left')
            try:
                g.phase = Phase.END
                self.storage.save_game(g)
            except Exception as e:
                g.log(f"Failed to end game in night sequence with no survivors: {e}")
            return

        # Track night count
        try:
            current_night = int(getattr(g, '_night_count', 0)) + 1
        except Exception:
            current_night = 1
        try:
            g._night_count = current_night
        except Exception:
            pass

        # Announce night phase in game thread
        try:
            await self._announce_phase_change(g, Phase.NIGHT)
        except Exception as e:
            logging.getLogger(__name__).warning(f"Failed to announce night phase change: {e}")

        # Deliver queued medium messages to alive mediums only
        try:
            pm = getattr(g, 'private_messages', {}) or {}
            remaining: Dict[str, List[str]] = {}
            for pid, msgs in list(pm.items()):
                p = g.players.get(pid)
                if p and p.role_id == 'medium' and p.alive:
                    try:
                        user = await self.bot.fetch_user(int(pid))
                        for m in msgs:
                            try:
                                rendered = _format_private_message_for_send(m)
                                await user.send(rendered)
                            except Exception:
                                g.log(f"Failed to send medium message to {pid}")
                    except Exception:
                        g.log(f"Failed to fetch medium user {pid} for medium message delivery")
                else:
                    # retain other private messages for normal night delivery
                    remaining[pid] = msgs
            g.private_messages = remaining
        except Exception:
            g.log("Exception while delivering queued medium messages at night start")
        # initialize seer-result delivered tracking so RunButton can mark when it successfully DMs a seer
        try:
            if getattr(g, '_seer_results_delivered', None) is None:
                g._seer_results_delivered = set()
        except Exception:
            pass

        # Attempt to mute participants in designated VC (if permission available)
        channel_obj = self.bot.get_channel(channel_id)
        if channel_obj is None:
            try:
                channel_obj = await self.bot.fetch_channel(channel_id)
            except Exception:
                channel_obj = None
        guild = getattr(channel_obj, 'guild', None) if channel_obj else None
        if guild:
            try:
                bot_member = guild.get_member(self.bot.user.id) or await guild.fetch_member(self.bot.user.id)
            except Exception:
                bot_member = None
            if bot_member and bot_member.guild_permissions.mute_members:
                designated = getattr(g, '_designated_vc_id', None)
                for pid, p in g.players.items():
                    try:
                        member = guild.get_member(int(pid)) or await guild.fetch_member(int(pid))
                    except Exception:
                        member = None
                    if not member:
                        continue
                    if getattr(member, 'voice', None) and getattr(member.voice, 'channel', None):
                        if designated and member.voice.channel.id != designated:
                            continue
                        try:
                            await member.edit(mute=True)
                        except Exception as e:
                            g.log(f"Failed to mute {member.display_name}: {e}")
            else:
                g.log("Bot lacks mute_members permission or bot member not found; skipping night mute")

        # Build list of alive players and wolf ids
        alive = [p for p in g.players.values() if p.alive]
        g.log(f"DEBUG: Alive players: {[(p.id, p.name, p.role_id) for p in alive]}")
        
        # Check roles dict
        try:
            roles_info = {}
            for role_id, role_obj in getattr(g, 'roles', {}).items():
                faction = getattr(role_obj, 'faction', None)
                roles_info[role_id] = faction
            g.log(f"DEBUG: Roles faction mapping: {roles_info}")
        except Exception as e:
            g.log(f"DEBUG: Failed to get roles info: {e}")
        
        wolf_ids = [p.id for p in alive if p.role_id and getattr(g.roles.get(p.role_id), 'faction', None) == 'werewolf']
        g.log(f"DEBUG: Detected wolf_ids: {wolf_ids}")

        # Do not create server wolf channel; notify wolves via DM only when there are 2 or more wolves
        if wolf_ids and len(wolf_ids) >= 2:
            try:
                g._wolf_group_members = list(wolf_ids)
            except Exception:
                g._wolf_group_members = wolf_ids
            try:
                g.log(f"Wolf group members set: {g._wolf_group_members}")
            except Exception:
                pass
            for wid in wolf_ids:
                try:
                    # Prefer cached user when available to avoid network fetch; fallback to fetch_user
                    user = None
                    get_user_fn = getattr(self.bot, 'get_user', None)
                    if callable(get_user_fn):
                        try:
                            user = get_user_fn(int(wid))
                        except Exception:
                            user = None
                    if not user:
                        try:
                            user = await self.bot.fetch_user(int(wid))
                        except Exception:
                            user = None

                    if user:
                        try:
                            await user.send(msg('wolf_group_started'))
                        except Exception:
                            # simple retry once after a short pause
                            try:
                                await asyncio.sleep(0.5)
                                await user.send(msg('wolf_group_started'))
                            except Exception as e:
                                try:
                                    g.log(f"Failed to send wolf_group_started DM to {wid}: {e}")
                                except Exception:
                                    pass
                                # record for operator visibility
                                try:
                                    fails = getattr(g, '_wolf_dm_failures', []) or []
                                    if wid not in fails:
                                        fails.append(wid)
                                    g._wolf_dm_failures = fails
                                except Exception:
                                    pass
                    else:
                        try:
                            g.log(f"Failed to fetch user for wolf id {wid} when starting wolf group")
                        except Exception:
                            pass
                        try:
                            fails = getattr(g, '_wolf_dm_failures', []) or []
                            if wid not in fails:
                                fails.append(wid)
                            g._wolf_dm_failures = fails
                        except Exception:
                            pass
                except Exception as e:
                    try:
                        g.log(f"Unexpected error notifying wolf {wid}: {e}")
                    except Exception:
                        pass
                    try:
                        # store the exception detail for later inspection
                        errs = getattr(g, '_wolf_dm_errors', []) or []
                        errs.append({'wid': wid, 'error': str(e)})
                        g._wolf_dm_errors = errs
                    except Exception:
                        pass
        else:
            # ensure no wolf group members stored for lone-wolf games
            try:
                g._wolf_group_members = []
            except Exception:
                pass

        # Schedule 30s remaining notifier for wolves
        try:
            # If a runtime override exists, use it; if it's None, treat as infinite (no timeout).
            night_timeout = getattr(g, '_runtime_night_timeout', None)
            try:
                night_timeout = int(night_timeout) if night_timeout is not None else None
            except Exception:
                night_timeout = None
            if night_timeout and night_timeout > 30 and wolf_ids:
                wait_seconds = night_timeout - 30

                async def _wolf_30s_notifier():
                    try:
                        await self._sleep_while_not_paused(wait_seconds, g)
                        for wid in wolf_ids:
                            try:
                                p = g.players.get(wid)
                                if not p or not p.alive:
                                    continue
                                user = await self.bot.fetch_user(int(wid))
                                try:
                                    await user.send(msg('wolf_night_30s_dm'))
                                except Exception:
                                    pass
                            except Exception:
                                pass
                    except asyncio.CancelledError:
                        return
                    except Exception:
                        return

                try:
                    t = asyncio.create_task(_wolf_30s_notifier())
                    g._wolf_30s_task = t
                except Exception:
                    g._wolf_30s_task = None
        except Exception:
            pass

        # Send night prompts to roles that act at night
        # Prepare night events mapping
        try:
            if getattr(g, '_night_events', None) is None:
                g._night_events = {}
        except Exception:
            g._night_events = {}

        for pid, p in g.players.items():
            if not p.alive:
                continue
            role = p.role_id

            # Build options depending on role
            options: List[discord.SelectOption] = []
            try:
                if role == 'seer':
                    if current_night == 1:
                        # first-night seer: notify immediate result instead of presenting choices
                        try:
                            candidates = []
                            for t in alive:
                                if t.id == pid:
                                    continue
                                try:
                                    target_role = g.roles.get(t.role_id) if t.role_id and getattr(g, 'roles', None) else None
                                    if t.role_id == 'jester' or (target_role and getattr(target_role, 'id', None) == 'jester'):
                                        seer_result = '白'
                                    elif target_role and getattr(target_role, 'faction', None) == 'werewolf':
                                        seer_result = '黒'
                                    else:
                                        seer_result = '白'
                                except Exception:
                                    seer_result = '白'
                                if seer_result == '白':
                                    candidates.append(t)
                            if not candidates:
                                # inform seer there's no white candidate
                                try:
                                    user = self.bot.get_user(int(pid)) if getattr(self.bot, 'get_user', None) else None
                                    if not user:
                                        user = await self.bot.fetch_user(int(pid))
                                    try:
                                        await user.send(msg('seer_no_white'))
                                    except Exception:
                                        pass
                                except Exception:
                                    pass
                                options = []
                            else:
                                try:
                                    import random as _random
                                    chosen = _random.choice(candidates)
                                except Exception:
                                    # fallback: pick first candidate if random fails
                                    chosen = candidates[0]
                                target_name = chosen.name
                                try:
                                    user = self.bot.get_user(int(pid)) if getattr(self.bot, 'get_user', None) else None
                                    if not user:
                                        user = await self.bot.fetch_user(int(pid))
                                    try:
                                        await user.send(msg('seer_result', target=target_name, result='白'))
                                    except Exception:
                                        pass
                                except Exception:
                                    pass
                                options = []
                        except Exception:
                                options = [discord.SelectOption(label=t.name, value=str(t.id)) for t in alive if t.id != pid]
                    else:
                        options = [discord.SelectOption(label=t.name, value=str(t.id)) for t in alive]

                elif getattr(g.roles.get(role), 'faction', None) == 'werewolf' and current_night >= 2:
                    # Treat any role whose configured faction is 'werewolf' as a wolf for night prompts
                    options = [discord.SelectOption(label=t.name, value=str(t.id)) for t in alive if t.id not in wolf_ids]

                elif role == 'knight' and current_night >= 2:
                    prev = getattr(g, '_knight_prev_protect', {}).get(pid)
                    opts = []
                    for t in alive:
                        if prev and t.id == prev:
                            continue
                        opts.append(discord.SelectOption(label=t.name, value=str(t.id)))
                    options = opts

                elif role == 'arsonist':
                    helper = getattr(g, 'possible_arsonist_targets', None)
                    if callable(helper):
                        try:
                            candidates = helper(pid)
                            options = [discord.SelectOption(label=t.name, value=str(t.id)) for t in candidates]
                        except Exception:
                            options = []
                    else:
                        opts = []
                        for t in alive:
                            try:
                                if t.id == pid:
                                    continue
                                if not t.alive:
                                    continue
                                if getattr(t, 'oiled', False):
                                    continue
                                opts.append(discord.SelectOption(label=t.name, value=str(t.id)))
                            except Exception:
                                continue
                        options = opts
                elif role == 'sage':
                    # Sage can choose to deploy a shield (結界) from night 2 onwards if they have any left.
                    # Skip sage prompts on night 1.
                    if current_night < 2:
                        # Skip sage on first night
                        options = []
                    else:
                        try:
                            shields_left = getattr(g, '_sage_shields_left', {}) or {}
                            shields = int(shields_left.get(pid, 0))
                        except Exception:
                            shields = 0
                        if shields > 0:
                            try:
                                shield_label = msg('sage_shield_label')
                            except Exception:
                                shield_label = '結界を張る'
                            # Do not show the remaining count on the control label; show it in the prompt instead.
                            try:
                                label = shield_label
                            except Exception:
                                label = shield_label
                            options = [discord.SelectOption(label=label, value='__shield__')]
                        else:
                            # No shields left: notify the player via DM so they know why they weren't asked
                            options = []
                            try:
                                # Resolve user and send a short DM (best-effort)
                                user = None
                                get_user_fn = getattr(self.bot, 'get_user', None)
                                if callable(get_user_fn):
                                    try:
                                        user = get_user_fn(int(pid))
                                    except Exception:
                                        user = None
                                if not user:
                                    try:
                                        user = await self.bot.fetch_user(int(pid))
                                    except Exception:
                                        user = None
                                if user:
                                    try:
                                        await user.send(msg('sage_shield_none_dm'))
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                else:
                    # roles without night actions are skipped
                    options = []
            except Exception:
                options = []

            if not options:
                continue

            # register event and view
            try:
                ev = asyncio.Event()
                g._night_events[pid] = ev
            except Exception:
                ev = asyncio.Event()
                try:
                    g._night_events[pid] = ev
                except Exception:
                    pass

            # For Sage, prefer a button-based view (use/skip) instead of a select
            # For Evil Busker, use a combined view with attack select + fake death button
            try:
                if role == 'sage':
                    try:
                        shields = int(getattr(g, '_sage_shields_left', {}).get(pid, 0))
                    except Exception:
                        shields = 0
                    view = SageActionView(g=g, player_id=pid, timeout=g.settings.night_duration_sec)
                elif role == 'evil_busker' and current_night >= 2:
                    # Evil Busker gets both attack selection and fake death button
                    try:
                        fake_active_set = getattr(g, '_busker_fake_active', set()) or set()
                        fake_uses_consumed = getattr(g, '_busker_fake_uses', {}) or {}
                        used_count = int(fake_uses_consumed.get(pid, 0))
                        try:
                            limit = int(g._busker_fake_limit())
                        except Exception:
                            limit = 2
                        uses_left = max(0, limit - used_count)
                        can_use_fake = uses_left > 0 and pid not in fake_active_set
                    except Exception:
                        uses_left = 0
                        can_use_fake = False
                    view = BuskerNightView(game=g, player_id=pid, attack_options=options, 
                                           can_use_fake=can_use_fake, uses_left=uses_left, 
                                           timeout=g.settings.night_duration_sec)
                else:
                    view = NightSelectView(g=g, player_id=pid, alive_opts=options, timeout=g.settings.night_duration_sec)
            except Exception:
                view = self.NightSelectView(timeout=g.settings.night_duration_sec, game=g, player_id=pid, options=options)
            # attach cog reference so RunButton can reliably resolve bot/get_user
            try:
                view._cog = self
            except Exception:
                pass

            # Prepare message and send
            try:
                robj = g.roles.get(role) if role and getattr(g, 'roles', None) else None
                display_role = robj.name if robj and getattr(robj, 'name', None) else (role or 'unknown')
                # Use a role-specific night prompt when appropriate. Sage doesn't select a target.
                try:
                    if role == 'sage':
                        try:
                            shields = int(getattr(g, '_sage_shields_left', {}).get(pid, 0))
                        except Exception:
                            shields = 0
                        night_msg = msg('sage_night_prompt', role=display_role, shields=shields)
                    elif role == 'evil_busker' and current_night >= 2:
                        try:
                            fake_uses_consumed = getattr(g, '_busker_fake_uses', {}) or {}
                            used_count = int(fake_uses_consumed.get(pid, 0))
                            try:
                                limit = int(g._busker_fake_limit())
                            except Exception:
                                limit = 2
                            uses_left = max(0, limit - used_count)
                        except Exception:
                            uses_left = 0
                        # Evil Busker has both attack and fake death options
                        night_msg = msg('busker_night_prompt_with_attack', role=display_role, uses=uses_left)
                    else:
                        night_msg = msg('night_prompt', role=display_role)
                except Exception:
                    # fallback to generic prompt
                    night_msg = msg('night_prompt', role=display_role)
                if role == 'knight':
                    try:
                        night_msg = night_msg + "\n" + msg('knight_exclude_prev_note')
                    except Exception:
                        pass

                # resolve user (cache first)
                user = None
                get_user_fn = getattr(self.bot, 'get_user', None)
                if callable(get_user_fn):
                    try:
                        user = get_user_fn(int(pid))
                    except Exception:
                        user = None
                if not user:
                    try:
                        user = await self.bot.fetch_user(int(pid))
                    except Exception:
                        user = None

                if not user:
                    try:
                        g.log(f"Could not fetch user object for {pid} when preparing night prompt")
                    except Exception:
                        pass
                    try:
                        fails = getattr(g, '_night_dm_failures', []) or []
                        if pid not in fails:
                            fails.append(pid)
                        g._night_dm_failures = fails
                    except Exception:
                        pass
                    try:
                        ev = g._night_events.get(pid)
                        if ev:
                            ev.set()
                    except Exception:
                        pass
                    continue

                try:
                    await user.send(night_msg, view=view)
                except Exception as e:
                    try:
                        g.log(f"Failed to send night prompt to {pid}: {e}")
                    except Exception:
                        pass
                    try:
                        fails = getattr(g, '_night_dm_failures', []) or []
                        if pid not in fails:
                            fails.append(pid)
                        g._night_dm_failures = fails
                    except Exception:
                        pass
                    try:
                        errs = getattr(g, '_night_dm_errors', []) or []
                        errs.append({'pid': pid, 'error': str(e)})
                        g._night_dm_errors = errs
                    except Exception:
                        pass
                    try:
                        ev = g._night_events.get(pid)
                        if ev:
                            ev.set()
                    except Exception:
                        pass
            except Exception:
                try:
                    g.log(f"Could not prepare night prompt for {p.name}")
                except Exception:
                    pass

        # Wait for responses (pause-aware via helper)
        try:
            await self._wait_for_night_responses(g)
        except Exception:
            try:
                g.log("Exception while waiting for night responses; proceeding")
            except Exception:
                pass

        # Execute night actions and post-night delivery
        try:
            # cancel 30s notifier if still scheduled
            try:
                t = getattr(g, '_wolf_30s_task', None)
                if t:
                    try:
                        t.cancel()
                    except Exception:
                        pass
                    try:
                        g._wolf_30s_task = None
                    except Exception:
                        pass
            except Exception:
                pass

            before_alive_ids = {p.id for p in g.players.values() if p.alive}
            try:
                g.night_actions(g._pending_night_choices)
            except Exception as e:
                g.log(f"Error applying night actions: {e}")
            after_alive_ids = {p.id for p in g.players.values() if p.alive}
            dead_ids = list(before_alive_ids - after_alive_ids)

            # Deliver private messages generated during night (skip dead recipients)
            try:
                for pid, msgs in list(getattr(g, 'private_messages', {}).items()):
                    p = g.players.get(pid)
                    if not p or not p.alive:
                        g.log(f"Skipping private messages to {pid} during night (dead or missing)")
                        continue
                    try:
                        user = await self.bot.fetch_user(int(pid))
                        for m in msgs:
                            try:
                                # Skip queued seer results for mediums/night delivery if any
                                try:
                                    if isinstance(m, dict) and m.get('key') in ('seer_result', 'seer_result_followup'):
                                        delivered = getattr(g, '_seer_results_delivered', set())
                                        if str(pid) in delivered:
                                            continue
                                except Exception:
                                    pass
                                rendered = _format_private_message_for_send(m)
                                await user.send(rendered)
                            except Exception:
                                g.log(f"Failed to send private message to {pid}")
                    except Exception:
                        g.log(f"Failed to fetch user {pid} for private messages")
                g.private_messages = {}
            except Exception:
                g.log("Exception during private message delivery")

            # collect dead names for announcement and store on game
            dead_names: List[str] = []
            for did in dead_ids:
                p = g.players.get(did)
                if p:
                    dead_names.append(p.name)
            try:
                g._last_night_dead = dead_names
                g._last_night_dead_ids = dead_ids
            except Exception:
                g._last_night_dead = []
                g._last_night_dead_ids = []
        except Exception as e:
            g.log(f"Error during night actions: {e}")

        # After night, run win check
        try:
            channel = self.bot.get_channel(channel_id)
        except Exception:
            channel = None
        try:
            if channel is None:
                try:
                    channel = await self.bot.fetch_channel(channel_id)
                except Exception:
                    channel = None
        except Exception:
            channel = None

        try:
            # Mark this as a night actions completion check
            g._check_win_context = 'night_actions_complete'
            handled = await self._evaluate_and_handle_win(g, channel)
            if handled:
                return
        except Exception as e:
            try:
                g.log(f"check_win after night failed: {e}")
            except Exception:
                pass

        # No winner: transition to DAY
        try:
            g.phase = Phase.DAY
        except Exception:
            pass

        # If this was the first night, pause briefly before fully transitioning to day
        try:
            if int(current_night) == 1:
                try:
                    # Respect pause behavior (use helper that honors g._paused)
                    await self._sleep_while_not_paused(10, g)
                except Exception:
                    # best-effort sleep; ignore failures
                    pass
        except Exception:
            pass

        # persist game state; do not post a 'no public logs' message to channel
        try:
            self.storage.save_game(g)
        except Exception:
            pass

        # Unmute alive players (permission-guarded) — during game continuation unmute only alive players
        try:
            await self._unmute_all_participants(g, channel, only_alive=True)
        except Exception:
            try:
                g.log("Exception during unmuting alive players after night")
            except Exception:
                pass

        # Deliver any busker revival prompts (engine may have enqueued 'busker_revive_prompt' during check_win)
        try:
            pm = getattr(g, 'private_messages', {}) or {}
            remaining = {}
            for pid, msgs in list(pm.items()):
                # filter out busker_revive_prompt entries and handle them with an interactive view
                other_msgs = []
                for m in msgs:
                    try:
                        if isinstance(m, dict) and m.get('key') == 'busker_revive_prompt':
                            # send revive DM with a select of alive targets (exclude the busker themself)
                            try:
                                user = await self.bot.fetch_user(int(pid))
                            except Exception:
                                user = None
                            try:
                                # build options from current alive players excluding the busker
                                opts = []
                                for pp in g.players.values():
                                    try:
                                        if not pp.alive:
                                            continue
                                        if pp.id == pid:
                                            continue
                                        opts.append(discord.SelectOption(label=pp.name, value=str(pp.id)))
                                    except Exception:
                                        continue
                                if user:
                                    v = self.BuskerReviveView(timeout=300, game=g, player_id=pid, options=opts)
                                    try:
                                        v._cog = self
                                    except Exception:
                                        pass
                                    try:
                                        await user.send(msg('busker_revive_dm_header'), view=v)
                                    except Exception:
                                        try:
                                            await user.send(msg('busker_revive_dm_header'))
                                        except Exception:
                                            pass
                            except Exception:
                                try:
                                    g.log(f"Failed to send busker revive DM to {pid}")
                                except Exception:
                                    pass
                        else:
                            other_msgs.append(m)
                    except Exception:
                        other_msgs.append(m)
                if other_msgs:
                    remaining[pid] = other_msgs
            # replace private_messages with remaining (remove processed busker prompts)
            g.private_messages = remaining
        except Exception:
            try:
                g.log('Exception while delivering busker revive prompts')
            except Exception:
                pass

        # If wolves were tied, attempt a revote among wolves with an overall night timeout
        try:
            tie_candidates = getattr(g, '_wolf_tie', None)
        except Exception:
            tie_candidates = None

        if tie_candidates:
            # Use runtime override if provided; otherwise keep None for infinite timeout.
            night_timeout = getattr(g, '_runtime_night_timeout', None)
            try:
                night_timeout = int(night_timeout) if night_timeout is not None else None
            except Exception:
                night_timeout = None
            revote_deadline = None
            if night_timeout:
                try:
                    import time
                    revote_deadline = time.time() + float(night_timeout)
                except Exception:
                    revote_deadline = None

            # Loop performing wolf revotes until cleared or deadline
            while getattr(g, '_wolf_tie', None):
                # build options among tie candidates
                options = []
                for tid in tie_candidates:
                    p = g.players.get(tid)
                    if p:
                        options.append(discord.SelectOption(label=p.name, value=str(p.id)))

                wolf_views = []
                for pid, p in g.players.items():
                    if p.role_id and getattr(g.roles.get(p.role_id), 'faction', None) == 'werewolf' and p.alive:
                        try:
                            user = await self.bot.fetch_user(int(pid))
                            v = NightSelectView(g=g, player_id=pid, alive_opts=options, timeout=None)
                            try:
                                v._cog = self
                            except Exception:
                                pass
                            try:
                                try:
                                    robj = g.roles.get('werewolf') if getattr(g, 'roles', None) else None
                                    display_role = robj.name if robj and getattr(robj, 'name', None) else 'werewolf'
                                except Exception:
                                    display_role = 'werewolf'
                                await user.send(msg('night_prompt', role=display_role), view=v)
                            except Exception:
                                pass
                            wolf_views.append(v)
                        except Exception:
                            pass

                # wait for wolf views (honor pause and remaining time)
                for v in wolf_views:
                    try:
                        if revote_deadline:
                            import time
                            remaining = revote_deadline - time.time()
                            if remaining <= 0:
                                raise asyncio.TimeoutError()
                            await self._wait_view_with_pause(v, int(remaining), g)
                        else:
                            await self._wait_view_with_pause(v, None, g)
                    except asyncio.TimeoutError:
                        # deadline reached for this view
                        pass
                    except Exception:
                        pass

                # apply night actions again to reflect new wolf choices
                try:
                    # clear previous tie flag to allow engine to re-evaluate
                    try:
                        g._wolf_tie = None
                    except Exception:
                        pass
                    g.night_actions(g._pending_night_choices)
                except Exception as e:
                    g.log(f"Error during wolf revote night_actions: {e}")

                tie_candidates = getattr(g, '_wolf_tie', None)
                # if still tied and deadline exceeded, abort attack
                import time
                if tie_candidates and revote_deadline and time.time() >= revote_deadline:
                    try:
                        g._wolf_tie = None
                        # clear pending wolf attacks
                        try:
                            for wid in [pid for pid, p in g.players.items() if p.role_id and getattr(g.roles.get(p.role_id), 'faction', None) == 'werewolf']:
                                try:
                                    if wid in getattr(g, '_pending_night_choices', {}) and g._pending_night_choices.get(wid):
                                        g._pending_night_choices[wid] = None
                                except Exception:
                                    pass
                        except Exception:
                            pass
                        # notify wolves and channel about timeout
                        for pid, p in g.players.items():
                            if p.role_id and getattr(g.roles.get(p.role_id), 'faction', None) == 'werewolf' and p.alive:
                                try:
                                    user = await self.bot.fetch_user(int(pid))
                                    try:
                                        await user.send(msg('wolf_revote_timeout_dm'))
                                    except Exception:
                                        pass
                                except Exception:
                                    pass
                        if channel:
                            try:
                                await self._send_to_game_thread(g, content=msg('wolf_revote_timeout_public'))
                            except Exception:
                                pass
                    except Exception:
                        pass
                    break

        # After resolving night revotes and moving to DAY, start voting immediately
        try:
            # Check if game was force-closed before starting day vote
            if g.phase == Phase.CLOSED:
                g.log('Day vote start aborted: game force-closed')
                return
                
            # ALWAYS use the fixed vote timeout from game settings to prevent /ww_end_vote issues
            vote_timeout = getattr(g, '_fixed_vote_timeout', None)
            g.log(f"PHASE CHANGE: Using fixed vote timeout: {vote_timeout}s")
            
            # CRITICAL: Clear guesser re-vote flag when moving to new day to show death announcements
            g._in_re_vote_after_guess = False
            g.log("PHASE CHANGE: Cleared re-vote flag for new day")
            
            if channel:
                if vote_timeout is None:
                    await self._start_day_vote_channel(g, channel, None)
                else:
                    await self._start_day_vote_channel(g, channel, int(vote_timeout))
        except Exception:
            pass
        finally:
            # Clear night sequence started flag so next night can start
            try:
                g._night_sequence_started = False
            except Exception:
                pass

    async def _wait_for_night_responses(self, g: Game):
        # wait for all events of players who were asked to respond
        # This function is now timeout-aware: it will await until all events are set
        # or until g.settings.night_duration_sec (or g._runtime_night_timeout) elapses.
        events = [ev.wait() for ev in g._night_events.values()]
        if not events:
            return
        # Determine timeout: use runtime override; if it's None, treat as infinite (no timeout)
        timeout = getattr(g, '_runtime_night_timeout', None)
        if timeout is None:
            # no timeout requested: wait until all events complete
            await asyncio.gather(*events)
            return
        try:
            await asyncio.wait_for(asyncio.gather(*events), timeout=timeout)
        except asyncio.TimeoutError:
            # timeout expired; return so caller can handle unresponsive players
            return

    async def _sleep_while_not_paused(self, seconds: int, g: Game):
        """Sleep for up to `seconds` but pause the countdown while g._paused is True."""
        try:
            if seconds is None or int(seconds) <= 0:
                return
        except Exception:
            return
        step = 0.5
        elapsed = 0.0
        # Ensure pause_event exists
        pe = getattr(g, '_pause_event', None)
        if pe is None:
            g._pause_event = asyncio.Event()
            pe = g._pause_event
            # if not paused, set the event so waiters don't block
            if not getattr(g, '_paused', False):
                try:
                    pe.set()
                except Exception:
                    pass

        while elapsed < float(seconds):
            # if paused, wait until resumed
            if getattr(g, '_paused', False):
                try:
                    await pe.wait()
                except Exception:
                    # if waiting fails, small sleep to avoid busy loop
                    try:
                        await asyncio.sleep(step)
                    except Exception:
                        pass
                # don't advance elapsed while paused
                continue
            # not paused: sleep a short step and increment elapsed
            remaining = float(seconds) - elapsed
            try:
                await asyncio.sleep(min(step, remaining))
            except Exception:
                pass
            elapsed += step

    async def _wait_view_with_pause(self, view: ui.View, timeout: Optional[int], g: Game):
        """Wait for a ui.View to finish while honoring g._paused. If timeout is provided,
        the countdown pauses during g._paused periods. If timeout is None, wait indefinitely
        until the view completes (pausing/resuming doesn't affect completion except it suspends timing).
        
        Returns True if view completed normally, False if game was deleted/closed.
        """
        # create a task for the view.wait() so we can poll its completion
        t = asyncio.create_task(view.wait())
        game_id = str(getattr(g, 'game_id', ''))
        try:
            if timeout is None:
                # wait until the view finishes; pause simply suspends sleeping
                while True:
                    if t.done():
                        await t
                        return True
                    
                    # Check if game still exists in storage
                    try:
                        if game_id and hasattr(self.storage, 'load_game'):
                            check_game = self.storage.load_game(game_id)
                            if not check_game:
                                # Game was deleted, cancel view and return
                                try:
                                    view.stop()
                                except Exception:
                                    pass
                                return False
                    except Exception:
                        pass
                    
                    if getattr(g, '_paused', False):
                        pe = getattr(g, '_pause_event', None)
                        if pe is None:
                            g._pause_event = asyncio.Event()
                            pe = g._pause_event
                            if not getattr(g, '_paused', False):
                                try:
                                    pe.set()
                                except Exception:
                                    pass
                        try:
                            await pe.wait()
                        except Exception:
                            try:
                                await asyncio.sleep(0.5)
                            except Exception:
                                pass
                        continue
                    # not paused, short sleep to yield
                    try:
                        await asyncio.sleep(0.5)
                    except Exception:
                        pass
            else:
                elapsed = 0.0
                step = 0.5
                total = float(timeout)
                pe = getattr(g, '_pause_event', None)
                if pe is None:
                    g._pause_event = asyncio.Event()
                    pe = g._pause_event
                    if not getattr(g, '_paused', False):
                        try:
                            pe.set()
                        except Exception:
                            pass
                while elapsed < total:
                    if t.done():
                        await t
                        return True
                    
                    # Check if game still exists in storage
                    try:
                        if game_id and hasattr(self.storage, 'load_game'):
                            check_game = self.storage.load_game(game_id)
                            if not check_game:
                                # Game was deleted, cancel view and return
                                try:
                                    view.stop()
                                except Exception:
                                    pass
                                return False
                    except Exception:
                        pass
                    
                    if getattr(g, '_paused', False):
                        try:
                            await pe.wait()
                        except Exception:
                            try:
                                await asyncio.sleep(step)
                            except Exception:
                                pass
                        continue
                    remaining = total - elapsed
                    try:
                        await asyncio.sleep(min(step, remaining))
                    except Exception:
                        pass
                    elapsed += step
                # timeout reached; if view is still running, return (caller will handle pending votes)
                return True
        finally:
            if not t.done():
                try:
                    t.cancel()
                except Exception:
                    pass

    class BuskerFakeDeathView(ui.View):
        def __init__(self, timeout: int, game: Game, player_id: str, uses_left: int = 0):
            super().__init__(timeout=timeout)
            self.game = game
            self.player_id = player_id
            self.uses_left = uses_left
            # two buttons: use fake death, skip
            try:
                self.add_item(self.UseFakeButton(row=0))
                self.add_item(self.SkipButton(row=0))
            except Exception:
                pass

        class UseFakeButton(ui.Button):
            def __init__(self, row: int = 0):
                try:
                    label = msg('busker_fake_use_button')
                except Exception:
                    label = '偽装死を使う'
                super().__init__(label=label, style=discord.ButtonStyle.danger, row=row)

            async def callback(self, interaction: discord.Interaction):
                view: WerewolfCog.BuskerFakeDeathView = self.view  # type: ignore
                try:
                    await interaction.response.defer(ephemeral=True)
                except Exception:
                    pass
                try:
                    # register fake death usage
                    try:
                        view.game._pending_night_choices[view.player_id] = '__fake_death__'
                    except Exception:
                        pass
                    # send ephemeral confirmation and attempt DM
                    try:
                        await interaction.followup.send(msg('busker_fake_confirmed'), ephemeral=True)
                    except Exception:
                        try:
                            await interaction.response.send_message(msg('busker_fake_confirmed'), ephemeral=True)
                        except Exception:
                            pass
                    try:
                        await interaction.user.send(msg('busker_fake_confirmed'))
                    except Exception:
                        pass
                except Exception:
                    try:
                        view.game.log(f"Error handling busker UseFakeButton for {view.player_id}")
                    except Exception:
                        pass
                finally:
                    try:
                        ev = view.game._night_events.get(view.player_id)
                        if ev:
                            ev.set()
                    except Exception:
                        pass
                    try:
                        view.stop()
                    except Exception:
                        pass

        class SkipButton(ui.Button):
            def __init__(self, row: int = 0):
                try:
                    label = msg('busker_fake_skip_button')
                except Exception:
                    label = 'スキップ'
                super().__init__(label=label, style=discord.ButtonStyle.secondary, row=row)

            async def callback(self, interaction: discord.Interaction):
                view: WerewolfCog.BuskerFakeDeathView = self.view  # type: ignore
                try:
                    await interaction.response.defer(ephemeral=True)
                except Exception:
                    pass
                try:
                    try:
                        view.game._pending_night_choices[view.player_id] = None
                    except Exception:
                        pass
                    try:
                        await interaction.followup.send(msg('busker_fake_skipped'), ephemeral=True)
                    except Exception:
                        try:
                            await interaction.response.send_message(msg('busker_fake_skipped'), ephemeral=True)
                        except Exception:
                            pass
                    try:
                        await interaction.user.send(msg('busker_fake_skipped'))
                    except Exception:
                        pass
                except Exception:
                    try:
                        view.game.log(f"Error handling busker SkipButton for {view.player_id}")
                    except Exception:
                        pass
                finally:
                    try:
                        ev = view.game._night_events.get(view.player_id)
                        if ev:
                            ev.set()
                    except Exception:
                        pass
                    try:
                        view.stop()
                    except Exception:
                        pass

    class NightSelectView(ui.View):
        def __init__(self, timeout: int, game: Game, player_id: str, options: List[discord.SelectOption]):
            super().__init__(timeout=timeout)
            self.game = game
            self.player_id = player_id
            self.selected_target = None
            # place the select on row 0
            self.add_item(self.TargetSelect(options=options, row=0))
            # add an Execute button on a new row (row=1) so it appears below the select
            try:
                self.add_item(self.RunButton(row=1))
            except Exception:
                pass

        class TargetSelect(ui.Select):
            def __init__(self, options: List[discord.SelectOption], row: int = 0):
                try:
                    placeholder = msg('vote_placeholders')[0]
                except Exception:
                    placeholder = 'Choose a target...'
                super().__init__(placeholder=placeholder, min_values=1, max_values=1, options=options, row=row)

            async def callback(self, interaction: discord.Interaction):
                # parent view contains game and player_id
                view: WerewolfCog.NightSelectView = self.view  # type: ignore
                try:
                    # defer to give the bot time to process (avoids "application did not respond")
                    await interaction.response.defer(ephemeral=True)
                except Exception:
                    # if defer fails, attempt a robust ack so the client doesn't show an error
                    try:
                        await _ack_interaction(interaction, content=None, ephemeral=True)
                    except Exception:
                        logging.getLogger(__name__).exception('TargetSelect.callback: failed to ack interaction')

                selected = self.values[0]
                try:
                    # stage the selection; do not apply until user presses Run
                    view.selected_target = selected
                    # Acknowledge selection and prompt for Execute
                    ev = None
                    # (no wolf-group voting logic here; keep per-player pending choice behavior)
                    # Build a friendly confirmation using player name when possible
                    try:
                        target = view.game.players.get(selected)
                        target_label = target.name if target else str(selected)
                    except Exception:
                        target_label = str(selected)

                    # Acknowledge via followup (since we deferred): selection staged
                    try:
                        await interaction.followup.send(msg('night_choice_registered', target=target_label) + msg('execute_button_instruction'))
                    except Exception:
                        try:
                            await interaction.response.send_message(msg('night_choice_registered', target=target_label) + msg('execute_button_instruction'))
                        except Exception:
                            pass

                    # Note: seer reveal is deferred until the user presses the Execute (実行) button.
                except Exception as e:
                    # log and inform user
                    try:
                        view.game.log(f"Error registering night choice for {view.player_id}: {e}")
                    except Exception:
                        print(f"Error logging night choice failure: {e}")
                    try:
                        await interaction.followup.send("Failed to register your choice due to an internal error.")
                    except Exception:
                        try:
                            await interaction.response.send_message("Failed to register your choice due to an internal error.")
                        except Exception:
                            pass
                finally:
                    # do not stop the view here; wait for explicit Execute (Run) press
                    pass

        class RunButton(ui.Button):
            def __init__(self, row: int = 1):
                super().__init__(label=msg('execute_button'), style=discord.ButtonStyle.danger, row=row)

            async def callback(self, interaction: discord.Interaction):
                view: WerewolfCog.NightSelectView = self.view  # type: ignore
                try:
                    # apply staged selection if present
                    sel = getattr(view, 'selected_target', None)
                    if sel is None:
                        try:
                            await interaction.response.send_message(msg('no_selection'), ephemeral=True)
                        except Exception:
                            pass
                        return
                    try:
                        view.game._pending_night_choices[view.player_id] = sel
                    except Exception:
                        pass
                    # If the actor is a seer, reveal the checked role/faction privately now that Execute was pressed
                    try:
                        actor = view.game.players.get(view.player_id)
                        if actor and actor.role_id == 'seer':
                            t = view.game.players.get(sel)
                            if t:
                                role_id = t.role_id
                                role_info = view.game.roles.get(role_id) if role_id else None
                                faction = None
                                try:
                                    faction = role_info.faction if role_info and hasattr(role_info, 'faction') else None
                                except Exception:
                                    faction = None
                                if faction == 'werewolf':
                                    seer_result = '黒'
                                else:
                                    seer_result = '白'
                                # attempt to send DM to the seer actor with localized message
                                try:
                                    # Robust user resolution: try several bot references and both get_user (cache) and fetch_user
                                    async def _resolve_user():
                                        candidates = []
                                        try:
                                            if getattr(view, '_cog', None) and getattr(view._cog, 'bot', None):
                                                candidates.append(view._cog.bot)
                                        except Exception:
                                            pass
                                        try:
                                            if getattr(interaction, 'client', None):
                                                candidates.append(interaction.client)
                                        except Exception:
                                            pass
                                        try:
                                            b = getattr(self, 'bot', None)
                                            if b:
                                                candidates.append(b)
                                        except Exception:
                                            pass

                                        seen = set()
                                        for bot_obj in candidates:
                                            try:
                                                if id(bot_obj) in seen:
                                                    continue
                                                seen.add(id(bot_obj))
                                                # try cached get_user first
                                                get_user_fn = getattr(bot_obj, 'get_user', None)
                                                if callable(get_user_fn):
                                                    try:
                                                        u = get_user_fn(int(view.player_id))
                                                    except Exception:
                                                        u = None
                                                    if u:
                                                        # may be a coroutine or User
                                                        if inspect.isawaitable(u):
                                                            try:
                                                                u = await u
                                                            except Exception:
                                                                u = None
                                                        if u:
                                                            return u
                                                # fall back to fetch_user
                                                fetch_fn = getattr(bot_obj, 'fetch_user', None)
                                                if callable(fetch_fn):
                                                    try:
                                                        u = await fetch_fn(int(view.player_id))
                                                        if u:
                                                            return u
                                                    except Exception:
                                                        pass
                                            except Exception:
                                                continue
                                        return None

                                    user = await _resolve_user()

                                    if user:
                                        # Try sending DM with small retry/backoff and two payload formats (i18n then fallback)
                                        sent = False
                                        last_exc = None
                                        for attempt, payload in enumerate((msg('seer_result_followup', target=str(t.name), result=seer_result), f"占い結果: {t.name} は {seer_result} でした。")):
                                            try:
                                                await user.send(payload)
                                                sent = True
                                                break
                                            except Exception as e:
                                                last_exc = e
                                                try:
                                                    await asyncio.sleep(0.3 * (attempt + 1))
                                                except Exception:
                                                    pass

                                        if sent:
                                            try:
                                                if getattr(view.game, '_seer_results_delivered', None) is None:
                                                    view.game._seer_results_delivered = set()
                                                view.game._seer_results_delivered.add(str(view.player_id))
                                            except Exception:
                                                pass
                                        else:
                                            # Enqueue fallback so engine will attempt later
                                            try:
                                                view.game._push_private(str(view.player_id), {'key': 'seer_result_followup', 'params': {'target': str(t.name), 'result': seer_result}})
                                            except Exception:
                                                try:
                                                    view.game.log(f"Failed to DM seer {view.player_id}: {last_exc}")
                                                except Exception:
                                                    pass
                                    else:
                                        # No user object could be resolved; enqueue fallback and log
                                        try:
                                            view.game._push_private(str(view.player_id), {'key': 'seer_result_followup', 'params': {'target': str(t.name), 'result': seer_result}})
                                        except Exception:
                                            try:
                                                view.game.log(f"Could not resolve seer user {view.player_id} to DM result")
                                            except Exception:
                                                pass
                                except Exception:
                                    # swallow to avoid stopping night processing
                                    try:
                                        view.game.log(f"Exception while delivering seer result to {getattr(view, 'player_id', None)}")
                                    except Exception:
                                        pass
                    except Exception:
                        # don't let seer notify issues stop execution
                        pass
                    # set event if present
                    try:
                        ev = view.game._night_events.get(view.player_id)
                        if ev:
                            ev.set()
                    except Exception:
                        pass
                    # acknowledge and stop view
                    try:
                        # Get target name instead of ID
                        target = view.game.players.get(sel)
                        target_name = target.name if target else str(sel)
                        await interaction.response.send_message(msg('night_choice_executed', target=target_name))
                    except Exception:
                        try:
                            # Get target name instead of ID for fallback
                            target = view.game.players.get(sel)
                            target_name = target.name if target else str(sel)
                            await interaction.followup.send(msg('night_choice_executed', target=target_name))
                        except Exception:
                            pass
                finally:
                    try:
                        view.stop()
                    except Exception:
                        pass

        # end of TargetSelect

    # end of NightSelectView

    class BuskerReviveView(ui.View):
        """View sent to a revived Evil Busker to choose an extra-attack target.

        Usage: instantiate with game and player_id and send via user.send(..., view=v)
        """
        def __init__(self, timeout: int, game: Game, player_id: str, options: List[discord.SelectOption]):
            super().__init__(timeout=timeout)
            self.game = game
            self.player_id = player_id
            self.selected_target: Optional[str] = None
            try:
                self.add_item(self.TargetSelect(options=options, row=0))
            except Exception:
                pass
            try:
                self.add_item(self.ConfirmButton(row=1))
                self.add_item(self.CancelButton(row=1))
            except Exception:
                pass

        class TargetSelect(ui.Select):
            def __init__(self, options: List[discord.SelectOption], row: int = 0):
                try:
                    placeholder = msg('busker_revive_select_placeholder')
                except Exception:
                    placeholder = 'Choose your extra-attack target'
                super().__init__(placeholder=placeholder, min_values=1, max_values=1, options=options, row=row)

            async def callback(self, interaction: discord.Interaction):
                view: WerewolfCog.BuskerReviveView = self.view  # type: ignore
                try:
                    await interaction.response.defer(ephemeral=True)
                except Exception:
                    pass
                try:
                    view.selected_target = self.values[0]
                    # Acknowledge selection
                    try:
                        target = view.game.players.get(view.selected_target)
                        label = target.name if target else str(view.selected_target)
                    except Exception:
                        label = str(view.selected_target)
                    try:
                        await interaction.followup.send(msg('night_choice_registered', target=label), ephemeral=True)
                    except Exception:
                        try:
                            await interaction.response.send_message(msg('night_choice_registered', target=label), ephemeral=True)
                        except Exception:
                            pass
                except Exception:
                    try:
                        view.game.log(f"BuskerReviveView: failed to register selection for {view.player_id}")
                    except Exception:
                        pass

        class ConfirmButton(ui.Button):
            def __init__(self, row: int = 1):
                super().__init__(label=msg('execute_button'), style=discord.ButtonStyle.danger, row=row)

            async def callback(self, interaction: discord.Interaction):
                view: WerewolfCog.BuskerReviveView = self.view  # type: ignore
                try:
                    await interaction.response.defer(ephemeral=True)
                except Exception:
                    pass
                try:
                    tgt = getattr(view, 'selected_target', None)
                    if not tgt:
                        try:
                            await interaction.followup.send(msg('no_selection'), ephemeral=True)
                        except Exception:
                            pass
                        return
                    # perform the extra-attack via engine helper
                    try:
                        killed = view.game.busker_perform_extra_attack(view.player_id, tgt)
                        # inform the busker of result
                        if killed:
                            try:
                                names = ', '.join([view.game.players[k].name for k in killed if k in view.game.players])
                            except Exception:
                                names = str(killed)
                            try:
                                await interaction.followup.send(msg('busker_revive_confirm', target=names), ephemeral=True)
                            except Exception:
                                pass
                        else:
                            try:
                                await interaction.followup.send(msg('busker_revive_no_target'), ephemeral=True)
                            except Exception:
                                pass
                    except Exception:
                        try:
                            view.game.log(f"Busker extra-attack failed for {view.player_id}")
                        except Exception:
                            pass
                except Exception:
                    pass
                finally:
                    try:
                        view.stop()
                    except Exception:
                        pass

        class CancelButton(ui.Button):
            def __init__(self, row: int = 1):
                super().__init__(label=msg('guess_command_dm_cancelled'), style=discord.ButtonStyle.secondary, row=row)

            async def callback(self, interaction: discord.Interaction):
                view: WerewolfCog.BuskerReviveView = self.view  # type: ignore
                try:
                    await interaction.response.defer(ephemeral=True)
                except Exception:
                    pass
                try:
                    try:
                        await interaction.followup.send(msg('action_cancelled'), ephemeral=True)
                    except Exception:
                        try:
                            await interaction.response.send_message(msg('action_cancelled'), ephemeral=True)
                        except Exception:
                            pass
                except Exception:
                    pass
                finally:
                    try:
                        view.stop()
                    except Exception:
                        pass

    def _sanitize_logs(self, lines: List[str]) -> List[str]:
        """Filter out sensitive log lines that may reveal roles or private info.

        This is a conservative filter: any line containing role keywords or 'Assigned' is removed.
        """
        sensitive_keywords = ['werewolf', '人狼', 'seer', '占い', 'checked', 'was killed by wolves', 'kill', 'lynched', '狂人', 'madman', 'voted', 'vote']
        out: List[str] = []
        for l in lines:
            try:
                low = l.lower()
            except Exception:
                # if line is not a string for some reason, skip it
                continue
            # drop any private markers or assigned lines
            if l.startswith('[PRIVATE]') or 'assigned' in low:
                continue
            if any(k.lower() in low for k in sensitive_keywords):
                continue
            out.append(l)
        return out

    async def _unmute_all_participants(self, g: Game, channel: Optional[discord.TextChannel], only_alive: bool = False):
        """Attempt to unmute participants who are currently in voice channels.

        Parameters:
        - g: Game
        - channel: TextChannel used to resolve guild
        - only_alive: if True, only unmute players who are alive; if False, attempt to unmute all participants.

        This is permission-guarded: only proceeds if the bot member has mute_members permission.
        """
        if channel is None:
            # try to obtain channel from game id if possible
            try:
                channel = self.bot.get_channel(int(g.game_id))
            except Exception:
                channel = None
        try:
            if not channel:
                return
            guild = getattr(channel, 'guild', None)
            if not guild:
                return
            try:
                bot_member = guild.get_member(self.bot.user.id) or await guild.fetch_member(self.bot.user.id)
            except Exception:
                bot_member = None
            if not bot_member or not bot_member.guild_permissions.mute_members:
                return
            designated = getattr(g, '_designated_vc_id', None)
            for pid, p in g.players.items():
                try:
                    # attempt to unmute according to only_alive flag
                    if only_alive and not getattr(p, 'alive', False):
                        # skip dead players when only_alive requested
                        continue
                    try:
                        member = guild.get_member(int(pid)) or await guild.fetch_member(int(pid))
                    except Exception:
                        member = None
                    if not member:
                        continue
                    if getattr(member, 'voice', None) and getattr(member.voice, 'channel', None):
                        if designated and member.voice.channel.id != designated:
                            continue
                        try:
                            await member.edit(mute=False)
                        except Exception as e:
                            try:
                                g.log(f"Failed to unmute {member.display_name}: {e}")
                            except Exception:
                                pass
                except Exception:
                    continue
        except Exception:
            try:
                g.log("Exception during unmute all participants")
            except Exception:
                pass

    @app_commands.command(name='ww_unmute_all', description='全員のミュートを解除します（オーナーまたはミュート権限を持つメンバーのみ）。')
    async def ww_unmute_all(self, interaction: discord.Interaction):
        """Attempt to unmute all participants currently in the designated voice channel for the game.

        Permission: command caller must be the game owner or have the guild permission to mute members.
        """
        channel = interaction.channel
        if not channel or not isinstance(channel, discord.TextChannel):
            try:
                await safe_interaction_send(interaction, content='このコマンドはサーバー内のチャンネルで実行してください。', ephemeral=True)
            except Exception:
                pass
            return

        # Defer before unmute processing
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            pass

        # Try to load game if present; but allow command even if no game exists (emergency unmute)
        g = self.storage.load_game(str(channel.id))

        try:
            await interaction.followup.send(content='ミュート解除を開始します...', ephemeral=True)
        except Exception:
            try:
                await safe_interaction_send(interaction, content='ミュート解除を開始します...', ephemeral=True)
            except Exception:
                pass

        # If a game exists, prefer unmuting via game helper which respects designated VC
        if g:
            try:
                await self._unmute_all_participants(g, channel, only_alive=False)
            except Exception as e:
                try:
                    await interaction.followup.send(content=msg('internal_error_short', error='unmute_all'), ephemeral=True)
                except Exception:
                    try:
                        await safe_interaction_send(interaction, content=msg('internal_error_short', error='unmute_all'), ephemeral=True)
                    except Exception:
                        pass
                try:
                    g.log(f"ww_unmute_all failed: {e}")
                except Exception:
                    pass
                return

            try:
                await interaction.followup.send(content='全員のミュート解除を実行しました。', ephemeral=True)
            except Exception:
                try:
                    await safe_interaction_send(interaction, content='全員のミュート解除を実行しました。', ephemeral=True)
                except Exception:
                    pass
            return

        # No game found: perform a best-effort unmute of all voice-channel members in the guild
        try:
            guild = channel.guild
            if not guild:
                try:
                    await interaction.followup.send(content='ギルド情報が取得できなかったため実行できません。', ephemeral=True)
                except Exception:
                    try:
                        await safe_interaction_send(interaction, content='ギルド情報が取得できなかったため実行できません。', ephemeral=True)
                    except Exception:
                        pass
                return

            # Attempt to get bot member to check permissions
            try:
                bot_member = guild.get_member(self.bot.user.id) or await guild.fetch_member(self.bot.user.id)
            except Exception:
                bot_member = None

            # Iterate all guild members and unmute those in voice channels
            failures = []
            for member in list(guild.members):
                try:
                    if getattr(member, 'voice', None) and getattr(member.voice, 'channel', None):
                        try:
                            await member.edit(mute=False)
                        except Exception as e:
                            failures.append(str(member.id))
                except Exception:
                    continue

            try:
                if failures:
                    await safe_interaction_send(interaction, content=f"アンミュートを実行しましたが、失敗したユーザーがいます（数={len(failures)}）。", ephemeral=True)
                else:
                    await safe_interaction_send(interaction, content='全員のミュート解除を実行しました。', ephemeral=True)
            except Exception:
                pass
            return
        except Exception as e:
            try:
                await safe_interaction_send(interaction, content=msg('internal_error_short', error='unmute_all'), ephemeral=True)
            except Exception:
                pass
            try:
                if g:
                    g.log(f"ww_unmute_all unexpected error: {e}")
            except Exception:
                pass
            return

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Relay DM messages from wolves to their wolf-group peers during night when _wolf_group_members is set
        try:
            # ignore bot messages
            if message.author.bot:
                return
            # Only consider direct messages to the bot (private channel)
            if not isinstance(message.channel, discord.DMChannel):
                return
            author_id = str(message.author.id)
            # search games to find any game where this author is in _wolf_group_members
            try:
                games = []
                if hasattr(self.storage, '_games'):
                    games = list(getattr(self.storage, '_games').values())
            except Exception:
                games = []

            for g in games:
                try:
                    members = getattr(g, '_wolf_group_members', []) or []
                    # Only relay during NIGHT phase
                    try:
                        if getattr(g, 'phase', None) != Phase.NIGHT:
                            continue
                    except Exception:
                        pass
                    # Compute current active wolf recipients: alive players with role 'werewolf'
                    try:
                        active_members = []
                        for wid in members:
                            try:
                                p = g.players.get(wid)
                                if p and p.alive and p.role_id and getattr(g.roles.get(p.role_id), 'faction', None) == 'werewolf':
                                    active_members.append(wid)
                            except Exception:
                                continue
                    except Exception:
                        active_members = members

                    if author_id in active_members:
                        # Safe sender display name fallback (User may not have display_name)
                        try:
                            sender_name = getattr(message.author, 'display_name', None) or getattr(message.author, 'name', str(message.author))
                        except Exception:
                            sender_name = str(message.author)

                        # Relay this message to all other active members (exclude sender)
                        for wid in active_members:
                            if wid == author_id:
                                continue
                            try:
                                try:
                                    user = None
                                    get_user_fn = getattr(self.bot, 'get_user', None)
                                    if callable(get_user_fn):
                                        try:
                                            user = get_user_fn(int(wid))
                                        except Exception:
                                            user = None
                                    if not user:
                                        user = await self.bot.fetch_user(int(wid))
                                except Exception:
                                    user = None
                                if not user:
                                    g.log(f"Failed to fetch wolf recipient user {wid} for relay")
                                    try:
                                        fails = getattr(g, '_wolf_dm_failures', []) or []
                                        if wid not in fails:
                                            fails.append(wid)
                                        g._wolf_dm_failures = fails
                                    except Exception:
                                        pass
                                    continue
                                try:
                                    # Try localized format first, then fallback to simple prefix
                                    sent = False
                                    last_exc = None
                                    for attempt, payload in enumerate((msg('wolf_chat_relay', name=sender_name, content=message.content), f"[人狼チャット] {sender_name}: {message.content}")):
                                        try:
                                            # If payload is an i18n key call result it will be a str
                                            await user.send(payload)
                                            sent = True
                                            try:
                                                g.log(f"Relayed wolf DM from {author_id} to {wid} (attempt {attempt+1})")
                                            except Exception:
                                                pass
                                            break
                                        except Exception as e:
                                            last_exc = e
                                            # small backoff before retry
                                            try:
                                                await asyncio.sleep(0.4 * (attempt + 1))
                                            except Exception:
                                                pass

                                    if not sent:
                                        # final failure: record and continue
                                        try:
                                            g.log(f"Failed to send wolf relay DM to {wid}: {last_exc}")
                                        except Exception:
                                            pass
                                        try:
                                            fails = getattr(g, '_wolf_dm_failures', []) or []
                                            if wid not in fails:
                                                fails.append(wid)
                                            g._wolf_dm_failures = fails
                                        except Exception:
                                            pass
                                        try:
                                            errs = getattr(g, '_wolf_dm_errors', []) or []
                                            errs.append({'wid': wid, 'error': str(last_exc)})
                                            g._wolf_dm_errors = errs
                                        except Exception:
                                            pass
                                        continue
                                except Exception:
                                    # individual send failed unexpectedly; log and continue
                                    try:
                                        g.log(f"Failed to relay wolf DM from {author_id} to {wid}")
                                    except Exception:
                                        pass
                                    continue
                            except Exception:
                                # failed to fetch user; continue
                                    try:
                                        g.log(f"Failed to fetch wolf user {wid} during relay")
                                    except Exception:
                                        pass
                                    continue
                        return
                    else:
                        try:
                            g.log(f"Received wolf DM from {author_id} but no active_members matched; members={members}, active_members={active_members}, phase={getattr(g, 'phase', None)}")
                        except Exception:
                            pass
                except Exception:
                    continue
        except Exception:
            # never raise from on_message
            return

    def _format_vote_tally_lines_with_abstain(self, tally_lines: List[str]) -> List[str]:
        """Format tally lines, skipping zero votes for all entries including abstain."""
        formatted_lines = []
        for line in tally_lines:
            label, count_str = line.split(": ")
            count = int(count_str)
            
            # Skip all zero vote entries for cleaner display
            if count > 0:
                formatted_lines.append(f"• {line}票")
        
        return formatted_lines
        
        return formatted_lines

    async def _start_day_vote_channel(self, g: Game, channel: discord.TextChannel, vote_duration: int):
        """Start an anonymous channel-based vote. Non-responders are treated as abstain."""
        # CRITICAL: Clear forced end states at the start of each new vote to prevent carryover issues
        try:
            g._forced_end_vote = False
            g._emergency_vote_reset = False
            g._vote_invalidated_by_guess = False
            g._vote_finalized = False
            g.log("VOTE START: Cleared all forced end states for fresh voting session")
        except Exception:
            pass
        
        # Prepare voting storage - always start fresh for new voting session
        # Build choices from alive players plus an abstain option
        alive = [p for p in g.players.values() if p.alive]
        
        # Initialize fresh pending votes for all alive players.
        # Use 'invalid' to indicate the player has not yet made an explicit choice.
        # Explicit abstain ('__abstain__') is set only when the player actively selects it.
        pending = {}
        try:
            for p in alive:
                try:
                    pid = str(p.id)
                    pending[pid] = 'invalid'
                except Exception:
                    pass
            g.log(f"VOTE INIT: Initialized fresh pending votes for {len(pending)} players")
        except Exception:
            pass
        
        try:
            # Set the new pending votes
            g._pending_votes = pending
            
            # Set vote start time for countdown
            import time
            g._day_vote_started_at = time.time()
            g.log("VOTE INIT: Set vote start time for countdown")
            
            # Debug logging for vote initialization
            try:
                session_id = getattr(g, '_current_vote_session_id', 'unknown')
                g.log(f"DEBUG: Initialized voting session {session_id} with pending votes: {pending}")
            except Exception:
                pass
        except Exception:
            # best-effort: if setting fails, fall back to empty mapping
            try:
                g._pending_votes = {}
            except Exception:
                pass
        options = []
        for p in alive:
            options.append(discord.SelectOption(label=p.name, value=str(p.id)))
        # add abstain option only when enabled at runtime
        try:
            allow_abstain = getattr(g, '_runtime_allow_abstain', True)
        except Exception:
            allow_abstain = True
        if allow_abstain:
            try:
                abstain_label = msg('vote_abstain_label')
            except Exception:
                abstain_label = '棄権'
            options.append(discord.SelectOption(label=abstain_label, value='__abstain__'))

        # Build view with possibly multiple selects if options exceed Discord's 25-option limit
        def chunk_options(opts: List[discord.SelectOption], size: int = 25):
            for i in range(0, len(opts), size):
                yield opts[i:i+size]

        selects = list(chunk_options(options, 25))

        # Discord allows up to 5 components (selects) per message; batch selects into groups of up to 5
        batches: List[List[List[discord.SelectOption]]] = []
        for i in range(0, len(selects), 5):
            batches.append(selects[i:i+5])

        views: List[ui.View] = []
        # expose active vote views on the game so operator commands can stop them
        try:
            g._active_vote_views = views
        except Exception:
            pass
        # Only start/send voting UI if the engine is in DAY or already in VOTE.
        # Do NOT send vote UI while engine is still NIGHT; adapter must respect engine phase.
        try:
            if g.phase == Phase.DAY:
                try:
                    g.start_day_vote()
                except Exception as e:
                    try:
                        g.log(f"start_day_vote failed: {e}")
                    except Exception:
                        pass
                    # abort sending UI if we cannot start vote
                    try:
                        self.storage.save_game(g)
                    except Exception:
                        pass
                    return
                # record vote start timestamp for remaining-time checks (used by /ww_guess)
                try:
                    import time
                    g._day_vote_started_at = time.time()
                except Exception:
                    pass
                # clear per-vote flags when a new vote starts
                try:
                    g._vote_finalized = False
                except Exception:
                    pass
                try:
                    g._revote_in_progress = False
                except Exception:
                    pass
            elif g.phase == Phase.VOTE:
                # already in VOTE phase, continue
                pass
            else:
                try:
                    g.log(f"Refusing to send day vote UI: game phase is {g.phase}")
                except Exception:
                    pass
                return
        except Exception:
            # defensive: do not proceed if we cannot determine phase
            try:
                g.log("Could not determine game phase before starting vote UI; aborting vote UI send")
            except Exception:
                pass
            try:
                self.storage.save_game(g)
            except Exception:
                pass
            return

        # Announce who died last night BEFORE sending the voting UI so deaths are revealed first
        try:
            # Before announcing publicly, deliver DM notifications to those killed last night
            last_night_dead_ids = getattr(g, '_last_night_dead_ids', []) or []
            failed_dead_dms = await self._send_death_notifications(g, last_night_dead_ids)

            # notify channel of any DM failures to dead players
            try:
                if failed_dead_dms:
                    failures = []
                    for pid in failed_dead_dms:
                        p = g.players.get(pid)
                        failures.append(p.name if p else pid)
                    try:
                        await self._send_to_game_thread(g, content="Could not deliver death DM to: " + ", ".join(failures) + ".")
                    except Exception:
                        pass
            except Exception:
                pass

            # Announce who died last night (public, sanitized via i18n)
            # Skip announcement only if we're in an active re-vote during the same day (not next day)
            try:
                in_re_vote_after_guess = getattr(g, '_in_re_vote_after_guess', False)
                
                # CRITICAL: If this is a new day/turn, clear the re-vote flag to show death announcements
                if in_re_vote_after_guess:
                    # Check if this is actually a new turn by checking if we have last night deaths
                    # If we have last_night_dead_ids, this is a new day and we should announce deaths
                    last_dead_ids = getattr(g, '_last_night_dead_ids', None) or []
                    if last_dead_ids:
                        g._in_re_vote_after_guess = False
                        in_re_vote_after_guess = False
                        g.log("DEATH ANNOUNCEMENT: Cleared re-vote flag for new day - will show death announcement")
                
                if not in_re_vote_after_guess:
                    # Prefer the engine-provided list of last-night deaths if available
                    last_dead_ids = getattr(g, '_last_night_dead_ids', None) or []
                    dead_now = []
                    for did in last_dead_ids:
                        p = g.players.get(did)
                        if p:
                            dead_now.append(p)
                        else:
                            # If player object missing, append the raw id string for best-effort display
                            class _Anon:
                                def __init__(self, name):
                                    self.name = name
                            dead_now.append(_Anon(did))

                    if dead_now:
                        try:
                            await self._send_to_game_thread(g, content=msg('dead_players_public', names=", ".join(p.name for p in dead_now)))
                        except Exception:
                            pass
                    else:
                        try:
                            await self._send_to_game_thread(g, content=msg('dead_players_public_none'))
                        except Exception:
                            pass

                # Notify bakery roles publicly in the thread if any alive bakery exists
                try:
                    any_bakery_alive = any(p.alive and p.role_id == 'bakery' for p in g.players.values())
                    if any_bakery_alive:
                        try:
                            await self._send_to_game_thread(g, content=msg('bakery_bread_ready'))
                        except Exception:
                            # ignore send failures
                            pass
                except Exception:
                    pass
            except Exception:
                pass
        except Exception:
            pass

        # Announce phase change to thread and use enhanced voting UI
        thread_voting_success = False
        try:
            await self._announce_phase_change(g, Phase.VOTE)
            await self._start_enhanced_voting_in_thread(g, vote_duration)
            thread_voting_success = True
            logging.getLogger(__name__).info("Thread voting UI started successfully")
        except Exception as e:
            logging.getLogger(__name__).error(f"Failed to start enhanced voting in thread: {e}")
            logging.getLogger(__name__).error(f"Thread voting error traceback: {traceback.format_exc()}")
            # Fallback to original voting UI in main channel will be used below

        # Only send to main channel if thread voting failed or thread doesn't exist
        if not thread_voting_success or not hasattr(g, '_game_thread_id'):
            logging.getLogger(__name__).info(f"Using main channel fallback: thread_success={thread_voting_success}, has_thread={hasattr(g, '_game_thread_id')}")
            # send each batch as a separate message with its own view (fallback if thread fails)
            for batch_idx, batch in enumerate(batches):
                try:
                    # CRITICAL: Use fixed timeout if vote_duration is None or problematic
                    if vote_duration is None or vote_duration <= 0:
                        vote_duration = getattr(g, '_fixed_vote_timeout', 180)
                        g.log(f"VOTE SETUP: Using fixed timeout due to invalid duration: {vote_duration}s")
                    
                    # Ensure timeout value is reasonable - if None, use a very high value instead of discord.py's default
                    timeout_value = vote_duration if vote_duration and vote_duration > 0 else 3600  # 1 hour default
                    v = self.VotingView(timeout=timeout_value, game=g, channel=channel, options=batch)
                    
                    # Log timeout configuration
                    try:
                        g.log(f"VOTING SETUP: Created VotingView with timeout={timeout_value}s (vote_duration={vote_duration})")
                    except Exception:
                        pass
                    
                    # Track active voting views for forced termination
                    if not hasattr(g, '_active_voting_views'):
                        g._active_voting_views = []
                    g._active_voting_views.append(v)
                    g.log(f"VOTING SETUP: Added voting view to active list (total: {len(g._active_voting_views)})")
                    
                    # try sending the voting view with retries; if it fails, fallback to plain messages
                    sent = False
                    for attempt in range(3):
                        try:
                            # CRITICAL: Always show vote duration correctly to users
                            actual_duration = vote_duration if vote_duration and vote_duration > 0 else None
                            if actual_duration is None:
                                content_msg = msg('day_vote_started_no_seconds', page=batch_idx+1, pages=len(batches))
                            else:
                                content_msg = msg('day_vote_started', page=batch_idx+1, pages=len(batches), seconds=actual_duration)
                            m = await channel.send(content=content_msg, view=v)
                            # Store message ID for potential UI updates
                            v.message_id = m.id
                            g._voting_message_id = m.id
                            g.log(f"VOTING SETUP: Saved voting message ID: {m.id}")
                            sent = True
                            break
                        except Exception as e:
                            try:
                                g.log(f"Failed to send voting UI batch {batch_idx+1} attempt {attempt+1}: {e}")
                            except Exception:
                                pass
                            print(f"Failed to send voting UI batch {batch_idx+1} attempt {attempt+1}")
                            traceback.print_exc()
                            try:
                                await asyncio.sleep(1)
                            except Exception:
                                pass

                    if not sent:
                        try:
                            g.log(f"Voting UI batch {batch_idx+1} failed after retries; aborting vote UI send")
                        except Exception:
                            pass
                        try:
                            await channel.send("Error: failed to post voting UI after several attempts. The game will persist; owner may need to /ww_close and recreate the lobby.")
                        except Exception:
                            pass
                        try:
                            self.storage.save_game(g)
                        except Exception:
                            pass
                        return

                    # remember the sent voting message so we can clear its components/messages on full stop
                    try:
                        msgs = getattr(g, '_day_vote_messages', None)
                        if msgs is None:
                            try:
                                g._day_vote_messages = []
                                msgs = g._day_vote_messages
                            except Exception:
                                msgs = None
                        if msgs is not None:
                            try:
                                # store as dict with channel and message ids to allow async cleanup
                                rec = {'channel_id': getattr(channel, 'id', None), 'message_id': getattr(m, 'id', None)}
                                msgs.append(rec)
                                try:
                                    logging.getLogger(__name__).info(f"Recorded day vote message for cleanup: {rec}")
                                except Exception:
                                    pass
                            except Exception:
                                try:
                                    tup = (getattr(channel, 'id', None), getattr(m, 'id', None))
                                    msgs.append(tup)
                                    try:
                                        logging.getLogger(__name__).info(f"Recorded day vote message (tuple) for cleanup: {tup}")
                                    except Exception:
                                        pass
                                except Exception:
                                    pass
                    except Exception:
                        pass

                    views.append(v)
                except Exception as e:
                    try:
                        g.log(f"Failed to construct voting view for batch {batch_idx+1}: {e}")
                    except Exception:
                        pass
                    import traceback
                    traceback.print_exc()
                    try:
                        await channel.send("Error constructing voting UI; owner may need to /ww_close and recreate the lobby.")
                    except Exception:
                        pass
                    try:
                        self.storage.save_game(g)
                    except Exception:
                        pass
                    return
        else:
            # Thread voting succeeded, but we still need to wait for the timeout
            logging.getLogger(__name__).info("Thread voting succeeded, creating dummy view for timeout handling")
            # Create a dummy view that doesn't send any messages but handles timeout properly
            dummy_view = ui.View(timeout=vote_duration or 300)
            views.append(dummy_view)
        

        # Wait for all views to timeout (pause-aware).
        # Start a background reminder task that will send a 30-second remaining reminder.
        reminder_task = None
        # store on game so other commands (eg. /ww_close) can cancel it
        try:
            # Cancel existing reminder task if it exists
            existing_reminder_task = getattr(g, '_day_vote_reminder_task', None)
            if existing_reminder_task and not existing_reminder_task.done():
                existing_reminder_task.cancel()
            g._day_vote_reminder_task = None
        except Exception:
            pass
        try:
            async def _reminder_when_30s_left():
                try:
                    # Check if game has ended before proceeding
                    if g.phase in (Phase.END, Phase.CLOSED):
                        g.log("Skipping 30s reminder - game has ended")
                        return
                        
                    # if no timeout specified or <=30, send immediately at (if <=30, no prior delay)
                    if vote_duration is None:
                        return
                    total = int(vote_duration)
                    if total <= 30:
                        # Check again before sending
                        if g.phase in (Phase.END, Phase.CLOSED):
                            g.log("Skipping immediate 30s reminder - game has ended")
                            return
                        # send immediately
                        try:
                            await self._send_to_game_thread(g, content=msg('day_vote_30s_public'))
                        except Exception:
                            pass
                        # DM reminder: only send to alive players who have not yet voted
                        try:
                            pending = getattr(g, '_pending_votes', {}) or {}
                            # Consider '__invalid__' as not-yet-voted so we DM-remind such players
                            not_voted = [p for p in g.players.values() if p.alive and (p.id not in pending or pending.get(p.id) is None or pending.get(p.id) == '__invalid__')]
                            for p in not_voted:
                                try:
                                    user = await self.bot.fetch_user(int(p.id))
                                    try:
                                        await user.send(msg('day_vote_30s_dm'))
                                    except Exception:
                                        pass
                                except Exception:
                                    pass
                        except Exception:
                            pass
                        return

                    # wait until total - 30 seconds remaining, honoring pause via _sleep_while_not_paused
                    try:
                        await self._sleep_while_not_paused(total - 30, g)
                    except Exception:
                        return

                    # Check if game has ended during wait
                    if g.phase in (Phase.END, Phase.CLOSED):
                        g.log("Skipping 30s reminder after wait - game has ended")
                        return

                    # if phase changed or views finished, skip
                    try:
                        if getattr(g, '_active_vote_views', None) is None:
                            pass
                    except Exception:
                        pass

                    # send public reminder only if game is still active
                    try:
                        if g.phase not in (Phase.END, Phase.CLOSED):
                            await self._send_to_game_thread(g, content=msg('day_vote_30s_public'))
                    except Exception:
                        pass

                    # DM reminder: only send to alive players who have not yet voted, and only if game is active
                    try:
                        if g.phase not in (Phase.END, Phase.CLOSED):
                            pending = getattr(g, '_pending_votes', {}) or {}
                            # Consider '__invalid__' as not-yet-voted so we DM-remind such players
                            not_voted = [p for p in g.players.values() if p.alive and (p.id not in pending or pending.get(p.id) is None or pending.get(p.id) == '__invalid__')]
                            for p in not_voted:
                                try:
                                    user = await self.bot.fetch_user(int(p.id))
                                    try:
                                        await user.send(msg('day_vote_30s_dm'))
                                    except Exception:
                                        pass
                                except Exception:
                                    pass
                    except Exception:
                        pass
                    except Exception:
                        pass
                except Exception:
                    pass

            reminder_task = asyncio.create_task(_reminder_when_30s_left())
            try:
                g._day_vote_reminder_task = reminder_task
            except Exception:
                pass

            for v in views:
                try:
                    await self._wait_view_with_pause(v, vote_duration, g)
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            # cancel reminder if still pending
            try:
                if reminder_task and not reminder_task.done():
                    reminder_task.cancel()
                try:
                    # ensure stored reference cleared
                    if getattr(g, '_day_vote_reminder_task', None):
                        g._day_vote_reminder_task = None
                except Exception:
                    pass
            except Exception:
                pass

        # clear active vote views reference so other commands know there are no active vote UIs
        try:
            g._active_vote_views = []
        except Exception:
            pass

        # After timeout, ensure that all non-responders are treated as abstain
        lock = None
        acquired_lock = False
        try:
            # Prevent concurrent resolution runs (race between timeout path and forced-end)
            # Use a per-game asyncio.Lock to avoid stale-flag races.
            try:
                lock = getattr(g, '_vote_resolution_lock', None)
            except Exception:
                lock = None
            if lock is None:
                try:
                    import asyncio as _asyncio
                    lock = _asyncio.Lock()
                    try:
                        g._vote_resolution_lock = lock
                    except Exception:
                        pass
                except Exception:
                    lock = None
            try:
                # Check if this is a forced end vote (should skip lock waiting)
                forced_end = getattr(g, '_forced_end_vote', False)
                
                if forced_end:
                    try:
                        g.log('FORCED END VOTE: Skipping lock wait for admin-forced resolution')
                    except Exception:
                        pass
                elif lock and lock.locked():
                    try:
                        g.log('Another resolution is in progress (lock); waiting for it to complete')
                    except Exception:
                        pass
                    # Wait for the lock to be released (with timeout)
                    try:
                        await asyncio.wait_for(lock.acquire(), timeout=5.0)
                        acquired_lock = True
                        try:
                            g.log('Lock acquired after waiting; checking if post-resolution work is needed')
                        except Exception:
                            pass
                        # Check if the other resolution already completed everything
                        # If phase is NIGHT, we may need to start the night sequence
                        if g.phase == Phase.NIGHT:
                            try:
                                # Check if night sequence was already started
                                if not getattr(g, '_night_sequence_started', False):
                                    try:
                                        g.log('Starting night sequence after resolution lock wait')
                                    except Exception:
                                        pass
                                    # Release lock before starting night sequence (it's a long-running task)
                                    try:
                                        if acquired_lock and lock:
                                            lock.release()
                                            acquired_lock = False
                                    except Exception:
                                        pass
                                    try:
                                        self.bot.loop.create_task(self._run_night_sequence(g, int(channel.id)))
                                    except Exception:
                                        asyncio.create_task(self._run_night_sequence(g, int(channel.id)))
                                    return
                                else:
                                    try:
                                        g.log('Night sequence already started; nothing to do')
                                    except Exception:
                                        pass
                                    # Release lock and return
                                    try:
                                        if acquired_lock and lock:
                                            lock.release()
                                            acquired_lock = False
                                    except Exception:
                                        pass
                                    return
                            except Exception:
                                pass
                        # If game ended or other phase, just release lock and return
                        try:
                            if acquired_lock and lock:
                                lock.release()
                                acquired_lock = False
                        except Exception:
                            pass
                        return
                    except asyncio.TimeoutError:
                        try:
                            g.log('Timed out waiting for resolution lock; skipping vote timeout handler')
                        except Exception:
                            pass
                        return
                    except Exception:
                        try:
                            g.log('Error waiting for resolution lock; skipping vote timeout handler')
                        except Exception:
                            pass
                        return
            except Exception:
                pass
            try:
                if lock:
                    await lock.acquire()
                    acquired_lock = True
            except Exception:
                acquired_lock = False
            # Main vote resolution - ensure lock is released on any exit below
            alive_ids = [p.id for p in alive]
            for pid in alive_ids:
                if pid not in g._pending_votes:
                    # mark as invalid (timeout non-response -> do not count)
                    g._pending_votes[pid] = '__invalid__'
                    # notify the user via DM that their vote was invalidated due to timeout
                    # Skip DM notification if we're in a re-vote after guess (will restart voting)
                    in_re_vote_after_guess = getattr(g, '_in_re_vote_after_guess', False)
                    if not in_re_vote_after_guess:
                        try:
                            user = await self.bot.fetch_user(int(pid))
                            try:
                                await user.send(msg('vote_invalid_dm'))
                            except Exception:
                                pass
                        except Exception:
                            pass

            # map votes into engine.cast_vote format
            # defensive: ensure engine is in VOTE phase so cast_vote succeeds
            try:
                if g.phase != Phase.VOTE:
                    # Only attempt to start vote if engine is in DAY; do NOT force phase changes.
                    try:
                        if g.phase == Phase.DAY:
                            try:
                                g.start_day_vote()
                            except Exception as e:
                                try:
                                    g.log(f"start_day_vote failed at vote resolution: {e}")
                                except Exception:
                                    pass
                                # cannot proceed applying votes if we can't enter VOTE
                                try:
                                    self.storage.save_game(g)
                                except Exception:
                                    pass
                                return
                        else:
                            try:
                                g.log(f"Not applying pending votes because game phase is {g.phase}")
                            except Exception:
                                pass
                            try:
                                self.storage.save_game(g)
                            except Exception:
                                pass
                            return
                    except Exception:
                        pass
            except Exception:
                pass

            # first clear engine.votes and cast according to pending_votes
            # At timeout, ensure any non-responders are recorded as abstain into the engine (others have already cast via the callback)
            for uid, choice in g._pending_votes.items():
                # skip invalid timeouts: do not count in any tally
                if choice in ['__invalid__', 'invalid', '']:
                    try:
                        g.log(f"VOTE RESOLUTION: Skipping invalid vote for player {uid}: '{choice}'")
                        # leave no vote recorded for this uid
                        continue
                    except Exception:
                        continue
                # translate abstain marker
                if choice == '__abstain__' or choice is None:
                    # cast final abstain if the user didn't explicitly cast
                    try:
                        g.cast_vote(uid, None)
                    except Exception as e:
                        try:
                            g.log(f"Failed to cast abstain for {uid}: {e}")
                        except Exception:
                            pass
                else:
                    try:
                        ok = g.cast_vote(uid, choice)
                        if not ok:
                            try:
                                g.log(f"cast_vote returned False for {uid} -> {choice}")
                            except Exception:
                                pass
                    except Exception as e:
                        try:
                            g.log(f"Failed to cast vote for {uid}: {e}")
                        except Exception:
                            pass

            # As a final defensive check, if g.votes is empty but we had _pending_votes, attempt to translate pending votes to g.votes directly
            # CRITICAL: Only do this if vote is being force-ended to prevent unauthorized vote generation
            try:
                forced_end = getattr(g, '_forced_end_vote', False)
                if not g.votes and any(v is not None for v in g._pending_votes.values()) and forced_end:
                    g.log("CRITICAL: Auto-generating votes from pending votes due to forced end")
                    for uid, choice in g._pending_votes.items():
                        # skip explicit abstains and invalid timeouts already handled
                        if choice == '__abstain__' or choice is None:
                            g.votes.append(Vote(from_id=uid, target_id=None))
                        elif choice in ['__invalid__', 'invalid', '']:
                            # skip invalid/timeout votes
                            try:
                                g.log(f"VOTE RESOLUTION FALLBACK: Skipping invalid vote for player {uid}: '{choice}'")
                            except Exception:
                                pass
                            continue
                        else:
                            g.votes.append(Vote(from_id=uid, target_id=choice))
                    g.log("Translated pending votes directly into g.votes as fallback (forced end only)")
                elif not g.votes and any(v is not None for v in g._pending_votes.values()):
                    g.log("CRITICAL: Vote auto-generation blocked - no forced end flag")
            except Exception:
                pass

            # resolve votes (with additional safety checks)
            try:
                # Check if we are creating new voting UI (should skip all vote resolution)
                creating_ui = getattr(g, '_creating_vote_ui', False)
                emergency_reset = getattr(g, '_emergency_vote_reset', False)
                vote_invalidated = getattr(g, '_vote_invalidated_by_guess', False)
                in_re_vote = getattr(g, '_in_re_vote_after_guess', False)
                current_session = getattr(g, '_current_vote_session_id', '')
                is_guesser_session = 'post_guess_vote' in current_session or 'emergency_vote' in current_session
                
                g.log(f'VOTE RESOLUTION CHECK: creating_ui={creating_ui}, emergency_reset={emergency_reset}, vote_invalidated={vote_invalidated}, in_re_vote={in_re_vote}, session={current_session}, is_guesser_session={is_guesser_session}')
                
                if creating_ui:
                    g.log('Skipping g.resolve_votes() - currently creating new voting UI')
                    return
                
                if emergency_reset:
                    g.log('EMERGENCY: Blocking g.resolve_votes() - emergency reset active')
                    return
                if vote_invalidated:
                    g.log('Blocking g.resolve_votes() - votes invalidated by guess')
                    return
                if is_guesser_session and not in_re_vote:
                    g.log(f'Blocking g.resolve_votes() - guesser session detected (not in re-vote): {current_session}')
                    return
                elif in_re_vote:
                    g.log(f'Allowing g.resolve_votes() - in re-vote phase after guess: {current_session}')
                    
                g.resolve_votes()
            except Exception as e:
                try:
                    g.log(f"resolve_votes failed: {e}")
                except Exception:
                    pass

            # If engine indicated a day-tie, run a revote among tied candidates.
            try:
                day_tie = getattr(g, '_day_tie', None)
            except Exception:
                day_tie = None

            if day_tie:
                try:
                    # Before starting the revote, publicly disclose the initial vote results
                    try:
                        # build anonymous tally from g.votes
                        counts_tmp: Dict[Optional[str], int] = {}
                        for v in g.votes:
                            counts_tmp[v.target_id] = counts_tmp.get(v.target_id, 0) + 1
                        alive_tmp = [p for p in g.players.values() if p.alive]
                        rows_tmp: List[tuple[str, int]] = []
                        try:
                            vote_targets_tmp = [p for p in alive_tmp]
                            for p in vote_targets_tmp:
                                c = counts_tmp.get(p.id, 0)
                                rows_tmp.append((p.name, c))
                        except Exception:
                            for pid, p in g.players.items():
                                c = counts_tmp.get(pid, 0)
                                rows_tmp.append((p.name, c))
                        # abstain
                        abstain_tmp = counts_tmp.get(None, 0)
                        try:
                            abstain_label_tmp = msg('vote_abstain_label')
                        except Exception:
                            abstain_label_tmp = '棄権'
                        rows_tmp.append((abstain_label_tmp, abstain_tmp))
                        try:
                            rows_sorted_tmp = sorted(rows_tmp, key=lambda x: (-x[1], x[0].lower()))
                        except Exception:
                            rows_sorted_tmp = rows_tmp
                        # compose embed and send
                        try:
                            try:
                                title_tmp = msg('tally_embed')[0] + '（最初の投票）'
                            except Exception:
                                title_tmp = '投票結果（最初の投票）'
                            try:
                                valid_label_tmp = msg('tally_embed')[1]
                            except Exception:
                                valid_label_tmp = '有効投票数'
                            total_valid_tmp = sum(1 for v in g.votes if getattr(v, 'target_id', None) != '__invalid__')
                            embed_tmp = discord.Embed(title=title_tmp, colour=0x3498db)
                            try:
                                embed_tmp.add_field(name=valid_label_tmp, value=str(total_valid_tmp), inline=False)
                            except Exception:
                                embed_tmp.description = f"{valid_label_tmp}: {total_valid_tmp}\n"
                            desc_tmp = "\n".join([f"{label}: {count}" for label, count in rows_sorted_tmp])
                            if getattr(embed_tmp, 'description', None):
                                embed_tmp.description = (embed_tmp.description or '') + "\n" + desc_tmp
                            else:
                                embed_tmp.description = desc_tmp
                            try:
                                await self._send_to_game_thread(g, embed=embed_tmp)
                            except Exception:
                                # ignore send failures and continue to revote UI
                                pass
                        except Exception:
                            pass
                    except Exception:
                        pass
                    # Build revote options only among tie candidates that are still valid/alive.
                    # Preserve original tie ordering from engine where possible.
                    valid_tie_list: List[Optional[str]] = []
                    try:
                        for tid in day_tie:
                            # None represents abstain in engine's tie list
                            if tid is None:
                                valid_tie_list.append(None)
                            else:
                                # Normalize candidate id to string for comparisons with Select values
                                sid = str(tid)
                                # Try to resolve player by string key first, then by int
                                p = g.players.get(sid) or g.players.get(tid)
                                # Only include players who still exist and are alive
                                if p and getattr(p, 'alive', False):
                                    valid_tie_list.append(sid)
                    except Exception:
                        # If anything goes wrong, fall back to original day_tie list (stringify entries)
                        try:
                            valid_tie_list = [None if x is None else str(x) for x in list(day_tie)]
                        except Exception:
                            valid_tie_list = list(day_tie)

                    # Build options including explicit Abstain option if present in the (filtered) tie set
                    options: List[discord.SelectOption] = []
                    for tid in valid_tie_list:
                        if tid is None:
                            try:
                                abstain_label = msg('vote_abstain_label')
                            except Exception:
                                abstain_label = '棄権'
                            options.append(discord.SelectOption(label=abstain_label, value='__abstain__'))
                        else:
                            try:
                                p = g.players.get(tid)
                                if p:
                                    options.append(discord.SelectOption(label=p.name, value=str(tid)))
                            except Exception:
                                # ignore missing players
                                pass

                    # If there is exactly one revote candidate, auto-lynch them immediately
                    try:
                        if len(options) == 1:
                            try:
                                pick = options[0].value
                                victim = g.players.get(pick)
                                if victim:
                                    # use centralized kill to respect lovers pairing
                                    killed = g._kill_player(victim.id, reason='lynch') or []
                                    if killed:
                                        # Send death notification DM to lynched players
                                        try:
                                            await self._send_death_notifications(g, killed)
                                        except Exception:
                                            pass
                                        try:
                                            g.log(f"Day: only one revote candidate; lynched {victim.name}")
                                        except Exception:
                                            pass
                                        try:
                                            g.phase = Phase.CHECK_WIN
                                        except Exception:
                                            pass
                                        try:
                                            g._last_lynched_ids = [victim.id]
                                        except Exception:
                                            pass
                            except Exception:
                                pass
                            # clear tie and skip the normal revote UI
                            try:
                                g._day_tie = None
                            except Exception:
                                pass
                    except Exception:
                        pass

                    # Reset pending votes to collect fresh revote choices
                    g._pending_votes = {}
                    # Clear any previously resolved votes so the revote starts from a clean slate
                    try:
                        g.votes = []
                    except Exception:
                        pass

                    # Initialize pending votes for all alive players to enable voting
                    try:
                        alive_players = [p for p in g.players.values() if p.alive]
                        for player in alive_players:
                            g._pending_votes[str(player.id)] = 'invalid'
                        g.log(f"REVOTE SETUP: Initialized pending votes for {len(alive_players)} players")
                    except Exception:
                        pass

                    # CRITICAL: Clear forced end flags and reset timer for revote
                    try:
                        g._forced_end_vote = False
                        g._emergency_vote_reset = False
                        g._vote_invalidated_by_guess = False
                        g._vote_finalized = False
                        g.log("REVOTE SETUP: Cleared all forced end and blocking flags")
                    except Exception:
                        pass
                    
                    # Cancel any existing countdown tasks before starting new revote
                    try:
                        countdown_task = getattr(g, '_countdown_task', None)
                        revote_countdown_task = getattr(g, '_revote_countdown_task', None)
                        
                        if countdown_task and not countdown_task.done():
                            countdown_task.cancel()
                            g.log("REVOTE SETUP: Cancelled existing main countdown task")
                            
                        if revote_countdown_task and not revote_countdown_task.done():
                            revote_countdown_task.cancel()
                            g.log("REVOTE SETUP: Cancelled existing revote countdown task")
                    except Exception:
                        pass
                    
                    # Reset vote start time for proper countdown in revote
                    try:
                        import time
                        g._day_vote_started_at = time.time()
                        g.log("REVOTE SETUP: Reset vote start time for countdown")
                    except Exception:
                        pass

                    # Post a single revote UI in the public channel so players can re-vote (overwrite their choice)
                    # Guard against duplicate revote UIs being posted from multiple code paths by using
                    # a simple in-progress flag on the game object.
                    revote_views = []
                    try:
                        # If another revote is already in progress, skip creating a new UI
                        if getattr(g, '_revote_in_progress', False):
                            revote_views = []
                        else:
                            try:
                                g._revote_in_progress = True
                            except Exception:
                                pass
                            try:
                                g._active_vote_views = revote_views
                            except Exception:
                                pass
                            try:
                                # Use enhanced voting UI for revote similar to normal voting
                                await self._start_enhanced_revote_in_thread(g, options, vote_duration)
                                revote_views = getattr(g, '_active_vote_views', [])
                            except Exception:
                                # Fallback to old VotingView if enhanced UI fails
                                v = self.VotingView(timeout=vote_duration, game=g, channel=channel, options=[options])
                                try:
                                    if vote_duration is None:
                                        await self._send_to_game_thread(g, content=msg('day_revote_prompt_no_seconds'), view=v)
                                    else:
                                        await self._send_to_game_thread(g, content=msg('day_revote_prompt', seconds=vote_duration), view=v)
                                    revote_views.append(v)
                                except Exception:
                                    # If channel send fails (permissions), fallback to DM per player
                                    alive_players = [p for p in g.players.values() if p.alive]
                                    for p in alive_players:
                                        try:
                                            user = await self.bot.fetch_user(int(p.id))
                                            vv = self.VotingView(timeout=vote_duration, game=g, channel=channel, options=[options])
                                            try:
                                                if vote_duration is None:
                                                    await user.send(msg('day_revote_prompt_no_seconds'), view=vv)
                                                else:
                                                    await user.send(msg('day_revote_prompt', seconds=vote_duration), view=vv)
                                            except Exception:
                                                pass
                                            revote_views.append(vv)
                                        except Exception:
                                            pass
                            except Exception:
                                revote_views = []
                    except Exception:
                        revote_views = []

                    # Wait for the revote view(s) to finish (pause-aware)
                    if revote_views:
                        try:
                            # wait on each view (if multiple) but allow the single channel view to accept repeated interactions
                            for v in revote_views:
                                try:
                                    await self._wait_view_with_pause(v, vote_duration, g)
                                except Exception:
                                    pass
                        except Exception:
                            pass

                    # Clear active revote views reference and clear the in-progress flag
                    try:
                        g._active_vote_views = []
                    except Exception:
                        pass
                    try:
                        g._revote_in_progress = False
                    except Exception:
                        pass

                    # Map pending revote votes into engine
                    # Do NOT mark non-responders as invalid - let them be actual abstentions
                    try:
                        raw_pending = getattr(g, '_pending_votes', {}) or {}
                        norm_pending = {}
                        try:
                            for k, v in raw_pending.items():
                                try:
                                    norm_pending[str(k)] = v
                                except Exception:
                                    norm_pending[k] = v
                        except Exception:
                            norm_pending = raw_pending or {}
                        # Do NOT add '__invalid__' entries for non-responders in revote
                        # Let them be treated as genuine abstentions instead
                        try:
                            g._pending_votes = norm_pending
                        except Exception:
                            try:
                                setattr(g, '_pending_votes', norm_pending)
                            except Exception:
                                pass
                    except Exception:
                        pass
                    for uid, choice in getattr(g, '_pending_votes', {}).items():
                        try:
                            if choice == '__abstain__' or choice is None:
                                g.cast_vote(uid, None)
                            else:
                                # accept only choices that are in the (filtered) tie set
                                try:
                                    if choice in valid_tie_list:
                                        g.cast_vote(uid, choice)
                                    else:
                                        g.cast_vote(uid, None)
                                except Exception:
                                    # fallback: be conservative and treat as abstain
                                    g.cast_vote(uid, None)
                        except Exception:
                            pass

                    # Fallback translation if engine.votes is empty - ONLY during forced end
                    try:
                        forced_end_revote = getattr(g, '_forced_end_vote', False)
                        if not g.votes and any(v is not None for v in getattr(g, '_pending_votes', {}).values()) and forced_end_revote:
                            g.log("CRITICAL: Auto-generating revote votes from pending votes due to forced end")
                            for uid, choice in getattr(g, '_pending_votes', {}).items():
                                if choice == '__abstain__' or choice is None:
                                    g.votes.append(Vote(from_id=uid, target_id=None))
                                else:
                                    try:
                                        if choice in valid_tie_list:
                                            g.votes.append(Vote(from_id=uid, target_id=choice))
                                        else:
                                            g.votes.append(Vote(from_id=uid, target_id=None))
                                    except Exception:
                                        g.votes.append(Vote(from_id=uid, target_id=None))
                        elif not g.votes and any(v is not None for v in getattr(g, '_pending_votes', {}).values()):
                            g.log("CRITICAL: Revote auto-generation blocked - no forced end flag")
                    except Exception:
                        pass

                    # Clear existing day_tie so engine can set new flags on resolve
                    try:
                        g._day_tie = None
                    except Exception:
                        pass

                    # Resolve revote
                    try:
                        # Check for invalidation before resolving revote
                        if getattr(g, '_emergency_vote_reset', False):
                            g.log('EMERGENCY: Blocking first revote g.resolve_votes() - emergency reset active')
                        elif getattr(g, '_vote_invalidated_by_guess', False):
                            g.log('Blocking first revote g.resolve_votes() - votes invalidated by guess')
                        else:
                            g.resolve_votes()
                    except Exception:
                        pass

                    # If tie still persists after revote, pick a random tied alive candidate and lynch
                    try:
                        if getattr(g, '_day_tie', None):
                            tie_after = g._day_tie
                            import random as _random
                            alive_tied = [tid for tid in tie_after if g.players.get(tid) and g.players.get(tid).alive]
                            pick = (_random.choice(alive_tied) if alive_tied else _random.choice(tie_after))
                            victim = g.players.get(pick)
                            if victim:
                                victim.alive = False
                                g.log(f"Day: tie persisted after revote; randomly lynched {victim.name}")
                                # Send death notification DM to randomly lynched player
                                try:
                                    await self._send_death_notifications(g, [victim.id])
                                except Exception:
                                    pass
                                # Announce random lynch to the public channel so players know it was random
                                try:
                                    if channel:
                                        try:
                                            await self._send_to_game_thread(g, content=msg('random_lynch_public', name=victim.name))
                                        except Exception:
                                            # fallback: simple localized fallback without name
                                            try:
                                                await self._send_to_game_thread(g, content=msg('random_lynch_public', name=''))
                                            except Exception:
                                                pass
                                except Exception:
                                    pass
                                try:
                                    g.phase = Phase.CHECK_WIN
                                except Exception:
                                    pass
                                # queue medium notification for mediums
                                try:
                                    role = g.roles.get(victim.role_id) if victim.role_id else None
                                    medium_result = 'werewolf' if (role and getattr(role, 'faction', None) == 'werewolf') else 'village'
                                    for pp in g.players.values():
                                        if pp.role_id == 'medium':
                                            try:
                                                msgs = g.private_messages.get(pp.id) or []
                                                msgs.append({'key': 'medium_result', 'params': {'victim': victim.name, 'result': medium_result}})
                                                g.private_messages[pp.id] = msgs
                                            except Exception:
                                                pass
                                except Exception:
                                    pass
                            try:
                                g._day_tie = None
                            except Exception:
                                pass
                    except Exception:
                        pass
                except Exception:
                    # If any unexpected error happens in revote handling, log and continue
                    try:
                        g.log('Error during day revote handling')
                    except Exception:
                        pass

            # Deliver any engine-generated private messages now (e.g., medium notifications)
            try:
                failed_private_dms_after_vote: List[str] = []
                # We will not deliver medium-role private messages immediately after vote resolution.
                # Keep medium messages queued to be delivered at the next night start.
                remaining_private: Dict[str, List[str]] = {}
                for pid, msgs in list(getattr(g, 'private_messages', {}).items()):
                    p = g.players.get(pid)
                    # If this recipient is a medium, retain the messages for night delivery
                    if p and p.role_id == 'medium':
                        remaining_private[pid] = msgs
                        continue
                    # otherwise attempt to deliver now
                    try:
                        user = await self.bot.fetch_user(int(pid))
                        for m in msgs:
                            try:
                                # Skip queued seer results post-vote to avoid duplicates
                                try:
                                    if isinstance(m, dict) and m.get('key') in ('seer_result', 'seer_result_followup'):
                                        delivered = getattr(g, '_seer_results_delivered', set())
                                        if str(pid) in delivered:
                                            continue
                                except Exception:
                                    pass
                                rendered = _format_private_message_for_send(m)
                                await user.send(rendered)
                            except Exception:
                                if pid not in failed_private_dms_after_vote:
                                    failed_private_dms_after_vote.append(pid)
                    except Exception:
                        if pid not in failed_private_dms_after_vote:
                            failed_private_dms_after_vote.append(pid)
                # Replace g.private_messages with only the retained medium messages
                g.private_messages = remaining_private
                if failed_private_dms_after_vote:
                    failures = [g.players.get(pid).name if g.players.get(pid) else pid for pid in failed_private_dms_after_vote]
                    try:
                        await self._send_to_game_thread(g, content=msg('could_not_deliver_private', names=", ".join(failures)))
                    except Exception:
                        pass
            except Exception:
                pass

            # After resolving votes, immediately check for win and/or continue to next phase
            try:
                # CRITICAL: Check if game was force-closed before announcing results
                if g.phase == Phase.CLOSED:
                    g.log("Vote result announcement blocked: game force-closed")
                    return
                
                # compute who was lynched by comparing alive sets before/after vote resolution
                before_alive = {p.id for p in alive}
                after_alive = {p.id for p in g.players.values() if p.alive}
                lynched_ids = list(before_alive - after_alive)
                
                # Check if we are creating new voting UI (should skip all result announcements)  
                creating_ui = getattr(g, '_creating_vote_ui', False)
                if creating_ui:
                    g.log(f"VOTE RESULT BLOCKED in _start_day_vote_channel: Currently creating new voting UI (session: {getattr(g, '_current_vote_session_id', 'unknown')})")
                    return
                
                # Check if this is a guesser-invalidated vote (should be blocked)
                # vs a normal vote resolution (should proceed even if phase changed to NIGHT)
                vote_invalidated_by_guess = getattr(g, '_vote_invalidated_by_guess', False)
                current_session = getattr(g, '_current_vote_session_id', '')
                is_guesser_session = 'post_guess_vote' in current_session or 'emergency_vote' in current_session
                in_re_vote = getattr(g, '_in_re_vote_after_guess', False)
                
                if vote_invalidated_by_guess or (is_guesser_session and not in_re_vote):
                    g.log(f"VOTE RESULT BLOCKED in _start_day_vote_channel: Vote invalidated by guess action or guesser session (session: {current_session}, flags: invalidated={vote_invalidated_by_guess}, in_re_vote={in_re_vote})")
                    return
                elif in_re_vote and is_guesser_session:
                    # This is a legitimate re-vote completion after guesser - allow result announcement
                    g.log(f"VOTE RESULT ALLOWED in _start_day_vote_channel: Re-vote completion after guess (session: {current_session})")
                    # Only clear the flag if this is coming from actual timeout/completion (not guesser-triggered resolution)
                    # We can detect this by checking if we're still in the initial guesser execution phase
                    if not getattr(g, '_vote_invalidated_by_guess', False):
                        g._in_re_vote_after_guess = False
                        g.log(f"VOTE RESULT: Clearing re-vote flag - vote completion detected (session: {current_session})")
                elif not is_guesser_session:
                    # Normal vote (not related to guesser) - proceed normally
                    g.log(f"VOTE RESULT ALLOWED in _start_day_vote_channel: Normal vote session (session: {current_session})")
                # mark this vote as finalized to prevent duplicate finalization from other code paths
                try:
                    g._vote_finalized = True
                except Exception:
                    pass
                # First, announce vote result (lynch/no-lynch) so it appears before any game result
                if lynched_ids:
                    try:
                        # Send death notification DM to lynched players
                        try:
                            await self._send_death_notifications(g, lynched_ids)
                        except Exception:
                            pass
                        
                        names = []
                        for did in lynched_ids:
                            p = g.players.get(did)
                            if p:
                                names.append(p.name)
                        if names:
                            try:
                                g.log(f"VOTE RESULT: About to announce lynch of {names} (session: {getattr(g, '_current_vote_session_id', 'unknown')})")
                                await self._send_to_game_thread(g, content=msg('lynched_public', names=", ".join(names)))
                                g.log(f"VOTE RESULT: Successfully announced lynch of {names}")
                            except Exception as e:
                                g.log(f"VOTE RESULT: Failed to send lynch message: {e}")
                    except Exception as e:
                        g.log(f"VOTE RESULT: Error preparing lynch message: {e}")
                else:
                    try:
                        g.log(f"VOTE RESULT: About to announce no lynch (session: {getattr(g, '_current_vote_session_id', 'unknown')})")
                        await self._send_to_game_thread(g, content=msg('no_lynch_public'))
                        g.log(f"VOTE RESULT: Successfully announced no lynch")
                    except Exception:
                        pass
                # After announcing the vote result, post anonymous tally (so it appears even if CHECK_WIN ends the game)
                try:
                    counts: Dict[Optional[str], int] = {}
                    for v in g.votes:
                        counts[v.target_id] = counts.get(v.target_id, 0) + 1

                    rows: List[tuple[str, int]] = []
                    try:
                        vote_targets = [p for p in alive]
                        for p in vote_targets:
                            c = counts.get(p.id, 0)
                            rows.append((p.name, c))
                    except Exception:
                        for pid, p in g.players.items():
                            c = counts.get(pid, 0)
                            rows.append((p.name, c))

                    abstain_count = counts.get(None, 0)
                    try:
                        abstain_label = msg('vote_abstain_label')
                    except Exception:
                        abstain_label = '棄権'
                    rows.append((abstain_label, abstain_count))

                    try:
                        rows_sorted = sorted(rows, key=lambda x: (-x[1], x[0].lower()))
                    except Exception:
                        rows_sorted = rows

                    try:
                        total_valid = 0
                        for v in g.votes:
                            if v.target_id == '__invalid__':
                                continue
                            total_valid += 1
                    except Exception:
                        total_valid = sum(c for k, c in counts.items() if k != '__invalid__')

                    tally_lines = [f"{label}: {count}" for label, count in rows_sorted]
                    try:
                        try:
                            title = msg('tally_embed')[0] + '（匿名）'
                        except Exception:
                            title = '🗳️ 投票結果（匿名）'
                        try:
                            valid_label = msg('tally_embed')[1]
                        except Exception:
                            valid_label = '有効投票数'

                        embed = discord.Embed(
                            title=title, 
                            colour=0x3498db,
                            timestamp=self._get_jst_timestamp()
                        )
                        
                        # Add voting summary field
                        summary_text = f"📄 {valid_label}: **{total_valid}**票"
                        embed.add_field(name="投票概要", value=summary_text, inline=False)
                        
                        # Format tally lines with better styling - use helper function  
                        if tally_lines:
                            formatted_lines = self._format_vote_tally_lines_with_abstain(tally_lines)
                            
                            if formatted_lines:
                                embed.add_field(
                                    name="投票内訳", 
                                    value="\n".join(formatted_lines), 
                                    inline=False
                                )
                            else:
                                embed.add_field(name="投票内訳", value="有効な投票はありませんでした", inline=False)
                        else:
                            embed.add_field(name="投票内訳", value="有効な投票はありませんでした", inline=False)
                        
                        embed.set_footer(text="投票結果は匿名で表示されます")
                        
                        try:
                            await self._send_to_game_thread(g, embed=embed)
                        except Exception:
                            try:
                                fallback_text = f"{title}\n{valid_label}: {total_valid}\n" + "\n".join(tally_lines)
                                await self._send_to_game_thread(g, content=fallback_text)
                            except Exception:
                                pass
                    except Exception:
                        pass

                except Exception:
                    pass

                # After announcing the vote result and tally, run the win check and announce game result if any
                try:
                    handled = await self._evaluate_and_handle_win(g, channel)
                    if handled:
                        return
                    # if game continues to NIGHT, start the night sequence
                    if g.phase == Phase.NIGHT:
                        try:
                            # schedule night sequence asynchronously
                            try:
                                self.bot.loop.create_task(self._run_night_sequence(g, int(channel.id)))
                            except Exception:
                                asyncio.create_task(self._run_night_sequence(g, int(channel.id)))
                        except Exception:
                            # ignore scheduling failures but persist state
                            self.storage.save_game(g)
                except Exception as e:
                    # log and continue
                    g.log(f"check_win after votes failed: {e}")
                    # persist game
                    self.storage.save_game(g)
            except Exception as e:
                # log and continue
                try:
                    g.log(f"Exception during vote finalization: {e}")
                except Exception:
                    pass
                # persist game
                try:
                    self.storage.save_game(g)
                except Exception:
                    pass

        except Exception:
            # defensive: swallow errors during vote finalization so bot stays alive
            try:
                g.log('Exception during vote finalization (ignored)')
            except Exception:
                pass
        finally:
            # Always release lock if we acquired it
            try:
                if acquired_lock and lock:
                    try:
                        lock.release()
                    except Exception:
                        pass
            except Exception:
                pass

    async def _do_resolve_pending_votes(self, g: Game, channel: Optional[discord.TextChannel]):
        """Resolve g._pending_votes into engine votes, run resolution, announce results,
        check for win, unmute participants appropriately, and persist state.

        This function mirrors the final resolution steps from _start_day_vote_channel
        so that forced-end commands can call it directly.
        """
        try:
            # Log entry point
            current_session = getattr(g, '_current_vote_session_id', 'unknown')
            forced_end = getattr(g, '_forced_end_vote', False)
            g.log(f"VOTE RESOLUTION ENTRY: session={current_session}, phase={g.phase}, forced_end={forced_end}, invalidation_flags=(emergency_reset={getattr(g, '_emergency_vote_reset', False)}, vote_invalidated={getattr(g, '_vote_invalidated_by_guess', False)}, finalized={getattr(g, '_vote_finalized', False)})")
            
            # Log current voting state for debugging
            try:
                pending_votes = getattr(g, '_pending_votes', {})
                g.log(f"VOTE RESOLUTION: Pending votes count: {len(pending_votes)}, Engine votes count: {len(g.votes)}")
                g.log(f"VOTE RESOLUTION: Pending votes detail: {pending_votes}")
                alive_count = len([p for p in g.players.values() if p.alive])
                g.log(f"VOTE RESOLUTION: Alive players: {alive_count}")
            except Exception:
                pass
            
            # CRITICAL: Check if game was force-closed
            if g.phase == Phase.CLOSED:
                g.log("Vote resolution aborted: game force-closed")
                return
            # CRITICAL EARLY BLOCKING: Prevent resolution during wrong phase
            if g.phase != Phase.VOTE:
                try:
                    g.log(f'VOTE RESOLUTION BLOCKED: Game phase is {g.phase}, not VOTE - aborting all resolution')
                    g.log(f'VOTE RESOLUTION BLOCKED: forced_end={forced_end}, emergency_reset={emergency_reset}')
                    g.log(f'VOTE RESOLUTION BLOCKED: This may be why the vote is not resolving properly')
                except Exception:
                    pass
                return
                
            g.log(f"VOTE RESOLUTION: Starting resolution (session: {current_session})")
            
            # Check for emergency vote reset flag (highest priority)
            emergency_reset = getattr(g, '_emergency_vote_reset', False)
            forced_end = getattr(g, '_forced_end_vote', False)
            
            # Allow forced end to bypass emergency reset
            if emergency_reset and not forced_end:
                try:
                    g.log(f'VOTE RESOLUTION BLOCKED: Emergency reset flag is {emergency_reset} (not forced)')
                except Exception:
                    pass
                return
                
            # Check for vote invalidation flag (set by guess events)
            vote_invalidated = getattr(g, '_vote_invalidated_by_guess', False)
            
            # Allow forced end to bypass vote invalidation
            if vote_invalidated and not forced_end:
                try:
                    g.log(f'VOTE RESOLUTION BLOCKED: Vote invalidated flag is {vote_invalidated} (not forced)')
                except Exception:
                    pass
                return
                
            # Check for vote finalized flag
            vote_finalized = getattr(g, '_vote_finalized', False)
            if vote_finalized:
                try:
                    g.log(f'VOTE RESOLUTION BLOCKED: Vote finalized flag is {vote_finalized}')
                except Exception:
                    pass
                return
            
            g.log(f"VOTE RESOLUTION: All checks passed - emergency_reset={emergency_reset}, vote_invalidated={vote_invalidated}, vote_finalized={vote_finalized}")
            g.log(f"VOTE RESOLUTION: Current votes count: {len(g.votes)}, pending votes count: {len(getattr(g, '_pending_votes', {}))}")
                
            # Extra safety: Force clear engine votes if invalidation flags are set
            try:
                if getattr(g, '_emergency_vote_reset', False) or getattr(g, '_vote_invalidated_by_guess', False):
                    g.votes = []
                    g.log('Emergency vote data clearing due to invalidation flags')
                    return
            except Exception:
                pass
            
            # defensive: ensure we have a channel object if possible
            if channel is None:
                try:
                    channel = self.bot.get_channel(int(g.game_id))
                except Exception:
                    channel = None
        except Exception:
            channel = None

        # If a revote is currently in progress or this vote round was already finalized,
        # skip resolving here to avoid duplicate announcements or racing the revote UI.
        try:
            if getattr(g, '_revote_in_progress', False):
                try:
                    active_views = getattr(g, '_active_vote_views', None)
                except Exception:
                    active_views = None
                # If there are active revote views, another revote UI is truly in progress;
                # skip resolving to avoid racing the revote flow. If the flag is set but
                # there are no active views, assume the flag is stale and clear it so
                # resolution can proceed.
                try:
                    if active_views:
                        try:
                            g.log('Skipping _resolve_pending_votes because revote is in progress (active views present)')
                        except Exception:
                            pass
                        return
                    else:
                        try:
                            # stale flag detected; clear and continue
                            g._revote_in_progress = False
                            try:
                                g.log('Cleared stale _revote_in_progress flag during forced resolution')
                            except Exception:
                                pass
                        except Exception:
                            pass
                except Exception:
                    # If anything goes wrong inspecting active_views, be conservative and continue
                    try:
                        g.log('Error inspecting revote active views; proceeding with resolution')
                    except Exception:
                        pass
        except Exception:
            pass
        try:
            if getattr(g, '_vote_finalized', False):
                try:
                    g.log('Skipping _resolve_pending_votes because vote already finalized')
                except Exception:
                    pass
                return
        except Exception:
            pass
        try:
            lock = getattr(g, '_vote_resolution_lock', None)
        except Exception:
            lock = None

        acquired_lock = False
        try:
            # Ensure a per-game lock exists (mirror timeout path) so both timeout and
            # forced-end resolution use the same synchronization primitive.
            if lock is None:
                try:
                    import asyncio as _asyncio
                    lock = _asyncio.Lock()
                    try:
                        g._vote_resolution_lock = lock
                    except Exception:
                        pass
                except Exception:
                    lock = None

            # If someone else already acquired the lock, skip resolution to avoid racing
            try:
                if lock and lock.locked():
                    # A resolution is in progress. Instead of returning immediately
                    # (which can cause races where a stale revote flag was cleared),
                    # wait a short time for the lock to be released and then acquire
                    # it to proceed. If the wait times out, give up and skip.
                    try:
                        g.log('Resolution lock is held; waiting briefly to acquire')
                    except Exception:
                        pass
                    try:
                        import asyncio as _asyncio
                        try:
                            await _asyncio.wait_for(lock.acquire(), timeout=3.0)
                            acquired_lock = True
                        except Exception:
                            try:
                                g.log('Timed out waiting for resolution lock; scheduling retry for forced resolution')
                            except Exception:
                                pass
                            # Schedule background retry attempts to resolve after the other resolution completes.
                            try:
                                # fire-and-forget: will attempt a few times and give up
                                try:
                                    # increase attempts to be more tolerant of short races
                                    asyncio.get_running_loop().create_task(self._retry_resolve_after_lock(g, channel, attempts=10, delay=1.0))
                                except Exception:
                                    # fallback to asyncio.create_task if get_running_loop not available
                                    try:
                                        asyncio.create_task(self._retry_resolve_after_lock(g, channel, attempts=10, delay=1.0))
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                            return
                    except Exception:
                        # If wait_for failed for some reason, skip resolution to be safe
                        try:
                            g.log('Failed to wait for resolution lock; skipping forced resolution')
                        except Exception:
                            pass
                        return
            except Exception:
                pass

            # Now acquire the lock so no other resolution can run concurrently
            try:
                if lock:
                    await lock.acquire()
                    acquired_lock = True
            except Exception:
                acquired_lock = False

            # If the boolean in-progress flag is set but we hold the lock, clear stale flag
            try:
                if getattr(g, '_vote_resolution_in_progress', False):
                    try:
                        g._vote_resolution_in_progress = False
                        try:
                            g.log('Cleared stale _vote_resolution_in_progress flag during forced resolution')
                        except Exception:
                            pass
                    except Exception:
                        pass
            except Exception:
                pass

            try:
                g._vote_resolution_in_progress = True
            except Exception:
                pass
        except Exception:
            # If lock setup/acquire fails, continue but do not hold a lock
            acquired_lock = False

        try:
            # Treat any remaining non-responders as invalid and notify via DM
            alive = [p for p in g.players.values() if p.alive]
            alive_ids = [p.id for p in alive]
            for pid in alive_ids:
                if pid not in getattr(g, '_pending_votes', {}):
                    try:
                        g._pending_votes[pid] = '__invalid__'
                    except Exception:
                        pass
                    # Skip DM notification if we're in a re-vote after guess (will restart voting)
                    in_re_vote_after_guess = getattr(g, '_in_re_vote_after_guess', False)
                    if not in_re_vote_after_guess:
                        try:
                            user = await self.bot.fetch_user(int(pid))
                            try:
                                await user.send(msg('vote_invalid_dm'))
                            except Exception:
                                pass
                        except Exception:
                            pass
        except Exception:
            pass

        try:
            # Apply pending votes into engine
            for uid, choice in getattr(g, '_pending_votes', {}).items():
                if choice == '__invalid__':
                    continue
                if choice == '__abstain__' or choice is None:
                    try:
                        g.cast_vote(uid, None)
                    except Exception:
                        pass
                else:
                    try:
                        g.cast_vote(uid, choice)
                    except Exception:
                        pass

            # Fallback translation if engine.votes is empty - ONLY during forced end
            try:
                forced_end_general = getattr(g, '_forced_end_vote', False)
                if not g.votes and any(v is not None for v in getattr(g, '_pending_votes', {}).values()) and forced_end_general:
                    g.log("CRITICAL: Auto-generating general votes from pending votes due to forced end")
                    for uid, choice in getattr(g, '_pending_votes', {}).items():
                        if choice == '__abstain__' or choice is None:
                            g.votes.append(Vote(from_id=uid, target_id=None))
                        elif choice == '__invalid__':
                            continue
                        else:
                            g.votes.append(Vote(from_id=uid, target_id=choice))
                elif not g.votes and any(v is not None for v in getattr(g, '_pending_votes', {}).values()):
                    g.log("CRITICAL: General vote auto-generation blocked - no forced end flag")
            except Exception:
                pass

            # Check if there are no valid votes (all players abstained or no one voted)
            try:
                # CRITICAL: Check for invalidation before processing zero votes result
                if getattr(g, '_emergency_vote_reset', False) or getattr(g, '_vote_invalidated_by_guess', False):
                    try:
                        g.log('Blocking zero votes processing - votes invalidated by guess action')
                    except Exception:
                        pass
                    return
                
                valid_votes = [v for v in g.votes if v.target_id != '__invalid__']
                pending_votes = getattr(g, '_pending_votes', {})
                
                if not valid_votes and not any(v for v in pending_votes.values() if v not in ('__invalid__', None)):
                    # Check invalidation again before announcing zero votes
                    if getattr(g, '_emergency_vote_reset', False) or getattr(g, '_vote_invalidated_by_guess', False):
                        try:
                            g.log('Blocking zero votes announcement - votes invalidated by guess action')
                        except Exception:
                            pass
                        return
                    
                    # No one voted or everyone abstained - treat as mass abstention
                    try:
                        g.log('Day vote: No valid votes received, treating as mass abstention')
                    except Exception:
                        pass
                    
                    # Force phase transition to CHECK_WIN to continue game flow
                    try:
                        g.phase = Phase.CHECK_WIN
                    except Exception:
                        pass
                    
                    # Announce to channel that no one voted
                    try:
                        if channel:
                            try:
                                await self._send_to_game_thread(g, content=msg('day_vote_no_votes'))
                            except Exception:
                                try:
                                    await channel.send('誰も投票しませんでした。処刑は行われません。')
                                except Exception:
                                    pass
                    except Exception:
                        pass
                    
                    # Skip the rest of vote resolution and go directly to result processing
                    try:
                        g._vote_finalized = True
                    except Exception:
                        pass
                    try:
                        g._vote_resolution_in_progress = False
                    except Exception:
                        pass
                    
                    # Save and process CHECK_WIN phase to handle game continuation
                    try:
                        self.storage.save_game(g)
                    except Exception:
                        pass
                    
                    # Process CHECK_WIN phase to handle game continuation
                    try:
                        # Mark this as vote resolution (not guesser action) for proper phase transition
                        g._check_win_context = 'vote_resolution'
                        await self._evaluate_and_handle_win(g, channel)
                    except Exception:
                        try:
                            g.log('Failed to process CHECK_WIN phase after zero votes')
                        except Exception:
                            pass
                    return
            except Exception:
                pass

            # CRITICAL: Final check before resolving votes and processing results
            if getattr(g, '_emergency_vote_reset', False) or getattr(g, '_vote_invalidated_by_guess', False):
                try:
                    g.log('Blocking vote resolution - votes invalidated by guess action')
                except Exception:
                    pass
                return

            # Resolve votes in engine
            try:
                g.resolve_votes()
            except Exception:
                pass

            # If engine indicated a day-tie, run a revote among tied candidates.
            try:
                day_tie = getattr(g, '_day_tie', None)
            except Exception:
                day_tie = None

            if day_tie:
                try:
                    # Before starting the revote, publicly disclose the initial vote results
                    try:
                        # build anonymous tally from g.votes
                        counts_tmp: Dict[Optional[str], int] = {}
                        for v in g.votes:
                            counts_tmp[v.target_id] = counts_tmp.get(v.target_id, 0) + 1
                        alive_tmp = [p for p in g.players.values() if p.alive]
                        rows_tmp: List[tuple[str, int]] = []
                        try:
                            vote_targets_tmp = [p for p in alive_tmp]
                            for p in vote_targets_tmp:
                                c = counts_tmp.get(p.id, 0)
                                rows_tmp.append((p.name, c))
                        except Exception:
                            for pid, p in g.players.items():
                                c = counts_tmp.get(pid, 0)
                                rows_tmp.append((p.name, c))
                        # abstain
                        abstain_tmp = counts_tmp.get(None, 0)
                        try:
                            abstain_label_tmp = msg('vote_abstain_label')
                        except Exception:
                            abstain_label_tmp = '棄権'
                        rows_tmp.append((abstain_label_tmp, abstain_tmp))
                        try:
                            rows_sorted_tmp = sorted(rows_tmp, key=lambda x: (-x[1], x[0].lower()))
                        except Exception:
                            rows_sorted_tmp = rows_tmp
                        # compose embed and send
                        try:
                            try:
                                title_tmp = msg('tally_embed')[0] + '（最初の投票）'
                            except Exception:
                                title_tmp = '投票結果（最初の投票）'
                            try:
                                valid_label_tmp = msg('tally_embed')[1]
                            except Exception:
                                valid_label_tmp = '有効投票数'
                            total_valid_tmp = sum(1 for v in g.votes if getattr(v, 'target_id', None) != '__invalid__')
                            embed_tmp = discord.Embed(title=title_tmp, colour=0x3498db)
                            try:
                                embed_tmp.add_field(name=valid_label_tmp, value=str(total_valid_tmp), inline=False)
                            except Exception:
                                embed_tmp.description = f"{valid_label_tmp}: {total_valid_tmp}\n"
                            desc_tmp = "\n".join([f"{label}: {count}" for label, count in rows_sorted_tmp])
                            if getattr(embed_tmp, 'description', None):
                                embed_tmp.description = (embed_tmp.description or '') + "\n" + desc_tmp
                            else:
                                embed_tmp.description = desc_tmp
                            try:
                                await self._send_to_game_thread(g, embed=embed_tmp)
                            except Exception:
                                # ignore send failures and continue to revote UI
                                pass
                        except Exception:
                            pass
                    except Exception:
                        pass

                    # Build revote options only among tie candidates that are still valid/alive.
                    # Preserve original tie ordering from engine where possible.
                    valid_tie_list: List[Optional[str]] = []
                    has_abstain_option = False
                    try:
                        for tid in day_tie:
                            # None represents abstain in engine's tie list
                            if tid is None:
                                has_abstain_option = True
                                # Don't add None to valid_tie_list yet; we'll add abstain option separately
                            else:
                                p = g.players.get(tid)
                                # Only include players who still exist and are alive
                                if p and getattr(p, 'alive', False):
                                    # use string ids so they match Select.value which is a string
                                    try:
                                        valid_tie_list.append(str(tid))
                                    except Exception:
                                        valid_tie_list.append(tid)
                    except Exception:
                        # If anything goes wrong, fall back to original day_tie list
                        # ensure we string-ify non-None ids where possible
                        try:
                            for x in day_tie:
                                if x is None:
                                    has_abstain_option = True
                                else:
                                    valid_tie_list.append(str(x))
                        except Exception:
                            valid_tie_list = [x for x in day_tie if x is not None]
                            has_abstain_option = None in day_tie

                    # Build options including explicit Abstain option if present in the (filtered) tie set
                    options: List[discord.SelectOption] = []
                    for tid in valid_tie_list:
                        if tid is None:
                            try:
                                abstain_label = msg('vote_abstain_label')
                            except Exception:
                                abstain_label = '棄権'
                            options.append(discord.SelectOption(label=abstain_label, value='__abstain__'))
                        else:
                            try:
                                p = g.players.get(tid)
                                if p:
                                    options.append(discord.SelectOption(label=p.name, value=str(tid)))
                            except Exception:
                                # ignore missing players
                                pass

                    # If there is exactly one revote candidate, auto-lynch them immediately
                    try:
                        if len(options) == 1:
                            try:
                                pick = options[0].value
                                # pick may be a string id (because Select uses strings) or special markers
                                victim = None
                                try:
                                    if pick not in ('__abstain__', '__invalid__'):
                                        victim = g.players.get(int(pick))
                                except Exception:
                                    try:
                                        victim = g.players.get(pick)
                                    except Exception:
                                        victim = None

                                if victim:
                                    # use centralized kill to respect lovers pairing
                                    killed = g._kill_player(victim.id, reason='lynch') or []
                                    if killed:
                                        try:
                                            g.log(f"Day: only one revote candidate; lynched {victim.name}")
                                        except Exception:
                                            pass
                                        try:
                                            g.phase = Phase.CHECK_WIN
                                        except Exception:
                                            pass
                                        try:
                                            g._last_lynched_ids = [victim.id]
                                        except Exception:
                                            pass
                            except Exception:
                                pass
                            # clear tie and skip the normal revote UI
                            try:
                                g._day_tie = None
                            except Exception:
                                pass
                    except Exception:
                        pass

                    # Reset pending votes to collect fresh revote choices
                    g._pending_votes = {}
                    # Clear any previously resolved votes so the revote starts from a clean slate
                    try:
                        g.votes = []
                    except Exception:
                        pass

                    # Initialize pending votes for all alive players to enable voting
                    try:
                        alive_players = [p for p in g.players.values() if p.alive]
                        for player in alive_players:
                            g._pending_votes[str(player.id)] = 'invalid'
                        g.log(f"REVOTE SETUP: Initialized pending votes for {len(alive_players)} players")
                    except Exception:
                        pass

                    # CRITICAL: Clear forced end flags and reset timer for revote
                    try:
                        g._forced_end_vote = False
                        g._emergency_vote_reset = False
                        g._vote_invalidated_by_guess = False
                        g._vote_finalized = False
                        g.log("REVOTE SETUP: Cleared all forced end and blocking flags")
                    except Exception:
                        pass
                    
                    # Cancel any existing countdown tasks before starting new revote
                    try:
                        countdown_task = getattr(g, '_countdown_task', None)
                        revote_countdown_task = getattr(g, '_revote_countdown_task', None)
                        
                        if countdown_task and not countdown_task.done():
                            countdown_task.cancel()
                            g.log("REVOTE SETUP: Cancelled existing main countdown task")
                            
                        if revote_countdown_task and not revote_countdown_task.done():
                            revote_countdown_task.cancel()
                            g.log("REVOTE SETUP: Cancelled existing revote countdown task")
                    except Exception:
                        pass
                    
                    # Reset vote start time for proper countdown in revote
                    try:
                        import time
                        g._day_vote_started_at = time.time()
                        g.log("REVOTE SETUP: Reset vote start time for countdown")
                    except Exception:
                        pass

                    # Post a single revote UI in the public channel so players can re-vote (overwrite their choice)
                    # Guard against duplicate revote UIs being posted from multiple code paths by using
                    # a simple in-progress flag on the game object.
                    revote_views = []
                    try:
                        # If another revote is already in progress, skip creating a new UI
                        if getattr(g, '_revote_in_progress', False):
                            revote_views = []
                        else:
                            try:
                                g._revote_in_progress = True
                            except Exception:
                                pass
                            try:
                                g._active_vote_views = revote_views
                            except Exception:
                                pass
                            try:
                                vote_duration = getattr(g, '_runtime_day_vote_timeout', None)
                                # Use enhanced voting UI for revote similar to normal voting
                                await self._start_enhanced_revote_in_thread(g, options, vote_duration)
                                revote_views = getattr(g, '_active_vote_views', [])
                            except Exception:
                                # Fallback to old VotingView if enhanced UI fails
                                v = self.VotingView(timeout=vote_duration, game=g, channel=channel, options=[options])
                                try:
                                    if vote_duration is None:
                                        await self._send_to_game_thread(g, content=msg('day_revote_prompt_no_seconds'), view=v)
                                    else:
                                        await self._send_to_game_thread(g, content=msg('day_revote_prompt', seconds=vote_duration), view=v)
                                    revote_views.append(v)
                                except Exception:
                                    # If channel send fails (permissions), fallback to DM per player
                                    alive_players = [p for p in g.players.values() if p.alive]
                                    for p in alive_players:
                                        try:
                                            user = await self.bot.fetch_user(int(p.id))
                                            vv = self.VotingView(timeout=vote_duration, game=g, channel=channel, options=[options])
                                            try:
                                                if vote_duration is None:
                                                    await user.send(msg('day_revote_prompt_no_seconds'), view=vv)
                                                else:
                                                    await user.send(msg('day_revote_prompt', seconds=vote_duration), view=vv)
                                            except Exception:
                                                pass
                                            revote_views.append(vv)
                                        except Exception:
                                            pass
                            except Exception:
                                revote_views = []
                    except Exception:
                        revote_views = []

                    # Wait for the revote view(s) to finish (pause-aware)
                    if revote_views:
                        try:
                            # wait on each view (if multiple) but allow the single channel view to accept repeated interactions
                            for v in revote_views:
                                try:
                                    await self._wait_view_with_pause(v, vote_duration, g)
                                except Exception:
                                    pass
                        except Exception:
                            pass
                        finally:
                            # Always clear active revote views and in-progress flag in finally block
                            try:
                                g._active_vote_views = []
                            except Exception:
                                pass
                            try:
                                g._revote_in_progress = False
                            except Exception:
                                pass
                    else:
                        # No views created (options empty or error), clear flags immediately
                        try:
                            g._active_vote_views = []
                        except Exception:
                            pass
                        try:
                            g._revote_in_progress = False
                        except Exception:
                            pass

                    # Debug: log pending revote state and valid tie list so we can trace abstain/typing issues
                    try:
                        try:
                            g.log(f"Revote pending before mapping: {repr(getattr(g, '_pending_votes', {}))}")
                        except Exception:
                            pass
                        try:
                            g.log(f"Revote valid_tie_list: {repr(valid_tie_list)}")
                        except Exception:
                            pass
                    except Exception:
                        pass

                    # Map pending revote votes into engine
                    for uid, choice in getattr(g, '_pending_votes', {}).items():
                        try:
                            # Skip invalid votes - don't cast them as abstain
                            if choice in ['__invalid__', 'invalid', None, '']:
                                try:
                                    g.log(f"REVOTE: Skipping invalid vote for player {uid}: '{choice}'")
                                except Exception:
                                    pass
                                continue
                            
                            # normalize uid to int when possible (engine usually uses numeric ids)
                            try:
                                from_id = int(uid)
                            except Exception:
                                from_id = uid

                            # normalize choice handling: abstain special marker
                            if choice == '__abstain__':
                                try:
                                    g.cast_vote(from_id, None)
                                except Exception:
                                    # fallback: try with uid as str if engine expects that
                                    try:
                                        g.cast_vote(uid, None)
                                    except Exception:
                                        pass
                            else:
                                # choice comes from Select.value and is a string; valid_tie_list was stringified
                                try:
                                    if choice in valid_tie_list:
                                        # convert target id back to int if possible
                                        try:
                                            target_id = int(choice)
                                        except Exception:
                                            target_id = choice
                                        g.cast_vote(from_id, target_id)
                                    else:
                                        # Invalid choice - skip it, don't cast as abstain
                                        try:
                                            g.log(f"REVOTE: Skipping invalid choice for player {uid}: '{choice}' (not in valid_tie_list)")
                                        except Exception:
                                            pass
                                except Exception:
                                    # fallback: skip invalid votes instead of treating as abstain
                                    try:
                                        g.log(f"REVOTE: Exception processing vote for player {uid}, skipping")
                                    except Exception:
                                        pass
                        except Exception:
                            pass

                    # Fallback translation if engine.votes is empty - ONLY during forced end
                    try:
                        forced_end_retie = getattr(g, '_forced_end_vote', False)
                        if not g.votes and any(v is not None for v in getattr(g, '_pending_votes', {}).values()) and forced_end_retie:
                            g.log("CRITICAL: Auto-generating retie votes from pending votes due to forced end")
                            for uid, choice in getattr(g, '_pending_votes', {}).items():
                                try:
                                    # Skip invalid votes - don't translate them as abstain
                                    if choice in ['__invalid__', 'invalid', '', None]:
                                        try:
                                            g.log(f"REVOTE FALLBACK: Skipping invalid vote for player {uid}: '{choice}'")
                                        except Exception:
                                            pass
                                        continue
                                        
                                    try:
                                        from_id = int(uid)
                                    except Exception:
                                        from_id = uid
                                    if choice == '__abstain__':
                                        g.votes.append(Vote(from_id=from_id, target_id=None))
                                    else:
                                        try:
                                            if choice in valid_tie_list:
                                                try:
                                                    target = int(choice)
                                                except Exception:
                                                    target = choice
                                                g.votes.append(Vote(from_id=from_id, target_id=target))
                                            else:
                                                # Invalid choice - skip it, don't add as abstain
                                                try:
                                                    g.log(f"REVOTE FALLBACK: Skipping invalid choice for player {uid}: '{choice}' (not in valid_tie_list)")
                                                except Exception:
                                                    pass
                                        except Exception:
                                            # Skip malformed votes instead of treating as abstain
                                            try:
                                                g.log(f"REVOTE FALLBACK: Exception processing vote for player {uid}, skipping")
                                            except Exception:
                                                pass
                                except Exception:
                                    # best-effort: skip malformed pending entries
                                    pass
                        elif not g.votes and any(v is not None for v in getattr(g, '_pending_votes', {}).values()):
                            g.log("CRITICAL: Retie vote auto-generation blocked - no forced end flag")
                    except Exception:
                        pass

                    # Clear existing day_tie so engine can set new flags on resolve
                    try:
                        g._day_tie = None
                    except Exception:
                        pass

                    # Resolve revote
                    try:
                        # Check for invalidation before resolving revote
                        if getattr(g, '_emergency_vote_reset', False):
                            g.log('EMERGENCY: Blocking second revote g.resolve_votes() - emergency reset active')
                        elif getattr(g, '_vote_invalidated_by_guess', False):
                            g.log('Blocking second revote g.resolve_votes() - votes invalidated by guess')
                        else:
                            g.resolve_votes()
                    except Exception:
                        pass

                    # If tie still persists after revote, pick a random tied alive candidate and lynch
                    try:
                        if getattr(g, '_day_tie', None):
                            tie_after = g._day_tie
                            import random as _random
                            alive_tied = [tid for tid in tie_after if g.players.get(tid) and g.players.get(tid).alive]
                            pick = (_random.choice(alive_tied) if alive_tied else _random.choice(tie_after))
                            victim = g.players.get(pick)
                            if victim:
                                victim.alive = False
                                g.log(f"Day: tie persisted after revote; randomly lynched {victim.name}")
                                # Send death notification DM to randomly lynched player
                                try:
                                    await self._send_death_notifications(g, [victim.id])
                                except Exception:
                                    pass
                                # Announce random lynch to the public thread before changing phase
                                try:
                                    if channel:
                                        try:
                                            await self._send_to_game_thread(g, content=msg('random_lynch_public', name=victim.name))
                                        except Exception:
                                            try:
                                                await self._send_to_game_thread(g, content=msg('random_lynch_public', name=''))
                                            except Exception:
                                                pass
                                except Exception:
                                    pass
                                try:
                                    g.phase = Phase.CHECK_WIN
                                except Exception:
                                    pass
                                # queue medium notification for mediums
                                try:
                                    role = g.roles.get(victim.role_id) if victim.role_id else None
                                    medium_result = 'werewolf' if (role and getattr(role, 'faction', None) == 'werewolf') else 'village'
                                    for pp in g.players.values():
                                        if pp.role_id == 'medium':
                                            try:
                                                msgs = g.private_messages.get(pp.id) or []
                                                msgs.append({'key': 'medium_result', 'params': {'victim': victim.name, 'result': medium_result}})
                                                g.private_messages[pp.id] = msgs
                                            except Exception:
                                                pass
                                except Exception:
                                    pass
                            try:
                                g._day_tie = None
                            except Exception:
                                pass
                    except Exception:
                        pass
                except Exception:
                    # If any unexpected error happens in revote handling, log and continue
                    try:
                        g.log('Error during day revote handling')
                    except Exception:
                        pass

            # After resolving votes, check for win and announce/handle accordingly
            try:
                before_alive = {p.id for p in [p for p in g.players.values() if p.alive]}
            except Exception:
                before_alive = set()

            try:
                after_alive = {p.id for p in g.players.values() if p.alive}
            except Exception:
                after_alive = set()

            lynched_ids = list(before_alive - after_alive)
            # mark that we've finalized this vote round to avoid duplicate announcements
            try:
                g._vote_finalized = True
            except Exception:
                pass
            try:
                # clear the in-progress guard now that we've finalized
                g._vote_resolution_in_progress = False
            except Exception:
                pass
            try:
                # Clear forced end flag after successful resolution
                g._forced_end_vote = False
                g.log("VOTE RESOLUTION COMPLETE: Cleared forced end flag")
            except Exception:
                pass
                pass
            # announce vote result (lynch/no-lynch) first
            # But skip if vote was invalidated by guesser action
            vote_invalidated_by_guess = getattr(g, '_vote_invalidated_by_guess', False)
            if lynched_ids and channel and not vote_invalidated_by_guess:
                try:
                    # Send death notification DM to lynched players FIRST
                    try:
                        g.log(f"VOTE RESULT MAIN: Sending death notifications to lynched players: {lynched_ids}")
                        failed_dms = await self._send_death_notifications(g, lynched_ids)
                        if failed_dms:
                            g.log(f"VOTE RESULT MAIN: Some death DMs failed to send: {failed_dms}")
                        else:
                            g.log(f"VOTE RESULT MAIN: All death notifications sent successfully")
                    except Exception as e:
                        g.log(f"VOTE RESULT MAIN: Failed to send death notifications: {e}")
                        # Continue despite DM failure
                    
                    # Then announce the lynch publicly
                    names = []
                    for did in lynched_ids:
                        p = g.players.get(did)
                        if p:
                            names.append(p.name)
                    if names:
                        try:
                            g.log(f"VOTE RESULT MAIN: About to send lynch announcement for {names} (session: {getattr(g, '_current_vote_session_id', 'unknown')})")
                            await self._send_to_game_thread(g, content=msg('lynched_public', names=", ".join(names)))
                            g.log(f"VOTE RESULT MAIN: Successfully sent lynch announcement for {names}")
                        except Exception as e:
                            g.log(f"VOTE RESULT MAIN: Failed to send lynch announcement: {e}")
                except Exception as e:
                    g.log(f"VOTE RESULT MAIN: Error preparing lynch announcement: {e}")
            elif vote_invalidated_by_guess:
                g.log(f"VOTE RESULT MAIN: Skipping lynch announcement - vote invalidated by guess (session: {getattr(g, '_current_vote_session_id', 'unknown')})")

            # Post anonymous tally (ensure players always see counts even if CHECK_WIN ends the game)
            # But skip if vote was invalidated by guesser action
            if not vote_invalidated_by_guess:
                try:
                    counts: Dict[Optional[str], int] = {}
                    for v in g.votes:
                        counts[v.target_id] = counts.get(v.target_id, 0) + 1

                    rows: List[tuple[str, int]] = []
                    try:
                        vote_targets = [p for p in alive]
                        for p in vote_targets:
                            c = counts.get(p.id, 0)
                            rows.append((p.name, c))
                    except Exception:
                        for pid, p in g.players.items():
                            c = counts.get(pid, 0)
                            rows.append((p.name, c))

                    abstain_count = counts.get(None, 0)
                    try:
                        abstain_label = msg('vote_abstain_label')
                    except Exception:
                        abstain_label = '棄権'
                    rows.append((abstain_label, abstain_count))

                    try:
                        rows_sorted = sorted(rows, key=lambda x: (-x[1], x[0].lower()))
                    except Exception:
                        rows_sorted = rows

                    try:
                        total_valid = 0
                        for v in g.votes:
                            if v.target_id == '__invalid__':
                                continue
                            total_valid += 1
                    except Exception:
                        total_valid = sum(c for k, c in counts.items() if k != '__invalid__')

                    tally_lines = [f"{label}: {count}" for label, count in rows_sorted]
                    try:
                        try:
                            title = msg('tally_embed')[0] + '（匿名）'
                        except Exception:
                            title = '🗳️ 投票結果（匿名）'
                        try:
                            valid_label = msg('tally_embed')[1]
                        except Exception:
                            valid_label = '有効投票数'

                        embed = discord.Embed(
                            title=title, 
                            colour=0x3498db,
                            timestamp=self._get_jst_timestamp()
                        )
                        
                        # Add voting summary field
                        summary_text = f"📄 {valid_label}: **{total_valid}**票"
                        embed.add_field(name="投票概要", value=summary_text, inline=False)
                        
                        # Format tally lines with better styling - use helper function
                        if tally_lines:
                            formatted_lines = self._format_vote_tally_lines_with_abstain(tally_lines)
                            
                            if formatted_lines:
                                embed.add_field(
                                    name="投票内訳", 
                                    value="\n".join(formatted_lines), 
                                    inline=False
                                )
                            else:
                                embed.add_field(name="投票内訳", value="有効な投票はありませんでした", inline=False)
                        else:
                            embed.add_field(name="投票内訳", value="有効な投票はありませんでした", inline=False)
                        
                        embed.set_footer(text="投票結果は匿名で表示されます")
                        
                        try:
                            await self._send_to_game_thread(g, embed=embed)
                        except Exception:
                            try:
                                fallback_text = f"{title}\n{valid_label}: {total_valid}\n" + "\n".join(tally_lines)
                                await self._send_to_game_thread(g, content=fallback_text)
                            except Exception:
                                pass
                    except Exception:
                        pass
                except Exception:
                    pass
            else:
                g.log(f"VOTE TALLY: Skipping vote result embed - vote invalidated by guess (session: {getattr(g, '_current_vote_session_id', 'unknown')})")

            # Delegate win evaluation and end-of-game handling to the centralized helper.
            game_ended = False
            try:
                # Mark this as vote resolution (not guesser action) for proper phase transition
                g._check_win_context = 'vote_resolution'
                handled = await self._evaluate_and_handle_win(g, channel)
                if handled:
                    game_ended = True
            except Exception:
                try:
                    g.log('Error while evaluating/handling win after vote resolution')
                except Exception:
                    pass

            # If game ended, persist and return
            if game_ended:
                try:
                    self.storage.save_game(g)
                except Exception:
                    pass
                return

            # No winner: leave phase as the engine determined (CHECK_WIN -> NIGHT) and unmute alive players
            # (Do not overwrite g.phase here; engine.check_win() is responsible for transitioning to NIGHT when appropriate.)

            try:
                await self._unmute_all_participants(g, channel, only_alive=True)
            except Exception:
                try:
                    g.log('Exception during unmute after vote resolution')
                except Exception:
                    pass

            # If game transitioned to NIGHT, start night sequence
            if g.phase == Phase.NIGHT:
                try:
                    g.log(f'PHASE TRANSITION: Game transitioned to NIGHT after vote resolution (session: {getattr(g, "_current_vote_session_id", "unknown")}); starting night sequence')
                except Exception:
                    pass
                try:
                    channel_id = int(channel.id) if channel else int(g.game_id)
                    g.log(f'PHASE TRANSITION: About to schedule night sequence for channel {channel_id}')
                    self.bot.loop.create_task(self._run_night_sequence(g, channel_id))
                    g.log(f'PHASE TRANSITION: Successfully scheduled night sequence')
                except Exception:
                    try:
                        asyncio.create_task(self._run_night_sequence(g, channel_id))
                        g.log(f'PHASE TRANSITION: Successfully scheduled night sequence (fallback)')
                    except Exception as e:
                        try:
                            g.log(f'PHASE TRANSITION: Failed to schedule night sequence after vote resolution: {e}')
                        except Exception:
                            pass

            # persist
            try:
                self.storage.save_game(g)
            except Exception:
                pass
        except Exception:
            # final fallback persistence
            try:
                self.storage.save_game(g)
            except Exception:
                pass
        finally:
            # Ensure we release the resolution lock if we acquired it earlier
            try:
                if acquired_lock and lock:
                    try:
                        lock.release()
                    except Exception:
                        pass
            except Exception:
                pass
            
            # Clear forced end vote flag
            try:
                if getattr(g, '_forced_end_vote', False):
                    g._forced_end_vote = False
                    g.log('FORCED END VOTE: Cleared forced end vote flag')
            except Exception:
                pass

    async def _resolve_worker(self, g: Game):
        """Background per-game resolver worker: process queued resolve requests sequentially."""
        try:
            g.log("BACKGROUND WORKER: Starting resolve worker")
            q = getattr(g, '_resolve_queue', None)
            if q is None:
                g.log("BACKGROUND WORKER: No resolve queue found, exiting")
                return
            
            item_count = 0
            while True:
                try:
                    g.log(f"BACKGROUND WORKER: Waiting for queue item (processed: {item_count})")
                    item = await q.get()
                    item_count += 1
                    g.log(f"BACKGROUND WORKER: Got queue item #{item_count}")
                except asyncio.CancelledError:
                    g.log("BACKGROUND WORKER: Cancelled, exiting")
                    return
                except Exception as e:
                    g.log(f"BACKGROUND WORKER: Queue get failed: {e}, exiting")
                    return
                    
                channel, fut = None, None
                try:
                    try:
                        channel, fut = item
                    except Exception:
                        channel = item
                        fut = None
                    
                    # Check invalidation flags before processing
                    emergency_reset = getattr(g, '_emergency_vote_reset', False)
                    vote_invalidated = getattr(g, '_vote_invalidated_by_guess', False)
                    if emergency_reset or vote_invalidated:
                        g.log(f"BACKGROUND WORKER: Skipping item #{item_count} due to invalidation flags - emergency_reset={emergency_reset}, vote_invalidated={vote_invalidated}")
                        if fut is not None and not fut.done():
                            try:
                                fut.set_result(False)
                            except Exception:
                                pass
                        continue
                        
                    g.log(f"BACKGROUND WORKER: Processing item #{item_count} - about to call _do_resolve_pending_votes")
                    g.log("VOTE RESOLUTION CALL: from background worker")
                    # perform the actual resolution (uses existing implementation)
                    try:
                        await self._do_resolve_pending_votes(g, channel)
                        g.log(f"BACKGROUND WORKER: Resolution completed for item #{item_count}")
                        if fut is not None and not fut.done():
                            try:
                                fut.set_result(True)
                            except Exception:
                                pass
                    except Exception as e:
                        g.log(f"BACKGROUND WORKER: Resolution failed for item #{item_count}: {e}")
                        try:
                            if fut is not None and not fut.done():
                                fut.set_exception(e)
                        except Exception:
                            pass
                finally:
                    try:
                        q.task_done()
                    except Exception:
                        pass

        except Exception:
            # If the worker loop throws unexpectedly, exit the worker gracefully.
            try:
                return
            except Exception:
                return

    async def _resolve_pending_votes(self, g: Game, channel: Optional[discord.TextChannel], wait: bool = True):
        """Enqueue a resolve request for the game. If wait is True, await completion.

        This serializes concurrent resolve requests via a per-game queue/worker so
        timeout and admin-forced resolves never race.
        """
        # CRITICAL: Check if game was force-closed
        if g.phase == Phase.CLOSED:
            g.log("Vote resolution request blocked: game force-closed")
            return
            
        fut = None
        try:
            # ensure per-game queue and worker
            q = getattr(g, '_resolve_queue', None)
            if q is None:
                try:
                    g._resolve_queue = asyncio.Queue()
                    q = g._resolve_queue
                except Exception as e:
                    logging.getLogger(__name__).warning(f"Failed to create resolve queue: {e}")
                    q = None
            # ensure worker task
            try:
                wt = getattr(g, '_resolve_worker_task', None)
            except Exception:
                wt = None
            if q is not None and (not wt or getattr(wt, 'done', lambda: False)()):
                try:
                    g._resolve_worker_task = asyncio.create_task(self._resolve_worker(g))
                except Exception:
                    try:
                        loop = asyncio.get_running_loop()
                        g._resolve_worker_task = loop.create_task(self._resolve_worker(g))
                    except Exception as e:
                        logging.getLogger(__name__).warning(f"Failed to create resolve worker: {e}")
                        g._resolve_worker_task = None

            # create a future for the caller to await completion if requested
            try:
                loop = asyncio.get_running_loop()
                fut = loop.create_future()
            except Exception as e:
                logging.getLogger(__name__).warning(f"Failed to create future: {e}")
                fut = None

            # enqueue
            try:
                if q is not None:
                    await q.put((channel, fut))
                else:
                    # fallback: call immediately if queue could not be created
                    g.log("VOTE RESOLUTION CALL: from _resolve_pending_votes (queue fallback)")
                    await self._do_resolve_pending_votes(g, channel)
                    if fut is not None and not fut.done():
                        fut.set_result(True)
                    return
            except Exception as e:
                # fallback to immediate call
                logging.getLogger(__name__).warning(f"Failed to enqueue resolve request: {e}")
                try:
                    g.log("VOTE RESOLUTION CALL: from _resolve_pending_votes (enqueue fallback)")
                    await self._do_resolve_pending_votes(g, channel)
                    if fut is not None and not fut.done():
                        fut.set_result(True)
                except Exception:
                    pass
                return

            if wait and fut is not None:
                try:
                    await fut
                except Exception:
                    # ignore errors from resolution for caller
                    pass
        except Exception as e:
            # best-effort: if enqueuing fails, attempt direct resolution
            logging.getLogger(__name__).error(f"Unexpected error in _resolve_pending_votes: {e}")
            try:
                g.log("VOTE RESOLUTION CALL: from _resolve_pending_votes (error fallback)")
                await self._do_resolve_pending_votes(g, channel)
                if fut is not None and not fut.done():
                    fut.set_result(True)
            except Exception:
                pass

        finally:
            # Always clear vote resolution in-progress flag
            try:
                g._vote_resolution_in_progress = False
            except Exception:
                pass

    async def _retry_resolve_after_lock(self, g: Game, channel: Optional[discord.TextChannel], attempts: int = 10, delay: float = 1.0):
        """Background retry helper: attempt to resolve pending votes a few times after a timed-out lock wait.

        Uses incremental backoff between attempts and logs progress. If the per-game
        resolution lock becomes free during retries, this helper calls
        _resolve_pending_votes() (fire-and-forget) and returns.
        """
        try:
            for attempt in range(attempts):
                # Check for invalidation before each attempt
                try:
                    if hasattr(g, '_vote_invalidated_by_guess') and g._vote_invalidated_by_guess:
                        g.log(f"Retry resolve: Aborted attempt {attempt+1} - votes invalidated by guess action")
                        return
                    if hasattr(g, '_emergency_vote_reset') and g._emergency_vote_reset:
                        g.log(f"Retry resolve: Aborted attempt {attempt+1} - emergency vote reset active")
                        return
                except Exception:
                    pass
                
                try:
                    # backoff: gradually increase sleep time slightly per attempt
                    wait = delay * (1 + attempt * 0.25)
                    await asyncio.sleep(wait)
                except Exception:
                    pass

                # Check invalidation again after sleep
                try:
                    if hasattr(g, '_vote_invalidated_by_guess') and g._vote_invalidated_by_guess:
                        g.log(f"Retry resolve: Aborted attempt {attempt+1} after sleep - votes invalidated by guess action")
                        return
                    if hasattr(g, '_emergency_vote_reset') and g._emergency_vote_reset:
                        g.log(f"Retry resolve: Aborted attempt {attempt+1} after sleep - emergency vote reset active")
                        return
                except Exception:
                    pass

                # If lock not present or not locked, attempt resolution
                try:
                    lock = getattr(g, '_vote_resolution_lock', None)
                except Exception:
                    lock = None
                locked = False
                try:
                    if lock and getattr(lock, 'locked', None):
                        locked = lock.locked()
                except Exception:
                    locked = False

                try:
                    g.log(f"Retry resolve after lock: attempt {attempt+1}/{attempts} (locked={locked})")
                except Exception:
                    pass

                if not locked:
                    try:
                        # Final invalidation check before attempting resolution
                        if hasattr(g, '_vote_invalidated_by_guess') and g._vote_invalidated_by_guess:
                            g.log(f"Retry resolve: Skipping attempt {attempt+1} - votes invalidated by guess action")
                            return
                        if hasattr(g, '_emergency_vote_reset') and g._emergency_vote_reset:
                            g.log(f"Retry resolve: Skipping attempt {attempt+1} - emergency vote reset active")
                            return
                            
                        # call the resolver; it will guard with lock itself
                        await self._resolve_pending_votes(g, channel)
                    except Exception:
                        try:
                            g.log('Retry resolve after lock failed during attempt')
                        except Exception:
                            pass
                    return

            # final give-up: log and schedule a last-ditch delayed single attempt after a longer pause
            # BUT ONLY if not invalidated
            try:
                if hasattr(g, '_vote_invalidated_by_guess') and g._vote_invalidated_by_guess:
                    g.log('Retry resolve after lock: Skipping final attempt - votes invalidated by guess action')
                    return
                if hasattr(g, '_emergency_vote_reset') and g._emergency_vote_reset:
                    g.log('Retry resolve after lock: Skipping final attempt - emergency vote reset active')
                    return
                    
                g.log('Retry resolve after lock: giving up after attempts; scheduling final delayed attempt')
            except Exception:
                pass

            try:
                # schedule one final attempt after a longer delay (non-blocking)
                def _schedule_final():
                    try:
                        # Check invalidation in the final scheduled attempt
                        if hasattr(g, '_vote_invalidated_by_guess') and g._vote_invalidated_by_guess:
                            try:
                                g.log('Final scheduled retry: Skipped - votes invalidated by guess action')
                            except Exception:
                                pass
                            return
                        if hasattr(g, '_emergency_vote_reset') and g._emergency_vote_reset:
                            try:
                                g.log('Final scheduled retry: Skipped - emergency vote reset active')
                            except Exception:
                                pass
                            return
                            
                        asyncio.create_task(self._resolve_pending_votes(g, channel))
                    except Exception:
                        try:
                            g.log('Final scheduled retry failed to schedule')
                        except Exception:
                            pass

                try:
                    # try schedule on the running loop
                    loop = asyncio.get_running_loop()
                    loop.call_later(max(5.0, delay * attempts), _schedule_final)
                except Exception:
                    # fallback: fire-and-forget a sleep+call in a task
                    async def _delayed():
                        try:
                            await asyncio.sleep(max(5.0, delay * attempts))
                            await self._resolve_pending_votes(g, channel)
                        except Exception:
                            try:
                                g.log('Final delayed retry failed')
                            except Exception:
                                pass

                    try:
                        asyncio.create_task(_delayed())
                    except Exception:
                        pass
            except Exception:
                pass
        except Exception:
            try:
                g.log('Unexpected error in _retry_resolve_after_lock')
            except Exception:
                pass

    class VotingView(ui.View):
        def __init__(self, timeout: int, game: Game, channel: discord.TextChannel, options: List[List[discord.SelectOption]]):
            super().__init__(timeout=timeout)
            self.game = game
            self.channel = channel
            # Store the current vote session ID to validate this view is still valid
            self.vote_session_id = getattr(game, '_current_vote_session_id', None)
            # options is a list of option-lists (chunks); add a select per chunk
            # Defensive: if the game's runtime flag disallows abstain, remove any abstain option
            try:
                allow_abstain = getattr(game, '_runtime_allow_abstain', True)
            except Exception:
                allow_abstain = True
            for idx, opts in enumerate(options):
                # make a shallow copy to avoid mutating caller-supplied lists
                safe_opts = list(opts) if isinstance(opts, list) else opts
                if not allow_abstain:
                    try:
                        safe_opts = [o for o in safe_opts if getattr(o, 'value', None) != '__abstain__']
                    except Exception:
                        # if filtering fails for any reason, leave options as-is
                        pass
                try:
                    self.add_item(self.VoteSelect(options=safe_opts, chunk_idx=idx))
                except Exception:
                    # if adding a select fails, continue
                    pass

        async def on_timeout(self):
            """Called when the view times out. Resolve pending votes."""
            try:
                # CRITICAL: Log timeout occurrence with session info
                current_session_id = getattr(self.game, '_current_vote_session_id', None)
                try:
                    self.game.log(f'VOTING VIEW TIMEOUT: session={self.vote_session_id}, current_session={current_session_id}, game_phase={self.game.phase}')
                    self.game.log(f'TIMEOUT FLAGS: emergency_reset={getattr(self.game, "_emergency_vote_reset", False)}, vote_invalidated={getattr(self.game, "_vote_invalidated_by_guess", False)}, forced_end={getattr(self.game, "_forced_end_vote", False)}')
                except Exception:
                    pass
                
                # CRITICAL: Check if vote time has actually elapsed
                vote_started = getattr(self.game, '_day_vote_started_at', None)
                vote_duration = getattr(self.game, '_runtime_day_vote_timeout', None)
                
                if vote_started and vote_duration:
                    import time
                    elapsed = time.time() - vote_started
                    if elapsed < (vote_duration - 5):  # Allow 5 second buffer
                        try:
                            self.game.log(f'VOTING VIEW TIMEOUT: Blocked - timeout occurred too early! elapsed={elapsed:.1f}s, expected={vote_duration}s')
                        except Exception:
                            pass
                        return
                    else:
                        try:
                            self.game.log(f'VOTING VIEW TIMEOUT: Valid timeout - elapsed={elapsed:.1f}s, expected={vote_duration}s')
                        except Exception:
                            pass
                elif not vote_duration:
                    try:
                        self.game.log('VOTING VIEW TIMEOUT: No vote duration set - proceeding with timeout')
                    except Exception:
                        pass
                else:
                    try:
                        self.game.log(f'VOTING VIEW TIMEOUT: No vote start time - vote_started={vote_started}, vote_duration={vote_duration}')
                    except Exception:
                        pass
                
                # Check for emergency vote reset (highest priority)
                emergency_reset = getattr(self.game, '_emergency_vote_reset', False)
                forced_end = getattr(self.game, '_forced_end_vote', False)
                
                # CRITICAL: Only proceed with timeout if it's legitimate or forced
                if emergency_reset and not forced_end:
                    try:
                        self.game.log('EMERGENCY: VotingView timeout blocked - emergency reset in progress (not forced)')
                    except Exception:
                        pass
                    return
                elif forced_end:
                    try:
                        self.game.log('VOTING VIEW TIMEOUT: Processing forced end timeout')
                    except Exception:
                        pass
                else:
                    try:
                        self.game.log('VOTING VIEW TIMEOUT: Processing legitimate timeout')
                    except Exception:
                        pass
                
                # Check if this view was marked as emergency invalidated
                if getattr(self, '_emergency_invalidated', False):
                    try:
                        self.game.log('EMERGENCY: VotingView timeout blocked - view emergency invalidated')
                    except Exception:
                        pass
                    return
                
                # Check if this view was invalidated by a guesser action
                if getattr(self, '_invalidated_for_guess', False):
                    try:
                        self.game.log('VotingView timed out but was invalidated by guess - skipping resolution')
                    except Exception:
                        pass
                    return
                    
                # Check if this view belongs to an old vote session
                if self.vote_session_id and current_session_id and self.vote_session_id != current_session_id:
                    try:
                        self.game.log(f'VotingView timed out but belongs to old session {self.vote_session_id} (current: {current_session_id}) - skipping resolution')
                    except Exception:
                        pass
                    return
                    
                # Check if all votes were invalidated by a guesser action
                # BUT allow if we are in re-vote phase after guess OR if forced end is active
                vote_invalidated = getattr(self.game, '_vote_invalidated_by_guess', False)
                in_re_vote = getattr(self.game, '_in_re_vote_after_guess', False)
                forced_end = getattr(self.game, '_forced_end_vote', False)
                
                if vote_invalidated and not in_re_vote and not forced_end:
                    try:
                        self.game.log('VotingView timed out but votes invalidated by guess (not in re-vote, not forced) - skipping resolution')
                    except Exception:
                        pass
                    return
                elif vote_invalidated and (in_re_vote or forced_end):
                    try:
                        self.game.log(f'VotingView timed out - votes were invalidated but proceeding (re_vote={in_re_vote}, forced={forced_end})')
                    except Exception:
                        pass
                
                # CRITICAL: Check if game phase is not VOTE - this prevents resolution during wrong phase
                if self.game.phase not in (Phase.VOTE,):
                    try:
                        self.game.log(f'VotingView timed out but game phase is {self.game.phase} (not VOTE) - skipping resolution')
                    except Exception:
                        pass
                    return
                    
                # Check if game was force-closed
                if self.game.phase == Phase.CLOSED:
                    try:
                        self.game.log('VotingView timeout aborted: game force-closed')
                    except Exception:
                        pass
                    return
                    
                # Get reference to the cog through the bot
                cog = None
                try:
                    if hasattr(self.game, '_bot'):
                        bot = self.game._bot
                        if bot:
                            cog = bot.get_cog('WerewolfCog')
                except Exception:
                    pass
                
                if cog:
                    try:
                        self.game.log(f'VotingView timed out - resolving pending votes for session {self.vote_session_id}')
                        self.game.log("VOTE RESOLUTION CALL: from VotingView.on_timeout()")
                    except Exception:
                        pass
                    try:
                        await cog._resolve_pending_votes(self.game, self.channel)
                    except Exception as e:
                        try:
                            self.game.log(f'Error resolving votes on timeout: {e}')
                        except Exception:
                            pass
            except Exception:
                pass

        class VoteSelect(ui.Select):
            def __init__(self, options: List[discord.SelectOption], chunk_idx: int = 0):
                # name the placeholder to indicate chunk if multiple selects
                try:
                    placeholders = msg('vote_placeholders')
                    if chunk_idx > 0:
                        placeholder = placeholders[1].format(page=chunk_idx+1)
                    else:
                        placeholder = placeholders[0]
                except Exception:
                    if chunk_idx > 0:
                        placeholder = f'Vote for a player (page {chunk_idx+1})...'
                    else:
                        placeholder = 'Vote for a player...'
                super().__init__(placeholder=placeholder, min_values=1, max_values=1, options=options)

            async def callback(self, interaction: discord.Interaction):
                view: WerewolfCog.VotingView = self.view  # type: ignore
                user_id = str(interaction.user.id)
                
                # Debug log for revote interaction
                try:
                    view.game.log(f"VOTE CALLBACK: User {user_id} attempting to vote, game phase: {view.game.phase}")
                    view.game.log(f"VOTE CALLBACK: Current pending votes: {getattr(view.game, '_pending_votes', {})}")
                except Exception:
                    pass
                
                # Check if voting has been force-ended
                try:
                    if getattr(view.game, '_forced_end_vote', False) or getattr(view, '_forced_disabled', False):
                        try:
                            await interaction.response.send_message('投票は管理者により強制終了されました。', ephemeral=True)
                        except Exception:
                            pass
                        return
                except Exception:
                    pass
                
                # Immediately ACK the interaction to avoid "This interaction failed".
                # Use a thinking-defer so the client shows the spinner (thinking) while we process.
                sent_initial_ack = False
                try:
                    try:
                        await interaction.response.defer(thinking=True, ephemeral=True)
                        sent_initial_ack = True
                    except Exception:
                        # fallback: try a plain defer without thinking
                        try:
                            await interaction.response.defer(ephemeral=True)
                            sent_initial_ack = True
                        except Exception:
                            sent_initial_ack = False
                except Exception:
                    sent_initial_ack = False

                selected = self.values[0]
                # check that voter is alive
                if not view.game.players.get(user_id) or not view.game.players.get(user_id).alive:
                    try:
                        await interaction.followup.send('You are dead and cannot vote.', ephemeral=True)
                    except Exception:
                        try:
                            await interaction.response.send_message('You are dead and cannot vote.', ephemeral=True)
                        except Exception:
                            pass
                    return
                # If this view was invalidated by a guesser action, reject selection
                try:
                    if getattr(view, '_invalidated_for_guess', False):
                        try:
                            await interaction.followup.send(msg('guess_invalid_old_vote_ui'), ephemeral=True)
                        except Exception:
                            try:
                                await interaction.response.send_message(msg('guess_invalid_old_vote_ui'), ephemeral=True)
                            except Exception:
                                pass
                        return
                except Exception:
                    pass
                # translate abstain marker and cast the vote (with short retry) so it can be changed later
                # record pending vote immediately so timeout handler can pick it up
                if selected == '__abstain__':
                    view.game._pending_votes[user_id] = '__abstain__'
                    target = None
                    try:
                        view.game.log(f"DEBUG VOTE: Player {user_id} voted to abstain")
                    except Exception:
                        pass
                else:
                    view.game._pending_votes[user_id] = selected
                    target = selected
                    try:
                        view.game.log(f"DEBUG VOTE: Player {user_id} voted for {selected}")
                    except Exception:
                        pass

                # Try to register vote with longer retries (useful if engine briefly flips phase)
                registered = False
                try:
                    # retry loop: up to ~3.0s total (30 * 0.1s)
                    for _ in range(30):
                        try:
                            ok = view.game.cast_vote(user_id, target)
                            if ok:
                                registered = True
                                break
                            # if cast_vote returned False, sometimes the engine may still be in DAY phase
                            # Attempt to advance DAY->VOTE if appropriate, then retry
                            try:
                                if view.game.phase == Phase.DAY:
                                    try:
                                        view.game.start_day_vote()
                                    except Exception:
                                        # ignore failures to transition
                                        pass
                            except Exception:
                                pass
                        except Exception as e:
                            try:
                                view.game.log(f"cast_vote exception for {user_id}: {e}")
                            except Exception:
                                pass
                        # If not registered yet, wait briefly before retrying
                        try:
                            await asyncio.sleep(0.1)
                        except Exception:
                            pass
                except Exception:
                    pass

                # Update the initial ephemeral ack to a confirmation message when possible.
                try:
                    if sent_initial_ack:
                        # Create personalized vote confirmation message
                        if selected == '__abstain__':
                            try:
                                confirmation_msg = msg('vote_recorded_abstain')
                            except Exception:
                                confirmation_msg = '棄権に投票しました。制限時間まで変更できます。'
                        else:
                            # Find the target player name
                            target_name = selected  # fallback to ID
                            try:
                                target_player = view.game.players.get(selected)
                                if target_player:
                                    target_name = target_player.name
                            except Exception:
                                pass
                            
                            try:
                                confirmation_msg = msg('vote_recorded_target', target=target_name)
                            except Exception:
                                confirmation_msg = f'{target_name}に投票しました。制限時間まで変更できます。'
                        
                        try:
                            # edit the original/deferred response to show final confirmation
                            await interaction.edit_original_response(content=confirmation_msg)
                        except Exception:
                            # If edit fails, try followup send (ephemeral)
                            try:
                                await interaction.followup.send(confirmation_msg, ephemeral=True)
                            except Exception:
                                # DM fallback
                                try:
                                    await interaction.user.send(confirmation_msg)
                                except Exception:
                                    pass
                except Exception:
                    # Best-effort only; don't raise
                    try:
                        # Fallback to generic message
                        await interaction.user.send(msg('vote_recorded'))
                    except Exception:
                        pass


def run_bot(token: str):
    """Run the bot using asyncio.run to allow awaiting add_cog before start."""
    async def _run():
        # Configure logging to help debug interaction failures
        logging.basicConfig(level=logging.INFO)
        intents = discord.Intents.default()
        # Require members intent for fetching guild members and voice-state events.
        # Note: you must also enable "Server Members Intent" in the Discord Developer
        # Portal for your bot application if not already enabled.
        try:
            intents.members = True
        except Exception:
            pass
        try:
            intents.voice_states = True
        except Exception:
            pass
        bot = commands.Bot(command_prefix="!", intents=intents)

        @bot.event
        async def on_ready():
            print(f"Bot ready: {bot.user}")
            # Sync app commands. For fast development you can set WW_GUILD_ID env var
            # to the guild id to sync only to that guild (instant). Otherwise sync global commands.
            import os
            try:
                guild_id = os.environ.get('WW_GUILD_ID')
                if guild_id:
                    try:
                        gid = int(guild_id)
                        guild = discord.Object(id=gid)
                        # Ensure global commands are copied into the guild for instant availability
                        try:
                            bot.tree.copy_global_to(guild=guild)
                        except Exception:
                            pass
                        await bot.tree.sync(guild=guild)
                        print(f"Synced commands to guild {gid}")
                    except Exception as e:
                        print(f"Failed to sync to guild {guild_id}: {e}")
                else:
                    try:
                        await bot.tree.sync()
                        print("Synced global commands")
                    except Exception as e:
                        print(f"Failed to sync global commands: {e}")
            except Exception as e:
                print("Error during command sync:", e)

            # Diagnostic: list registered app commands currently in the tree
            try:
                cmds = bot.tree.get_commands()
                print("Registered app commands:")
                for c in cmds:
                    try:
                        print(f" - {c.name} (guild: {getattr(c, 'guild', None)})")
                    except Exception:
                        print(f" - {c.name}")
            except Exception as e:
                print("Failed to list app commands:", e)

        # Log every incoming interaction for debugging (shows if Discord sends an interaction)
        @bot.event
        async def on_interaction(interaction: discord.Interaction):
            try:
                print(f"Received interaction: type={type(interaction)} cmd={getattr(interaction, 'command', None)} user={getattr(interaction.user, 'id', None)} channel={getattr(interaction, 'channel_id', None)}")
            except Exception:
                pass

        # on_message listener removed from run loop; handled in Cog

        # Add cog (await because add_cog may be coroutine in discord.py v2)
        await bot.add_cog(WerewolfCog(bot))
        # Try to load WordWolfCog if available (importlib fallback)
        try:
            import importlib
            spec = importlib.util.find_spec('.wordwolf_cog', package=__package__)
            if spec is not None:
                mod = importlib.import_module(f'{__package__}.wordwolf_cog')
            else:
                mod = importlib.import_module('wordwolf_cog')
            WordWolfCogImpl = getattr(mod, 'WordWolfCog', None)
            if WordWolfCogImpl:
                try:
                    await bot.add_cog(WordWolfCogImpl(bot))
                except Exception:
                    pass
        except Exception:
            pass

        # Global handler for app command errors to print full tracebacks for debugging
        @bot.tree.error
        async def on_app_command_error(interaction: discord.Interaction, error: Exception):
            try:
                print(f"App command error for {interaction.command}: {error}")
                import traceback as _tb
                _tb.print_exception(type(error), error, error.__traceback__)
                # Also try to notify the user if interaction is valid
                try:
                    if interaction and not interaction.response.is_done():
                        await interaction.response.send_message("内部エラーが発生しました。管理者に連絡してください。", ephemeral=True)
                except Exception:
                    pass
            except Exception:
                # Ensure that error handler never raises
                pass

        try:
            await bot.start(token)
        finally:
            # During cancellation/shutdown some tasks may raise CancelledError while
            # closing the bot; swallow CancelledError here so the process exits cleanly
            try:
                await bot.close()
            except asyncio.CancelledError:
                # suppress cancellation during close
                pass
            except Exception:
                # best-effort close: ignore other errors during shutdown
                pass

    asyncio.run(_run())


if __name__ == '__main__':
    import os
    token = os.environ.get('WW_BOT_TOKEN')
    # Fallback: try to read token from various possible locations
    if not token:
        # Try multiple possible paths for token file
        possible_paths = [
            r"C:\Users\yu-ma\OneDrive\デスクトップ\recorder\werewolf\token.txt",
            r"C:\Users\yu-ma\Desktop\recorder\token.txt", 
            "./token.txt",
            "../token.txt"
        ]
        
        for fallback_path in possible_paths:
            try:
                if os.path.exists(fallback_path):
                    with open(fallback_path, 'r', encoding='utf-8') as f:
                        token = f.read().strip()
                        if token:  # Only accept non-empty tokens
                            print(f"Loaded bot token from {fallback_path}")
                            break
            except Exception as e:
                print(f"Failed to read token file {fallback_path}: {e}")
                continue

    if not token:
        print('No bot token found. Set WW_BOT_TOKEN env var or place token.txt in one of the expected locations.')
        print('Expected locations:')
        print('  - C:\\Users\\yu-ma\\OneDrive\\デスクトップ\\recorder\\werewolf\\token.txt')
        print('  - C:\\Users\\yu-ma\\Desktop\\recorder\\token.txt')
        print('  - ./token.txt')
        print('  - ../token.txt')
    else:
        run_bot(token)
