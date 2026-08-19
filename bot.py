import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("DISCORD_GUILD_ID")
CTM_USER_ID = os.getenv("CTM_USER_ID")
CTM_ROLE_ID = os.getenv("CTM_ROLE_ID")
DATABASE_PATH = os.getenv("DATABASE_PATH", "ctm_recruits.db")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with connect_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS recruits (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                created_at TEXT NOT NULL,
                active_days INTEGER NOT NULL DEFAULT 0,
                activity_hours REAL NOT NULL DEFAULT 0,
                training_completed TEXT NOT NULL DEFAULT '',
                conduct INTEGER,
                communication INTEGER,
                teamwork INTEGER,
                knowledge INTEGER,
                performance INTEGER,
                strengths TEXT NOT NULL DEFAULT '',
                weaknesses TEXT NOT NULL DEFAULT '',
                warnings TEXT NOT NULL DEFAULT '',
                recommendation TEXT NOT NULL DEFAULT '',
                recommended_rank TEXT NOT NULL DEFAULT '',
                recommendation_notes TEXT NOT NULL DEFAULT '',
                finalized_at TEXT,
                vote_open INTEGER NOT NULL DEFAULT 0,
                vote_message_id INTEGER,
                vote_channel_id INTEGER,
                PRIMARY KEY (guild_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS votes (
                guild_id INTEGER NOT NULL,
                recruit_id INTEGER NOT NULL,
                voter_id INTEGER NOT NULL,
                choice TEXT NOT NULL CHECK(choice IN ('accept', 'deny', 'abstain')),
                voted_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, recruit_id, voter_id),
                FOREIGN KEY (guild_id, recruit_id)
                    REFERENCES recruits(guild_id, user_id)
                    ON DELETE CASCADE
            );
            """
        )


def get_recruit(guild_id: int, user_id: int) -> Optional[sqlite3.Row]:
    with connect_db() as conn:
        return conn.execute(
            "SELECT * FROM recruits WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        ).fetchone()


def score_total(row: sqlite3.Row) -> Optional[int]:
    fields = ["conduct", "communication", "teamwork", "knowledge", "performance"]
    if any(row[field] is None for field in fields):
        return None
    return sum(int(row[field]) for field in fields)


def vote_counts(guild_id: int, recruit_id: int) -> dict[str, int]:
    counts = {"accept": 0, "deny": 0, "abstain": 0}
    with connect_db() as conn:
        rows = conn.execute(
            """
            SELECT choice, COUNT(*) AS total
            FROM votes
            WHERE guild_id=? AND recruit_id=?
            GROUP BY choice
            """,
            (guild_id, recruit_id),
        ).fetchall()
    for row in rows:
        counts[row["choice"]] = row["total"]
    return counts


def can_manage_ctm(member: discord.Member) -> bool:
    if member.guild_permissions.manage_guild:
        return True
    if CTM_USER_ID and str(member.id) == CTM_USER_ID:
        return True
    if CTM_ROLE_ID:
        try:
            role_id = int(CTM_ROLE_ID)
        except ValueError:
            role_id = -1
        if any(role.id == role_id for role in member.roles):
            return True
    return False


def recruit_embed(row: sqlite3.Row, include_votes: bool = False) -> discord.Embed:
    total = score_total(row)
    embed = discord.Embed(
        title=f"CTM Recruit Report — {row['username']}",
        description=f"<@{row['user_id']}>",
        colour=discord.Colour.blurple(),
    )
    embed.add_field(name="Active Days", value=str(row["active_days"]), inline=True)
    embed.add_field(name="Activity Hours", value=f"{row['activity_hours']:g}", inline=True)
    embed.add_field(name="Score", value=f"{total}/25" if total is not None else "Not fully scored", inline=True)

    def score_value(name: str) -> str:
        value = row[name]
        return f"{value}/5" if value is not None else "—"

    embed.add_field(name="Conduct", value=score_value("conduct"), inline=True)
    embed.add_field(name="Communication", value=score_value("communication"), inline=True)
    embed.add_field(name="Teamwork", value=score_value("teamwork"), inline=True)
    embed.add_field(name="Knowledge", value=score_value("knowledge"), inline=True)
    embed.add_field(name="Performance", value=score_value("performance"), inline=True)
    embed.add_field(name="Training Completed", value=row["training_completed"] or "—", inline=False)
    embed.add_field(name="Strengths", value=row["strengths"] or "—", inline=False)
    embed.add_field(name="Weaknesses", value=row["weaknesses"] or "—", inline=False)
    embed.add_field(name="Warnings / Issues", value=row["warnings"] or "None", inline=False)

    recommendation = row["recommendation"] or "Not finalized"
    if row["recommended_rank"]:
        recommendation += f" — {row['recommended_rank']}"
    embed.add_field(name="CTM Recommendation", value=recommendation, inline=False)
    if row["recommendation_notes"]:
        embed.add_field(name="Recommendation Notes", value=row["recommendation_notes"], inline=False)

    if include_votes:
        counts = vote_counts(row["guild_id"], row["user_id"])
        status = "OPEN" if row["vote_open"] else "CLOSED"
        embed.add_field(
            name=f"Membership Vote — {status}",
            value=(
                f"✅ Accept: **{counts['accept']}**\n"
                f"❌ Deny: **{counts['deny']}**\n"
                f"➖ Abstain: **{counts['abstain']}**"
            ),
            inline=False,
        )
    embed.set_footer(text="Chief of Training & Membership")
    return embed


class VoteView(discord.ui.View):
    def __init__(self, guild_id: int, recruit_id: int, disabled: bool = False):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.recruit_id = recruit_id
        self.accept.custom_id = f"ctm_vote:accept:{guild_id}:{recruit_id}"
        self.deny.custom_id = f"ctm_vote:deny:{guild_id}:{recruit_id}"
        self.abstain.custom_id = f"ctm_vote:abstain:{guild_id}:{recruit_id}"
        for item in self.children:
            item.disabled = disabled

    async def cast_vote(self, interaction: discord.Interaction, choice: str) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This vote only works inside the server.", ephemeral=True)
            return
        row = get_recruit(self.guild_id, self.recruit_id)
        if row is None or not row["vote_open"]:
            await interaction.response.send_message("This vote is closed.", ephemeral=True)
            return
        if interaction.user.id == self.recruit_id:
            await interaction.response.send_message("Recruits cannot vote on their own membership.", ephemeral=True)
            return
        with connect_db() as conn:
            conn.execute(
                """
                INSERT INTO votes (guild_id, recruit_id, voter_id, choice, voted_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, recruit_id, voter_id)
                DO UPDATE SET choice=excluded.choice, voted_at=excluded.voted_at
                """,
                (self.guild_id, self.recruit_id, interaction.user.id, choice, now_iso()),
            )
        refreshed = get_recruit(self.guild_id, self.recruit_id)
        await interaction.response.edit_message(embed=recruit_embed(refreshed, include_votes=True), view=self)
        await interaction.followup.send(f"Your vote is now **{choice.title()}**.", ephemeral=True)

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, emoji="✅", custom_id="placeholder_accept")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cast_vote(interaction, "accept")

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, emoji="❌", custom_id="placeholder_deny")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cast_vote(interaction, "deny")

    @discord.ui.button(label="Abstain", style=discord.ButtonStyle.secondary, emoji="➖", custom_id="placeholder_abstain")
    async def abstain(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cast_vote(interaction, "abstain")


class CTMBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        init_db()
        with connect_db() as conn:
            active_votes = conn.execute(
                """
                SELECT guild_id, user_id, vote_message_id
                FROM recruits
                WHERE vote_open=1 AND vote_message_id IS NOT NULL
                """
            ).fetchall()
        for row in active_votes:
            self.add_view(VoteView(row["guild_id"], row["user_id"]), message_id=row["vote_message_id"])
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()


bot = CTMBot()
recruit_group = app_commands.Group(name="recruit", description="CTM recruit management")


async def require_ctm(interaction: discord.Interaction) -> bool:
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("This command only works inside the server.", ephemeral=True)
        return False
    if not can_manage_ctm(interaction.user):
        await interaction.response.send_message("You do not have permission to manage CTM recruit records.", ephemeral=True)
        return False
    return True


@recruit_group.command(name="create", description="Create a CTM record for a recruit")
async def create_recruit(interaction: discord.Interaction, member: discord.Member):
    if not await require_ctm(interaction):
        return
    if member.bot:
        await interaction.response.send_message("Bots cannot be recruits.", ephemeral=True)
        return
    with connect_db() as conn:
        existing = conn.execute(
            "SELECT 1 FROM recruits WHERE guild_id=? AND user_id=?",
            (interaction.guild_id, member.id),
        ).fetchone()
        if existing:
            await interaction.response.send_message("That member already has a recruit record.", ephemeral=True)
            return
        conn.execute(
            """
            INSERT INTO recruits (guild_id, user_id, username, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (interaction.guild_id, member.id, str(member), now_iso()),
        )
    row = get_recruit(interaction.guild_id, member.id)
    await interaction.response.send_message(embed=recruit_embed(row))


@recruit_group.command(name="update", description="Update all recruit activity and notes")
@app_commands.describe(
    active_days="Total active training days",
    activity_hours="Total meaningful activity hours",
    training_completed="Short summary of completed training",
    strengths="Documented strengths",
    weaknesses="Documented weaknesses",
    warnings="Warnings or issues; use 'None' if there are none",
)
async def update_recruit(
    interaction: discord.Interaction,
    member: discord.Member,
    active_days: app_commands.Range[int, 0, 7],
    activity_hours: app_commands.Range[float, 0, 1000],
    training_completed: str,
    strengths: str,
    weaknesses: str,
    warnings: str,
):
    if not await require_ctm(interaction):
        return
    if get_recruit(interaction.guild_id, member.id) is None:
        await interaction.response.send_message("That member does not have a recruit record.", ephemeral=True)
        return

    warnings_value = "" if warnings.strip().lower() == "none" else warnings
    with connect_db() as conn:
        conn.execute(
            """
            UPDATE recruits
            SET active_days=?, activity_hours=?, training_completed=?, strengths=?, weaknesses=?, warnings=?
            WHERE guild_id=? AND user_id=?
            """,
            (
                active_days,
                float(activity_hours),
                training_completed,
                strengths,
                weaknesses,
                warnings_value,
                interaction.guild_id,
                member.id,
            ),
        )
    refreshed = get_recruit(interaction.guild_id, member.id)
    await interaction.response.send_message(embed=recruit_embed(refreshed))


@recruit_group.command(name="score", description="Score the five CTM evaluation categories")
async def score_recruit(
    interaction: discord.Interaction,
    member: discord.Member,
    conduct: app_commands.Range[int, 1, 5],
    communication: app_commands.Range[int, 1, 5],
    teamwork: app_commands.Range[int, 1, 5],
    knowledge: app_commands.Range[int, 1, 5],
    performance: app_commands.Range[int, 1, 5],
):
    if not await require_ctm(interaction):
        return
    if get_recruit(interaction.guild_id, member.id) is None:
        await interaction.response.send_message("That member does not have a recruit record.", ephemeral=True)
        return
    with connect_db() as conn:
        conn.execute(
            """
            UPDATE recruits
            SET conduct=?, communication=?, teamwork=?, knowledge=?, performance=?
            WHERE guild_id=? AND user_id=?
            """,
            (conduct, communication, teamwork, knowledge, performance, interaction.guild_id, member.id),
        )
    row = get_recruit(interaction.guild_id, member.id)
    await interaction.response.send_message(embed=recruit_embed(row))


@recruit_group.command(name="view", description="View a recruit's current report card")
async def view_recruit(interaction: discord.Interaction, member: discord.Member):
    row = get_recruit(interaction.guild_id, member.id)
    if row is None:
        await interaction.response.send_message("That member does not have a recruit record.", ephemeral=True)
        return
    await interaction.response.send_message(embed=recruit_embed(row, include_votes=bool(row["vote_message_id"])))


@recruit_group.command(name="finalize", description="Record CTM's final evaluation and recommendation")
@app_commands.choices(
    recommendation=[
        app_commands.Choice(name="Recommend Acceptance", value="Recommend Acceptance"),
        app_commands.Choice(name="Recommend Denial", value="Recommend Denial"),
    ],
    recommended_rank=[
        app_commands.Choice(name="Private", value="Private"),
        app_commands.Choice(name="Private First Class", value="Private First Class"),
        app_commands.Choice(name="Lance Corporal", value="Lance Corporal"),
        app_commands.Choice(name="No Rank Recommendation", value=""),
    ],
)
async def finalize_recruit(
    interaction: discord.Interaction,
    member: discord.Member,
    recommendation: app_commands.Choice[str],
    recommended_rank: app_commands.Choice[str],
    notes: Optional[str] = None,
):
    if not await require_ctm(interaction):
        return
    row = get_recruit(interaction.guild_id, member.id)
    if row is None:
        await interaction.response.send_message("That member does not have a recruit record.", ephemeral=True)
        return
    with connect_db() as conn:
        conn.execute(
            """
            UPDATE recruits
            SET recommendation=?, recommended_rank=?, recommendation_notes=?, finalized_at=?
            WHERE guild_id=? AND user_id=?
            """,
            (
                recommendation.value,
                recommended_rank.value,
                notes or "",
                now_iso(),
                interaction.guild_id,
                member.id,
            ),
        )
    refreshed = get_recruit(interaction.guild_id, member.id)
    await interaction.response.send_message(
        content="CTM evaluation finalized. The membership vote is still the final acceptance/denial decision.",
        embed=recruit_embed(refreshed),
    )


@recruit_group.command(name="vote", description="Post a recruit report card and open the membership vote")
async def open_vote(interaction: discord.Interaction, member: discord.Member):
    if not await require_ctm(interaction):
        return
    row = get_recruit(interaction.guild_id, member.id)
    if row is None:
        await interaction.response.send_message("That member does not have a recruit record.", ephemeral=True)
        return
    if not row["recommendation"]:
        await interaction.response.send_message("Finalize the CTM recommendation before opening the membership vote.", ephemeral=True)
        return
    if row["vote_open"]:
        await interaction.response.send_message("A vote is already open for that recruit.", ephemeral=True)
        return
    with connect_db() as conn:
        conn.execute("DELETE FROM votes WHERE guild_id=? AND recruit_id=?", (interaction.guild_id, member.id))
        conn.execute(
            """
            UPDATE recruits
            SET vote_open=1, vote_message_id=NULL, vote_channel_id=?
            WHERE guild_id=? AND user_id=?
            """,
            (interaction.channel_id, interaction.guild_id, member.id),
        )
    refreshed = get_recruit(interaction.guild_id, member.id)
    view = VoteView(interaction.guild_id, member.id)
    await interaction.response.send_message(
        content=f"**Membership vote for {member.mention}**\nReview the CTM report below before voting.",
        embed=recruit_embed(refreshed, include_votes=True),
        view=view,
    )
    message = await interaction.original_response()
    with connect_db() as conn:
        conn.execute(
            """
            UPDATE recruits
            SET vote_message_id=?
            WHERE guild_id=? AND user_id=?
            """,
            (message.id, interaction.guild_id, member.id),
        )
    bot.add_view(view, message_id=message.id)


@recruit_group.command(name="closevote", description="Close a recruit membership vote and show the tally")
async def close_vote(interaction: discord.Interaction, member: discord.Member):
    if not await require_ctm(interaction):
        return
    row = get_recruit(interaction.guild_id, member.id)
    if row is None:
        await interaction.response.send_message("That member does not have a recruit record.", ephemeral=True)
        return
    if not row["vote_open"]:
        await interaction.response.send_message("There is no open vote for that recruit.", ephemeral=True)
        return
    with connect_db() as conn:
        conn.execute(
            "UPDATE recruits SET vote_open=0 WHERE guild_id=? AND user_id=?",
            (interaction.guild_id, member.id),
        )
    refreshed = get_recruit(interaction.guild_id, member.id)
    closed_view = VoteView(interaction.guild_id, member.id, disabled=True)
    if row["vote_channel_id"] and row["vote_message_id"]:
        channel = interaction.guild.get_channel(row["vote_channel_id"])
        if channel and hasattr(channel, "fetch_message"):
            try:
                message = await channel.fetch_message(row["vote_message_id"])
                await message.edit(embed=recruit_embed(refreshed, include_votes=True), view=closed_view)
            except discord.HTTPException:
                pass
    counts = vote_counts(interaction.guild_id, member.id)
    await interaction.response.send_message(
        f"Vote closed for {member.mention}: "
        f"✅ **{counts['accept']}** accept, "
        f"❌ **{counts['deny']}** deny, "
        f"➖ **{counts['abstain']}** abstain."
    )


bot.tree.add_command(recruit_group)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} ({bot.user.id})")


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN is missing. Add it to your .env file.")
    bot.run(TOKEN)