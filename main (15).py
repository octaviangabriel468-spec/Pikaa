import discord
from discord.ext import commands, tasks
import os
import random
import asyncio
import json
from datetime import datetime, timedelta
import re
import time

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Global variables for combined functionality
tournaments = {}  # {guild_id: Tournament}
sp_data = {}  # {guild_id: {user_id: sp_amount}}
role_permissions = {}  # {guild_id: {'htr': [role_ids], 'adr': [role_ids], 'tlr': [role_ids]}}
teams = {}  # {guild_id: {team_id: [player1, player2]}}
team_invitations = {}  # {guild_id: {user_id: [inviter_id1, inviter_id2, ...]}}
player_teams = {}  # {guild_id: {user_id: team_id}}
log_channels = {}  # {guild_id: channel_id}
bracket_roles = {}  # {guild_id: {user_id: [emoji1, emoji2, ...]}}
logs_channels = {}  # {guild_id: channel_id} for !logs command
logs_messages = {}  # {guild_id: message_id} to track auto-updating messages
active_games = {}  # {guild_id: {'number': int, 'range': [min, max], 'channel_id': int}}
host_registrations = {'active': False, 'hosters': [], 'max_hosters': 10}

# Tournament class
class Tournament:
    def __init__(self):
        self.players = []
        self.max_players = 0
        self.active = False
        self.channel = None
        self.target_channel = None
        self.message = None
        self.rounds = []
        self.results = []
        self.eliminated = []
        self.fake_count = 1
        self.map = ""
        self.abilities = ""
        self.prize = ""
        self.title = ""
        self.mode = "1v1"

# Fake player class for tournaments
class FakePlayer:
    def __init__(self, name, user_id):
        self.display_name = name
        self.name = name
        self.nick = name
        self.id = user_id
        self.mention = f"@{user_id}"

    def __str__(self):
        return self.mention

def get_tournament(guild_id):
    """Get tournament for specific guild"""
    if guild_id not in tournaments:
        tournaments[guild_id] = Tournament()
    return tournaments[guild_id]

# JSON Database functions
def init_db():
    """Initialize JSON database files"""
    db_files = {
        'warnings.json': [],
        'user_levels.json': {},
        'guild_config.json': {},
        'level_roles.json': {},
        'automod_warnings.json': {},
        'user_accounts.json': {},
        'tickets.json': [],
        'user_data.json': {}
    }
    
    for filename, default_data in db_files.items():
        if not os.path.exists(filename):
            with open(filename, 'w') as f:
                json.dump(default_data, f)

