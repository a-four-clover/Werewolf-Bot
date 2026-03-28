"""
Utility functions for Discord werewolf bot.
"""
import logging
from typing import Optional
import discord


async def safe_interaction_send(interaction: discord.Interaction, content: Optional[str] = None, ephemeral: bool = True, channel: Optional[object] = None):
    """
    Safely send a message to a Discord interaction, with fallback options.
    Attempts interaction.response, then interaction.followup, then channel.send if provided.
    """
    try:
        if hasattr(interaction, 'response') and not interaction.response.is_done():
            await interaction.response.send_message(content=content, ephemeral=ephemeral)
            return
    except Exception:
        pass

    try:
        if hasattr(interaction, 'followup'):
            await interaction.followup.send(content=content, ephemeral=ephemeral)
            return
    except Exception:
        pass

    # Last resort: channel send if provided
    if channel:
        try:
            await channel.send(content=content)
        except Exception:
            pass


async def _ack_interaction(interaction: discord.Interaction, content: Optional[str] = None, ephemeral: bool = True):
    """Acknowledge a component interaction with safe fallback."""
    try:
        if hasattr(interaction, 'response') and not interaction.response.is_done():
            await interaction.response.send_message(content=content, ephemeral=ephemeral)
        else:
            if hasattr(interaction, 'followup'):
                await interaction.followup.send(content=content, ephemeral=ephemeral)
            else:
                await safe_interaction_send(interaction, content=content, ephemeral=ephemeral)
    except Exception:
        logging.getLogger(__name__).exception('_ack_interaction failed')


def _format_private_message_for_send(m):
    """Format a private message for sending to Discord."""
    # Handle i18n message format: {'key': 'message_key', 'params': {...}}
    if isinstance(m, dict) and 'key' in m:
        try:
            from .i18n import msg
        except ImportError:
            # Fallback for when relative import fails
            import sys
            import os
            sys.path.append(os.path.dirname(__file__))
            from i18n import msg
        key = m['key']
        params = m.get('params', {})
        return msg(key, **params)
    # Handle old format with header and body
    elif isinstance(m, dict) and 'header' in m and 'body' in m:
        return f"**{m['header']}**\n{m['body']}"
    # Fallback to string representation
    else:
        return str(m)


def _safe_display_name(obj) -> str:
    """Safely extract display name from Discord user/member object."""
    try:
        if hasattr(obj, 'display_name') and obj.display_name:
            return obj.display_name
        elif hasattr(obj, 'name') and obj.name:
            return obj.name
        elif hasattr(obj, 'id'):
            return str(obj.id)
        else:
            return str(obj)
    except Exception:
        return "Unknown"