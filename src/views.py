"""
UI View components for werewolf Discord bot.
Contains all discord.ui.View and discord.ui.Select classes.
"""
from typing import Optional
import discord
from discord import ui

from .utils import _ack_interaction
from .engine import Game
from .i18n import msg


class ConfirmEndView(ui.View):
    """Confirmation view for ending night phase."""
    
    def __init__(self, owner_id: int, timeout: float = 60.0):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.result = None

    async def interaction_check(self, inter: discord.Interaction) -> bool:
        # only the invoking user may interact
        return inter.user.id == self.owner_id

    @ui.button(label='OK', style=discord.ButtonStyle.danger)
    async def ok(self, interaction_btn: discord.Interaction, button: ui.Button):
        self.result = True
        self.stop()
        try:
            await interaction_btn.response.edit_message(content=msg('confirm_force_night'), view=None)
        except Exception:
            try:
                await interaction_btn.response.send_message(msg('confirm_force_night'), ephemeral=True)
            except Exception:
                pass

    @ui.button(label='Cancel', style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction_btn: discord.Interaction, button: ui.Button):
        self.result = False
        self.stop()
        try:
            await interaction_btn.response.edit_message(content=msg('action_cancelled'), view=None)
        except Exception:
            try:
                await interaction_btn.response.send_message(msg('action_cancelled'), ephemeral=True)
            except Exception:
                pass


class ConfirmEndVoteView(ui.View):
    """Confirmation view for ending vote phase."""
    
    def __init__(self, owner_id: int, timeout: float = 60.0):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.result = None

    async def interaction_check(self, inter: discord.Interaction) -> bool:
        # only the invoking user may interact
        return inter.user.id == self.owner_id

    @ui.button(label='OK', style=discord.ButtonStyle.danger)
    async def ok(self, interaction_btn: discord.Interaction, button: ui.Button):
        self.result = True
        self.stop()
        try:
            await interaction_btn.response.edit_message(content=msg('end_vote_confirmed'), view=None)
        except Exception:
            try:
                await interaction_btn.response.send_message(msg('end_vote_confirmed'), ephemeral=True)
            except Exception:
                pass

    @ui.button(label='Cancel', style=discord.ButtonStyle.secondary)  
    async def cancel(self, interaction_btn: discord.Interaction, button: ui.Button):
        self.result = False
        self.stop()
        try:
            await interaction_btn.response.edit_message(content=msg('action_cancelled'), view=None)
        except Exception:
            try:
                await interaction_btn.response.send_message(msg('action_cancelled'), ephemeral=True)
            except Exception:
                pass


class SageActionView(ui.View):
    """View for sage night actions."""
    
    def __init__(self, g: Game, player_id: str, timeout=180):
        super().__init__(timeout=timeout)
        self.game = g
        self.player_id = player_id
        
        # Calculate remaining shields
        try:
            shields = int(getattr(g, '_sage_shields_left', {}).get(player_id, 0))
        except Exception:
            shields = 0
        self.shields = shields

    @ui.button(label='盾を使用', style=discord.ButtonStyle.primary)
    async def use_shield(self, interaction: discord.Interaction, button: ui.Button):
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            pass
            
        try:
            # Register shield usage
            self.game._pending_night_choices[self.player_id] = '__shield__'
            
            # Send confirmation
            try:
                await interaction.followup.send("盾を使用しました", ephemeral=True)
            except Exception:
                try:
                    await interaction.response.send_message("盾を使用しました", ephemeral=True)
                except Exception:
                    pass
                    
            # Attempt DM
            try:
                await interaction.user.send("盾を使用しました")
            except Exception:
                pass
                
        except Exception:
            try:
                self.game.log(f"Error handling sage shield use for {self.player_id}")
            except Exception:
                pass
                
        finally:
            # Signal completion
            try:
                ev = self.game._night_events.get(self.player_id)
                if ev:
                    ev.set()
            except Exception:
                pass
            self.stop()

    @ui.button(label='スキップ', style=discord.ButtonStyle.secondary)
    async def skip_action(self, interaction: discord.Interaction, button: ui.Button):
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            pass
            
        try:
            # Register skip
            self.game._pending_night_choices[self.player_id] = None
            
            # Send confirmation
            try:
                await interaction.followup.send("行動をスキップしました", ephemeral=True)
            except Exception:
                try:
                    await interaction.response.send_message("行動をスキップしました", ephemeral=True)
                except Exception:
                    pass
                    
        except Exception:
            try:
                self.game.log(f"Error handling sage skip for {self.player_id}")
            except Exception:
                pass
                
        finally:
            # Signal completion
            try:
                ev = self.game._night_events.get(self.player_id)
                if ev:
                    ev.set()
            except Exception:
                pass
            self.stop()


