import os
import re
import asyncio
from collections import defaultdict, deque
from datetime import datetime, timedelta

import discord
from discord.ext import commands
from discord import app_commands


OWNER_ID = 1197074121117413417
REJOIN_INVITE = "https://discord.gg/rU9FyNEUQ7"

INVITE_BLOCKER_CHANNELS = {
    1502628012762333245,
    1544043872261378171,
    1544058961039466656,
    1544059092233093120,
}

ANTI_SPAM_CHANNELS = {
    1502628012762333245,
    1544043872261378171,
}

TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.messages = True
intents.message_content = True

bot = commands.Bot(command_prefix=".", intents=intents)

settings = defaultdict(lambda: {
    "anti_nuke": True,
    "invite_blocker": True,
    "bot_detection": True,
    "dm_logging": True,
    "anti_spam": True,
    "dont_chat": True,
    "ban_system": True,
    "anti_raid": True,
})

dont_chat_channels = {}
dont_chat_messages = {}
dont_chat_kicks = {}

recent_messages = defaultdict(lambda: defaultdict(deque))
nuke_actions = defaultdict(lambda: defaultdict(deque))
join_tracker = defaultdict(deque)

raid_lockdown = defaultdict(bool)
raid_unlock_tasks = {}

invite_strikes = defaultdict(lambda: defaultdict(deque))
spam_tracker = defaultdict(lambda: defaultdict(deque))
tracked_messages = defaultdict(lambda: defaultdict(deque))

INVITE_REGEX = re.compile(
    r"(?:https?://)?(?:www\.)?(?:discord\.gg|discord\.com/invite)/[A-Za-z0-9-]+",
    re.IGNORECASE
)


def is_owner(user):
    return user.id == OWNER_ID


def has_manage_permission(member):
    if member.id == OWNER_ID:
        return True

    perms = member.guild_permissions
    return perms.administrator or perms.manage_messages or perms.kick_members


def can_kick(bot_member, target):
    if target.id == target.guild.owner_id:
        return False

    if target.id == bot_member.id:
        return False

    return bot_member.top_role > target.top_role


async def send_owner_log(guild, title, description):
    if not settings[guild.id]["dm_logging"]:
        return

    try:
        owner = await bot.fetch_user(OWNER_ID)

        embed = discord.Embed(
            title=title,
            description=description,
            color=discord.Color.blurple(),
            timestamp=datetime.utcnow()
        )

        await owner.send(embed=embed)
    except Exception:
        pass


async def delete_all_user_messages(guild, user):
    deleted = 0

    for channel in guild.text_channels:
        try:
            if not channel.permissions_for(guild.me).manage_messages:
                continue

            async for message in channel.history(limit=None):
                if message.author.id == user.id:
                    try:
                        await message.delete()
                        deleted += 1
                    except (
                        discord.NotFound,
                        discord.Forbidden,
                        discord.HTTPException
                    ):
                        pass

        except (
            discord.Forbidden,
            discord.HTTPException,
            discord.NotFound
        ):
            continue

    tracked = tracked_messages[guild.id][user.id]

    while tracked:
        try:
            message = tracked.popleft()
            if not message.deleted:
                await message.delete()
                deleted += 1
        except Exception:
            pass

    return deleted


async def dm_kicked_user(user, reason):
    try:
        embed = discord.Embed(
            title="You have been kicked",
            description=(
                f"You were kicked from the server.\n\n"
                f"**Reason:** {reason}\n\n"
                f"You may rejoin using the invite below:"
            ),
            color=discord.Color.red()
        )

        embed.add_field(
            name="Rejoin",
            value=REJOIN_INVITE,
            inline=False
        )

        await user.send(embed=embed)
    except Exception:
        pass


