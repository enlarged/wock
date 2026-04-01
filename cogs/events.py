import re
import discord
import aiohttp
import io
from discord.ext import commands
from models.configs import GuildConfig, UserConfig
from models.afk import AFK
from utils.parser import EmbedParser
from datetime import datetime
import asyncio
import random
from collections import defaultdict

def get_xp_for_level(level: int) -> int:
    """Calculate total XP needed to reach a specific level.
    Uses quadratic scaling: each level requires 100 * level XP total."""
    return sum(100 * i for i in range(1, level))

def get_level_from_xp(xp: int) -> int:
    """Calculate level from total XP using quadratic scaling."""
    level = 1
    while True:
        xp_needed = get_xp_for_level(level + 1)
        if xp >= xp_needed:
            level += 1
        else:
            break
    return level

class Events(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.left_members = {}
        # Precise colors from Wock screenshots
        self.c_blue = 0x5d92f2  # Created
        self.c_red = 0xf25d5d   # Deleted
        self.c_green = 0x5df271 # Updated
        
        # Regex patterns for media detection
        self.tiktok_regex = r"https?://(www\.|vm\.|vt\.)?tiktok\.com/[\w\d\/\?=\.&]+"
        self.invite_regex = r"discord\.gg/[a-zA-Z0-9-]+|discord\.com/invite/[a-zA-Z0-9-]+"
        
        # Activity tracking
        self.activity_data = defaultdict(lambda: defaultdict(lambda: {'messages': 0, 'voice_time': 0, 'last_seen': None}))
        self.voice_sessions = {}
        self.tracker_channels = {}  # Store tracker message references for background task

    async def get_log_channel(self, guild: discord.Guild):
        if not guild: return None
        res = await GuildConfig.find_one(GuildConfig.guild_id == guild.id)
        return guild.get_channel(res.modlog_channel_id) if res and res.modlog_channel_id else None

    async def fetch_mod(self, guild, action):
        await asyncio.sleep(1)
        try:
            async for entry in guild.audit_logs(limit=1, action=action):
                if (datetime.utcnow() - entry.created_at).total_seconds() < 10:
                    return entry.user
        except: pass
        return None

    async def fetch_mod_entry(self, guild: discord.Guild, action: discord.AuditLogAction, target_id: int = None):
        await asyncio.sleep(1)
        try:
            async for entry in guild.audit_logs(limit=6, action=action):
                if (datetime.utcnow() - entry.created_at).total_seconds() > 15:
                    continue
                if target_id is not None:
                    entry_target_id = getattr(entry.target, "id", None)
                    if entry_target_id != target_id:
                        continue
                return entry
        except Exception:
            pass
        return None

    def wock_embed(self, event_name: str, color: int, mod_user: discord.abc.User = None):
        e = discord.Embed(color=color)
        if mod_user:
            icon = getattr(mod_user, 'display_avatar', None)
            icon_url = icon.url if icon else None
            name = getattr(mod_user, 'display_name', None) or getattr(mod_user, 'name', event_name)
            e.set_author(name=name, icon_url=icon_url)
        else:
            e.set_author(name=event_name)

        time_str = datetime.now().strftime('%I:%M %p').lstrip("0")
        if mod_user:
            e.set_footer(text=f"User ID: {mod_user.id} • Today at {time_str}")
        else:
            e.set_footer(text=f"Today at {time_str}")
        return e

    def _truncate(self, text: str, limit: int = 900):
        if text is None:
            return "-"
        text = str(text)
        return text if len(text) <= limit else text[:limit - 3] + "..."

    # --- CHANNEL EVENTS ---

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Handle AFK greetings, removal, media reposts, gallery enforcement, and activity tracking"""
        if message.author.bot or not message.guild:
            return
        
        # --- IGNORE CHECK (early exit for ignored members/channels) ---
        guild_config = await GuildConfig.find_one(GuildConfig.guild_id == message.guild.id)
        if guild_config:
            # Check if member is ignored
            if message.author.id in guild_config.ignored_members:
                return
            
            # Check if channel is ignored
            if message.channel.id in guild_config.ignored_channels:
                return
        
        # --- ALIAS INTERCEPTION (rewrite command if aliased) ---
        if guild_config and guild_config.aliases and message.content.startswith(await self.bot.get_prefix(message)):
            prefix = (await self.bot.get_prefix(message))[0]
            parts = message.content[len(prefix):].split(maxsplit=1)
            
            if parts:
                command_name = parts[0].lower()
                if command_name in guild_config.aliases:
                    # Replace alias with actual command
                    actual_command = guild_config.aliases[command_name]
                    args = parts[1] if len(parts) > 1 else ""
                    message.content = f"{prefix}{actual_command}" + (f" {args}" if args else "")
        
        # --- GALLERY HANDLER (check if channel is gallery-only) ---
        guild_config = await GuildConfig.find_one(GuildConfig.guild_id == message.guild.id)
        if guild_config and message.channel.id in guild_config.gallery_channels:
            # Gallery channels only allow attachments - delete ALL messages without attachments
            if not message.attachments:
                try:
                    await message.delete()
                    embed = discord.Embed(color=0xf25d5d, description="📸 This channel only allows attachments (images, videos, etc.)")
                    warning_msg = await message.channel.send(embed=embed)
                    await asyncio.sleep(5)
                    await warning_msg.delete()
                except Exception as e:
                    print(f"Failed to delete gallery message: {e}")
                return
        
        # --- REPOST HANDLER (must be before AFK check) ---
        if self.bot.user in message.mentions:
            # Check for TikTok links
            tiktok_match = re.search(self.tiktok_regex, message.content)
            if tiktok_match:
                try:
                    async with message.channel.typing():
                        api_url = f"https://www.tikwm.com/api/?url={tiktok_match.group(0)}"
                        async with aiohttp.ClientSession() as session:
                            async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                                res = await resp.json()
                                data = res.get("data")
                                if data and data.get("play"):
                                    author = data.get("author", {})
                                    content = f"**{author.get('nickname')}** (@{author.get('unique_id')})"
                                    
                                    async with session.get(data["play"], timeout=aiohttp.ClientTimeout(total=30)) as video_resp:
                                        video_data = await video_resp.read()
                                        file = discord.File(fp=io.BytesIO(video_data), filename="tiktok-repost.mp4")
                                        await message.channel.send(content=content, file=file)
                except Exception as e:
                    print(f"❌ TikTok repost error: {e}")
                return
        
        # --- MESSAGE FILTER HANDLER & GALLERY CHECK ---
        if guild_config:
            # Check for invite links
            if guild_config.filter_invites:
                if re.search(self.invite_regex, message.content):
                    try:
                        await message.delete()
                        embed = discord.Embed(color=0xf25d5d, description="🔗 Invite links are not allowed in this server")
                        await message.channel.send(embed=embed, delete_after=5)
                        return
                    except:
                        pass
            
            # Check for filtered words
            if guild_config.filtered_words:
                message_lower = message.content.lower()
                for word in guild_config.filtered_words:
                    if word.lower() in message_lower:
                        try:
                            await message.delete()
                            embed = discord.Embed(color=0xf25d5d, description="🚫 That word is not allowed in this server")
                            await message.channel.send(embed=embed, delete_after=5)
                            return
                        except:
                            pass
            
            # Check for spam (multiple messages in quick succession)
            if guild_config.filter_spam:
                # This would typically be handled by a separate spam detection system
                # For now, we'll keep it simple with a basic check
                pass
        
        # --- AUTORESPONDER HANDLER ---
        if guild_config and guild_config.autoresponders:
            message_lower = message.content.lower()
            for trigger, responder_data in guild_config.autoresponders.items():
                # Case-insensitive trigger check
                if trigger.lower() in message_lower:
                    try:
                        # Check exclusive channels restriction
                        exclusive_channels = responder_data.get("exclusive_channels", [])
                        if exclusive_channels and message.channel.id not in exclusive_channels:
                            continue
                        
                        # Check exclusive roles restriction
                        exclusive_roles = responder_data.get("exclusive_roles", [])
                        if exclusive_roles:
                            member_role_ids = [role.id for role in message.author.roles]
                            if not any(role_id in member_role_ids for role_id in exclusive_roles):
                                continue
                        
                        # Get response content
                        response = responder_data.get("response", "")
                        if not response:
                            continue
                        
                        # Apply variable substitution
                        response = response.replace("{user}", message.author.name)
                        response = response.replace("{user.mention}", message.author.mention)
                        response = response.replace("{server}", message.guild.name)
                        
                        # Send response
                        await message.channel.send(response)
                        
                        # Apply roles
                        add_roles = responder_data.get("add_roles", [])
                        for role_id in add_roles:
                            role = message.guild.get_role(role_id)
                            if role:
                                try:
                                    await message.author.add_roles(role)
                                except:
                                    pass
                        
                        remove_roles = responder_data.get("remove_roles", [])
                        for role_id in remove_roles:
                            role = message.guild.get_role(role_id)
                            if role:
                                try:
                                    await message.author.remove_roles(role)
                                except:
                                    pass
                    except:
                        pass
        
        # --- AFK HANDLER ---
        # Check if user is mentioning anyone and notify AFK users
        if message.mentions:
            for user in message.mentions:
                afk_record = await AFK.find_one(
                    AFK.guild_id == message.guild.id,
                    AFK.user_id == user.id
                )
                
                if afk_record:
                    embed = discord.Embed(
                        color=0x242429,
                        description=f"**{user}** is AFK: *{afk_record.message}*\n\n⏰ {discord.utils.format_dt(afk_record.timestamp, style='R')}"
                    )
                    try:
                        await message.reply(embed=embed, mention_author=False)
                    except:
                        pass
        
        # Remove AFK status if the AFK user is sending a message
        afk_record = await AFK.find_one(
            AFK.guild_id == message.guild.id,
            AFK.user_id == message.author.id
        )
        
        if afk_record:
            await afk_record.delete()
            embed = discord.Embed(
                color=0x43b581,
                description=f"✅ Welcome back **{message.author.name}**! Your AFK status has been removed."
            )
            try:
                await message.reply(embed=embed, mention_author=False, delete_after=5)
            except:
                pass
        
        # --- ACTIVITY TRACKING ---
        user_activity = self.activity_data[message.guild.id][message.author.id]
        user_activity['messages'] += 1
        user_activity['last_seen'] = datetime.utcnow()
        
        # --- REACTION TRIGGERS ---
        if message.guild and not message.author.bot:
            guild_config = await GuildConfig.find_one(GuildConfig.guild_id == message.guild.id)
            if guild_config:
                # Check for reaction triggers
                if guild_config.reaction_triggers:
                    message_lower = message.content.lower()
                    for trigger_word, trigger_data in guild_config.reaction_triggers.items():
                        if trigger_word in message_lower:
                            try:
                                emoji = trigger_data.get("emoji")
                                if emoji:
                                    await message.add_reaction(emoji)
                            except:
                                pass
                
                # Check for previous reaction triggers
                if guild_config.previous_react_triggers:
                    message_lower = message.content.lower()
                    for trigger_word, trigger_data in guild_config.previous_react_triggers.items():
                        if trigger_word in message_lower:
                            try:
                                emoji = trigger_data.get("emoji")
                                if emoji:
                                    # React to previous message
                                    async for prev_message in message.channel.history(limit=2):
                                        if prev_message.id != message.id:
                                            await prev_message.add_reaction(emoji)
                                            break
                            except:
                                pass
                
                # Auto reactions on all messages in specific channels
                if guild_config.auto_reactions:
                    channel_id = str(message.channel.id)
                    if channel_id in guild_config.auto_reactions:
                        try:
                            for emoji in guild_config.auto_reactions[channel_id]:
                                await message.add_reaction(emoji)
                        except:
                            pass
        
        # --- LEVELING SYSTEM ---
        if message.guild and not message.author.bot:
            guild_config = await GuildConfig.find_one(GuildConfig.guild_id == message.guild.id)
            if guild_config and guild_config.leveling_enabled:
                # Award XP for message (10-25 XP per message)
                xp_gained = random.randint(10, 25)
                
                user_config = await UserConfig.find_one(UserConfig.user_id == message.author.id)
                if not user_config:
                    user_config = UserConfig(user_id=message.author.id)
                
                old_level = user_config.level
                user_config.xp += xp_gained
                
                # Calculate level using quadratic scaling (100*level XP per level)
                new_level = get_level_from_xp(user_config.xp)
                
                if new_level > old_level:
                    user_config.level = new_level
                    await user_config.save()
                    
                    # Check for level roles
                    level_role_id = guild_config.level_roles.get(str(new_level))
                    if level_role_id:
                        try:
                            role = message.guild.get_role(level_role_id)
                            if role and not message.author.bot:
                                await message.author.add_roles(role)
                        except:
                            pass
                    
                    # Send level up message
                    if guild_config.level_channel_id:
                        channel = message.guild.get_channel(guild_config.level_channel_id)
                        if channel:
                            if guild_config.level_message:
                                # Use custom message with variables
                                msg = guild_config.level_message
                                msg = msg.replace("{user}", message.author.name)
                                msg = msg.replace("{user.mention}", message.author.mention)
                                msg = msg.replace("{level}", str(new_level))
                                msg = msg.replace("{old_level}", str(old_level))
                                await channel.send(msg)
                            else:
                                # Default level up message
                                embed = discord.Embed(
                                    color=0x242429,
                                    description=f"🎉 {message.author.mention} reached **Level {new_level}**!"
                                )
                                await channel.send(embed=embed)
                else:
                    await user_config.save()



    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel):
        log = await self.get_log_channel(channel.guild)
        if not log: return
        mod = await self.fetch_mod(channel.guild, discord.AuditLogAction.channel_create)
        
        e = self.wock_embed("Channel Created", self.c_blue, mod)
        e.description = f"{mod.mention if mod else '@Unknown'} created a channel: -"
        e.add_field(name="Channel", value=f"{channel.mention if getattr(channel, 'mention', None) else '#' + channel.name} ({channel.id})", inline=False)
        e.add_field(name="Moderator", value=f"{mod if mod else 'Unknown'} ({mod.id if mod else '0'})", inline=False)
        await log.send(embed=e)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        log = await self.get_log_channel(channel.guild)
        if not log: return
        mod = await self.fetch_mod(channel.guild, discord.AuditLogAction.channel_delete)
        
        e = self.wock_embed("Channel Deleted", self.c_red, mod)
        e.description = f"{mod.mention if mod else '@Unknown'} deleted the channel: -"
        e.add_field(name="Channel", value=f"#{channel.name} ({channel.id})", inline=False)
        e.add_field(name="Moderator", value=f"{mod if mod else 'Unknown'} ({mod.id if mod else '0'})", inline=False)
        await log.send(embed=e)

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before, after):
        log = await self.get_log_channel(after.guild)
        if not log:
            return

        changes = []
        if before.name != after.name:
            changes.append(("Name", before.name, after.name))
        if before.topic != after.topic:
            changes.append(("Topic", before.topic or "-", after.topic or "-"))
        if before.slowmode_delay != after.slowmode_delay:
            changes.append(("Slowmode", f"{before.slowmode_delay}s", f"{after.slowmode_delay}s"))

        if not changes:
            return

        entry = await self.fetch_mod_entry(after.guild, discord.AuditLogAction.channel_update, target_id=after.id)
        mod = entry.user if entry else None
        mod_line = f"{mod.mention if mod else '@Unknown'} updated the channel: -"

        e = self.wock_embed("Channel Updated", 0xF0B232, mod)
        e.description = mod_line
        e.add_field(name="Channel", value=f"{after.mention} ({after.id})", inline=False)
        e.add_field(name="Moderator", value=f"{mod if mod else 'Unknown'} ({mod.id if mod else '0'})", inline=False)

        for label, old_value, new_value in changes[:3]:
            e.add_field(name=f"Old {label}", value=self._truncate(old_value), inline=True)
            e.add_field(name=f"New {label}", value=self._truncate(new_value), inline=True)

        await log.send(embed=e)

    # --- MEMBER EVENTS ---
    @commands.Cog.listener()
    async def on_member_join(self, member):
        if member.bot:
            return

        guild = member.guild
        cfg = await GuildConfig.find_one(GuildConfig.guild_id == guild.id)

        # Autorole assignment
        if cfg and cfg.autoroles:
            for role_id in cfg.autoroles:
                role = guild.get_role(role_id)
                if role:
                    try:
                        await member.add_roles(role)
                    except:
                        pass

        log = await self.get_log_channel(guild)
        if log:
            e = self.wock_embed("Member Joined", self.c_blue, member)
            e.description = f"{member.mention} joined the server: -"
            e.add_field(name="Member", value=f"{member} ({member.id})", inline=False)
            e.add_field(name="Account Created", value=discord.utils.format_dt(member.created_at, style='R'), inline=False)
            await log.send(embed=e)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        if member.bot:
            return

        guild = member.guild
        log = await self.get_log_channel(guild)
        if not log:
            return

        entry = await self.fetch_mod_entry(guild, discord.AuditLogAction.kick, target_id=member.id)
        action_name = "left"
        mod = None
        if entry:
            action_name = "was kicked"
            mod = entry.user
        else:
            ban_entry = await self.fetch_mod_entry(guild, discord.AuditLogAction.ban, target_id=member.id)
            if ban_entry:
                action_name = "was banned"
                mod = ban_entry.user

        e = self.wock_embed("Member Left", self.c_red, mod or member)
        e.description = f"{member.mention} {action_name} the server: -"
        e.add_field(name="Member", value=f"{member} ({member.id})", inline=False)
        if mod:
            e.add_field(name="Moderator", value=f"{mod} ({mod.id})", inline=False)
        e.add_field(name="Joined", value=discord.utils.format_dt(member.joined_at, style='R') if member.joined_at else "Unknown", inline=False)
        await log.send(embed=e)

    @commands.Cog.listener()
    async def on_user_update(self, before, after):
        if before.bot:
            return

        username_changed = before.name != after.name
        avatar_changed = before.display_avatar != after.display_avatar

        if username_changed:
            from models.configs import UserConfig
            res = await UserConfig.find_one(UserConfig.user_id == after.id)
            if not res:
                res = UserConfig(user_id=after.id, username_history=[])

            if not hasattr(res, 'username_history'):
                res.username_history = []

            res.username_history.append({
                "name": after.name,
                "timestamp": datetime.utcnow().isoformat()
            })
            await res.save()

        if username_changed or avatar_changed:
            for guild in self.bot.guilds:
                member = guild.get_member(after.id)
                if not member:
                    continue
                log = await self.get_log_channel(guild)
                if not log:
                    continue

                e = self.wock_embed("User Updated", 0xF0B232, after)
                e.description = f"{after.mention} updated profile details: -"
                e.add_field(name="User", value=f"{after} ({after.id})", inline=False)
                if username_changed:
                    e.add_field(name="Old Username", value=self._truncate(before.name), inline=True)
                    e.add_field(name="New Username", value=self._truncate(after.name), inline=True)
                if avatar_changed:
                    e.add_field(name="Avatar", value="Avatar updated", inline=False)
                    e.set_thumbnail(url=after.display_avatar.url)
                await log.send(embed=e)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.bot:
            return

        log = await self.get_log_channel(after.guild)
        if not log:
            return

        changes = []
        if before.nick != after.nick:
            changes.append(("Nickname", before.nick or "-", after.nick or "-"))

        before_roles = {r.id for r in before.roles if not r.is_default()}
        after_roles = {r.id for r in after.roles if not r.is_default()}
        if before_roles != after_roles:
            added = [after.guild.get_role(rid).mention for rid in (after_roles - before_roles) if after.guild.get_role(rid)]
            removed = [after.guild.get_role(rid).mention for rid in (before_roles - after_roles) if after.guild.get_role(rid)]
            if added:
                changes.append(("Roles Added", "-", ", ".join(added)))
            if removed:
                changes.append(("Roles Removed", ", ".join(removed), "-"))

        if before.timed_out_until != after.timed_out_until:
            changes.append(("Timeout", str(before.timed_out_until or "None"), str(after.timed_out_until or "None")))

        if not changes:
            return

        entry = await self.fetch_mod_entry(after.guild, discord.AuditLogAction.member_update, target_id=after.id)
        mod = entry.user if entry else None

        e = self.wock_embed("Member Updated", 0xF0B232, mod)
        e.description = f"{mod.mention if mod else '@Unknown'} updated a member: -"
        e.add_field(name="Member", value=f"{after.mention} ({after.id})", inline=False)
        e.add_field(name="Moderator", value=f"{mod if mod else 'Unknown'} ({mod.id if mod else '0'})", inline=False)

        for label, old_value, new_value in changes[:3]:
            e.add_field(name=f"Old {label}", value=self._truncate(old_value), inline=True)
            e.add_field(name=f"New {label}", value=self._truncate(new_value), inline=True)

        await log.send(embed=e)

    # --- MESSAGE EVENTS ---
    @commands.Cog.listener()
    async def on_message_delete(self, message):
        if message.author.bot or not message.guild: return
        
        # --- STICKY MESSAGE HANDLER ---
        guild_config = await GuildConfig.find_one(GuildConfig.guild_id == message.guild.id)
        if guild_config and guild_config.sticky_messages:
            channel_id = str(message.channel.id)
            if channel_id in guild_config.sticky_messages:
                sticky_data = guild_config.sticky_messages[channel_id]
                # Check if the deleted message was the sticky message
                if sticky_data.get("message_id") == message.id:
                    try:
                        # Repost the sticky message
                        content = sticky_data.get("content", "")
                        if content:
                            new_msg = await message.channel.send(content)
                            # Update the stored message ID
                            guild_config.sticky_messages[channel_id]["message_id"] = new_msg.id
                            await guild_config.save()
                    except:
                        pass
        
        log = await self.get_log_channel(message.guild)
        if not log: return

        e = self.wock_embed("Message Deleted", self.c_red, message.author)
        e.description = f"{message.author.mention} deleted a message: -"
        e.add_field(name="Channel", value=f"{message.channel.mention} ({message.channel.id})", inline=False)
        e.add_field(name="User", value=f"{message.author} ({message.author.id})", inline=False)
        e.add_field(name="Content", value=self._truncate(message.content or "-"), inline=False)
        await log.send(embed=e)

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        if before.author.bot or before.content == after.content: return
        log = await self.get_log_channel(before.guild)
        if not log: return

        e = self.wock_embed("Message Updated", 0xF0B232, before.author)
        e.description = (
            f"**User:** {before.author.mention} ({before.author.id})\n"
            f"**Channel:** {before.channel.mention}\n\n"
            f"**Old Content**\n{self._truncate(before.content or '-')}\n\n"
            f"**New Content**\n{self._truncate(after.content or '-')}"
        )
        await log.send(embed=e)

    # --- ROLE EVENTS ---
    @commands.Cog.listener()
    async def on_guild_role_create(self, role):
        log = await self.get_log_channel(role.guild)
        if not log: return
        mod = await self.fetch_mod(role.guild, discord.AuditLogAction.role_create)
        
        e = self.wock_embed("Role Created", self.c_blue, mod)
        e.description = f"{mod.mention if mod else '@Unknown'} created a role: -"
        e.add_field(name="Role", value=f"{role.mention} ({role.id})", inline=False)
        e.add_field(name="Moderator", value=f"{mod if mod else 'Unknown'} ({mod.id if mod else '0'})", inline=False)
        await log.send(embed=e)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role):
        log = await self.get_log_channel(role.guild)
        if not log: return
        mod = await self.fetch_mod(role.guild, discord.AuditLogAction.role_delete)
        
        e = self.wock_embed("Role Deleted", self.c_red, mod)
        e.description = f"{mod.mention if mod else '@Unknown'} deleted a role: -"
        e.add_field(name="Role", value=f"{role.name} ({role.id})", inline=False)
        e.add_field(name="Moderator", value=f"{mod if mod else 'Unknown'} ({mod.id if mod else '0'})", inline=False)
        await log.send(embed=e)

    @commands.Cog.listener()
    async def on_guild_role_update(self, before, after):
        log = await self.get_log_channel(after.guild)
        if not log:
            return

        changes = []
        if before.name != after.name:
            changes.append(("Name", before.name, after.name))
        if before.color != after.color:
            changes.append(("Color", str(before.color), str(after.color)))
        if before.hoist != after.hoist:
            changes.append(("Hoisted", str(before.hoist), str(after.hoist)))

        if not changes:
            return

        entry = await self.fetch_mod_entry(after.guild, discord.AuditLogAction.role_update, target_id=after.id)
        mod = entry.user if entry else None

        e = self.wock_embed("Role Updated", 0xF0B232, mod)
        e.description = f"{mod.mention if mod else '@Unknown'} updated a role: -"
        e.add_field(name="Role", value=f"{after.mention} ({after.id})", inline=False)
        e.add_field(name="Moderator", value=f"{mod if mod else 'Unknown'} ({mod.id if mod else '0'})", inline=False)
        for label, old_value, new_value in changes[:3]:
            e.add_field(name=f"Old {label}", value=self._truncate(old_value), inline=True)
            e.add_field(name=f"New {label}", value=self._truncate(new_value), inline=True)
        await log.send(embed=e)

    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild):
        log = await self.get_log_channel(after)
        if not log:
            return

        changes = []
        if before.name != after.name:
            changes.append(("Name", before.name, after.name))
        if before.description != after.description:
            changes.append(("Description", before.description or "-", after.description or "-"))
        if before.icon != after.icon:
            changes.append(("Icon", "Updated", "Updated"))

        if not changes:
            return

        entry = await self.fetch_mod_entry(after, discord.AuditLogAction.guild_update, target_id=after.id)
        mod = entry.user if entry else None

        e = self.wock_embed("Server Updated", 0xF0B232, mod)
        e.description = f"{mod.mention if mod else '@Unknown'} updated the server: -"
        e.add_field(name="Server", value=f"{after.name} ({after.id})", inline=False)
        e.add_field(name="Moderator", value=f"{mod if mod else 'Unknown'} ({mod.id if mod else '0'})", inline=False)
        for label, old_value, new_value in changes[:3]:
            e.add_field(name=f"Old {label}", value=self._truncate(old_value), inline=True)
            e.add_field(name=f"New {label}", value=self._truncate(new_value), inline=True)
        await log.send(embed=e)

    # --- VOICE EVENTS (VoiceMaster) ---
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """Handle VoiceMaster channel creation and deletion"""
        guild = member.guild
        
        config = await GuildConfig.find_one(GuildConfig.guild_id == guild.id)
        if not config or not config.voicemaster_enabled or not config.voicemaster_channel_id:
            return
        
        lobby_channel = guild.get_channel(config.voicemaster_channel_id)
        if not lobby_channel:
            return

        if after.channel and after.channel.id == lobby_channel.id:
            try:
                channel_name = f"{member.display_name}'s Channel"
                new_channel = await guild.create_voice_channel(
                    channel_name,
                    category=lobby_channel.category,
                    reason=f"VoiceMaster: Created for {member.display_name}"
                )
                
                await member.move_to(new_channel)
                
                if not hasattr(config, 'voicemaster_user_channels'):
                    config.voicemaster_user_channels = {}
                
                if not isinstance(config.voicemaster_user_channels, dict):
                    config.voicemaster_user_channels = {}
                
                config.voicemaster_user_channels[str(member.id)] = new_channel.id
                await config.save()
            except Exception as e:
                pass

        if before.channel:
            try:
                if not hasattr(config, 'voicemaster_user_channels'):
                    config.voicemaster_user_channels = {}
                
                if not isinstance(config.voicemaster_user_channels, dict):
                    config.voicemaster_user_channels = {}
                
                if str(member.id) in config.voicemaster_user_channels:
                    stored_channel_id = config.voicemaster_user_channels[str(member.id)]
                    
                    if before.channel.id == stored_channel_id:
                        channel = guild.get_channel(stored_channel_id)
                        if channel and len(channel.members) == 0:
                            await channel.delete(reason=f"VoiceMaster: {member.display_name} left")
                            del config.voicemaster_user_channels[str(member.id)]
                            await config.save()
            except Exception as e:
                pass

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        """Handle reaction role assignments and self-react monitoring"""
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return
        
        config = await GuildConfig.find_one(GuildConfig.guild_id == guild.id)
        if not config:
            return
        
        member = guild.get_member(payload.user_id)
        if not member or member.bot:
            return
        
        # --- NOSELFREACT MONITORING ---
        if config.noselfreact_enabled:
            try:
                channel = guild.get_channel(payload.channel_id)
                message = await channel.fetch_message(payload.message_id)
                
                # Check if user reacted to their own message
                if message.author.id == payload.user_id:
                    # Check if emoji is monitored
                    emoji_str = str(payload.emoji)
                    if not config.noselfreact_monitored_emojis or emoji_str in config.noselfreact_monitored_emojis:
                        # Check if member is exempt
                        if payload.user_id in config.noselfreact_exempt_members:
                            return
                        
                        # Check if channel is exempt
                        if payload.channel_id in config.noselfreact_exempt_channels:
                            return
                        
                        # Check if any of member's roles are exempt
                        for role in member.roles:
                            if role.id in config.noselfreact_exempt_roles:
                                return
                        
                        # Check if staff bypass is enabled and user is staff
                        if config.noselfreact_staff_bypass and member.guild_permissions.manage_messages:
                            return
                        
                        # Apply punishment
                        punishment = config.noselfreact_punishment
                        try:
                            if punishment == "kick":
                                await member.kick(reason="Self-react violation")
                            elif punishment == "ban":
                                await guild.ban(member, reason="Self-react violation")
                            elif punishment == "timeout":
                                from datetime import timedelta
                                await member.timeout(timedelta(hours=1), reason="Self-react violation")
                            elif punishment == "warn":
                                # Send DM warning
                                try:
                                    await member.send("⚠️ You have been warned for self-reacting on your own message.")
                                except:
                                    pass
                        except:
                            pass
            except:
                pass
        
        # --- REACTION ROLES ---
        if not config.reaction_roles:
            return
        
        message_id = str(payload.message_id)
        if message_id not in config.reaction_roles:
            return
        
        emoji_str = str(payload.emoji)
        if emoji_str not in config.reaction_roles[message_id]:
            return
        
        role_id = config.reaction_roles[message_id][emoji_str]
        role = guild.get_role(role_id)
        
        if not role:
            return
        
        try:
            await member.add_roles(role)
        except:
            pass

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        """Handle reaction role removals"""
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return
        
        config = await GuildConfig.find_one(GuildConfig.guild_id == guild.id)
        if not config or not config.reaction_roles:
            return
        
        message_id = str(payload.message_id)
        if message_id not in config.reaction_roles:
            return
        
        emoji_str = str(payload.emoji)
        if emoji_str not in config.reaction_roles[message_id]:
            return
        
        role_id = config.reaction_roles[message_id][emoji_str]
        role = guild.get_role(role_id)
        member = guild.get_member(payload.user_id)
        
        if not role or not member or member.bot:
            return
        
        try:
            await member.remove_roles(role)
        except:
            pass

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """Track voice activity"""
        if member.bot:
            return
        
        user_id = member.id
        guild_id = member.guild.id
        
        # User joined voice
        if before.channel is None and after.channel is not None:
            self.voice_sessions[user_id] = {
                'guild_id': guild_id,
                'started': datetime.utcnow()
            }
        
        # User left voice
        elif before.channel is not None and after.channel is None:
            if user_id in self.voice_sessions:
                session = self.voice_sessions[user_id]
                time_in_voice = (datetime.utcnow() - session['started']).total_seconds()
                
                user_activity = self.activity_data[guild_id][user_id]
                user_activity['voice_time'] += int(time_in_voice)
                user_activity['last_seen'] = datetime.utcnow()
                
                del self.voice_sessions[user_id]

    async def _check_antinuke_action(self, guild: discord.Guild, user: discord.User, action: str):
        """Check if antinuke should respond to an action"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == guild.id)
        if not config or not config.antinuke_enabled:
            return False
        
        if user.id in config.antinuke_trusted or user.id in config.antinuke_whitelist:
            return False
        
        if user.id == guild.owner_id:
            return False
        
        if not config.antinuke_modules.get(action, False):
            return False
        
        return True

    async def _execute_antinuke_punishment(self, guild: discord.Guild, user: discord.User):
        """Execute the configured punishment on a user"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == guild.id)
        if not config:
            return
        
        try:
            member = guild.get_member(user.id)
            if not member:
                return
            
            if config.antinuke_action == "kick":
                await member.kick(reason="[Antinuke] Suspicious activity detected")
            elif config.antinuke_action == "timeout":
                await member.timeout(timedelta(hours=1), reason="[Antinuke] Suspicious activity detected")
            else:
                await guild.ban(user, reason="[Antinuke] Suspicious activity detected")
        except:
            pass

    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild):
        """Monitor guild setting changes"""
        if not await self._check_antinuke_action(after, None, "guild"):
            return
        
        try:
            async for entry in after.audit_logs(limit=1, action=discord.AuditLogAction.guild_update):
                if entry.user and (datetime.utcnow() - entry.created_at).total_seconds() < 5:
                    if entry.user.bot:
                        return
                    
                    await self._execute_antinuke_punishment(after, entry.user)
                    break
        except:
            pass

    @commands.Cog.listener()
    async def on_channel_create(self, channel: discord.abc.GuildChannel):
        """Monitor channel creation"""
        if not channel.guild:
            return
        
        if not await self._check_antinuke_action(channel.guild, None, "channelcreate"):
            return
        
        try:
            async for entry in channel.guild.audit_logs(limit=1, action=discord.AuditLogAction.channel_create):
                if entry.user and (datetime.utcnow() - entry.created_at).total_seconds() < 5:
                    if entry.user.bot:
                        return
                    
                    await channel.delete()
                    await self._execute_antinuke_punishment(channel.guild, entry.user)
                    break
        except:
            pass

    @commands.Cog.listener()
    async def on_channel_delete(self, channel: discord.abc.GuildChannel):
        """Monitor channel deletion"""
        if not channel.guild:
            return
        
        if not await self._check_antinuke_action(channel.guild, None, "channel"):
            return
        
        try:
            async for entry in channel.guild.audit_logs(limit=1, action=discord.AuditLogAction.channel_delete):
                if entry.user and (datetime.utcnow() - entry.created_at).total_seconds() < 5:
                    if entry.user.bot:
                        return
                    
                    await self._execute_antinuke_punishment(channel.guild, entry.user)
                    break
        except:
            pass

    @commands.Cog.listener()
    async def on_channel_update(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel):
        """Monitor channel updates"""
        if not after.guild:
            return
        
        if not await self._check_antinuke_action(after.guild, None, "channel"):
            return
        
        try:
            async for entry in after.guild.audit_logs(limit=1, action=discord.AuditLogAction.channel_update):
                if entry.user and (datetime.utcnow() - entry.created_at).total_seconds() < 5:
                    if entry.user.bot:
                        return
                    
                    await self._execute_antinuke_punishment(after.guild, entry.user)
                    break
        except:
            pass

    @commands.Cog.listener()
    async def on_role_create(self, role: discord.Role):
        """Monitor role creation"""
        if not role.guild:
            return
        
        if not await self._check_antinuke_action(role.guild, None, "rolecreate"):
            return
        
        try:
            async for entry in role.guild.audit_logs(limit=1, action=discord.AuditLogAction.role_create):
                if entry.user and (datetime.utcnow() - entry.created_at).total_seconds() < 5:
                    if entry.user.bot:
                        return
                    
                    await role.delete()
                    await self._execute_antinuke_punishment(role.guild, entry.user)
                    break
        except:
            pass

    @commands.Cog.listener()
    async def on_role_delete(self, role: discord.Role):
        """Monitor role deletion"""
        if not role.guild:
            return
        
        if not await self._check_antinuke_action(role.guild, None, "role"):
            return
        
        try:
            async for entry in role.guild.audit_logs(limit=1, action=discord.AuditLogAction.role_delete):
                if entry.user and (datetime.utcnow() - entry.created_at).total_seconds() < 5:
                    if entry.user.bot:
                        return
                    
                    await self._execute_antinuke_punishment(role.guild, entry.user)
                    break
        except:
            pass

    @commands.Cog.listener()
    async def on_role_update(self, before: discord.Role, after: discord.Role):
        """Monitor role updates"""
        if not after.guild:
            return
        
        if not await self._check_antinuke_action(after.guild, None, "role"):
            return
        
        try:
            async for entry in after.guild.audit_logs(limit=1, action=discord.AuditLogAction.role_update):
                if entry.user and (datetime.utcnow() - entry.created_at).total_seconds() < 5:
                    if entry.user.bot:
                        return
                    
                    await self._execute_antinuke_punishment(after.guild, entry.user)
                    break
        except:
            pass

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        """Monitor member bans"""
        if not await self._check_antinuke_action(guild, user, "ban"):
            return
        
        try:
            async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.ban):
                if entry.user and (datetime.utcnow() - entry.created_at).total_seconds() < 5:
                    if entry.user.bot:
                        return
                    
                    await guild.unban(user)
                    await self._execute_antinuke_punishment(guild, entry.user)
                    break
        except:
            pass

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """Monitor member kicks/removals"""
        if member.bot:
            return
        
        if not await self._check_antinuke_action(member.guild, member, "kick"):
            return
        
        try:
            async for entry in member.guild.audit_logs(limit=1, action=discord.AuditLogAction.kick):
                if entry.user and (datetime.utcnow() - entry.created_at).total_seconds() < 5:
                    if entry.user.bot:
                        return
                    
                    try:
                        await member.guild.ban(member, reason="[Antinuke] Unauthorized removal")
                    except:
                        pass
                    
                    await self._execute_antinuke_punishment(member.guild, entry.user)
                    break
        except:
            pass

    @commands.Cog.listener()
    async def on_webhooks_update(self, channel: discord.abc.GuildChannel):
        """Monitor webhook creation/deletion"""
        if not channel.guild:
            return
        
        if not await self._check_antinuke_action(channel.guild, None, "webhooks"):
            return
        
        try:
            async for entry in channel.guild.audit_logs(limit=1, action=discord.AuditLogAction.webhook_create):
                if entry.user and (datetime.utcnow() - entry.created_at).total_seconds() < 5:
                    if entry.user.bot:
                        return
                    
                    webhooks = await channel.webhooks()
                    for webhook in webhooks:
                        if webhook.user and webhook.user.id == entry.user.id:
                            try:
                                await webhook.delete()
                            except:
                                pass
                    
                    await self._execute_antinuke_punishment(channel.guild, entry.user)
                    break
        except:
            pass

    @commands.Cog.listener()
    async def on_integration_update(self, integration: discord.Integration):
        """Monitor integration updates"""
        try:
            guild = integration.guild
            if not guild or not await self._check_antinuke_action(guild, None, "integration"):
                return
            
            async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.integration_update):
                if entry.user and (datetime.utcnow() - entry.created_at).total_seconds() < 5:
                    if entry.user.bot:
                        return
                    
                    await self._execute_antinuke_punishment(guild, entry.user)
                    break
        except:
            pass


async def setup(bot):
    await bot.add_cog(Events(bot))