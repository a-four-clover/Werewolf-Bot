"""
Guess functionality for werewolf game.
Handles /ww_guess command and related operations.
"""
import logging
import asyncio
from typing import Optional

import discord
from discord import app_commands, ui
from discord.ext import commands

from ..utils import _ack_interaction
from ..engine import Game, Phase
from ..i18n import msg


class GuessCog(commands.Cog):
    """Handles guess-related commands and operations."""
    
    def __init__(self, bot: commands.Bot, storage):
        self.bot = bot
        self.storage = storage

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
            import traceback
            error_details = f"Guess command error: {e}\n{traceback.format_exc()}"
            logging.getLogger(__name__).error(error_details)
            # Show user-friendly error but also provide debug info in console
            await interaction.followup.send(f"Debug: {str(e)[:100]}...", ephemeral=True)

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
        from ..guess_helpers import build_guess_options
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
                content = msg('night_choice_registered', target=str(self.player))
                await _ack_interaction(inter, content=content, ephemeral=True)

            @ui.select(custom_id='guess_select_role', placeholder=msg('guess_dm_header_roles_list'), min_values=1, max_values=1, options=role_opts)
            async def select_role(self, inter: discord.Interaction, select: ui.Select):
                self.role = select.values[0]
                content = msg('night_choice_registered', target=str(self.role))
                await _ack_interaction(inter, content=content, ephemeral=True)

            @ui.button(label='実行', style=discord.ButtonStyle.danger)
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
        lock = getattr(g, '_guess_lock', None) 
        
        async with lock if lock else asyncio.Lock():
            # TODO: implement _wait_view_with_pause logic
            await asyncio.sleep(0.1)  # Placeholder
            view_completed = True  # Placeholder
        
        # Check view completion and extract choices
        if not view_completed or not getattr(view, 'player') or not getattr(view, 'role'):
            await interaction.followup.send(msg('guess_command_dm_cancelled'), ephemeral=True)
            return
            
        chosen_player = view.player
        chosen_role = view.role
        
        # IMMEDIATE STEP: Destroy old voting system upon guess execution
        self._invalidate_voting_system(g)
        
        # TODO: Complete guess execution logic
        await interaction.followup.send("Guess command placeholder - implementation needed", ephemeral=True)