async def kick_member_with_cleanup(guild, member, reason):
    bot_member = guild.me

    if not can_kick(bot_member, member):
        return False, 0

    deleted_count = await delete_all_user_messages(guild, member)

    try:
        await dm_kicked_user(member, reason)
    except Exception:
        pass

    try:
        await member.kick(reason=reason)
    except (
        discord.Forbidden,
        discord.HTTPException,
        discord.NotFound
    ):
        return False, deleted_count

    dont_chat_kicks[guild.id] = dont_chat_kicks.get(guild.id, 65) + 1

    await send_owner_log(
        guild,
        "Don't Chat — User Kicked",
        (
            f"**User:** {member} (`{member.id}`)\n"
            f"**Reason:** {reason}\n"
            f"**Messages deleted:** {deleted_count}\n"
            f"**Live kick count:** {dont_chat_kicks[guild.id]}"
        )
    )

    return True, deleted_count


async def register_nuke_action(guild, executor_id, action_name):
    if executor_id in {OWNER_ID, guild.owner_id, bot.user.id}:
        return

    now = datetime.utcnow()
    actions = nuke_actions[guild.id][executor_id]

    actions.append(now)

    while actions and (now - actions[0]).total_seconds() > 10:
        actions.popleft()

    if len(actions) >= 3:
        member = guild.get_member(executor_id)

        if member and can_kick(guild.me, member):
            try:
                await member.kick(
                    reason=f"Anti-Nuke: {action_name} threshold exceeded"
                )

                await send_owner_log(
                    guild,
                    "Anti-Nuke Triggered",
                    (
                        f"**Executor:** {member} (`{executor_id}`)\n"
                        f"**Trigger:** {action_name}\n"
                        f"**Actions:** {len(actions)} in 10 seconds"
                    )
                )
            except Exception:
                pass

        actions.clear()


