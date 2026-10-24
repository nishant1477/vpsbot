import discord
from discord.ext import commands
from discord.ui import View, Button, Select, Modal, TextInput
from typing import Optional, List
import asyncio
import io
from datetime import datetime, timezone

from utils import (
    ticket_embed, success_embed, error_embed, info_embed,
    has_permission, is_staff, PermissionLevel,
    get_logger, validate_ticket_subject, validate_ticket_description,
    log_ticket_event,
)
from database import (
    async_session_maker, Ticket, TicketStatus, TicketMessage,
    TicketParticipant, GuildConfig, User, get_or_create_user,
)
from config import settings
from sqlalchemy import select, func, desc

logger = get_logger("tickets")

CATEGORY_OPTIONS = [
    discord.SelectOption(label="General Support", value="general", description="General questions and support", emoji="🎧"),
    discord.SelectOption(label="Technical Support", value="technical", description="Technical issues and troubleshooting", emoji="🔧"),
    discord.SelectOption(label="VPS Issues", value="vps", description="VPS-related problems", emoji="🖥️"),
    discord.SelectOption(label="Minecraft Server", value="minecraft", description="Minecraft server issues", emoji="⛏️"),
    discord.SelectOption(label="Billing", value="billing", description="Billing and payment questions", emoji="💳"),
    discord.SelectOption(label="Abuse Report", value="abuse", description="Report abuse or violations", emoji="🚨"),
    discord.SelectOption(label="Other", value="other", description="Other inquiries", emoji="❓"),
]


def _staff_role_ids() -> List[int]:
    ids = list(settings.STAFF_ROLE_IDS) + list(settings.TICKET_STAFF_ROLE_IDS)
    return list(dict.fromkeys(ids))


def _is_ticket_staff(member: discord.Member) -> bool:
    if isinstance(member, discord.Member) and is_staff(member):
        return True
    return False


class TicketCategorySelect(Select):
    def __init__(self):
        super().__init__(placeholder="📂 Select a category...", options=CATEGORY_OPTIONS,
                         custom_id="arqon:ticket_category_select")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(TicketCreateModal(self.values[0]))


class TicketPanelView(View):
    """Persistent panel view posted with !ticket / !setup-tickets."""
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketCategorySelect())


