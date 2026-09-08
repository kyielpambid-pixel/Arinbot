import datetime
import discord
from discord import app_commands
from discord.ext import commands
import os
import re
import aiohttp
import asyncio
import sys

# ================== ENVIRONMENT VARIABLES ==================
TOKEN = os.getenv('DISCORD_TOKEN')
if not TOKEN:
    print("ERROR: Missing DISCORD_TOKEN environment variable. Please set it in your hosting.")
    sys.exit(1)

# ================== CONFIGURATION ==================
GUILD_ID = 1378412813147705456

# ================== ALLOWED ROLE NAMES ==================
ALLOWED_ROLES = [
    "Arin",
    "Owner",
    "founder",
    "Administrator",
    "Community Manager",
    "mod",
    "Nyxen",
    "The Two With Aura"
]

# ================== CHANNEL ID ==================
CHANNEL_GET_SCRIPT = 1544043877508448376
CHANNEL_SUPPORTED_SCRIPT = 1544045706061553685
CHANNEL_GET_KEY = 1544045748117700658
CHANNEL_HOW_TO_KEY = 1544045837565427732

# ================== CHANNELS PARA SA AUTO REPLIES ==================
AUTO_REPLY_CHANNELS = [
    1502628012762333245
]

# ================== LURAPH DEOBFUSCATOR ==================
LURAPH_URL_PATTERN = re.compile(r'https?://[^\s<>"\']+', re.IGNORECASE)

def is_luraph(content: str) -> bool:
    return bool(
        re.search(r'\bluraph\b', content, re.IGNORECASE) or
        re.search(r'luraph\.vip', content, re.IGNORECASE) or
        re.search(r'protected\s+by\s+luraph', content, re.IGNORECASE)
    )

def extract_luraph_loader_url(content: str) -> str:
    for match in LURAPH_URL_PATTERN.finditer(content):
        url = match.group(0).rstrip('.,;!?')
        if 'luraph' in url.lower():
            continue
        return url
    return None

def deobfuscate_luraph(content: str) -> str:
    # 1. Alisin ang mga comments
    cleaned = re.sub(r'--.*$', '', content, flags=re.MULTILINE)
    
    # 2. Kunin ang loader URL
    loader_url = extract_luraph_loader_url(cleaned)
    
    # 3. Kunin ang mga strings gamit ang string.char
    strings = []
    for match in re.finditer(r'string\.char\(([^)]+)\)', cleaned):
        try:
            parts = match.group(1).split(',')
            decoded = ''.join(chr(int(p.strip())) for p in parts if p.strip().isdigit())
            strings.append(decoded)
        except:
            pass
    
    # 4. I-replace ang mga obfuscated variables
    for s in strings:
        cleaned = cleaned.replace(f'string.char({",".join(str(ord(c)) for c in s)})', f'"{s}"')
    
    # 5. Hanapin ang loadstring o game:HttpGet para gumawa ng bagong loader
    if loader_url:
        return f'-- Luraph 14.7/14.8 Deobfuscated\n-- Original Loader: {loader_url}\n\nloadstring(game:HttpGet("{loader_url}"))()'
    
    # 6. Kung walang URL, ibalik yung cleaned na code
    return cleaned.strip()

async def try_luraph_deobfuscation(content):
    if not is_luraph(content):
        return None
    try:
        return await asyncio.to_thread(deobfuscate_luraph, content)
    except Exception as e:
        print(f"[Luraph] Deobfuscation failed: {e}")
        return None

# ================== INTENTS ==================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=".", intents=intents)

    async def setup_hook(self):
        guild = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        print("Commands synced directly to server!")

bot = MyBot()

