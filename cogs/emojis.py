"""Arqon Bot — custom emoji management.

Lets admins plug their own Discord emojis into the bot without touching code:

    /emojilist              → live view of every emoji slot + current value
    /editemoji <name> <emoji>  → bind a custom emoji (e.g. <:arqon_check:123…>)
    /editemoji <name> reset → back to the unicode default

Overrides persist to data/emojis.json and apply instantly — no restart.
Users can also hard-code overrides via EMOJI_* variables in .env
(e.g. EMOJI_SUCCESS=<:arqon_yes:1234567890123456789>).
"""
import discord
from discord.ext import commands

from utils import is_admin, get_logger, success_embed, error_embed, info_embed
from utils.emoji import (
    E, emojilist, set_override, reset_override, is_custom_emoji, DEFAULT_EMOJIS,
)
from config import settings

logger = get_logger("emojis")


class EmojisCog(commands.Cog):
    """Admin tooling for the custom emoji system."""

    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="emojilist", description="Show every emoji slot the bot uses")
    async def emojilist_cmd(self, ctx: commands.Context):
        current = emojilist()
        custom = {k: v for k, v in current.items() if is_custom_emoji(v)}
        lines = []
        for name, val in current.items():
            marker = E("sparkles") if is_custom_emoji(val) else "·"
            lines.append(f"{marker} `{name}` → {val}")

        embed = info_embed(
            f"{E('gem')} Emoji Map ({len(current)} slots)",
            f"{len(custom)} custom emoji(s) active\n\n" + "\n".join(lines[:60]),
        )
        embed.set_footer(text=f"{settings.BOT_FOOTER} • admin: /editemoji <name> <emoji>")
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="editemoji", description="Bind a custom Discord emoji to a bot slot (Admin)")
    @commands.guild_only()
    async def editemoji(self, ctx: commands.Context, name: str, emoji: str):
        if not is_admin(ctx.author):
            await ctx.send(embed=error_embed("Permission Denied", "Admin only."), ephemeral=True)
            return

        name = name.lower().strip()
        if name not in DEFAULT_EMOJIS:
            await ctx.send(embed=error_embed(
                "Unknown Slot",
                f"**{name}** isn't an emoji slot.\n"
                f"Valid slots: `/emojilist` — e.g. `success`, `vps`, `plans`, `invite`, `rocket`.",
            ), ephemeral=True)
            return

        arg = emoji.strip()
        if arg.lower() in {"reset", "none", "default"}:
            ok = await reset_override(name)
            if not ok:
                await ctx.send(embed=error_embed("Failed", "Could not write the emoji file (data/emojis.json)."),
                               ephemeral=True)
                return
            await ctx.send(embed=success_embed(
                f"{E('refresh')} Emoji Reset",
                f"`{name}` → back to default {E(name)}",
            ), ephemeral=True)
            return

        if not (is_custom_emoji(arg) or len(arg) <= 8):
            await ctx.send(embed=error_embed(
                "Invalid Emoji",
                "Send an actual custom emoji like `<:arqon_check:1234567890123456789>` "
                "(type `\\` before the emoji in chat to get that code), or a unicode emoji.",
            ), ephemeral=True)
            return

        ok = await set_override(name, arg)
        if not ok:
            await ctx.send(embed=error_embed("Failed", "Could not write the emoji file (data/emojis.json)."),
                           ephemeral=True)
            return

        await ctx.send(embed=success_embed(
            f"{E('sparkles')} Emoji Updated",
            f"`{name}` → {E(name)}\n"
            f"Applied instantly across the whole bot — no restart needed.",
        ), ephemeral=True)


async def setup(bot):
    await bot.add_cog(EmojisCog(bot))