class TicketCreateModal(Modal, title="Create Ticket"):
    def __init__(self, category: str):
        super().__init__(custom_id=f"arqon:ticket_create_{category}")
        self.category = category
        self.subject = TextInput(label="Subject", placeholder="Brief description of your issue",
                                 required=True, max_length=100)
        self.description = TextInput(label="Description", placeholder="Describe your issue in detail...",
                                     style=discord.TextStyle.paragraph, required=True, max_length=2000)
        self.add_item(self.subject)
        self.add_item(self.description)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            subject = validate_ticket_subject(self.subject.value)
            description = validate_ticket_description(self.description.value)
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed("Invalid Input", str(exc)), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild

        async with async_session_maker() as session:
            guild_config = (await session.execute(
                select(GuildConfig).where(GuildConfig.guild_id == guild.id))).scalar_one_or_none()

            if not guild_config or not guild_config.ticket_category_id:
                await interaction.followup.send(
                    embed=error_embed("Not Configured",
                                      "Ticket system is not set up yet. An admin must run `!setup-tickets`."),
                    ephemeral=True)
                return

            category_channel = guild.get_channel(guild_config.ticket_category_id)
            if not category_channel:
                await interaction.followup.send(
                    embed=error_embed("Category Missing", "The ticket category no longer exists. Re-run `!setup-tickets`."),
                    ephemeral=True)
                return

            # per-user open ticket limit
            max_tickets = guild_config.max_tickets_per_user or settings.MAX_TICKETS_PER_USER
            open_count = len((await session.execute(
                select(Ticket.id).where(
                    Ticket.guild_id == guild.id,
                    Ticket.creator_discord_id == interaction.user.id,
                    Ticket.status.in_([TicketStatus.OPEN, TicketStatus.CLAIMED]),
                ))).all())
            if open_count >= max_tickets:
                await interaction.followup.send(
                    embed=error_embed("Limit Reached", f"You already have **{open_count}** open ticket(s)."),
                    ephemeral=True)
                return

            ticket_number = ((await session.execute(
                select(func.max(Ticket.ticket_number)).where(Ticket.guild_id == guild.id))).scalar() or 0) + 1

            db_user = await get_or_create_user(session, interaction.user.id, interaction.user.name)

            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                interaction.user: discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, attach_files=True, embed_links=True, read_message_history=True),
                guild.me: discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, manage_channels=True, manage_messages=True),
            }
            for role_id in _staff_role_ids():
                role = guild.get_role(role_id)
                if role:
                    overwrites[role] = discord.PermissionOverwrite(
                        view_channel=True, send_messages=True, manage_messages=True, read_message_history=True)

            ticket_channel = await category_channel.create_text_channel(
                name=f"ticket-{ticket_number:04d}-{interaction.user.name}",
                overwrites=overwrites,
                topic=f"Ticket #{ticket_number} | {interaction.user} ({interaction.user.id}) | Category: {self.category}",
            )

            ticket = Ticket(
                ticket_number=ticket_number,
                guild_id=guild.id,
                channel_id=ticket_channel.id,
                creator_id=db_user.id,
                creator_discord_id=interaction.user.id,
                category=self.category,
                subject=subject,
                description=description,
                status=TicketStatus.OPEN,
                message_ids=[],
            )
            session.add(ticket)
            await session.commit()
            await session.refresh(ticket)

        # welcome message inside the ticket
        welcome = ticket_embed(
            f"🎫 Ticket #{ticket_number:04d}",
            f"**Subject:** {subject}\n**Category:** {self.category.title()}\n\n"
            f"{description}\n\nA member of staff will be with you shortly.",
        )
        welcome.set_footer(text=f"Opened by {interaction.user.display_name}")
        view = TicketControlView()
        await ticket_channel.send(content=f"{interaction.user.mention}", embed=welcome, view=view)
        try:
            await ticket_channel.pin()
        except Exception:
            pass

        log_ticket_event("CREATE", ticket_number, interaction.user.id)
        await interaction.followup.send(
            embed=success_embed("🎫 Ticket Created", f"Your ticket is ready: {ticket_channel.mention}"),
            ephemeral=True)