# ================== SLASH COMMAND: /gm ==================
@bot.tree.command(name="gm", description="Send an embedded custom message to a channel")
@app_commands.checks.has_any_role(*ALLOWED_ROLES)
@app_commands.describe(
    channel="Select the destination channel",
    message="Insert your custom message",
    title="Insert your title (optional)",
    mention="Optional mention (e.g. @everyone, @RoleName)",
    game_link="Roblox game link (optional)"
)
async def gm_cmd(interaction: discord.Interaction, channel: discord.TextChannel, message: str, title: str = None, mention: str = None, game_link: str = None):
    await interaction.response.defer(ephemeral=True)
    try:
        embed = discord.Embed(
            description=f"```lua\n{message}\n```",
            color=0x9b59b6
        )
        if title:
            embed.title = title
        if game_link:
            embed.url = game_link
        
        if mention:
            await channel.send(mention)
        
        await channel.send(embed=embed)
        await interaction.followup.send(f"✅ Message successfully sent to {channel.mention}!", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

# ================== SLASH COMMAND: /verify ==================
@bot.tree.command(name="verify", description="Create a verification message with a button")
@app_commands.checks.has_any_role(*ALLOWED_ROLES)
@app_commands.describe(
    channel="Channel where the verification message will be posted",
    message="Your custom message for the verification",
    unverified_role="Select the Unverified role to remove",
    verified_role="Select the Verified role to give",
    emoji="Select the emoji you want to use on the button"
)
async def verify_cmd(interaction: discord.Interaction, channel: discord.TextChannel, message: str, unverified_role: discord.Role, verified_role: discord.Role, emoji: str):
    await interaction.response.defer(ephemeral=True)
    try:
        embed = discord.Embed(
            description=f"```lua\n{message}\n```",
            color=0x9b59b6
        )
        embed.title = "🔐 **Verification**"
        view = VerifyView(unverified_role, verified_role, emoji)
        view.children[0].emoji = emoji
        await channel.send(embed=embed, view=view)
        await interaction.followup.send(f"✅ Verification message sent to {channel.mention}!", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

# ================== SLASH COMMAND: /update ==================
@bot.tree.command(name="update", description="Send an update announcement with features and changes")
@app_commands.checks.has_any_role(*ALLOWED_ROLES)
@app_commands.describe(
    channel="Select the destination channel",
    features="Insert your message. It will be auto-formatted as features (use + for additions)",
    version="Insert the version (e.g. 3.0.0)",
    game_name="Insert the name of the game (e.g. Blox Fruits)"
)
async def update_cmd(interaction: discord.Interaction, channel: discord.TextChannel, features: str, version: str = "1.0", game_name: str = None):
    await interaction.response.defer(ephemeral=True)
    try:
        embed = discord.Embed(
            title=f"🔄 [UPD] {game_name if game_name else 'Script'} updated",
            color=0x9b59b6
        )
        embed.timestamp = datetime.datetime.utcnow()
        embed.add_field(name="✨ New Features", value=f"```diff\n{features}\n```", inline=False)
        embed.add_field(name="Version", value=version, inline=False)
        await channel.send(embed=embed)
        await interaction.followup.send(f"✅ Update message sent to {channel.mention}!", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

# ================== SLASH COMMAND: /welcome ==================
@bot.tree.command(name="welcome", description="Configure the welcome message system")
@app_commands.checks.has_any_role(*ALLOWED_ROLES)
@app_commands.describe(
    action="Choose what to do with the welcome system",
    channel="The channel where the welcome message will be posted",
    message="Your custom welcome message (use {user} for the member's mention)"
)
@app_commands.choices(action=[
    app_commands.Choice(name="Enable", value="enable"),
    app_commands.Choice(name="Disable", value="disable"),
    app_commands.Choice(name="Set Channel", value="set_channel"),
    app_commands.Choice(name="Set Message", value="set_message")
])
async def welcome_cmd(interaction: discord.Interaction, action: str, channel: discord.TextChannel = None, message: str = None):
    await interaction.response.defer(ephemeral=True)
    try:
        settings = load_settings()
        guild_id = str(interaction.guild_id)
        if guild_id not in settings:
            settings[guild_id] = {}
        if action == "enable":
            settings[guild_id]['enabled'] = True
            save_settings(settings)
            await interaction.followup.send("✅ Welcome system **enabled**!", ephemeral=True)
        elif action == "disable":
            settings[guild_id]['enabled'] = False
            save_settings(settings)
            await interaction.followup.send("✅ Welcome system **disabled**!", ephemeral=True)
        elif action == "set_channel":
            if not channel:
                await interaction.followup.send("❌ You must provide a channel!", ephemeral=True)
                return
            settings[guild_id]['channel_id'] = channel.id
            save_settings(settings)
            await interaction.followup.send(f"✅ Welcome channel set to {channel.mention}!", ephemeral=True)
        elif action == "set_message":
            if not message:
                await interaction.followup.send("❌ You must provide a message!", ephemeral=True)
                return
            settings[guild_id]['message'] = message
            save_settings(settings)
            await interaction.followup.send(f"✅ Welcome message set!", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

# ================== WELCOME EVENT (Kapag may sumali) ==================
@bot.event
async def on_member_join(member):
    try:
        settings = load_settings()
        guild_id = str(member.guild_id)
        if guild_id in settings and settings[guild_id].get('enabled', False):
            channel_id = settings[guild_id].get('channel_id')
            message_template = settings[guild_id].get('message', 'Welcome {user} to the server!')
            welcome_channel = member.guild.get_channel(channel_id)
            if welcome_channel:
                final_message = message_template.replace('{user}', member.mention).replace('{server}', member.guild.name)
                embed = discord.Embed(
                    title="👋 Welcome!",
                    description=final_message,
                    color=0x9b59b6
                )
                embed.set_thumbnail(url=member.display_avatar.url)
                embed.timestamp = datetime.datetime.utcnow()
                await welcome_channel.send(embed=embed)
    except Exception as e:
        print(f"Welcome Error: {e}")

# ================== AUTO REPLIES ==================
@bot.event
async def on_message(message):
    if message.author.bot:
        return
    if message.channel.id not in AUTO_REPLY_CHANNELS:
        await bot.process_commands(message)
        return
    content = message.content.lower()
    if "key" in content:
        reply = (
            f"🔑 You can get key here:\n"
            f"<#{CHANNEL_GET_KEY}>\n\n"
            f"📖 How to get key? Here:\n"
            f"<#{CHANNEL_HOW_TO_KEY}>"
        )
        await message.reply(reply)
    elif "script" in content:
        reply = (
            f"📜 You can get the script here:\n"
            f"<#{CHANNEL_GET_SCRIPT}>\n\n"
            f"🎮 Supported game here:\n"
            f"<#{CHANNEL_SUPPORTED_SCRIPT}>"
        )
        await message.reply(reply)
    elif "supported" in content:
        reply = (
            f"🎮 Supported game here:\n"
            f"<#{CHANNEL_SUPPORTED_SCRIPT}>"
        )
        await message.reply(reply)
    elif "how to get key" in content:
        reply = (
            f"📖 How to get key? Here:\n"
            f"<#{CHANNEL_HOW_TO_KEY}>"
        )
        await message.reply(reply)
    await bot.process_commands(message)

# ================== PREFIX COMMANDS: .kick, .ban, .to ==================
@bot.command(name="kick")
@commands.has_any_role(*ALLOWED_ROLES)
async def kick_command(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    if member.id == ctx.guild.owner_id:
        await ctx.send("❌ You cannot perform this action on the server owner!")
        return
    try:
        await member.kick(reason=reason)
        await ctx.send(f"✅ **{member.mention}** has been kicked by {ctx.author.mention}. Reason: {reason}")
    except Exception as e:
        await ctx.send(f"❌ Error: {e}")

@bot.command(name="ban")
@commands.has_any_role(*ALLOWED_ROLES)
async def ban_command(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    if member.id == ctx.guild.owner_id:
        await ctx.send("❌ You cannot perform this action on the server owner!")
        return
    try:
        await member.ban(reason=reason)
        await ctx.send(f"✅ **{member.mention}** has been banned by {ctx.author.mention}. Reason: {reason}")
    except Exception as e:
        await ctx.send(f"❌ Error: {e}")

@bot.command(name="to")
@commands.has_any_role(*ALLOWED_ROLES)
async def to_command(ctx, member: discord.Member, minutes: int, *, reason: str = "No reason provided"):
    if member.id == ctx.guild.owner_id:
        await ctx.send("❌ You cannot perform this action on the server owner!")
        return
    try:
        duration = datetime.timedelta(minutes=minutes)
        await member.timeout(duration, reason=reason)
        await ctx.send(f"✅ **{member.mention}** has been timed out for {minutes} minute(s) by {ctx.author.mention}. Reason: {reason}")
    except Exception as e:
        await ctx.send(f"❌ Error: {e}")

# ================== VERIFICATION VIEW ==================
class VerifyView(discord.ui.View):
    def __init__(self, unverified_role, verified_role, emoji):
        super().__init__(timeout=None)
        self.unverified_role = unverified_role
        self.verified_role = verified_role
        self.emoji = emoji
    @discord.ui.button(label="Verify Me", style=discord.ButtonStyle.green)
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            member = interaction.user
            guild = interaction.guild
            if self.verified_role:
                await member.add_roles(self.verified_role)
            if self.unverified_role and self.unverified_role in member.roles:
                await member.remove_roles(self.unverified_role)
            await interaction.response.send_message("✅ **You are now verified!**", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)

# ================== DATA FUNCTIONS ==================
def load_settings():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_settings(settings):
    with open(DATA_FILE, 'w') as f:
        json.dump(settings, f, indent=4)

# ================== DATA FILE ==================
DATA_FILE = 'welcome_settings.json'

# ================== ERROR HANDLERS ==================
@verify_cmd.error
@update_cmd.error
@welcome_cmd.error
async def on_role_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingAnyRole):
        if interaction.response.is_done():
            await interaction.followup.send("❌ You do not have the required role to use this command!", ephemeral=True)
        else:
            await interaction.response.send_message("❌ You do not have the required role to use this command!", ephemeral=True)

@kick_command.error
@ban_command.error
@to_command.error
async def prefix_error(ctx, error):
    if isinstance(error, commands.MissingAnyRole):
        await ctx.send("❌ You do not have the required role to use this command!")

# ================== PREFIX HELP COMMAND ==================
class EmbedHelpCommand(commands.HelpCommand):
    async def send_bot_help(self, mapping):
        embed = discord.Embed(
            title="📚 Bot Commands",
            description="Ito ang mga commands na magagamit mo. Pindutin mo yung prefix `.` bago ang command.",
            color=0x9b59b6
        )
        for cog, commands_list in mapping.items():
            if commands_list:
                command_names = ", ".join([f"`{cmd.name}`" for cmd in commands_list if not cmd.hidden])
                if command_names:
                    embed.add_field(name=cog.qualified_name if cog else "General", value=command_names, inline=False)
        await self.get_destination().send(embed=embed)

    async def send_command_help(self, command):
        embed = discord.Embed(
            title=f"Command: `{command.name}`",
            description=f"**Description:** {command.help or 'No description provided'}\n\n**Usage:** `{self.get_command_signature(command)}`",
            color=0x9b59b6
        )
        await self.get_destination().send(embed=embed)

# I-setup yung bot help command
bot.help_command = EmbedHelpCommand()

# ================== RUN BOT ==================
bot.run(TOKEN)