class NightSelectView(ui.View):
    """Generic night action selection view."""
    
    def __init__(self, g: Game, player_id: str, alive_opts: list, timeout=180):
        super().__init__(timeout=timeout)
        self.g = g
        self.player_id = player_id
        self.target_id = None
        
        # Add target selection dropdown
        self.add_item(TargetSelect(alive_opts, placeholder="対象を選択してください"))

    class TargetSelect(ui.Select):
        def __init__(self, options: list, placeholder: str):
            super().__init__(placeholder=placeholder, options=options[:25], min_values=1, max_values=1)

        async def callback(self, interaction: discord.Interaction):
            # Set target on parent view
            view = self.view
            view.target_id = self.values[0]
            
            await _ack_interaction(interaction, 
                content=f"対象: {self.values[0]} を選択しました", ephemeral=True)


class VotingView(ui.View):
    """Enhanced voting view for day phase with status updates."""
    
    def __init__(self, options: list, game_id: str, session_id: str, timeout=300):
        super().__init__(timeout=timeout)
        self.game_id = game_id
        self.session_id = session_id
        self._invalidated_for_guess = False
        self._emergency_invalidated = False
        self._old_session = False
        
        # Add vote selection dropdown
        if options:
            self.add_item(self.VoteSelect(options))

    class VoteSelect(ui.Select):
        def __init__(self, options: list):
            # Limit to Discord's maximum of 25 options
            super().__init__(
                placeholder=msg('vote_select_placeholder'),
                options=options[:25],
                min_values=1,
                max_values=1
            )

        async def callback(self, interaction: discord.Interaction):
            # Handle vote selection
            view = self.view
            
            # Check if view is invalidated
            if getattr(view, '_invalidated_for_guess', False):
                await _ack_interaction(interaction, content=msg('vote_invalidated'), ephemeral=True)
                return
                
            target_id = self.values[0]
            
            # Get game from storage to update vote
            try:
                from .storage import InMemoryStorage
                # Try to get storage from bot cog
                bot = interaction.client
                cog = None
                for cog_name in bot.cogs:
                    if 'werewolf' in cog_name.lower() or 'WerewolfCog' in cog_name:
                        cog = bot.get_cog(cog_name)
                        break
                
                if cog and hasattr(cog, 'storage'):
                    storage = cog.storage
                    g = storage.load_game(view.game_id)
                    
                    if g:
                        # Record the vote
                        pending_votes = getattr(g, '_pending_votes', {}) or {}
                        user_id = str(interaction.user.id)
                        
                        # Convert abstain to proper value
                        if target_id == 'abstain':
                            pending_votes[user_id] = '__abstain__'
                            vote_display = msg('vote_abstain_display')
                        else:
                            pending_votes[user_id] = target_id
                            # Get target player name
                            target_player = g.players.get(target_id)
                            vote_display = target_player.name if target_player else target_id
                        
                        g._pending_votes = pending_votes
                        storage.save_game(g)
                        
                        # Update the message with new voting status if possible
                        try:
                            if hasattr(cog, '_create_voting_status_embed'):
                                updated_embed = cog._create_voting_status_embed(g)
                                await interaction.response.edit_message(embed=updated_embed, view=view)
                                # Send confirmation message as followup
                                await interaction.followup.send(
                                    content=msg('vote_confirmation', target=vote_display), 
                                    ephemeral=True
                                )
                            else:
                                await _ack_interaction(interaction, 
                                    content=msg('vote_confirmation', target=vote_display), ephemeral=True)
                        except:
                            await _ack_interaction(interaction, 
                                content=msg('vote_confirmation', target=vote_display), ephemeral=True)
                    else:
                        await _ack_interaction(interaction, content=msg('game_not_found'), ephemeral=True)
                else:
                    await _ack_interaction(interaction, content=msg('system_error'), ephemeral=True)
                    
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Vote processing error: {e}")
                await _ack_interaction(interaction, 
                    content=msg('vote_processing_error'), ephemeral=True)


class BuskerNightView(ui.View):
    """Combined view for Evil Busker: attack selection + fake death button"""
    
    def __init__(self, game: Game, player_id: str, attack_options: list, can_use_fake: bool = False, uses_left: int = 0, timeout=180):
        super().__init__(timeout=timeout)
        self.game = game
        self.player_id = player_id
        self.selected_target = None
        self.can_use_fake = can_use_fake
        self.uses_left = uses_left
        
        # Add attack target selection
        if attack_options:
            self.add_item(BuskerTargetSelect(attack_options))

    @ui.button(label='実行', style=discord.ButtonStyle.danger)
    async def execute_attack(self, interaction: discord.Interaction, button: ui.Button):
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            pass
        
        if not self.selected_target:
            await _ack_interaction(interaction, content="対象を選択してください", ephemeral=True)
            return
        
        # Register attack choice
        try:
            self.game._pending_night_choices[self.player_id] = self.selected_target
        except Exception:
            pass
        
        # Send confirmation
        await _ack_interaction(interaction, content=f"攻撃を実行しました: {self.selected_target}", ephemeral=True)
        
        # Signal completion
        try:
            ev = self.game._night_events.get(self.player_id)
            if ev:
                ev.set()
        except Exception:
            pass
        self.stop()