class TicketControlView(View):
    """In-channel control buttons (claim / close)."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.success, emoji="🙋", custom_id="arqon:ticket_claim")
    async def claim(self, interaction: discord.Interaction, button: Button):
        if not _is_ticket_staff(interaction.user):
            await interaction.response.send_message(embed=error_embed("Permission Denied", "Staff only"), ephemeral=True)
            return

        async with async_session_maker() as session:
            ticket = (await session.execute(
                select(Ticket).where(Ticket.channel_id == interaction.channel_id))).scalar_one_or_none()
            if not ticket:
                await interaction.response.send_message(embed=error_embed("Not Found", "Not a ticket channel"), ephemeral=True)
                return
            if ticket.status != TicketStatus.OPEN:
                await interaction.response.send_message(embed=error_embed("Already Claimed", "This ticket is already claimed or closed."), ephemeral=True)
                return

            claimer = await get_or_create_user(session, interaction.user.id, interaction.user.name)
            ticket.claimer_id = claimer.id
            ticket.claimer_discord_id = interaction.user.id
            ticket.status = TicketStatus.CLAIMED
            ticket.claimed_at = datetime.now(timezone.utc)
            await session.commit()

        embed = success_embed("🙋 Ticket Claimed", f"Claimed by {interaction.user.mention} — they will assist you.")
        await interaction.response.send_message(embed=embed)
        log_ticket_event("CLAIM", ticket.ticket_number, interaction.user.id)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="arqon:ticket_close")
    async def close(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message(embed=info_embed("Closing...", "Generating transcript and closing..."), ephemeral=True)
        await self._close_ticket(interaction)

    async def _close_ticket(self, interaction: discord.Interaction):
        async with async_session_maker() as session:
            ticket = (await session.execute(
                select(Ticket).where(Ticket.channel_id == interaction.channel_id))).scalar_one_or_none()
            if not ticket:
                return

            # permission: creator or staff
            is_creator = ticket.creator_discord_id == interaction.user.id
            if not is_creator and not _is_ticket_staff(interaction.user):
                await interaction.followup.send(embed=error_embed("Permission Denied", "Only the creator or staff can close this ticket."), ephemeral=True)
                return

            ticket.status = TicketStatus.CLOSED
            ticket.closed_at = datetime.now(timezone.utc)

            # fetch channel messages for transcript
            lines = [f"Transcript — Ticket #{ticket.ticket_number:04d} ({ticket.subject})",
                     f"Category: {ticket.category} • Created: {ticket.created_at:%Y-%m-%d %H:%M} UTC",
                     "=" * 60]
            try:
                async for msg in interaction.channel.history(limit=200, oldest_first=True):
                    who = f"{msg.author.name} ({msg.author.id})"
                    stamp = msg.created_at.strftime("%Y-%m-%d %H:%M")
                    lines.append(f"[{stamp}] {who}: {msg.content or '<attachments/embeds>'}")
                    for att in msg.attachments:
                        lines.append(f"    ↳ attachment: {att.url}")
            except Exception as exc:
                logger.warning(f"Transcript history failed: {exc}")

            transcript_text = "\n".join(lines)
            ticket.transcript = transcript_text

            session.add(TicketMessage(
                ticket_id=ticket.id,
                author_id=interaction.user.id,
                author_name=interaction.user.name,
                content=f"[ticket closed — transcript saved, {len(lines) - 3} lines]",
                is_staff=_is_ticket_staff(interaction.user),
            ))
            await session.commit()

            tchan_id = ticket.transcript_channel_id
            if not tchan_id:
                cfg = (await session.execute(
                    select(GuildConfig).where(GuildConfig.guild_id == ticket.guild_id))).scalar_one_or_none()
                tchan_id = cfg.ticket_transcript_channel_id if cfg else None

        # post transcript
        if tchan_id:
            channel = interaction.client.get_channel(int(tchan_id))
            if channel:
                try:
                    file = discord.File(io.BytesIO(transcript_text.encode("utf-8")),
                                        filename=f"transcript-ticket-{ticket.ticket_number:04d}.txt")
                    embed = ticket_embed(
                        f"📜 Transcript — Ticket #{ticket.ticket_number:04d}",
                        f"Subject: **{ticket.subject}**\n"
                        f"Opened by: <@{ticket.creator_discord_id}>\n"
                        f"Closed by: {interaction.user.mention}",
                    )
                    await channel.send(embed=embed, file=file)
                except Exception as exc:
                    logger.warning(f"Transcript send failed: {exc}")

        try:
            await interaction.channel.send(embed=info_embed(
                "🔒 Ticket Closed",
                f"Closed by {interaction.user.mention}. Channel will be deleted in 10 seconds."))
        except Exception:
            pass
        log_ticket_event("CLOSE", ticket.ticket_number, interaction.user.id)
        await asyncio.sleep(10)
        try:
            await interaction.channel.delete(reason=f"Ticket closed by {interaction.user}")
        except Exception:
            pass


class CloseReasonModal(Modal, title="Close Ticket with Reason"):
    reason = TextInput(label="Closing reason", style=discord.TextStyle.paragraph,
                       required=True, max_length=500)

    def __init__(self):
        super().__init__(custom_id="arqon:ticket_close_reason")
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        embed = ticket_embed("🔒 Ticket Closing",
                             f"**Reason:** {self.reason.value}\nClosing by {interaction.user.mention}")
        await interaction.channel.send(embed=embed)
        await TicketControlView()._close_ticket(interaction)


class AddUserModal(Modal, title="Add User to Ticket"):
    user_id = TextInput(label="User ID", placeholder="123456789012345678", required=True, max_length=20)

    def __init__(self):
        super().__init__(custom_id="arqon:ticket_add_user")
        self.add_item(self.user_id)

    async def on_submit(self, interaction: discord.Interaction):
        if not _is_ticket_staff(interaction.user):
            await interaction.response.send_message(embed=error_embed("Permission Denied", "Staff only"), ephemeral=True)
            return
        try:
            uid = int(self.user_id.value.strip())
        except ValueError:
            await interaction.response.send_message(embed=error_embed("Invalid ID", "Provide a numeric Discord ID."), ephemeral=True)
            return

        member = interaction.guild.get_member(uid)
        if not member:
            await interaction.response.send_message(embed=error_embed("Not Found", "User is not in this server."), ephemeral=True)
            return

        await interaction.channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True)
        async with async_session_maker() as session:
            ticket = (await session.execute(select(Ticket).where(Ticket.channel_id == interaction.channel_id))).scalar_one_or_none()
            if ticket:
                db_user = await get_or_create_user(session, uid, member.name)
                session.add(TicketParticipant(ticket_id=ticket.id, user_id=db_user.id, added_by=interaction.user.id))
                await session.commit()

        await interaction.response.send_message(embed=success_embed("User Added", f"{member.mention} can now see this ticket."), ephemeral=True)


class TicketsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="ticket", description="Post the ticket panel (staff)")
    @commands.guild_only()
    async def ticket_panel(self, ctx: commands.Context, channel: discord.TextChannel = None):
        if not has_permission(ctx.author, PermissionLevel.STAFF):
            await ctx.send(embed=error_embed(
                "Staff Only", "Only staff can post the ticket panel."), ephemeral=True)
            return
        channel = channel or ctx.channel
        embed = ticket_embed(
            "🎫 Support Tickets",
            "Need help? Open a private support ticket and our team will assist you.\n\n"
            "• Select a category below\n• Fill in the subject & description\n"
            "• Wait for staff — you'll get your own private channel",
        )
        embed.set_footer(text=f"Arqon Support • {ctx.guild.name}")
        await channel.send(embed=embed, view=TicketPanelView())
        await ctx.send(embed=success_embed("Panel Posted", f"Ticket panel posted in {channel.mention}."), ephemeral=True)

    @commands.hybrid_command(name="setup-tickets", description="Configure ticket category + transcript channel")
    @commands.guild_only()
    async def setup_tickets(self, ctx: commands.Context, category: discord.CategoryChannel = None,
                            transcript: discord.TextChannel = None):
        if not has_permission(ctx.author, PermissionLevel.ADMIN):
            await ctx.send(embed=error_embed("Permission Denied", "Admin only"), ephemeral=True)
            return

        category = category or ctx.channel.category
        transcript = transcript or ctx.channel

        async with async_session_maker() as session:
            cfg = (await session.execute(
                select(GuildConfig).where(GuildConfig.guild_id == ctx.guild.id))).scalar_one_or_none()
            if not cfg:
                cfg = GuildConfig(guild_id=ctx.guild.id)
                session.add(cfg)
            cfg.ticket_category_id = category.id if category else None
            cfg.ticket_transcript_channel_id = transcript.id
            await session.commit()

        embed = success_embed(
            "✅ Ticket System Configured",
            f"**Category:** {category.name if category else 'None'}\n**Transcripts:** {transcript.mention}",
        )
        embed.set_footer(text="Post the panel with !ticket")
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="add-user", description="Add a user to the current ticket")
    @commands.guild_only()
    async def add_user(self, ctx: commands.Context, member: discord.Member = None):
        if not member:
            await ctx.send_modal(AddUserModal())
            return
        if not _is_ticket_staff(ctx.author):
            await ctx.send(embed=error_embed("Permission Denied", "Staff only"), ephemeral=True)
            return
        await ctx.channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True)
        async with async_session_maker() as session:
            ticket = (await session.execute(select(Ticket).where(Ticket.channel_id == ctx.channel.id))).scalar_one_or_none()
            if ticket:
                db_user = await get_or_create_user(session, member.id, member.name)
                session.add(TicketParticipant(ticket_id=ticket.id, user_id=db_user.id, added_by=ctx.author.id))
                await session.commit()
        await ctx.send(embed=success_embed("User Added", f"{member.mention} can now see this ticket."), ephemeral=True)

    @commands.hybrid_command(name="remove-user", description="Remove a user from the current ticket")
    @commands.guild_only()
    async def remove_user(self, ctx: commands.Context, member: discord.Member):
        if not _is_ticket_staff(ctx.author):
            await ctx.send(embed=error_embed("Permission Denied", "Staff only"), ephemeral=True)
            return
        await ctx.channel.set_permissions(member, overwrite=None)
        await ctx.send(embed=success_embed("User Removed", f"{member.mention} no longer has access."), ephemeral=True)

    @commands.hybrid_command(name="close", description="Close the current ticket")
    @commands.guild_only()
    async def close_ticket(self, ctx: commands.Context):
        # permission: creator or staff
        async with async_session_maker() as session:
            ticket = (await session.execute(
                select(Ticket).where(Ticket.channel_id == ctx.channel.id))).scalar_one_or_none()
        if not ticket:
            await ctx.send(embed=error_embed("Not a Ticket", "Use this inside a ticket channel."), ephemeral=True)
            return
        if ticket.creator_discord_id != ctx.author.id and not _is_ticket_staff(ctx.author):
            await ctx.send(embed=error_embed("Permission Denied", "Only the creator or staff can close."), ephemeral=True)
            return

        await ctx.send(embed=info_embed("Closing...", "Generating transcript..."), ephemeral=True)

        # Reuse the interaction-based close via a lightweight shim
        class _CtxShim:
            channel = ctx.channel
            user = ctx.author
            guild = ctx.guild
            client = ctx.bot

            class _Resp:
                @staticmethod
                def is_done():
                    return True

            response = _Resp()

            class _Follow:
                @staticmethod
                async def send(*args, **kwargs):
                    pass

            followup = _Follow()

        await TicketControlView()._close_ticket(_CtxShim())

    @commands.hybrid_command(name="ticket-stats", description="Ticket statistics")
    @commands.guild_only()
    async def ticket_stats(self, ctx: commands.Context):
        async with async_session_maker() as session:
            total = (await session.execute(
                select(func.count(Ticket.id)).where(Ticket.guild_id == ctx.guild.id))).scalar() or 0
            open_c = (await session.execute(
                select(func.count(Ticket.id)).where(
                    Ticket.guild_id == ctx.guild.id, Ticket.status == TicketStatus.OPEN))).scalar() or 0
            claimed = (await session.execute(
                select(func.count(Ticket.id)).where(
                    Ticket.guild_id == ctx.guild.id, Ticket.status == TicketStatus.CLAIMED))).scalar() or 0
            closed = (await session.execute(
                select(func.count(Ticket.id)).where(
                    Ticket.guild_id == ctx.guild.id, Ticket.status == TicketStatus.CLOSED))).scalar() or 0

        embed = ticket_embed("📊 Ticket Statistics", None)
        embed.add_field(name="📋 Total", value=str(total), inline=True)
        embed.add_field(name="📬 Open", value=str(open_c), inline=True)
        embed.add_field(name="🙋 Claimed", value=str(claimed), inline=True)
        embed.add_field(name="🔒 Closed", value=str(closed), inline=True)
        await ctx.send(embed=embed, ephemeral=True)


async def setup(bot):
    bot.add_view(TicketPanelView())
    bot.add_view(TicketControlView())
    await bot.add_cog(TicketsCog(bot))