@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"Logged in as {bot.user}")
        print(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        print(f"Slash sync error: {e}")


@bot.event
async def on_member_join(member):
    guild = member.guild

    if member.bot and settings[guild.id]["bot_detection"]:
        await asyncio.sleep(1)

        try:
            async for entry in guild.audit_logs(
                limit=10,
                action=discord.AuditLogAction.bot_add
            ):
                if entry.target and entry.target.id == member.id:
                    executor = entry.user

                    if executor.id not in {
                        OWNER_ID,
                        guild.owner_id,
                        bot.user.id
                    }:
                        if can_kick(guild.me, member):
                            try:
                                await member.kick(
                                    reason="Unauthorized bot detected"
                                )

                                await send_owner_log(
                                    guild,
                                    "Bot Detection",
                                    (
                                        f"Unauthorized bot removed.\n"
                                        f"**Bot:** {member}\n"
                                        f"**Added by:** {executor}"
                                    )
                                )
                            except Exception:
                                pass

                    break

        except Exception:
            pass

        return

    if not settings[guild.id]["anti_raid"]:
        return

    now = datetime.utcnow()
    joins = join_tracker[guild.id]

    joins.append(now)

    while joins and (now - joins[0]).total_seconds() > 10:
        joins.popleft()

    if len(joins) >= 8 and not raid_lockdown[guild.id]:
        raid_lockdown[guild.id] = True

        await send_owner_log(
            guild,
            "Anti-Raid Activated",
            "8 or more members joined within 10 seconds. Lockdown activated for 60 seconds."
        )

        async def unlock():
            await asyncio.sleep(60)
            raid_lockdown[guild.id] = False
            join_tracker[guild.id].clear()

            await send_owner_log(
                guild,
                "Anti-Raid Deactivated",
                "The 60-second raid lockdown has ended."
            )

        task = asyncio.create_task(unlock())
        raid_unlock_tasks[guild.id] = task

    if raid_lockdown[guild.id]:
        if not member.bot and member.id != OWNER_ID:
            try:
                if can_kick(guild.me, member):
                    await dm_kicked_user(
                        member,
                        "Anti-Raid protection"
                    )
                    await member.kick(
                        reason="Anti-Raid lockdown"
                    )
            except Exception:
                pass


@bot.event
async def on_member_remove(member):
    guild = member.guild

    try:
        async for entry in guild.audit_logs(
            limit=10,
            action=discord.AuditLogAction.kick
        ):
            if entry.target and entry.target.id == member.id:
                await send_owner_log(
                    guild,
                    "Member Kicked",
                    (
                        f"**Member:** {member} (`{member.id}`)\n"
                        f"**Moderator:** {entry.user}"
                    )
                )
                break
    except Exception:
        pass


@bot.event
async def on_guild_channel_delete(channel):
    guild = channel.guild

    if not settings[guild.id]["anti_nuke"]:
        return

    try:
        async for entry in guild.audit_logs(
            limit=5,
            action=discord.AuditLogAction.channel_delete
        ):
            if entry.target and entry.target.id == channel.id:
                await register_nuke_action(
                    guild,
                    entry.user.id,
                    "Channel Delete"
                )
                break
    except Exception:
        pass


@bot.event
async def on_guild_role_delete(role):
    guild = role.guild

    if not settings[guild.id]["anti_nuke"]:
        return

    try:
        async for entry in guild.audit_logs(
            limit=5,
            action=discord.AuditLogAction.role_delete
        ):
            if entry.target and entry.target.id == role.id:
                await register_nuke_action(
                    guild,
                    entry.user.id,
                    "Role Delete"
                )
                break
    except Exception:
        pass


@bot.event
async def on_webhooks_update(channel):
    guild = channel.guild

    if not settings[guild.id]["anti_nuke"]:
        return

    try:
        async for entry in guild.audit_logs(
            limit=5,
            action=discord.AuditLogAction.webhook_delete
        ):
            if entry.target:
                await register_nuke_action(
                    guild,
                    entry.user.id,
                    "Webhook Delete"
                )
                break
    except Exception:
        pass


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    guild = message.guild

    if guild is None:
        await bot.process_commands(message)
        return

    user_id = message.author.id
    channel_id = message.channel.id

    tracked_messages[guild.id][user_id].append(message)

    if len(tracked_messages[guild.id][user_id]) > 100:
        tracked_messages[guild.id][user_id].popleft()

    # DON'T CHAT HERE
    protected_channel = dont_chat_channels.get(guild.id)

    if (
        settings[guild.id]["dont_chat"]
        and protected_channel
        and channel_id == protected_channel
        and message.author.id != OWNER_ID
    ):
        try:
            await message.delete()
        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException
        ):
            pass

        kicked, deleted = await kick_member_with_cleanup(
            guild,
            message.author,
            "Do Not Chat Here"
        )

        await bot.process_commands(message)
        return

    # INVITE BLOCKER
    if (
        settings[guild.id]["invite_blocker"]
        and channel_id in INVITE_BLOCKER_CHANNELS
        and INVITE_REGEX.search(message.content)
    ):
        member = message.author

        if not (
            member.guild_permissions.administrator
            or member.guild_permissions.manage_messages
            or member.guild_permissions.manage_guild
        ):
            try:
                await message.delete()
            except Exception:
                pass

            now = datetime.utcnow()
            strikes = invite_strikes[guild.id][user_id]
            strikes.append(now)

            while strikes and (now - strikes[0]).total_seconds() > 60:
                strikes.popleft()

            if len(strikes) >= 4:
                if can_kick(guild.me, member):
                    try:
                        await dm_kicked_user(
                            member,
                            "Invite spam"
                        )
                        await member.kick(
                            reason="Invite Blocker: 4 strikes"
                        )

                        await send_owner_log(
                            guild,
                            "Invite Blocker",
                            (
                                f"**User:** {member} (`{member.id}`)\n"
                                f"**Action:** Kicked\n"
                                f"**Strikes:** {len(strikes)}"
                            )
                        )
                    except Exception:
                        pass

                strikes.clear()

    # ANTI-SPAM
    if (
        settings[guild.id]["anti_spam"]
        and channel_id in ANTI_SPAM_CHANNELS
    ):
        member = message.author

        if not (
            member.guild_permissions.administrator
            or member.guild_permissions.manage_messages
        ):
            now = datetime.utcnow()
            spam = spam_tracker[guild.id][user_id]

            spam.append(now)

            while spam and (now - spam[0]).total_seconds() > 6:
                spam.popleft()

            if len(spam) >= 5:
                recent = list(
                    tracked_messages[guild.id][user_id]
                )

                for msg in recent:
                    try:
                        await msg.delete()
                    except Exception:
                        pass

                if can_kick(guild.me, member):
                    try:
                        await member.timeout(
                            timedelta(minutes=2),
                            reason="Anti-Spam"
                        )

                        await send_owner_log(
                            guild,
                            "Anti-Spam Triggered",
                            (
                                f"**User:** {member} (`{member.id}`)\n"
                                f"**Action:** 2-minute timeout"
                            )
                        )
                    except Exception:
                        pass

                spam.clear()

    await bot.process_commands(message)