class BuskerTargetSelect(ui.Select):
    """Target selection for busker night actions."""
    
    def __init__(self, options: list):
        super().__init__(
            placeholder="攻撃対象を選択してください",
            options=options[:25],
            min_values=1,
            max_values=1
        )

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        view.selected_target = self.values[0]
        
        await _ack_interaction(interaction, 
            content=f"対象: {self.values[0]} を選択しました", ephemeral=True)


class BuskerFakeDeathView(ui.View):
    """View for busker fake death action."""
    
    def __init__(self, game: Game, player_id: str, uses_left: int = 0, timeout=180):
        super().__init__(timeout=timeout)
        self.game = game
        self.player_id = player_id
        self.uses_left = uses_left

    @ui.button(label='偽装死を使用', style=discord.ButtonStyle.primary)
    async def use_fake_death(self, interaction: discord.Interaction, button: ui.Button):
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            pass
        
        # Register fake death usage
        try:
            self.game._pending_night_choices[self.player_id] = '__fake_death__'
        except Exception:
            pass
        
        await _ack_interaction(interaction, content="偽装死を使用しました", ephemeral=True)
        
        # Signal completion
        try:
            ev = self.game._night_events.get(self.player_id)
            if ev:
                ev.set()
        except Exception:
            pass
        self.stop()

    @ui.button(label='スキップ', style=discord.ButtonStyle.secondary)
    async def skip_fake_death(self, interaction: discord.Interaction, button: ui.Button):
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            pass
        
        # Register skip
        try:
            self.game._pending_night_choices[self.player_id] = None
        except Exception:
            pass
        
        await _ack_interaction(interaction, content="偽装死をスキップしました", ephemeral=True)
        
        # Signal completion
        try:
            ev = self.game._night_events.get(self.player_id)
            if ev:
                ev.set()
        except Exception:
            pass
        self.stop()


class StatsRecordConfirmView(ui.View):
    """統計記録の確認用View"""
    
    def __init__(self, owner_id: int, timeout: float = 300.0):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.result = None

    async def interaction_check(self, inter: discord.Interaction) -> bool:
        # オーナーのみ操作可能
        return inter.user.id == self.owner_id

    @ui.button(label=msg('stats_record_button'), style=discord.ButtonStyle.primary)
    async def record_stats(self, interaction: discord.Interaction, button: ui.Button):
        self.result = True
        self.stop()
        try:
            await interaction.response.edit_message(content=msg('stats_recorded'), view=None)
        except Exception:
            try:
                await interaction.response.send_message(msg('stats_recorded'), ephemeral=True)
            except Exception:
                pass

    @ui.button(label=msg('stats_skip_button'), style=discord.ButtonStyle.secondary)
    async def skip_record(self, interaction: discord.Interaction, button: ui.Button):
        self.result = False
        self.stop()
        try:
            await interaction.response.edit_message(content=msg('stats_not_recorded'), view=None)
        except Exception:
            try:
                await interaction.response.send_message(msg('stats_not_recorded'), ephemeral=True)
            except Exception:
                pass

    async def on_timeout(self):
        # タイムアウト時は記録しない
        self.result = False
        for item in self.children:
            item.disabled = True
        try:
            await self.message.edit(content=msg('stats_timeout'), view=self)
        except Exception:
            pass
    """View for busker revive action."""
    
    def __init__(self, game: Game, player_id: str, revive_options: list, timeout=180):
        super().__init__(timeout=timeout)
        self.game = game
        self.player_id = player_id
        self.selected_target = None
        
        # Add revive target selection
        if revive_options:
            self.add_item(BuskerReviveSelect(revive_options))

    @ui.button(label='蘇生実行', style=discord.ButtonStyle.success)
    async def execute_revive(self, interaction: discord.Interaction, button: ui.Button):
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            pass
        
        if not self.selected_target:
            await _ack_interaction(interaction, content="蘇生対象を選択してください", ephemeral=True)
            return
        
        # Register revive choice
        try:
            self.game._pending_night_choices[self.player_id] = self.selected_target
        except Exception:
            pass
        
        await _ack_interaction(interaction, content=f"蘇生を実行しました: {self.selected_target}", ephemeral=True)
        
        # Signal completion
        try:
            ev = self.game._night_events.get(self.player_id)
            if ev:
                ev.set()
        except Exception:
            pass
        self.stop()


class BuskerReviveSelect(ui.Select):
    """Target selection for busker revive actions."""
    
    def __init__(self, options: list):
        super().__init__(
            placeholder="蘇生対象を選択してください",
            options=options[:25],
            min_values=1,
            max_values=1
        )

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        view.selected_target = self.values[0]
        
        await _ack_interaction(interaction, 
            content=f"蘇生対象: {self.values[0]} を選択しました", ephemeral=True)