def load_json(filename):
    """Load data from JSON file"""
    try:
        with open(filename, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {} if filename != 'warnings.json' and filename != 'tickets.json' else []

def save_json(filename, data):
    """Save data to JSON file"""
    with open(filename, 'w') as f:
        json.dump(data, f, indent=2)

# Load and save data functions for SP system
def load_data():
    global sp_data, role_permissions, log_channels, bracket_roles
    try:
        with open('user_data.json', 'r') as f:
            data = json.load(f)
            sp_data = data.get('sp_data', {})
            role_permissions = data.get('role_permissions', {})
            log_channels = data.get('log_channels', {})
            bracket_roles = data.get('bracket_roles', {})
            # Teams data is not loaded since it contains Discord objects
            teams.clear()
            team_invitations.clear()
            player_teams.clear()
    except FileNotFoundError:
        pass

def save_data():
    data = {
        'sp_data': sp_data,
        'role_permissions': role_permissions,
        'log_channels': log_channels,
        'bracket_roles': bracket_roles
    }
    with open('user_data.json', 'w') as f:
        json.dump(data, f)

def add_sp(guild_id, user_id, sp):
    """Add seasonal points to a user"""
    guild_str = str(guild_id)
    user_str = str(user_id)

    if guild_str not in sp_data:
        sp_data[guild_str] = {}

    if user_str not in sp_data[guild_str]:
        sp_data[guild_str][user_str] = 0

    sp_data[guild_str][user_str] += sp
    save_data()
    # Update logs message when SP changes
    asyncio.create_task(update_logs_message(guild_id))

def get_sp(guild_id, user_id):
    """Get seasonal points for a user"""
    guild_str = str(guild_id)
    user_str = str(user_id)
    return sp_data.get(guild_str, {}).get(user_str, 0)

# Helper functions
def parse_time(time_str):
    """Parse time string like '1h', '30m', '2d' into timedelta"""
    if not time_str:
        return None
    
    match = re.match(r'(\d+)([mhdmo]+)', time_str.lower())
    if not match:
        return None
    
    amount, unit = match.groups()
    amount = int(amount)
    
    if unit == 'm':
        return timedelta(minutes=amount)
    elif unit == 'h':
        return timedelta(hours=amount)
    elif unit == 'd':
        return timedelta(days=amount)
    elif unit == 'mo':
        return timedelta(days=amount * 30)
    
    return None

async def is_staff(ctx):
    """Check if user is staff"""
    if ctx.author.guild_permissions.manage_messages:
        return True
    
    guild_config = load_json('guild_config.json')
    config = guild_config.get(str(ctx.guild.id), {})
    staff_roles = config.get('staff_roles', '')
    
    if staff_roles:
        staff_role_ids = staff_roles.split(',')
        user_role_ids = [str(role.id) for role in ctx.author.roles]
        return any(role_id in staff_role_ids for role_id in user_role_ids)
    
    return False

def has_permission(user, guild_id, permission_type):
    """Check if user has specific permission type"""
    guild_str = str(guild_id)
    if guild_str not in role_permissions:
        return False

    # ADR has all permissions
    if 'adr' in role_permissions[guild_str]:
        user_role_ids = [role.id for role in user.roles]
        adr_role_ids = role_permissions[guild_str]['adr']
        if any(role_id in adr_role_ids for role_id in user_role_ids):
            return True

    if permission_type not in role_permissions[guild_str]:
        return False

    user_role_ids = [role.id for role in user.roles]
    allowed_role_ids = role_permissions[guild_str][permission_type]

    return any(role_id in allowed_role_ids for role_id in user_role_ids)

def get_player_display_name(player, guild_id=None):
    """Get player display name"""
    if isinstance(player, FakePlayer):
        return player.display_name

    if hasattr(player, 'display_name'):
        return player.display_name
    elif hasattr(player, 'name'):
        return player.name
    else:
        return str(player)

def get_team_id(guild_id, user_id):
    """Get team ID for a user"""
    guild_str = str(guild_id)
    user_str = str(user_id)
    return player_teams.get(guild_str, {}).get(user_str)

def get_team_members(guild_id, team_id):
    """Get all members of a team"""
    guild_str = str(guild_id)
    return teams.get(guild_str, {}).get(team_id, [])

def get_teammate(guild_id, user_id):
    """Get the teammate of a user"""
    team_id = get_team_id(guild_id, user_id)
    if not team_id:
        return None
    team_members = get_team_members(guild_id, team_id)
    for member in team_members:
        if member.id != user_id:
            return member
    return None

def create_team(guild_id, player1, player2):
    """Create a new team with two players"""
    guild_str = str(guild_id)

    if guild_str not in teams:
        teams[guild_str] = {}
        player_teams[guild_str] = {}

    # Generate unique team ID
    team_id = f"team_{len(teams[guild_str]) + 1}_{guild_id}"

    teams[guild_str][team_id] = [player1, player2]
    player_teams[guild_str][str(player1.id)] = team_id
    player_teams[guild_str][str(player2.id)] = team_id

    return team_id

def remove_team(guild_id, team_id):
    """Remove a team and its members"""
    guild_str = str(guild_id)

    if guild_str in teams and team_id in teams[guild_str]:
        # Remove players from player_teams
        for player in teams[guild_str][team_id]:
            if str(player.id) in player_teams[guild_str]:
                del player_teams[guild_str][str(player.id)]

        # Remove team
        del teams[guild_str][team_id]

def get_team_display_name(guild_id, team_members):
    """Get display name for a team"""
    if len(team_members) == 2:
        name1 = get_player_display_name(team_members[0], guild_id)
        name2 = get_player_display_name(team_members[1], guild_id)
        return f"{name1} & {name2}"
    return "Unknown Team"

async def log_command(guild_id, user, command, details=""):
    """Log tournament commands to designated channel"""
    guild_str = str(guild_id)
    if guild_str not in log_channels:
        return

    try:
        channel = bot.get_channel(log_channels[guild_str])
        if not channel:
            return

        embed = discord.Embed(
            title="📋 Tournament Command Used",
            color=0x3498db,
            timestamp=datetime.now()
        )

        embed.add_field(name="User", value=user.display_name, inline=True)
        embed.add_field(name="Command", value=command, inline=True)
        if details:
            embed.add_field(name="Details", value=details, inline=False)

        await channel.send(embed=embed)
    except Exception as e:
        print(f"Error logging command: {e}")

# Automod functions
BAD_WORDS = ['badword1', 'badword2', 'spam', 'test_bad']

async def check_spam(message):
    """Check if message is spam (5 same consecutive messages in 5 seconds)"""
    if not message.guild:
        return False
    
    channel = message.channel
    count = 0
    now = datetime.now()
    last_content = None
    
    async for msg in channel.history(limit=6):
        if msg.author == message.author:
            msg_time = msg.created_at.replace(tzinfo=None)
            time_diff = (now - msg_time).total_seconds()
            
            if time_diff <= 5:
                if last_content is None:
                    last_content = msg.content
                    count = 1
                elif msg.content == last_content:
                    count += 1
                else:
                    break
            else:
                break
        else:
            break
    
    return count >= 5

async def check_emoji_spam(message):
    """Check if user sends 5 consecutive emoji messages in 5 seconds"""
    if not message.guild:
        return False
    
    channel = message.channel
    count = 0
    now = datetime.now()
    emoji_pattern = r'<:[^:]+:\d+>|[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF]'
    
    async for msg in channel.history(limit=6):
        if msg.author == message.author:
            msg_time = msg.created_at.replace(tzinfo=None)
            time_diff = (now - msg_time).total_seconds()
            
            if time_diff <= 5:
                emojis = re.findall(emoji_pattern, msg.content)
                if len(emojis) > 5:  # Message has more than 5 emojis
                    count += 1
                else:
                    break
            else:
                break
        else:
            break
    
    return count >= 5

async def check_bad_words(content):
    """Check if message contains 3 or more bad words"""
    content_lower = content.lower()
    bad_word_count = 0
    
    for bad_word in BAD_WORDS:
        words = content_lower.split()
        for word in words:
            if word.startswith(bad_word) or word.endswith(bad_word) or word == bad_word:
                bad_word_count += 1
                break
    
    return bad_word_count >= 3

async def check_links(content):
    """Check if message contains links"""
    url_pattern = r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'
    return bool(re.search(url_pattern, content))

# NEW COMMANDS IMPLEMENTATION

async def update_logs_message(guild_id):
    """Update the logs message when data changes"""
    guild_str = str(guild_id)
    
    if guild_str not in logs_channels or guild_str not in logs_messages:
        return
    
    try:
        channel = bot.get_channel(logs_channels[guild_str])
        if not channel or not hasattr(channel, 'fetch_message'):
            return
        
        message = await channel.fetch_message(logs_messages[guild_str])
        if not message:
            return
        
        # Generate updated embed
        embeds = await generate_logs_embeds(guild_id)
        
        if embeds:
            # Update the first message
            await message.edit(embed=embeds[0])
            
            # If there are additional embeds, send them as new messages
            if len(embeds) > 1:
                for embed in embeds[1:]:
                    await channel.send(embed=embed)
    except:
        pass

async def generate_logs_embeds(guild_id):
    """Generate embeds for the logs command"""
    guild = bot.get_guild(guild_id)
    if not guild:
        return []
    
    guild_str = str(guild_id)
    embeds = []
    current_embed = discord.Embed(
        title="📊 Server Activity Logs",
        color=0x00ff00,
        timestamp=datetime.now()
    )
    
    member_count = 0
    max_fields_per_embed = 25
    field_count = 0
    
    for member in guild.members:
        if member.bot:
            continue
            
        user_str = str(member.id)
        
        # Get linked account
        user_accounts = load_json('user_accounts.json')
        account_key = f"{guild_id}_{member.id}"
        account_data = user_accounts.get(account_key, {})
        linked_account = account_data.get('ign', 'Not Linked') if isinstance(account_data, dict) else 'Not Linked'
        
        # Get seasonal points
        sp_amount = get_sp(guild_id, member.id)
        
        # Get bracket roles (emojis)
        bracket_emojis = ''.join(bracket_roles.get(guild_str, {}).get(user_str, []))
        if not bracket_emojis:
            bracket_emojis = 'None'
        
        # Only show members who have at least one of: linked account, SP, or bracket roles
        if linked_account != 'Not Linked' or sp_amount > 0 or bracket_emojis != 'None':
            field_value = f"• **Linked Account:** {member.mention} - {linked_account}\n"
            field_value += f"• **Seasonal Points:** {member.mention} - {sp_amount} SP\n"
            field_value += f"• **Bracket Roles:** {member.mention} - {bracket_emojis}"
            
            current_embed.add_field(
                name=f"👤 {member.display_name}",
                value=field_value,
                inline=False
            )
            
            member_count += 1
            field_count += 1
            
            # Check if we need a new embed
            if field_count >= max_fields_per_embed:
                embeds.append(current_embed)
                current_embed = discord.Embed(
                    title="📊 Server Activity Logs (Continued)",
                    color=0x00ff00,
                    timestamp=datetime.now()
                )
                field_count = 0
    
    if field_count > 0 or member_count == 0:
        if member_count == 0:
            current_embed.add_field(
                name="No Active Members",
                value="No members with linked accounts, seasonal points, or bracket roles found.",
                inline=False
            )
        embeds.append(current_embed)
    
    return embeds

@bot.command()
async def logs(ctx, channel: discord.TextChannel):
    """Display server activity logs with linked accounts, SP, and bracket roles"""
    if not await is_staff(ctx):
        await ctx.send("You don't have permission to use this command.")
        return
    
    guild_str = str(ctx.guild.id)
    logs_channels[guild_str] = channel.id
    
    embeds = await generate_logs_embeds(ctx.guild.id)
    
    if embeds:
        # Send the first embed and store its message ID for updates
        message = await channel.send(embed=embeds[0])
        logs_messages[guild_str] = message.id
        
        # Send additional embeds if needed
        for embed in embeds[1:]:
            await channel.send(embed=embed)
        
        await ctx.send(f"✅ Logs have been posted in {channel.mention} and will auto-update when data changes!")
    else:
        await ctx.send("❌ No data to display.")

@bot.command()
async def update(ctx, number: int):
    """Read past messages and update user data"""
    if not await is_staff(ctx):
        await ctx.send("You don't have permission to use this command.")
        return
    
    if number < 1 or number > 1000:
        await ctx.send("Number must be between 1 and 1000.")
        return
    
    await ctx.send(f"📖 Reading {number} messages and updating user data...")
    
    updated_count = 0
    
    async for message in ctx.channel.history(limit=number):
        if message.author.bot:
            continue
        
        # Process leveling for this message
        await process_leveling(message)
        
        # Check for SP-related commands or activities in the message
        if message.content.startswith('!'):
            # This could be enhanced to parse specific commands that affect SP
            pass
        
        updated_count += 1
    
    # Update logs message after processing
    await update_logs_message(ctx.guild.id)
    
    await ctx.send(f"✅ Updated data for {updated_count} messages!")

@bot.command()
async def game(ctx, game_range: str):
    """Start a number guessing game"""
    if not await is_staff(ctx):
        await ctx.send("You don't have permission to use this command.")
        return
    
    # Parse the range (e.g., "1-50")
    try:
        if '-' in game_range:
            min_num, max_num = map(int, game_range.split('-'))
        else:
            await ctx.send("❌ Please use the format: !game 1-50")
            return
        
        if min_num >= max_num or min_num < 1 or max_num > 10000:
            await ctx.send("❌ Invalid range. Use a valid range like 1-50")
            return
        
    except ValueError:
        await ctx.send("❌ Please use the format: !game 1-50")
        return
    
    # Select random number
    selected_number = random.randint(min_num, max_num)
    
    guild_str = str(ctx.guild.id)
    active_games[guild_str] = {
        'number': selected_number,
        'range': [min_num, max_num],
        'channel_id': ctx.channel.id
    }
    
    embed = discord.Embed(
        title="🎲 Number Guessing Game Started!",
        description=f"I've selected a number between **{min_num}** and **{max_num}**!\n\nGuess the number by typing it in chat!",
        color=0xff9500
    )
    embed.set_footer(text=f"Range: {min_num} - {max_num}")
    
    await ctx.send(embed=embed)

# Bot events
@bot.event
async def on_ready():
    print(f'{bot.user} has logged in!')
    init_db()
    load_data()
    if not level_check.is_running():
        level_check.start()
    
    # Add persistent views for buttons to work after restart
    bot.add_view(TournamentView())
    bot.add_view(TournamentConfigView(None))
    bot.add_view(HosterRegistrationView())
    bot.add_view(AccountLinkView())
    
    print("🔧 Bot is ready and all systems operational!")

@bot.event
async def on_member_join(member):
    """Handle new member joins for welcomer system"""
    guild_id = str(member.guild.id)
    
    guild_config = load_json('guild_config.json')
    config = guild_config.get(guild_id, {})
    
    if config.get('welcomer_enabled') and config.get('welcomer_channel'):
        channel = bot.get_channel(config['welcomer_channel'])
        if channel:
            welcome_message = f"Welcome! <@{member.id}> Thanks for joining my server you are **GOAT** <:w_trkis:1400194042234667120> <:GOAT:1400194575125188811>"
            await channel.send(welcome_message)

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    
    # Check for number guessing game
    guild_str = str(message.guild.id)
    if guild_str in active_games and message.channel.id == active_games[guild_str]['channel_id']:
        try:
            guessed_number = int(message.content.strip())
            correct_number = active_games[guild_str]['number']
            
            if guessed_number == correct_number:
                embed = discord.Embed(
                    title="🎉 Congratulations!",
                    description=f"{message.author.mention} guessed the correct number: **{correct_number}**!",
                    color=0x00ff00
                )
                await message.channel.send(embed=embed)
                
                # Award SP for winning
                add_sp(message.guild.id, message.author.id, 1)
                
                # Remove the active game
                del active_games[guild_str]
        except ValueError:
            pass  # Not a number, ignore
    
    # Process leveling
    await process_leveling(message)
    
    # Process automod
    await process_automod(message)
    
    await bot.process_commands(message)

async def process_leveling(message):
    """Process user leveling system"""
    if not message.guild:
        return
    
    user_id = str(message.author.id)
    guild_id = str(message.guild.id)
    
    user_levels = load_json('user_levels.json')
    key = f"{guild_id}_{user_id}"
    
    now = datetime.now().isoformat()
    
    if key in user_levels:
        user_data = user_levels[key]
        last_message = datetime.fromisoformat(user_data.get('last_message', now))
        
        if (datetime.now() - last_message).total_seconds() >= 60:
            user_data['xp'] = user_data.get('xp', 0) + 15
            new_level = user_data['xp'] // 100
            old_level = user_data.get('level', 0)
            user_data['level'] = new_level
            user_data['last_message'] = now
            
            if new_level > old_level:
                await handle_level_up(message, new_level)
    else:
        user_levels[key] = {
            'xp': 15,
            'level': 0,
            'last_message': now
        }
    
    save_json('user_levels.json', user_levels)

async def handle_level_up(message, new_level):
    """Handle level up notification and role assignment"""
    guild_id = str(message.guild.id)
    user_id = message.author.id
    
    guild_config = load_json('guild_config.json')
    config = guild_config.get(guild_id, {}) if isinstance(guild_config, dict) else {}
    
    if config.get('leveling_channel'):
        channel = bot.get_channel(config['leveling_channel'])
        if channel and hasattr(channel, 'send'):
            await channel.send(
                f"**Thanks For Showing Your Activity <@{user_id}>! You just Stumbled Up To Level **{new_level}**. Keep GOING!!!!!** <:abilities:1402690411759407185>"
            )
    
    level_roles = load_json('level_roles.json')
    guild_roles = level_roles.get(guild_id, {}) if isinstance(level_roles, dict) else {}
    
    if str(new_level) in guild_roles:
        for role_id in guild_roles[str(new_level)]:
            role = message.guild.get_role(int(role_id))
            if role:
                try:
                    await message.author.add_roles(role)
                except:
                    pass

async def process_automod(message):
    """Process automod checks"""
    if not message.guild or message.author.guild_permissions.manage_messages:
        return
    
    guild_id = str(message.guild.id)
    guild_config = load_json('guild_config.json')
    config = guild_config.get(guild_id, {}) if isinstance(guild_config, dict) else {}
    
    if not config.get('automod_enabled'):
        return
    
    spam_channels = config.get('spam_channels', '').split(',')
    link_channels = config.get('link_channels', '').split(',')
    
    violations = []
    
    # Check spam (if not in spam channel)
    if str(message.channel.id) not in spam_channels:
        if await check_spam(message):
            violations.append("spam")
    
    # Check emoji spam
    if await check_emoji_spam(message):
        violations.append("emoji spam")
    
    # Check bad words
    if await check_bad_words(message.content):
        violations.append("inappropriate language")
    
    # Check links (if not in link channel)
    if str(message.channel.id) not in link_channels:
        if await check_links(message.content):
            violations.append("unauthorized links")
    
    if violations:
        await handle_automod_violation(message, violations, config.get('automod_log_channel'))

async def handle_automod_violation(message, violations, log_channel_id):
    """Handle automod violations"""
    user_id = str(message.author.id)
    guild_id = str(message.guild.id)
    
    automod_warnings = load_json('automod_warnings.json')
    key = f"{guild_id}_{user_id}"
    
    warning_count = automod_warnings.get(key, 0) + 1
    automod_warnings[key] = warning_count
    save_json('automod_warnings.json', automod_warnings)
    
    try:
        await message.delete()
    except:
        pass
    
    try:
        violation_text = ", ".join(violations)
        await message.author.send(
            f"Warning! Your message in **{message.guild.name}** was removed for: {violation_text}. "
            f"This is warning {warning_count}/3. At 3 warnings, you will be temporarily muted."
        )
    except:
        pass
    
    # Log the violation
    if log_channel_id:
        log_channel = bot.get_channel(log_channel_id)
        if log_channel and hasattr(log_channel, 'send'):
            embed = discord.Embed(
                title="Automod Violation",
                color=0xff0000,
                timestamp=datetime.now()
            )
            embed.add_field(name="User", value=f"{message.author.mention}", inline=True)
            embed.add_field(name="Channel", value=f"{message.channel.mention}", inline=True)
            embed.add_field(name="Violations", value=", ".join(violations), inline=True)
            embed.add_field(name="Warning Count", value=f"{warning_count}/3", inline=True)
            await log_channel.send(embed=embed)
    
    if warning_count >= 3:
        try:
            timeout_until = datetime.now() + timedelta(minutes=10)
            await message.author.edit(timed_out_until=timeout_until, reason="Automod: 3 violations reached")
            
            automod_warnings[key] = 0
            save_json('automod_warnings.json', automod_warnings)
            
            try:
                await message.author.send(
                    f"You have been automatically timed out for 10 minutes in **{message.guild.name}** "
                    "for reaching 3 automod violations."
                )
            except:
                pass
        except:
            # Fallback to old timeout method if edit doesn't work
            try:
                timeout_until = datetime.now() + timedelta(minutes=10)
                await message.author.timeout(timeout_until, reason="Automod: 3 violations reached")
            except:
                pass

# MODERATION COMMANDS
@bot.command()
async def warn(ctx, member: discord.Member, *, reason="No reason provided"):
    """Warn a user"""
    if not await is_staff(ctx):
        await ctx.send("You don't have permission to use this command.")
        return
    
    warnings = load_json('warnings.json')
    if not isinstance(warnings, list):
        warnings = []
    
    warning = {
        'user_id': member.id,
        'guild_id': ctx.guild.id,
        'reason': reason,
        'timestamp': datetime.now().isoformat()
    }
    warnings.append(warning)
    save_json('warnings.json', warnings)
    
    embed = discord.Embed(
        title="User Warned",
        color=0xffaa00,
        timestamp=datetime.now()
    )
    embed.add_field(name="User", value=member.mention, inline=True)
    embed.add_field(name="Reason", value=reason, inline=True)
    embed.add_field(name="Warned by", value=ctx.author.mention, inline=True)
    await ctx.send(embed=embed)

@bot.command()
async def warn_hs(ctx, member: discord.Member):
    """View user's warning history"""
    if not await is_staff(ctx):
        await ctx.send("You don't have permission to use this command.")
        return
    
    warnings = load_json('warnings.json')
    if not isinstance(warnings, list):
        warnings = []
    
    user_warnings = [w for w in warnings if w['user_id'] == member.id and w['guild_id'] == ctx.guild.id]
    
    if not user_warnings:
        await ctx.send(f"{member.mention} has no warnings.")
        return
    
    embed = discord.Embed(
        title=f"Warning History for {member.display_name}",
        color=0x0099ff,
        timestamp=datetime.now()
    )
    
    for i, warning in enumerate(user_warnings[-10:], 1):  # Show last 10 warnings
        embed.add_field(
            name=f"Warning {i}",
            value=f"**Reason:** {warning['reason']}\n**Date:** {warning['timestamp']}",
            inline=False
        )
    
    embed.set_footer(text=f"Total warnings: {len(user_warnings)}")
    await ctx.send(embed=embed)

@bot.command()
async def warn_rmv(ctx, member: discord.Member, number: int):
    """Remove a specific number of warnings from a user"""
    if not await is_staff(ctx):
        await ctx.send("You don't have permission to use this command.")
        return
    
    warnings = load_json('warnings.json')
    if not isinstance(warnings, list):
        warnings = []
    
    user_warnings = [w for w in warnings if w['user_id'] == member.id and w['guild_id'] == ctx.guild.id]
    
    if not user_warnings:
        await ctx.send(f"{member.mention} has no warnings to remove.")
        return
    
    # Remove the specified number of most recent warnings
    removed_count = min(number, len(user_warnings))
    user_warnings = user_warnings[:-removed_count]
    
    # Rebuild warnings list without the removed ones
    new_warnings = [w for w in warnings if not (w['user_id'] == member.id and w['guild_id'] == ctx.guild.id)]
    new_warnings.extend(user_warnings)
    save_json('warnings.json', new_warnings)
    
    await ctx.send(f"Removed {removed_count} warning(s) from {member.mention}.")

@bot.command()
async def mute(ctx, member: discord.Member, time_str: str = None, *, reason="No reason provided"):
    """Mute a user for a specified time (1m to 7d)"""
    if not await is_staff(ctx):
        await ctx.send("You don't have permission to use this command.")
        return
    
    if not time_str:
        await ctx.send("Please provide a time duration (e.g., 30m, 2h, 1d).")
        return
    
    duration = parse_time(time_str)
    if not duration:
        await ctx.send("Invalid time format. Use m (minutes), h (hours), d (days).")
        return
    
    # Check if duration is within limits (1m to 7d)
    min_duration = timedelta(minutes=1)
    max_duration = timedelta(days=7)
    
    if duration < min_duration or duration > max_duration:
        await ctx.send("Mute duration must be between 1 minute and 7 days.")
        return
    
    try:
        timeout_until = datetime.now() + duration
        await member.timeout(timeout_until, reason=f"Muted by {ctx.author.name}: {reason}")
        
        embed = discord.Embed(
            title="User Muted",
            color=0xff0000,
            timestamp=datetime.now()
        )
        embed.add_field(name="User", value=member.mention, inline=True)
        embed.add_field(name="Duration", value=time_str, inline=True)
        embed.add_field(name="Reason", value=reason, inline=True)
        embed.add_field(name="Muted by", value=ctx.author.mention, inline=True)
        await ctx.send(embed=embed)
        
    except discord.Forbidden:
        await ctx.send("I don't have permission to timeout this user.")
    except Exception as e:
        await ctx.send(f"Error muting user: {str(e)}")

@bot.command()
async def unmute(ctx, member: discord.Member):
    """Unmute a user"""
    if not await is_staff(ctx):
        await ctx.send("You don't have permission to use this command.")
        return
    
    try:
        await member.timeout(None, reason=f"Unmuted by {ctx.author.name}")
        await ctx.send(f"{member.mention} has been unmuted.")
    except discord.Forbidden:
        await ctx.send("I don't have permission to remove timeout from this user.")
    except Exception as e:
        await ctx.send(f"Error unmuting user: {str(e)}")

@bot.command()
async def ban(ctx, member: discord.Member, time_str: str = None, *, reason="No reason provided"):
    """Ban a user (temporarily if time is specified)"""
    if not await is_staff(ctx):
        await ctx.send("You don't have permission to use this command.")
        return
    
    try:
        await member.ban(reason=f"Banned by {ctx.author.name}: {reason}")
        
        embed = discord.Embed(
            title="User Banned",
            color=0x000000,
            timestamp=datetime.now()
        )
        embed.add_field(name="User", value=str(member), inline=True)
        embed.add_field(name="Reason", value=reason, inline=True)
        embed.add_field(name="Banned by", value=ctx.author.mention, inline=True)
        
        if time_str:
            duration = parse_time(time_str)
            if duration:
                embed.add_field(name="Duration", value=time_str, inline=True)
                asyncio.create_task(schedule_unban(ctx.guild, member, duration))
        
        await ctx.send(embed=embed)
        
    except discord.Forbidden:
        await ctx.send("I don't have permission to ban this user.")
    except Exception as e:
        await ctx.send(f"Error banning user: {str(e)}")

async def schedule_unban(guild, member, duration):
    """Schedule automatic unban"""
    await asyncio.sleep(duration.total_seconds())
    try:
        await guild.unban(member, reason="Temporary ban expired")
    except:
        pass

@bot.command()
async def unban(ctx, *, member_name):
    """Unban a user"""
    if not await is_staff(ctx):
        await ctx.send("You don't have permission to use this command.")
        return
    
    banned_users = [entry async for entry in ctx.guild.bans()]
    
    for ban_entry in banned_users:
        user = ban_entry.user
        if user.name.lower() == member_name.lower() or str(user) == member_name:
            try:
                await ctx.guild.unban(user, reason=f"Unbanned by {ctx.author.name}")
                await ctx.send(f"{user} has been unbanned.")
                return
            except Exception as e:
                await ctx.send(f"Error unbanning user: {str(e)}")
                return
    
    await ctx.send(f"User '{member_name}' not found in ban list.")

@bot.command()
async def kick(ctx, member: discord.Member, *, reason="No reason provided"):
    """Kick a user"""
    if not await is_staff(ctx):
        await ctx.send("You don't have permission to use this command.")
        return
    
    try:
        await member.kick(reason=f"Kicked by {ctx.author.name}: {reason}")
        
        embed = discord.Embed(
            title="User Kicked",
            color=0xffa500,
            timestamp=datetime.now()
        )
        embed.add_field(name="User", value=str(member), inline=True)
        embed.add_field(name="Reason", value=reason, inline=True)
        embed.add_field(name="Kicked by", value=ctx.author.mention, inline=True)
        await ctx.send(embed=embed)
        
    except discord.Forbidden:
        await ctx.send("I don't have permission to kick this user.")
    except Exception as e:
        await ctx.send(f"Error kicking user: {str(e)}")

# Configuration Commands
@bot.command()
async def welcomer_enable(ctx, channel: discord.TextChannel):
    """Enable welcomer system for the server"""
    if not await is_staff(ctx):
        await ctx.send("You don't have permission to use this command.")
        return
    
    guild_config = load_json('guild_config.json')
    guild_id = str(ctx.guild.id)
    
    if guild_id not in guild_config:
        guild_config[guild_id] = {}
    
    guild_config[guild_id]['welcomer_enabled'] = True
    guild_config[guild_id]['welcomer_channel'] = channel.id
    save_json('guild_config.json', guild_config)
    
    await ctx.send(f"Welcomer system has been enabled! Welcome messages will be sent to {channel.mention}.")

@bot.command()
async def automod_enable(ctx):
    """Enable automod for the server"""
    if not await is_staff(ctx):
        await ctx.send("You don't have permission to use this command.")
        return
    
    guild_config = load_json('guild_config.json')
    guild_id = str(ctx.guild.id)
    
    if guild_id not in guild_config:
        guild_config[guild_id] = {}
    
    guild_config[guild_id]['automod_enabled'] = True
    save_json('guild_config.json', guild_config)
    
    await ctx.send("Automod has been enabled for this server.")

@bot.command()
async def automod_log(ctx, channel: discord.TextChannel):
    """Set the automod log channel"""
    if not await is_staff(ctx):
        await ctx.send("You don't have permission to use this command.")
        return
    
    guild_config = load_json('guild_config.json')
    guild_id = str(ctx.guild.id)
    
    if guild_id not in guild_config:
        guild_config[guild_id] = {}
    
    guild_config[guild_id]['automod_log_channel'] = channel.id
    save_json('guild_config.json', guild_config)
    
    await ctx.send(f"Automod log channel set to {channel.mention}.")

@bot.command()
async def spam(ctx, *channels: discord.TextChannel):
    """Set channels where spam is allowed"""
    if not await is_staff(ctx):
        await ctx.send("You don't have permission to use this command.")
        return
    
    channel_ids = ','.join(str(ch.id) for ch in channels)
    
    guild_config = load_json('guild_config.json')
    guild_id = str(ctx.guild.id)
    
    if guild_id not in guild_config:
        guild_config[guild_id] = {}
    
    guild_config[guild_id]['spam_channels'] = channel_ids
    save_json('guild_config.json', guild_config)
    
    channel_mentions = ', '.join(ch.mention for ch in channels)
    await ctx.send(f"Spam is now allowed in: {channel_mentions}")

@bot.command()
async def link(ctx, *channels: discord.TextChannel):
    """Set channels where links are allowed"""
    if not await is_staff(ctx):
        await ctx.send("You don't have permission to use this command.")
        return
    
    channel_ids = ','.join(str(ch.id) for ch in channels)
    
    guild_config = load_json('guild_config.json')
    guild_id = str(ctx.guild.id)
    
    if guild_id not in guild_config:
        guild_config[guild_id] = {}
    
    guild_config[guild_id]['link_channels'] = channel_ids
    save_json('guild_config.json', guild_config)
    
    channel_mentions = ', '.join(ch.mention for ch in channels)
    await ctx.send(f"Links are now allowed in: {channel_mentions}")

# Leveling Commands
@bot.command()
async def leveling_channel(ctx, channel: discord.TextChannel):
    """Set the leveling announcement channel"""
    if not await is_staff(ctx):
        await ctx.send("You don't have permission to use this command.")
        return
    
    guild_config = load_json('guild_config.json')
    guild_id = str(ctx.guild.id)
    
    if guild_id not in guild_config:
        guild_config[guild_id] = {}
    
    guild_config[guild_id]['leveling_channel'] = channel.id
    save_json('guild_config.json', guild_config)
    
    await ctx.send(f"Leveling announcements will be sent to {channel.mention}.")

@bot.command()
async def levelrole(ctx, action_or_role, role_or_level=None, level=None):
    """Add or remove level roles"""
    if not await is_staff(ctx):
        await ctx.send("You don't have permission to use this command.")
        return
    
    level_roles = load_json('level_roles.json')
    guild_id = str(ctx.guild.id)
    
    if guild_id not in level_roles:
        level_roles[guild_id] = {}
    
    if action_or_role.lower() == 'elim':
        # Remove role: !levelrole elim @role
        if not role_or_level:
            await ctx.send("Please specify a role to remove.")
            return
        
        try:
            role = await commands.RoleConverter().convert(ctx, role_or_level)
        except:
            await ctx.send("Invalid role specified.")
            return
        
        # Remove role from all levels
        for level_num in level_roles[guild_id]:
            if str(role.id) in level_roles[guild_id][level_num]:
                level_roles[guild_id][level_num].remove(str(role.id))
        
        save_json('level_roles.json', level_roles)
        await ctx.send(f"Removed {role.mention} from level rewards.")
    
    else:
        # Add role: !levelrole @role 10
        try:
            role = await commands.RoleConverter().convert(ctx, action_or_role)
            target_level = int(role_or_level) if role_or_level else 0
        except:
            await ctx.send("Usage: `!levelrole @role <level>` or `!levelrole elim @role`")
            return
        
        if str(target_level) not in level_roles[guild_id]:
            level_roles[guild_id][str(target_level)] = []
        
        if str(role.id) not in level_roles[guild_id][str(target_level)]:
            level_roles[guild_id][str(target_level)].append(str(role.id))
        
        save_json('level_roles.json', level_roles)
        await ctx.send(f"Added {role.mention} as reward for reaching level {target_level}.")

@bot.command()
async def level(ctx, member: discord.Member = None):
    """Check a user's level"""
    if member is None:
        member = ctx.author
    
    user_levels = load_json('user_levels.json')
    key = f"{ctx.guild.id}_{member.id}"
    
    if key not in user_levels:
        await ctx.send(f"{member.mention} is not in the leveling system yet.")
        return
    
    user_data = user_levels[key]
    xp = user_data.get('xp', 0)
    level = user_data.get('level', 0)
    xp_for_next = (level + 1) * 100
    xp_needed = xp_for_next - xp
    
    embed = discord.Embed(
        title=f"Level Info for {member.display_name}",
        color=0x00ff00
    )
    embed.add_field(name="Current Level", value=level, inline=True)
    embed.add_field(name="Total XP", value=xp, inline=True)
    embed.add_field(name="XP to Next Level", value=xp_needed, inline=True)
    embed.set_thumbnail(url=member.display_avatar.url)
    
    await ctx.send(embed=embed)

@bot.command()
async def lock(ctx, *, args=None):
    """Lock a channel, optionally allowing specific roles"""
    if not await is_staff(ctx):
        await ctx.send("You don't have permission to use this command.")
        return
    
    channel = ctx.channel
    
    try:
        # Get the @everyone role
        everyone_role = ctx.guild.default_role
        
        # Get current @everyone permissions to preserve visibility
        current_overwrite = channel.overwrites_for(everyone_role)
        
        # Set permissions to deny send_messages while preserving visibility
        current_overwrite.send_messages = False
        await channel.set_permissions(everyone_role, overwrite=current_overwrite)
        
        # If specific roles are mentioned, allow them to send messages
        if args:
            role_converter = commands.RoleConverter()
            role_mentions = args.split()
            
            for role_mention in role_mentions:
                try:
                    role = await role_converter.convert(ctx, role_mention)
                    role_overwrite = channel.overwrites_for(role)
                    role_overwrite.send_messages = True
                    await channel.set_permissions(role, overwrite=role_overwrite)
                except:
                    pass
        
        await ctx.send("🔒 Channel locked.")
        
    except discord.Forbidden:
        await ctx.send("I don't have permission to modify channel permissions.")
    except Exception as e:
        await ctx.send(f"Error locking channel: {str(e)}")

@bot.command()
async def unlock(ctx):
    """Unlock a channel"""
    if not await is_staff(ctx):
        await ctx.send("You don't have permission to use this command.")
        return
    
    channel = ctx.channel
    
    try:
        # Get the @everyone role
        everyone_role = ctx.guild.default_role
        
        # Remove the send_messages permission override for @everyone
        current_overwrite = channel.overwrites_for(everyone_role)
        current_overwrite.send_messages = None
        
        if current_overwrite.is_empty():
            await channel.set_permissions(everyone_role, overwrite=None)
        else:
            await channel.set_permissions(everyone_role, overwrite=current_overwrite)
        
        await ctx.send("🔓 Channel unlocked.")
        
    except discord.Forbidden:
        await ctx.send("I don't have permission to modify channel permissions.")
    except Exception as e:
        await ctx.send(f"Error unlocking channel: {str(e)}")

# SP and Tournament Commands
@bot.command()
async def sp(ctx, member: discord.Member = None, sp_change: int = None):
    """View or manage seasonal points"""
    if member and sp_change is not None:
        # Staff command to add/remove SP
        if not await is_staff(ctx):
            await ctx.send("You don't have permission to modify SP.")
            return
        
        add_sp(ctx.guild.id, member.id, sp_change)
        current_sp = get_sp(ctx.guild.id, member.id)
        action = "added to" if sp_change > 0 else "removed from"
        await ctx.send(f"✅ {abs(sp_change)} SP {action} {member.mention}. Total: {current_sp} SP")
    else:
        # View SP
        if member is None:
            member = ctx.author
        
        current_sp = get_sp(ctx.guild.id, member.id)
        
        embed = discord.Embed(
            title=f"{member.display_name}'s Seasonal Points",
            color=0x00ff00
        )
        embed.add_field(name="Current SP", value=f"{current_sp} points", inline=True)
        embed.set_thumbnail(url=member.display_avatar.url)
        
        await ctx.send(embed=embed)

@bot.command()
async def bracketrole(ctx, member: discord.Member, *emojis):
    """Add bracket role emojis to a user"""
    if not await is_staff(ctx):
        await ctx.send("You don't have permission to use this command.")
        return
    
    guild_str = str(ctx.guild.id)
    user_str = str(member.id)
    
    if guild_str not in bracket_roles:
        bracket_roles[guild_str] = {}
    
    bracket_roles[guild_str][user_str] = list(emojis)
    save_data()
    
    # Update logs message
    await update_logs_message(ctx.guild.id)
    
    emoji_display = ''.join(emojis) if emojis else 'None'
    await ctx.send(f"✅ Bracket roles updated for {member.mention}: {emoji_display}")

# Tournament Configuration Views and Modals
class TournamentConfigModal(discord.ui.Modal, title="Tournament Configuration"):
    def __init__(self, target_channel):
        super().__init__()
        self.target_channel = target_channel

    title_field = discord.ui.TextInput(
        label="🏆 Tournament Title",
        placeholder="Enter tournament title...",
        default="",
        max_length=100
    )

    map_field = discord.ui.TextInput(
        label="🗺️ Map",
        placeholder="Enter map name...",
        default="",
        max_length=50
    )

    abilities_field = discord.ui.TextInput(
        label="💥 Abilities",
        placeholder="Enter abilities...",
        default="",
        max_length=100
    )

    mode_and_players_field = discord.ui.TextInput(
        label="🎮 Mode & Max Players",
        placeholder="1v1 8 or 2v2 4 (format: mode maxplayers)",
        default="",
        max_length=20
    )

    prize_field = discord.ui.TextInput(
        label="💶 Prize",
        placeholder="Enter prize...",
        default="",
        max_length=50
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Validate target channel
            if not self.target_channel:
                await interaction.response.send_message("❌ Invalid target channel. Please try again.", ephemeral=True)
                return

            # Parse mode and max players
            mode_players_parts = self.mode_and_players_field.value.strip().split()
            if len(mode_players_parts) != 2:
                await interaction.response.send_message("❌ Format should be: mode maxplayers (e.g., '1v1 8')", ephemeral=True)
                return

            mode = mode_players_parts[0].lower()
            max_players = int(mode_players_parts[1])

            if mode not in ["1v1", "2v2"]:
                await interaction.response.send_message("❌ Mode must be '1v1' or '2v2'!", ephemeral=True)
                return

            if mode == "2v2" and max_players not in [2, 4, 8, 16]:
                await interaction.response.send_message("❌ For 2v2 mode, max players (teams) must be 2, 4, 8, or 16!", ephemeral=True)
                return
            elif mode == "1v1" and max_players not in [2, 4, 8, 16, 32]:
                await interaction.response.send_message("❌ For 1v1 mode, max players must be 2, 4, 8, 16 or 32!", ephemeral=True)
                return
        except ValueError:
            await interaction.response.send_message("❌ Invalid format! Use: mode maxplayers (e.g., '1v1 8')", ephemeral=True)
            return
        except Exception as e:
            print(f"Error in tournament config modal: {e}")
            await interaction.response.send_message("❌ An error occurred. Please try again.", ephemeral=True)
            return

        # Get server-specific tournament and reset it
        tournament = get_tournament(interaction.guild.id)
        tournament.__init__()
        tournament.max_players = max_players
        tournament.mode = mode
        tournament.channel = self.target_channel
        tournament.target_channel = self.target_channel
        tournament.title = self.title_field.value
        tournament.map = self.map_field.value
        tournament.abilities = self.abilities_field.value
        tournament.prize = self.prize_field.value
        tournament.players = []
        tournament.eliminated = []
        tournament.active = False

        embed = discord.Embed(title=f"🏆 {tournament.title}", color=0x00ff00)
        embed.add_field(name="<:map:1409924163346370560> Map", value=tournament.map, inline=True)
        embed.add_field(name="<:abilities:1402690411759407185> Abilities", value=tournament.abilities, inline=True)
        embed.add_field(name="🎮 Mode", value=mode, inline=True)
        embed.add_field(name="<:LotsOfGems:1383151614940151908> Prize", value=tournament.prize, inline=True)
        embed.add_field(name="<:TrioIcon:1402690815771541685> Max Players", value=str(max_players), inline=True)

        # Enhanced Stumble Guys rules with updated emojis
        rules_text = (
            "🔹 **NO TEAMING** - Teams are only allowed in designated team modes\n"
            "🔸 **NO GRIEFING** - Don't intentionally sabotage other players\n"
            "🔹 **NO EXPLOITING** - Use of glitches or exploits will result in disqualification\n"
            "🔸 **FAIR PLAY** - Respect all players and play honorably\n"
            "🔹 **NO RAGE QUITTING** - Leaving mid-match counts as a forfeit\n"
            "🔸 **FOLLOW HOST** - Listen to tournament host instructions\n"
            "🔹 **NO TOXICITY** - Keep chat friendly and respectful\n"
            "🔸 **BE READY** - Join matches promptly when called\n"
            "🔹 **NO ALTS** - One account per player only"
        )

        embed.add_field(name="<:notr:1409923674387251280> **Stumble Guys Tournament Rules**", value=rules_text, inline=False)

        view = TournamentView()
        # Update the participant count button to show correct max players
        for item in view.children:
            if hasattr(item, 'custom_id') and item.custom_id == "participant_count":
                item.label = f"0/{max_players}"
                break

        # Send tournament message
        tournament.message = await self.target_channel.send(embed=embed, view=view)

        # Log tournament creation
        details = f"Mode: {mode}, Max players: {max_players}, Map: {tournament.map}, Prize: {tournament.prize}"
        await log_command(interaction.guild.id, interaction.user, "Tournament Created", details)

        # Respond with success
        await interaction.response.send_message("✅ Tournament created successfully!", ephemeral=True)

        print(f"✅ Tournament created: {max_players} max players, Map: {tournament.map}")

class TournamentConfigView(discord.ui.View):
    def __init__(self, target_channel=None):
        super().__init__(timeout=None)
        self.target_channel = target_channel

    @discord.ui.button(label="Set Tournament", style=discord.ButtonStyle.primary, custom_id="set_tournament_config")
    async def set_tournament(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            # Use the channel where the interaction happened if no target channel is set
            target_channel = self.target_channel or interaction.channel

            # Ensure we have a valid channel
            if not target_channel:
                return await interaction.response.send_message("❌ Unable to determine target channel. Please try again.", ephemeral=True)

            modal = TournamentConfigModal(target_channel)
            await interaction.response.send_modal(modal)
        except Exception as e:
            print(f"Error in set_tournament: {e}")
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message("❌ An error occurred. Please try again.", ephemeral=True)
                else:
                    await interaction.followup.send("❌ An error occurred. Please try again.", ephemeral=True)
            except Exception as follow_error:
                print(f"Failed to send error message: {follow_error}")

class TournamentView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return True

    @discord.ui.button(label="Register", style=discord.ButtonStyle.green, custom_id="tournament_register")
    async def register_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            tournament = get_tournament(interaction.guild.id)

            # Check tournament state
            if tournament.max_players == 0:
                return await interaction.response.send_message("❌ No tournament has been created yet.", ephemeral=True)
            if tournament.active:
                return await interaction.response.send_message("⚠️ Tournament already started.", ephemeral=True)

            # For 2v2 mode, check if user is in a team
            if tournament.mode == "2v2":
                team_id = get_team_id(interaction.guild.id, interaction.user.id)
                if not team_id:
                    return await interaction.response.send_message("❌ You need to be in a team to register for 2v2 tournaments! Use `!invite @teammate` to create a team.", ephemeral=True)

                # Check if team is already registered
                team_members = get_team_members(interaction.guild.id, team_id)
                if any(member in tournament.players for member in team_members):
                    return await interaction.response.send_message("❌ Your team is already registered.", ephemeral=True)

                # Check if tournament is full (max_players represents number of teams in 2v2)
                current_teams = len(tournament.players) // 2
                if current_teams >= tournament.max_players:
                    return await interaction.response.send_message("❌ Tournament is full.", ephemeral=True)

                tournament.players.extend(team_members)
                team_name = get_team_display_name(interaction.guild.id, team_members)

                for item in self.children:
                    if hasattr(item, 'custom_id') and item.custom_id == "participant_count":
                        teams_registered = len(tournament.players) // 2
                        item.label = f"{teams_registered}/{tournament.max_players}"
                        break

                await interaction.response.edit_message(view=self)
                await interaction.followup.send(f"✅ Team {team_name} registered! ({len(tournament.players) // 2}/{tournament.max_players} teams)", ephemeral=True)

            else:  # 1v1 mode
                if interaction.user in tournament.players:
                    return await interaction.response.send_message("❌ You are already registered.", ephemeral=True)

                # Check if there's space
                if len(tournament.players) >= tournament.max_players:
                    return await interaction.response.send_message("❌ Tournament is full.", ephemeral=True)

                tournament.players.append(interaction.user)

                for item in self.children:
                    if hasattr(item, 'custom_id') and item.custom_id == "participant_count":
                        item.label = f"{len(tournament.players)}/{tournament.max_players}"
                        break

                await interaction.response.edit_message(view=self)
                await interaction.followup.send(f"✅ {interaction.user.display_name} registered! ({len(tournament.players)}/{tournament.max_players})", ephemeral=True)

        except Exception as e:
            print(f"Error in register_button: {e}")
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message("❌ An error occurred. Please try again.", ephemeral=True)
                else:
                    await interaction.followup.send("❌ An error occurred. Please try again.", ephemeral=True)
            except Exception as follow_error:
                print(f"Failed to send error message: {follow_error}")

    @discord.ui.button(label="Unregister", style=discord.ButtonStyle.red, custom_id="tournament_unregister")
    async def unregister_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            tournament = get_tournament(interaction.guild.id)

            if tournament.max_players == 0:
                return await interaction.response.send_message("❌ No tournament has been created yet.", ephemeral=True)
            if tournament.active:
                return await interaction.response.send_message("⚠️ Tournament already started.", ephemeral=True)

            if tournament.mode == "2v2":
                team_id = get_team_id(interaction.guild.id, interaction.user.id)
                if not team_id:
                    return await interaction.response.send_message("❌ You are not in a team.", ephemeral=True)

                team_members = get_team_members(interaction.guild.id, team_id)
                if not any(member in tournament.players for member in team_members):
                    return await interaction.response.send_message("❌ Your team is not registered.", ephemeral=True)

                # Remove entire team
                for member in team_members:
                    if member in tournament.players:
                        tournament.players.remove(member)

                team_name = get_team_display_name(interaction.guild.id, team_members)

                for item in self.children:
                    if hasattr(item, 'custom_id') and item.custom_id == "participant_count":
                        teams_registered = len(tournament.players) // 2
                        item.label = f"{teams_registered}/{tournament.max_players}"
                        break

                await interaction.response.edit_message(view=self)
                await interaction.followup.send(f"✅ Team {team_name} unregistered! ({len(tournament.players) // 2}/{tournament.max_players} teams)", ephemeral=True)

            else:  # 1v1 mode
                if interaction.user not in tournament.players:
                    return await interaction.response.send_message("❌ You are not registered.", ephemeral=True)

                tournament.players.remove(interaction.user)

                for item in self.children:
                    if hasattr(item, 'custom_id') and item.custom_id == "participant_count":
                        item.label = f"{len(tournament.players)}/{tournament.max_players}"
                        break

                await interaction.response.edit_message(view=self)
                await interaction.followup.send(f"✅ {interaction.user.display_name} unregistered! ({len(tournament.players)}/{tournament.max_players})", ephemeral=True)

        except Exception as e:
            print(f"Error in unregister_button: {e}")
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message("❌ An error occurred. Please try again.", ephemeral=True)
                else:
                    await interaction.followup.send("❌ An error occurred. Please try again.", ephemeral=True)
            except Exception as follow_error:
                print(f"Failed to send error message: {follow_error}")

    @discord.ui.button(label="0/0", style=discord.ButtonStyle.secondary, disabled=True, custom_id="participant_count")
    async def participant_count(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass

    @discord.ui.button(label="🚀 Start Tournament", style=discord.ButtonStyle.primary, custom_id="start_tournament")
    async def start_tournament(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            tournament = get_tournament(interaction.guild.id)

            if not has_permission(interaction.user, interaction.guild.id, 'tlr') and not interaction.user.guild_permissions.manage_channels:
                return await interaction.response.send_message("❌ You don't have permission to start tournaments.", ephemeral=True)

            if tournament.max_players == 0:
                return await interaction.response.send_message("❌ No tournament has been created yet.", ephemeral=True)

            if tournament.active:
                return await interaction.response.send_message("❌ Tournament already started.", ephemeral=True)

            # Check minimum requirements
            if tournament.mode == "2v2":
                min_teams = 1  # Need at least 1 team to start
                current_teams = len(tournament.players) // 2
                if current_teams < min_teams:
                    return await interaction.response.send_message("❌ Not enough teams to start tournament (minimum 1 team).", ephemeral=True)
            else:
                if len(tournament.players) < 1:
                    return await interaction.response.send_message("❌ Not enough players to start tournament (minimum 1 player).", ephemeral=True)

            await interaction.response.send_message("🚀 Starting tournament...", ephemeral=True)

            # Auto-fill with bots to make even number
            if tournament.mode == "2v2":
                current_teams = len(tournament.players) // 2
                # Add bots one by one until we have an even number of teams
                while current_teams % 2 != 0:
                    # Create bot team
                    bot1_name = f"Bot{tournament.fake_count}"
                    bot1_id = 761557952975420886 + tournament.fake_count
                    bot1 = FakePlayer(bot1_name, bot1_id)
                    tournament.fake_count += 1

                    bot2_name = f"Bot{tournament.fake_count}"
                    bot2_id = 761557952975420886 + tournament.fake_count
                    bot2 = FakePlayer(bot2_name, bot2_id)
                    tournament.fake_count += 1

                    tournament.players.extend([bot1, bot2])
                    current_teams += 1

                # Group players by teams (keep real teams together)
                team_groups = []
                processed_players = set()

                for player in tournament.players:
                    if player in processed_players or isinstance(player, FakePlayer):
                        continue

                    team_id = get_team_id(interaction.guild.id, player.id)
                    if team_id:
                        teammate = get_teammate(interaction.guild.id, player.id)
                        if teammate and teammate in tournament.players:
                            team_groups.append([player, teammate])
                            processed_players.add(player)
                            processed_players.add(teammate)
                        else:
                            # Player has team but teammate not in tournament
                            team_groups.append([player])
                            processed_players.add(player)
                    else:
                        # Player not in a team
                        team_groups.append([player])
                        processed_players.add(player)

                # Add fake player teams
                fake_players = [p for p in tournament.players if isinstance(p, FakePlayer)]
                for i in range(0, len(fake_players), 2):
                    if i + 1 < len(fake_players):
                        team_groups.append([fake_players[i], fake_players[i+1]])

                # Shuffle team order but keep teammates together
                random.shuffle(team_groups)
                tournament.players = []
                for team in team_groups:
                    tournament.players.extend(team)

            else:
                # Add bots one by one until we have an even number of players
                while len(tournament.players) % 2 != 0:
                    bot_name = f"Bot{tournament.fake_count}"
                    bot_id = 761557952975420886 + tournament.fake_count
                    bot = FakePlayer(bot_name, bot_id)
                    tournament.players.append(bot)
                    tournament.fake_count += 1

                # Shuffle players for 1v1
                random.shuffle(tournament.players)

            tournament.active = True
            tournament.results = []
            tournament.rounds = []

            if tournament.mode == "2v2":
                # Create team pairs for 2v2
                team_pairs = []
                for i in range(0, len(tournament.players), 4):
                    team_a = [tournament.players[i], tournament.players[i+1]]
                    team_b = [tournament.players[i+2], tournament.players[i+3]]
                    team_pairs.append((team_a, team_b))
                tournament.rounds.append(team_pairs)
                current_round = team_pairs
            else:
                round_pairs = [(tournament.players[i], tournament.players[i+1]) for i in range(0, len(tournament.players), 2)]
                tournament.rounds.append(round_pairs)
                current_round = round_pairs

            embed = discord.Embed(
                title=f"🏆 {tournament.title} - Round 1",
                description=f"**Map:** {tournament.map}\n**Abilities:** {tournament.abilities}",
                color=0x3498db
            )

            if tournament.mode == "2v2":
                for i, match in enumerate(current_round, 1):
                    team_a, team_b = match
                    # Get bracket names for team members WITH emojis
                    team_a_display = []
                    team_b_display = []

                    guild_str = str(interaction.guild.id)

                    for player in team_a:
                        player_name = get_player_display_name(player, interaction.guild.id)
                        if guild_str in bracket_roles and str(player.id) in bracket_roles[guild_str] and not isinstance(player, FakePlayer):
                            emojis = ''.join(bracket_roles[guild_str][str(player.id)])
                            player_name = f"{player_name} {emojis}"
                        team_a_display.append(player_name)

                    for player in team_b:
                        player_name = get_player_display_name(player, interaction.guild.id)
                        if guild_str in bracket_roles and str(player.id) in bracket_roles[guild_str] and not isinstance(player, FakePlayer):
                            emojis = ''.join(bracket_roles[guild_str][str(player.id)])
                            player_name = f"{player_name} {emojis}"
                        team_b_display.append(player_name)

                    team_a_str = " & ".join(team_a_display)
                    team_b_str = " & ".join(team_b_display)

                    embed.add_field(
                        name=f"⚔️ Match {i}",
                        value=f"**{team_a_str}** <:VS:1402690899485655201> **{team_b_str}**\n<:Crown:1409926966236283012> Winner: *Waiting...*",
                        inline=False
                    )
            else:
                for i, match in enumerate(current_round, 1):
                    a, b = match
                    # Get bracket names
                    player_a = get_player_display_name(a, interaction.guild.id)
                    player_b = get_player_display_name(b, interaction.guild.id)

                    guild_str = str(interaction.guild.id)
                    if guild_str in bracket_roles and str(a.id) in bracket_roles[guild_str] and not isinstance(a, FakePlayer):
                        emojis = ''.join(bracket_roles[guild_str][str(a.id)])
                        player_a = f"{player_a} {emojis}"

                    if guild_str in bracket_roles and str(b.id) in bracket_roles[guild_str] and not isinstance(b, FakePlayer):
                        emojis = ''.join(bracket_roles[guild_str][str(b.id)])
                        player_b = f"{player_b} {emojis}"

                    embed.add_field(
                        name=f"⚔️ Match {i}",
                        value=f"**{player_a}** <:VS:1402690899485655201> **{player_b}**\n<:Crown:1409926966236283012> Winner: *Waiting...*",
                        inline=False
                    )

            embed.set_footer(text="Use !winner @player to record match results")

            # Create a new view without buttons for active tournament
            active_tournament_view = discord.ui.View()
            tournament.message = await interaction.channel.send(embed=embed, view=active_tournament_view)
            await interaction.followup.send("✅ Tournament started successfully!", ephemeral=True)

        except Exception as e:
            print(f"Error in start_tournament: {e}")
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message("❌ An error occurred while starting the tournament.", ephemeral=True)
                else:
                    await interaction.followup.send("❌ An error occurred while starting the tournament.", ephemeral=True)
            except Exception as follow_error:
                print(f"Failed to send error message: {follow_error}")

# Account linking system
class AccountLinkView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔗 Link Account", style=discord.ButtonStyle.primary, custom_id="link_account")
    async def link_account(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = AccountLinkModal()
        await interaction.response.send_modal(modal)

class AccountLinkModal(discord.ui.Modal, title="Link Your Account"):
    def __init__(self):
        super().__init__()

    ign = discord.ui.TextInput(
        label="In-Game Name (IGN)",
        placeholder="Enter your exact Stumble Guys username...",
        max_length=50
    )

    async def on_submit(self, interaction: discord.Interaction):
        user_accounts = load_json('user_accounts.json')
        key = f"{interaction.guild.id}_{interaction.user.id}"
        
        user_accounts[key] = {
            'ign': self.ign.value,
            'linked_at': datetime.now().isoformat(),
            'user_id': interaction.user.id,
            'guild_id': interaction.guild.id
        }
        save_json('user_accounts.json', user_accounts)
        
        # Update logs message
        await update_logs_message(interaction.guild.id)
        
        # Give verified role if configured
        guild_config = load_json('guild_config.json')
        config = guild_config.get(str(interaction.guild.id), {})
        verified_role_id = config.get('verified_role')
        
        if verified_role_id:
            role = interaction.guild.get_role(verified_role_id)
            if role:
                try:
                    await interaction.user.add_roles(role)
                except:
                    pass
        
        await interaction.response.send_message(
            f"✅ Account linked successfully! Your IGN: **{self.ign.value}**",
            ephemeral=True
        )

class HosterRegistrationView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return True

    @discord.ui.button(label="Register", style=discord.ButtonStyle.green, custom_id="hoster_register")
    async def register_hoster(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not host_registrations['active']:
            return await interaction.response.send_message("❌ Hoster registration is not active.", ephemeral=True)

        if interaction.user in host_registrations['hosters']:
            return await interaction.response.send_message("❌ You are already registered as a hoster.", ephemeral=True)

        if len(host_registrations['hosters']) >= host_registrations['max_hosters']:
            return await interaction.response.send_message("❌ Maximum number of hosters reached.", ephemeral=True)

        host_registrations['hosters'].append(interaction.user)

        # Update the embed
        embed = discord.Embed(
            title="🎯 Hoster Registration",
            description="Here the hosters will register to host tournaments!",
            color=0x00ff00
        )

        if host_registrations['hosters']:
            hoster_list = ""
            for i, hoster in enumerate(host_registrations['hosters'], 1):
                hoster_name = hoster.nick if hoster.nick else hoster.display_name
                hoster_list += f"{i}. {hoster_name}\n"
            embed.add_field(name="Hosters registered:", value=hoster_list, inline=False)
        else:
            embed.add_field(name="Hosters registered:", value="None yet", inline=False)

        embed.add_field(name="Hosters needed:", value=f"{len(host_registrations['hosters'])}/{host_registrations['max_hosters']}", inline=False)

        await interaction.response.edit_message(embed=embed, view=self)
        await interaction.followup.send(f"✅ {interaction.user.display_name} registered as hoster!", ephemeral=True)

# More commands from original file 2

@bot.command()
async def acc(ctx):
    """Display account linking panel"""
    embed = discord.Embed(
        title="🔗 Account Linking System",
        description="**Link your Stumble Guys account to unlock exclusive features!**\n\n🎮 **Benefits of linking:**\n• Get the verified player role\n• Access to exclusive channels\n• Show off your in-game name\n• Track your progress and stats",
        color=0x0099ff
    )
    
    view = AccountLinkView()
    await ctx.send(embed=embed, view=view)

@bot.command()
async def IGN(ctx, member: discord.Member = None):
    """Show user's in-game name"""
    if member is None:
        member = ctx.author
    
    user_accounts = load_json('user_accounts.json')
    key = f"{ctx.guild.id}_{member.id}"
    
    if key not in user_accounts:
        await ctx.send(f"{member.mention} hasn't linked their account yet.")
        return
    
    account_data = user_accounts[key]
    ign = account_data['ign']
    linked_at = account_data['linked_at']
    
    embed = discord.Embed(
        title=f"{member.display_name}'s Account",
        color=0x00ff00
    )
    embed.add_field(name="In-Game Name", value=ign, inline=True)
    embed.add_field(name="Linked", value=linked_at, inline=True)
    embed.set_thumbnail(url=member.display_avatar.url)
    
    await ctx.send(embed=embed)

# Tournament commands
@bot.command()
async def winner(ctx, member: discord.Member):
    """Set tournament winner"""
    try:
        await ctx.message.delete()
    except Exception as e:
        print(f"Failed to delete message: {e}")
        pass

    if not has_permission(ctx.author, ctx.guild.id, 'htr') and not has_permission(ctx.author, ctx.guild.id, 'tlr') and not ctx.author.guild_permissions.manage_channels:
        return await ctx.send("❌ You don't have permission to set winners.", delete_after=5)

    tournament = get_tournament(ctx.guild.id)

    if not tournament.active:
        return await ctx.send("❌ No active tournament.", delete_after=5)

    current_round = tournament.rounds[-1]

    # Find and update the match
    match_found = False
    eliminated_players = []
    match_index = -1
    winner_team = None
    loser_team = None

    if tournament.mode == "2v2":
        # Find which team the mentioned member belongs to
        member_team_id = get_team_id(ctx.guild.id, member.id)
        if not member_team_id:
            return await ctx.send("❌ This player is not in a team.", delete_after=5)

        member_team = get_team_members(ctx.guild.id, member_team_id)

        for i, match in enumerate(current_round):
            team_a, team_b = match
            if member in team_a:
                winner_team = team_a
                loser_team = team_b
                tournament.results.append(team_a)
                eliminated_players.extend(team_b)
                match_found = True
                match_index = i
                break
            elif member in team_b:
                winner_team = team_b
                loser_team = team_a
                tournament.results.append(team_b)
                eliminated_players.extend(team_a)
                match_found = True
                match_index = i
                break

        if match_found:
            winner_name = get_team_display_name(ctx.guild.id, winner_team)

    else:  # 1v1 mode
        for i, match in enumerate(current_round):
            a, b = match
            if member == a or member == b:
                tournament.results.append(member)
                eliminated_players.extend([a if member == b else b])
                match_found = True
                match_index = i
                break

        if match_found:
            winner_name = get_player_display_name(member, ctx.guild.id)

    if not match_found:
        return await ctx.send("❌ This player/team is not in the current round.", delete_after=5)

    # Add eliminated players to elimination list
    tournament.eliminated.extend(eliminated_players)

    # Update current tournament message to show the winner
    if tournament.message:
        try:
            current_embed = tournament.message.embeds[0]

            # Find and update the specific match field
            if match_index >= 0 and match_index < len(current_embed.fields):
                field = current_embed.fields[match_index]
                if "Match" in field.name:
                    field_value = field.value
                    lines = field_value.split('\n')
                    lines[1] = f"<:Crown:1409926966236283012> Winner: **{get_player_display_name(member, ctx.guild.id)}**"

                    current_embed.set_field_at(match_index, name=field.name, value='\n'.join(lines), inline=field.inline)
                    await tournament.message.edit(embed=current_embed)

        except Exception as e:
            print(f"Error updating tournament message: {e}")

    # Check if round is complete
    if len(tournament.results) == len(current_round):
        if len(tournament.results) == 1:
            # Tournament finished - determine placements and award SP
            winner_data = tournament.results[0]

            # Calculate placements based on elimination order
            all_eliminated = tournament.eliminated

            # Get the final 4 placements
            placements = [] # List of (place, player, sp_reward)

            # 1st place (winner)
            placements.append((1, winner_data, 3))
            if hasattr(winner_data, 'id') and not isinstance(winner_data, FakePlayer):
                add_sp(ctx.guild.id, winner_data.id, 3)

            # 2nd place (last eliminated)
            if len(all_eliminated) >= 1:
                placements.append((2, all_eliminated[-1], 2))
                player = all_eliminated[-1]
                if hasattr(player, 'id') and not isinstance(player, FakePlayer):
                    add_sp(ctx.guild.id, player.id, 2)

            # 3rd and 4th place
            if len(all_eliminated) >= 2:
                placements.append((3, all_eliminated[-2], 1))
                player = all_eliminated[-2]
                if hasattr(player, 'id') and not isinstance(player, FakePlayer):
                    add_sp(ctx.guild.id, player.id, 1)
            if len(all_eliminated) >= 3:
                placements.append((4, all_eliminated[-3], 1))
                player = all_eliminated[-3]
                if hasattr(player, 'id') and not isinstance(player, FakePlayer):
                    add_sp(ctx.guild.id, player.id, 1)

            # Create styled tournament winners embed
            winner_display = get_player_display_name(winner_data, ctx.guild.id)

            embed = discord.Embed(
                title="🏆 Tournament Winners!",
                description=f"Congratulations to **{winner_display}** for winning the\n**{tournament.title}** tournament! 🎉",
                color=0xffd700
            )

            # Add tournament info with custom emojis
            embed.add_field(name="<:map:1409924163346370560> Map", value=tournament.map, inline=True)
            embed.add_field(name="<:abilities:1402690411759407185> Abilities", value=tournament.abilities, inline=True)
            embed.add_field(name="🎮 Mode", value=tournament.mode, inline=True)

            # Create results text
            results_display = ""
            for place, player_obj, sp in placements:
                if place == 1:
                    emoji = "<:Medal_Gold:1402383868505624576>"
                elif place == 2:
                    emoji = "<:Medal_Silver:1402383899597869207>"
                elif place == 3:
                    emoji = "<:Medal_Bronze:1402383923991806063>"
                elif place == 4:
                    emoji = "4️⃣"
                else:
                    emoji = "📍"

                player_str = get_player_display_name(player_obj, ctx.guild.id)
                results_display += f"{emoji} {player_str}\n"

            embed.add_field(name="🏆 Final Rankings", value=results_display, inline=False)

            # Add prizes section with SP
            prize_text = ""
            for place, player_obj, sp in placements:
                if place == 1:
                    emoji = "<:Medal_Gold:1402383868505624576>"
                elif place == 2:
                    emoji = "<:Medal_Silver:1402383899597869207>"
                elif place == 3:
                    emoji = "<:Medal_Bronze:1402383923991806063>"
                elif place == 4:
                    emoji = "4️⃣"
                else:
                    emoji = "📍"

                place_suffix = "st" if place == 1 else "nd" if place == 2 else "rd" if place == 3 else "th"
                prize_text += f"{emoji} {place}{place_suffix}: {sp} Seasonal Points\n"

            embed.add_field(name="🏆 Prizes", value=prize_text, inline=False)

            # Add winner's avatar if it's a real player
            winner_player_obj = winner_data
            if hasattr(winner_player_obj, 'display_avatar') and not isinstance(winner_player_obj, FakePlayer):
                embed.set_thumbnail(url=winner_player_obj.display_avatar.url)

            # Add footer with tournament ID and timestamp
            embed.set_footer(text=f"Tournament completed • {datetime.now().strftime('%d.%m.%Y %H:%M')}")

            # Create a new view without buttons for the completed tournament
            completed_view = discord.ui.View()
            await ctx.send(embed=embed, view=completed_view)

            # Reset tournament
            tournament.__init__()
        else:
            # Create next round
            next_round_winners = tournament.results.copy()

            # Add fake players if odd number of winners
            while len(next_round_winners) % 2 != 0:
                bot_name = f"Bot{tournament.fake_count}"
                bot_id = 761557952975420886 + tournament.fake_count
                bot = FakePlayer(bot_name, bot_id)
                next_round_winners.append(bot)
                tournament.fake_count += 1

            next_round_pairs = []
            for i in range(0, len(next_round_winners), 2):
                next_round_pairs.append((next_round_winners[i], next_round_winners[i+1]))

            tournament.rounds.append(next_round_pairs)
            tournament.results = []

            round_num = len(tournament.rounds)
            embed = discord.Embed(
                title=f"🏆 {tournament.title} - Round {round_num}",
                description=f"**Map:** {tournament.map}\n**Abilities:** {tournament.abilities}",
                color=0x3498db
            )

            if tournament.mode == "2v2":
                for i, match in enumerate(next_round_pairs, 1):
                    team_a, team_b = match
                    # Get bracket names for team members WITH emojis
                    team_a_display = []
                    team_b_display = []

                    guild_str = str(ctx.guild.id)

                    for player in team_a:
                        player_name = get_player_display_name(player, ctx.guild.id)
                        if guild_str in bracket_roles and str(player.id) in bracket_roles[guild_str] and not isinstance(player, FakePlayer):
                            emojis = ''.join(bracket_roles[guild_str][str(player.id)])
                            player_name = f"{player_name} {emojis}"
                        team_a_display.append(player_name)

                    for player in team_b:
                        player_name = get_player_display_name(player, ctx.guild.id)
                        if guild_str in bracket_roles and str(player.id) in bracket_roles[guild_str] and not isinstance(player, FakePlayer):
                            emojis = ''.join(bracket_roles[guild_str][str(player.id)])
                            player_name = f"{player_name} {emojis}"
                        team_b_display.append(player_name)

                    team_a_str = " & ".join(team_a_display)
                    team_b_str = " & ".join(team_b_display)

                    embed.add_field(
                        name=f"⚔️ Match {i}",
                        value=f"**{team_a_str}** <:VS:1402690899485655201> **{team_b_str}**\n<:Crown:1409926966236283012> Winner: *Waiting...*",
                        inline=False
                    )
            else:
                for i, match in enumerate(next_round_pairs, 1):
                    a, b = match
                    # Get bracket names WITH emojis for next rounds
                    player_a = get_player_display_name(a, ctx.guild.id)
                    player_b = get_player_display_name(b, ctx.guild.id)

                    guild_str = str(ctx.guild.id)
                    if guild_str in bracket_roles and str(a.id) in bracket_roles[guild_str] and not isinstance(a, FakePlayer):
                        emojis = ''.join(bracket_roles[guild_str][str(a.id)])
                        player_a = f"{player_a} {emojis}"

                    if guild_str in bracket_roles and str(b.id) in bracket_roles[guild_str] and not isinstance(b, FakePlayer):
                        emojis = ''.join(bracket_roles[guild_str][str(b.id)])
                        player_b = f"{player_b} {emojis}"

                    embed.add_field(
                        name=f"⚔️ Match {i}",
                        value=f"**{player_a}** <:VS:1402690899485655201> **{player_b}**\n<:Crown:1409926966236283012> Winner: *Waiting...*",
                        inline=False
                    )

            embed.set_footer(text="Use !winner @player to record match results")

            # Create a new view without buttons for active tournament
            active_tournament_view = discord.ui.View()
            tournament.message = await ctx.send(embed=embed, view=active_tournament_view)

    await ctx.send(f"✅ {winner_name} wins their match!", delete_after=5)

@bot.command()
async def spu(ctx, *roles: discord.Role):
    """Set staff roles that can use ALL commands and moderation features"""
    if not ctx.author.guild_permissions.administrator:
        await ctx.send("You need administrator permission to use this command.")
        return
    
    if not roles:
        await ctx.send("Please mention at least one role.")
        return
    
    role_ids = ','.join(str(role.id) for role in roles)
    
    guild_config = load_json('guild_config.json')
    guild_id = str(ctx.guild.id)
    
    if guild_id not in guild_config:
        guild_config[guild_id] = {}
    
    guild_config[guild_id]['staff_roles'] = role_ids
    save_json('guild_config.json', guild_config)
    
    role_mentions = ', '.join(role.mention for role in roles)
    await ctx.send(f"Staff roles updated! These roles can now use ALL bot commands: {role_mentions}")

@bot.command()
async def verified_role(ctx, role: discord.Role):
    """Set the role to give users when they link their account"""
    if not ctx.author.guild_permissions.administrator:
        await ctx.send("You need administrator permission to use this command.")
        return
    
    guild_config = load_json('guild_config.json')
    guild_id = str(ctx.guild.id)
    
    if guild_id not in guild_config:
        guild_config[guild_id] = {}
    
    guild_config[guild_id]['verified_role'] = role.id
    save_json('guild_config.json', guild_config)
    
    await ctx.send(f"Verified role set to {role.mention}! Users will receive this role when they link their account.")

# Background task to check level roles
@tasks.loop(minutes=5)
async def level_check():
    """Periodically check and assign level roles"""
    user_levels = load_json('user_levels.json')
    level_roles = load_json('level_roles.json')
    
    if not isinstance(user_levels, dict) or not isinstance(level_roles, dict):
        return
    
    for key, user_data in user_levels.items():
        guild_id, user_id = key.split('_')
        user_level = user_data.get('level', 0)
        
        guild = bot.get_guild(int(guild_id))
        if not guild:
            continue
        
        member = guild.get_member(int(user_id))
        if not member:
            continue
        
        guild_roles = level_roles.get(guild_id, {})
        for level_num, role_ids in guild_roles.items():
            if user_level >= int(level_num):
                for role_id in role_ids:
                    role = guild.get_role(int(role_id))
                    if role and role not in member.roles:
                        try:
                            await member.add_roles(role, reason="Level role assignment")
                        except:
                            pass

# Error handling
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MemberNotFound):
        await ctx.send("User not found.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"Missing required argument: {error.param}")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("Invalid argument provided.")
    else:
        print(f"Unhandled error: {error}")

# Run the bot
if __name__ == "__main__":
    TOKEN = os.getenv("TOKEN")
    if not TOKEN:
        print("Please set the TOKEN environment variable")
    else:
        bot.run(TOKEN)