class DontChatGroup(app_commands.Group):
    def __init__(self):
        super().__init__(
            name="dontchat",
            description="Manage Don't Chat Here protection"
        )

    @app_commands.command(
        name="setup",
        description="Set the protected Don't Chat Here channel"
    )
    @app_commands.describe(
        channel="Channel to protect"
    )
    async def setup(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel
    ):
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "You don't have permission to use this command.",
                ephemeral=True
            )
            return

        guild = interaction.guild

        dont_chat_channels[guild.id] = channel.id
        dont_chat_messages[guild.id] = (
            "You were kicked for chatting in the Don't Chat Here channel."
        )

        # Always reset the stock count when setup is used.
        dont_chat_kicks[guild.id] = 65

        settings[guild.id]["dont_chat"] = True

        embed = discord.Embed(
            title="Don't Chat Here",
            description=(
                f"Protected channel: {channel.mention}\n\n"
                "Any non-owner who sends a message here will be "
                "kicked and their messages will be deleted."
            ),
            color=discord.Color.red()
        )

        embed.add_field(
            name="Live Kick Count",
            value=str(dont_chat_kicks[guild.id]),
            inline=False
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True
        )

    @app_commands.command(
        name="off",
        description="Disable Don't Chat Here"
    )
    async def off(self, interaction: discord.Interaction):
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "You don't have permission to use this command.",
                ephemeral=True
            )
            return

        settings[interaction.guild.id]["dont_chat"] = False

        await interaction.response.send_message(
            "Don't Chat Here has been disabled.",
            ephemeral=True
        )

    @app_commands.command(
        name="status",
        description="Show Don't Chat Here status"
    )
    async def status(self, interaction: discord.Interaction):
        guild = interaction.guild

        channel_id = dont_chat_channels.get(guild.id)
        count = dont_chat_kicks.get(guild.id, 65)

        channel = (
            guild.get_channel(channel_id)
            if channel_id
            else None
        )

        embed = discord.Embed(
            title="Don't Chat Here Status",
            color=discord.Color.blurple()
        )

        embed.add_field(
            name="Status",
            value="Enabled" if settings[guild.id]["dont_chat"] else "Disabled",
            inline=True
        )

        embed.add_field(
            name="Channel",
            value=channel.mention if channel else "Not configured",
            inline=True
        )

        embed.add_field(
            name="Live Kick Count",
            value=str(count),
            inline=True
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True
        )

    @app_commands.command(
        name="channel",
        description="Show the protected channel"
    )
    async def channel(self, interaction: discord.Interaction):
        guild = interaction.guild
        channel_id = dont_chat_channels.get(guild.id)

        channel = (
            guild.get_channel(channel_id)
            if channel_id
            else None
        )

        await interaction.response.send_message(
            f"Protected channel: {channel.mention if channel else 'Not configured'}",
            ephemeral=True
        )

    @app_commands.command(
        name="message",
        description="Change the kick DM message"
    )
    @app_commands.describe(
        message="Message sent to kicked users"
    )
    async def message(
        self,
        interaction: discord.Interaction,
        message: str
    ):
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "You don't have permission to use this command.",
                ephemeral=True
            )
            return

        dont_chat_messages[interaction.guild.id] = message

        await interaction.response.send_message(
            "Don't Chat Here kick message updated.",
            ephemeral=True
        )


bot.tree.add_command(DontChatGroup())


