import discord
from discord.ext import commands
from discord.ui import View, Button
from typing import Optional
import uuid as uuid_lib
from datetime import datetime, timezone, timedelta

from utils import (
    error_embed, success_embed, info_embed, purple_embed,
    has_permission, is_admin, PermissionLevel,
    get_logger, format_bytes,
)
from database import (
    async_session_maker, User, Wallet, Transaction, TransactionType,
    TransactionStatus, Invoice, InvoiceStatus, Plan, get_or_create_user,
)
from config import settings
from sqlalchemy import select, func, desc

logger = get_logger("billing")


def _fmt_money(cents: int, currency: str = "USD") -> str:
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) / 100:.2f} {currency}"


class DepositModal(discord.ui.Modal, title="Deposit Funds (demo)"):
    amount = discord.ui.TextInput(label="Amount (USD)", placeholder="10.00", required=True, max_length=10)

    def __init__(self):
        super().__init__(custom_id="arqon:billing_deposit")
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction):
        import math
        try:
            dollars = float(self.amount.value)
            if not math.isfinite(dollars) or dollars <= 0 or dollars > 10000:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(embed=error_embed("Invalid Amount", "Enter 0.01 - 10000."), ephemeral=True)
            return

        cents = int(round(dollars * 100))
        async with async_session_maker() as session:
            user = await get_or_create_user(session, interaction.user.id, interaction.user.name)
            if getattr(user, "is_banned", False):
                await interaction.response.send_message(
                    embed=error_embed("Account Suspended",
                                      "Banned accounts cannot use the wallet."),
                    ephemeral=True)
                return
            # atomic increment — two concurrent deposits can no longer lose credits
            from sqlalchemy import update as _sa_update
            res = await session.execute(
                _sa_update(Wallet)
                .where(Wallet.user_id == user.id)
                .values(balance=Wallet.balance + cents))
            if (res.rowcount or 0) == 0:
                # first deposit — no wallet row yet
                wallet = Wallet(user_id=user.id, balance=cents)
                session.add(wallet)
                # flush now — wallet.id is None until INSERT, and transactions
                #.wallet_id is NOT NULL (first deposit used to fail entirely)
                await session.flush()
            else:
                await session.flush()
                wallet = (await session.execute(
                    select(Wallet).where(Wallet.user_id == user.id))).scalar_one()
            session.add(Transaction(
                wallet_id=wallet.id,
                amount=cents,
                type=TransactionType.DEPOSIT,
                status=TransactionStatus.COMPLETED,
                description=f"Deposit by {interaction.user.name}",
                reference=str(uuid_lib.uuid4())[:12],
            ))
            await session.commit()
            balance = wallet.balance

        await interaction.response.send_message(
            embed=success_embed("💰 Deposit Complete", f"Added {_fmt_money(cents)}.\nNew balance: **{_fmt_money(balance)}**"),
            ephemeral=True)


class BillingCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="balance", description="Check your wallet balance")
    @commands.guild_only()
    async def balance(self, ctx: commands.Context):
        async with async_session_maker() as session:
            user = await get_or_create_user(session, ctx.author.id, ctx.author.name)
            wallet = (await session.execute(
                select(Wallet).where(Wallet.user_id == user.id))).scalar_one_or_none()

        if not wallet:
            embed = purple_embed("💼 Wallet", f"Balance: **$0.00**\nUse `!deposit` to add funds.")
        else:
            embed = purple_embed("💼 Wallet",
                                 f"Balance: **{_fmt_money(wallet.balance, wallet.currency)}**\n"
                                 f"Status: {'❄️ Frozen' if wallet.is_frozen else '✅ Active'}")
        embed.set_footer(text=settings.BOT_FOOTER)
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="deposit", description="Add funds to your wallet (demo)")
    @commands.guild_only()
    async def deposit(self, ctx: commands.Context):
        await ctx.send_modal(DepositModal())

    @commands.hybrid_command(name="transactions", description="Your recent transactions")
    @commands.guild_only()
    async def transactions(self, ctx: commands.Context):
        async with async_session_maker() as session:
            user = await get_or_create_user(session, ctx.author.id, ctx.author.name)
            wallet = (await session.execute(
                select(Wallet).where(Wallet.user_id == user.id))).scalar_one_or_none()
            if not wallet:
                await ctx.send(embed=info_embed("No Wallet", "You have no wallet yet. Use `!deposit`."), ephemeral=True)
                return
            txs = (await session.execute(
                select(Transaction)
                .where(Transaction.wallet_id == wallet.id)
                .order_by(desc(Transaction.created_at))
                .limit(10))).scalars().all()

        if not txs:
            await ctx.send(embed=info_embed("No Transactions", "No transactions recorded."), ephemeral=True)
            return

        embed = purple_embed(f"📜 Transactions ({len(txs)})", None)
        icons = {"deposit": "⬆️", "withdrawal": "⬇️", "purchase": "🛒", "refund": "↩️"}
        for tx in txs:
            icon = icons.get(tx.type.value, "💱")
            status_emoji = {"completed": "✅", "pending": "⏳", "failed": "❌"}.get(tx.status.value, "⚪")
            embed.add_field(
                name=f"{icon} {tx.type.value.title()} — {_fmt_money(tx.amount)}",
                value=f"{status_emoji} {tx.status.value.title()} • {tx.description or tx.reference or ''}\n"
                      f"<t:{int(tx.created_at.replace(tzinfo=timezone.utc).timestamp())}:R>",
                inline=False,
            )
        await ctx.send(embed=embed, ephemeral=True)

    # NOTE: /plans moved to cogs/plans.py — the interactive, invite-earned
    # version with plan buttons, OS dropdown and animated deployment.

    @commands.hybrid_command(name="createinvoice", description="Create an invoice for a user (Admin)")
    @commands.guild_only()
    async def create_invoice(self, ctx: commands.Context, member: discord.Member, amount: float, *, description: str = "Hosting service"):
        if not is_admin(ctx.author):
            await ctx.send(embed=error_embed("Permission Denied", "Admin only"), ephemeral=True)
            return

        cents = int(amount * 100)
        async with async_session_maker() as session:
            user = await get_or_create_user(session, member.id, member.name)
            invoice = Invoice(
                invoice_number=f"INV-{datetime.utcnow().strftime('%Y%m')}-{uuid_lib.uuid4().hex[:6].upper()}",
                user_id=user.id,
                amount=cents,
                status=InvoiceStatus.PENDING,
                due_date=datetime.utcnow() + timedelta(days=7),
            )
            session.add(invoice)
            await session.commit()

        embed = success_embed(
            "🧾 Invoice Created",
            f"**{invoice.invoice_number}** for {member.mention}\n"
            f"Amount: **{_fmt_money(cents)}** • Due: "
            f"<t:{int(invoice.due_date.replace(tzinfo=timezone.utc).timestamp())}:D>",
        )
        await ctx.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(BillingCog(bot))