@bot.tree.command(
    name="ban",
    description="Permanently ban a member"
)
@app_commands.describe(
    member="Member to ban",
    reason="Reason for the ban"
)
async def ban(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str = "No reason provided"
):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message(
            "You don't have permission to use this command.",
            ephemeral=True
        )
        return

    if not can_kick(interaction.guild.me, member):
        await interaction.response.send_message(
            "I cannot ban that member because of role hierarchy.",
            ephemeral=True
        )
        return

    try:
        await member.ban(reason=reason)

        await interaction.response.send_message(
            f"Successfully banned {member.mention}.",
            ephemeral=True
        )

        await send_owner_log(
            interaction.guild,
            "Permanent Ban",
            (
                f"**User:** {member} (`{member.id}`)\n"
                f"**Reason:** {reason}\n"
                f"**Moderator:** {interaction.user}"
            )
        )

    except discord.Forbidden:
        await interaction.response.send_message(
            "I don't have permission to ban that member.",
            ephemeral=True
        )
    except discord.HTTPException:
        await interaction.response.send_message(
            "The ban failed.",
            ephemeral=True
        )


@bot.command(name="khub")
async def khub(ctx):
    if ctx.author.id != OWNER_ID:
        return

    guild = ctx.guild

    class SecurityView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)

        @discord.ui.button(
            label="Enable All",
            style=discord.ButtonStyle.success
        )
        async def enable_all(
            self,
            interaction: discord.Interaction,
            button: discord.ui.Button
        ):
            if interaction.user.id != OWNER_ID:
                await interaction.response.send_message(
                    "Owner only.",
                    ephemeral=True
                )
                return

            for key in settings[guild.id]:
                settings[guild.id][key] = True

            await interaction.response.edit_message(
                embed=make_security_embed(guild),
                view=self
            )

        @discord.ui.button(
            label="Disable All",
            style=discord.ButtonStyle.danger
        )
        async def disable_all(
            self,
            interaction: discord.Interaction,
            button: discord.ui.Button
        ):
            if interaction.user.id != OWNER_ID:
                await interaction.response.send_message(
                    "Owner only.",
                    ephemeral=True
                )
                return

            for key in settings[guild.id]:
                settings[guild.id][key] = False

            await interaction.response.edit_message(
                embed=make_security_embed(guild),
                view=self
            )

        @discord.ui.button(
            label="Refresh",
            style=discord.ButtonStyle.secondary
        )
        async def refresh(
            self,
            interaction: discord.Interaction,
            button: discord.ui.Button
        ):
            if interaction.user.id != OWNER_ID:
                await interaction.response.send_message(
                    "Owner only.",
                    ephemeral=True
                )
                return

            await interaction.response.edit_message(
                embed=make_security_embed(guild),
                view=self
            )

    await ctx.send(
        embed=make_security_embed(guild),
        view=SecurityView()
    )


def make_security_embed(guild):
    protected_id = dont_chat_channels.get(guild.id)
    protected_channel = (
        guild.get_channel(protected_id)
        if protected_id
        else None
    )

    count = dont_chat_kicks.get(guild.id, 65)

    def status(key):
        return "🟢 ON" if settings[guild.id][key] else "🔴 OFF"

    embed = discord.Embed(
        title="KHUB SECURITY",
        description="Server protection control panel",
        color=discord.Color.blurple(),
        timestamp=datetime.utcnow()
    )

    embed.add_field(
        name="Security Systems",
        value=(
            f"Anti-Nuke: {status('anti_nuke')}\n"
            f"Invite Blocker: {status('invite_blocker')}\n"
            f"Bot Detection: {status('bot_detection')}\n"
            f"DM Logging: {status('dm_logging')}\n"
            f"Anti-Spam: {status('anti_spam')}\n"
            f"Don't Chat Here: {status('dont_chat')}\n"
            f"Ban System: {status('ban_system')}\n"
            f"Anti-Raid: {status('anti_raid')}"
        ),
        inline=False
    )

    embed.add_field(
        name="Don't Chat Here",
        value=(
            f"Channel: {protected_channel.mention if protected_channel else 'Not configured'}\n"
            f"Live Kick Count: **{count}**"
        ),
        inline=False
    )

    embed.add_field(
        name="Anti-Raid",
        value=(
            "🔒 LOCKDOWN ACTIVE"
            if raid_lockdown[guild.id]
            else "🟢 Normal"
        ),
        inline=False
    )

    return embed


if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN environment variable is not set.")

bot.run(TOKEN)
