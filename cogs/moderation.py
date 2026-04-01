import discord
from discord.ext import commands
from discord import ui
from datetime import datetime, timedelta
from models.moderation import ModCase
from models.warnings import Warning
from models.configs import GuildConfig, ScheduledMessage
from utils.paginator import WockPaginator
from utils.parser import EmbedParser
import io
import zipfile
import re

class ConfirmAction(ui.View):
    def __init__(self, ctx, member: discord.Member, action: str):
        super().__init__(timeout=30)
        self.ctx = ctx
        self.member = member
        self.action = action
        self.value = None

    @ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.ctx.author.id:
            return await interaction.response.send_message("This isn't your menu.", ephemeral=True)
        self.value = True
        self.stop()
        await interaction.response.defer()

    @ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.ctx.author.id:
            return await interaction.response.send_message("This isn't your menu.", ephemeral=True)
        self.value = False
        self.stop()
        await interaction.response.defer()

class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.jail_role_cache = {}

    async def _get_invoke_template(self, guild_id: int, mode: str, action: str):
        config = await GuildConfig.find_one(GuildConfig.guild_id == guild_id)
        if not config or not getattr(config, "invoke_messages", None):
            return None
        return config.invoke_messages.get(mode, {}).get(action)

    def _render_invoke_template(self, template: str, *, guild: discord.Guild, moderator, target=None, reason: str = "No reason provided", action: str = "", channel=None, case_number=None, role=None):
        target_mention = target.mention if getattr(target, "mention", None) else "Unknown User"
        target_name = getattr(target, "name", "Unknown User") if target else "Unknown User"
        target_display = getattr(target, "display_name", target_name) if target else target_name
        moderator_mention = moderator.mention if getattr(moderator, "mention", None) else str(moderator)
        moderator_name = getattr(moderator, "name", str(moderator))
        moderator_display = getattr(moderator, "display_name", moderator_name)
        channel_name = getattr(channel, "name", "Unknown Channel") if channel else "Unknown Channel"
        channel_mention = getattr(channel, "mention", channel_name) if channel else channel_name
        role_name = role.name if role else "No role"
        role_mention = role.mention if role else role_name

        replacements = {
            "{user}": target_mention,
            "{user.mention}": target_mention,
            "{user.name}": target_name,
            "{user.display_name}": target_display,
            "{user.id}": str(target.id) if target else "0",
            "{moderator}": moderator_mention,
            "{moderator.mention}": moderator_mention,
            "{moderator.name}": moderator_name,
            "{moderator.display_name}": moderator_display,
            "{moderator.id}": str(moderator.id) if getattr(moderator, "id", None) else "0",
            "{guild}": guild.name,
            "{guild.name}": guild.name,
            "{guild.member_count}": str(guild.member_count),
            "{reason}": reason,
            "{action}": action,
            "{channel}": channel_name,
            "{channel.name}": channel_name,
            "{channel.mention}": channel_mention,
            "{case}": str(case_number) if case_number is not None else "Unknown",
            "{case_number}": str(case_number) if case_number is not None else "Unknown",
            "{role}": role_mention,
            "{role.name}": role_name,
        }

        rendered = template
        for key, value in replacements.items():
            rendered = rendered.replace(key, str(value))
        return rendered

    async def _dispatch_invoke(self, ctx, mode: str, action: str, *, target=None, reason: str = "No reason provided", case_number=None, channel=None, role=None):
        template = await self._get_invoke_template(ctx.guild.id, mode, action)
        if not template:
            return False

        rendered = self._render_invoke_template(
            template,
            guild=ctx.guild,
            moderator=ctx.author,
            target=target,
            reason=reason,
            action=action,
            channel=channel or ctx.channel,
            case_number=case_number,
            role=role,
        )

        parser_markers = ["{title:", "{description:", "{color:", "{field_name", "{field_value", "{image:", "{thumbnail:", "{author:", "{footer:"]
        destination = target if mode == "dm" else (channel or ctx.channel)

        try:
            if any(marker in rendered.lower() for marker in parser_markers):
                parser = EmbedParser(ctx)
                embed = parser.parse(rendered)
                await destination.send(embed=embed)
            else:
                await destination.send(rendered)
            return True
        except discord.Forbidden:
            return False
        except Exception:
            return False

    async def cog_command_error(self, ctx, error):
        """Triggered when a command in this cog fails"""
        if isinstance(error, commands.MissingRequiredArgument):
            return await ctx.send_help(ctx.command)
        
        if isinstance(error, commands.MemberNotFound):
            return await self.bot.warn(ctx, f"I couldn't find that member.")
        
        if isinstance(error, commands.MissingPermissions):
            return await self.bot.warn(ctx, "You don't have permission to use this command.")

    async def dm_user(self, member: discord.Member, guild: discord.Guild, action: str, reason: str):
        """Helper to DM users before they are kicked/banned"""
        try:
            fake_ctx = type("InvokeContext", (), {"guild": guild, "author": guild.me, "channel": None})()
            template = await self._get_invoke_template(guild.id, "dm", action)
            if template:
                rendered = self._render_invoke_template(
                    template,
                    guild=guild,
                    moderator=guild.me,
                    target=member,
                    reason=reason,
                    action=action,
                )
                parser_markers = ["{title:", "{description:", "{color:", "{field_name", "{field_value", "{image:", "{thumbnail:", "{author:", "{footer:"]
                if any(marker in rendered.lower() for marker in parser_markers):
                    parser = EmbedParser(fake_ctx)
                    embed = parser.parse(rendered)
                    await member.send(embed=embed)
                else:
                    await member.send(rendered)
                return

            embed = discord.Embed(
                title="Notice",
                description=f"You have been **{action}** from **{guild.name}**",
                color=0xff0000
            )
            embed.add_field(name="Reason", value=reason)
            await member.send(embed=embed)
        except discord.Forbidden:
            pass

    async def save_case(self, guild_id: int, user_id: int, moderator_id: int, action: str, reason: str, duration: str = None):
        """Save a moderation case to the database"""
        try:
            last_case = await ModCase.find(ModCase.guild_id == guild_id).sort([("case_number", -1)]).first_or_none()
            case_number = (last_case.case_number + 1) if last_case else 1
            
            case = ModCase(
                guild_id=guild_id,
                user_id=user_id,
                moderator_id=moderator_id,
                action=action,
                reason=reason,
                timestamp=datetime.utcnow(),
                case_number=case_number,
                duration=duration
            )
            await case.insert()
            return case_number
        except Exception as e:
            print(f"Error saving moderation case: {e}")
            return None

    async def check_booster(self, ctx, member: discord.Member, action: str):
        """Warning embed if the user is a booster"""
        if member.premium_since is None:
            return True

        embed = discord.Embed(
            title="⚠️ Booster Warning",
            description=f"{member.mention} is a **Server Booster**.\nAre you sure you want to {action} them?",
            color=0xffcc00
        )
        view = ConfirmAction(ctx, member, action)
        msg = await ctx.send(embed=embed, view=view)
        
        await view.wait()
        try:
            await msg.delete()
        except:
            pass
            
        return view.value

        async def _can_moderate_target(self, ctx, member: discord.Member):
            if member.id == ctx.author.id:
                await self.bot.warn(ctx, "You cannot punish yourself.")
                return False
            if member.id == ctx.guild.owner_id:
                await self.bot.warn(ctx, "You cannot punish the server owner.")
                return False
            if member.top_role >= ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:
                await self.bot.warn(ctx, "You cannot punish someone with a higher or equal role.")
                return False
            if member.top_role >= ctx.guild.me.top_role:
                await self.bot.warn(ctx, "I cannot punish someone with a higher or equal role than mine.")
                return False
            return True

        async def _sanitize_punishment_role(self, role: discord.Role, mode: str):
            perms = role.permissions
            updates = {}

            if perms.administrator:
                updates["administrator"] = False

            if mode == "reactionmute":
                updates.update({
                    "add_reactions": False,
                    "use_external_emojis": False,
                    "use_external_stickers": False,
                })
            elif mode == "imagemute":
                updates.update({
                    "attach_files": False,
                    "embed_links": False,
                    "use_external_stickers": False,
                })
            elif mode in ("mute", "jail"):
                updates.update({
                    "send_messages": False,
                    "send_messages_in_threads": False,
                    "add_reactions": False,
                    "speak": False,
                    "connect": False,
                    "create_public_threads": False,
                    "create_private_threads": False,
                })

            if updates:
                await role.edit(permissions=perms.update(**updates), reason=f"Punishment role sanitize ({mode})")

        async def _ensure_punishment_overwrites(self, guild: discord.Guild, role: discord.Role, mode: str, jail_channel_id: int = None):
            deny = discord.PermissionOverwrite()

            if mode == "reactionmute":
                deny.add_reactions = False
                deny.use_external_emojis = False
                deny.use_external_stickers = False
            elif mode == "imagemute":
                deny.attach_files = False
                deny.embed_links = False
                deny.use_external_stickers = False
            elif mode == "mute":
                deny.send_messages = False
                deny.send_messages_in_threads = False
                deny.add_reactions = False
                deny.speak = False
                deny.connect = False
                deny.create_public_threads = False
                deny.create_private_threads = False
            elif mode == "jail":
                deny.view_channel = False
                deny.send_messages = False
                deny.send_messages_in_threads = False
                deny.add_reactions = False
                deny.speak = False
                deny.connect = False

            for channel in guild.channels:
                try:
                    await channel.set_permissions(role, overwrite=deny, reason=f"Apply {mode} permissions")
                except Exception:
                    continue

            if mode == "jail" and jail_channel_id:
                jail_channel = guild.get_channel(jail_channel_id)
                if jail_channel:
                    allow = discord.PermissionOverwrite(
                        view_channel=True,
                        send_messages=True,
                        read_message_history=True,
                        add_reactions=False,
                        attach_files=False,
                        embed_links=False
                    )
                    try:
                        await jail_channel.set_permissions(role, overwrite=allow, reason="Allow jailed users in jail channel")
                    except Exception:
                        pass

        async def _apply_punishment_role(self, ctx, member: discord.Member, role_id: int, mode: str, reason: str):
            if not role_id:
                settings_key = {
                    "reactionmute": "rmute",
                    "imagemute": "imute",
                    "mute": "mute",
                    "jail": "jail"
                }.get(mode, mode)
                await self.bot.warn(ctx, f"No {mode} role is configured. Use `settings {settings_key}` first.")
                return None

            role = ctx.guild.get_role(role_id)
            if not role:
                await self.bot.warn(ctx, f"Configured {mode} role no longer exists.")
                return None

            if role >= ctx.guild.me.top_role:
                await self.bot.warn(ctx, "That role is above my highest role.")
                return None

            await self._sanitize_punishment_role(role, mode)

            cfg = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
            jail_channel_id = cfg.jail_channel_id if cfg else None
            await self._ensure_punishment_overwrites(ctx.guild, role, mode, jail_channel_id=jail_channel_id)

            if role in member.roles:
                await self.bot.warn(ctx, f"**{member}** already has {role.mention}.")
                return None

            await member.add_roles(role, reason=f"{mode} by {ctx.author}: {reason}")
            return role

    @commands.group(name="purge", aliases=["clear"], invoke_without_command=True)
    @commands.has_permissions(manage_messages=True)
    @commands.bot_has_permissions(manage_messages=True)
    async def purge(self, ctx, amount: int = None):
        """Delete the last N messages in the channel"""
        if not amount:
            return await ctx.send_help(ctx.command)
        if amount < 1 or amount > 100:
            return await self.bot.warn(ctx, "You can only delete between 1 and 100 messages.")
        
        try:
            deleted = await ctx.channel.purge(limit=amount)
            case_num = await self.save_case(ctx.guild.id, 0, ctx.author.id, "purge", f"Purged {len(deleted)} messages")
            await self.bot.grant(ctx, f"Purged **{len(deleted)}** messages | Case **#{case_num}**")
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to purge: {str(e)}")

    @purge.command(name="bots")
    async def purge_bots(self, ctx, amount: int = 50):
        """Delete bot messages"""
        if amount < 1 or amount > 100:
            return await self.bot.warn(ctx, "You can only delete between 1 and 100 messages.")
        
        try:
            deleted = await ctx.channel.purge(limit=amount, check=lambda m: m.author.bot)
            case_num = await self.save_case(ctx.guild.id, 0, ctx.author.id, "purge_bots", f"Purged {len(deleted)} bot messages")
            await self.bot.grant(ctx, f"Purged **{len(deleted)}** bot messages | Case **#{case_num}**")
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to purge bots: {str(e)}")

    @purge.command(name="user")
    async def purge_user(self, ctx, user: discord.User = None, amount: int = 50):
        """Delete messages from a specific user"""
        if not user:
            return await ctx.send_help(ctx.command)
        if amount < 1 or amount > 100:
            return await self.bot.warn(ctx, "You can only delete between 1 and 100 messages.")
        
        try:
            deleted = await ctx.channel.purge(limit=amount, check=lambda m: m.author.id == user.id)
            case_num = await self.save_case(ctx.guild.id, 0, ctx.author.id, "purge_user", f"Purged {len(deleted)} messages from {user}")
            await self.bot.grant(ctx, f"Purged **{len(deleted)}** messages from **{user}** | Case **#{case_num}**")
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to purge user: {str(e)}")

    @purge.command(name="files")
    async def purge_files(self, ctx, amount: int = 50):
        """Delete messages with file attachments"""
        if amount < 1 or amount > 100:
            return await self.bot.warn(ctx, "You can only delete between 1 and 100 messages.")
        
        try:
            deleted = await ctx.channel.purge(limit=amount, check=lambda m: len(m.attachments) > 0)
            case_num = await self.save_case(ctx.guild.id, 0, ctx.author.id, "purge_files", f"Purged {len(deleted)} messages with files")
            await self.bot.grant(ctx, f"Purged **{len(deleted)}** messages with files | Case **#{case_num}**")
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to purge files: {str(e)}")

    @purge.command(name="attachments")
    async def purge_attachments(self, ctx, amount: int = 50):
        """Delete messages with attachments"""
        if amount < 1 or amount > 100:
            return await self.bot.warn(ctx, "You can only delete between 1 and 100 messages.")
        
        try:
            deleted = await ctx.channel.purge(limit=amount, check=lambda m: len(m.attachments) > 0)
            case_num = await self.save_case(ctx.guild.id, 0, ctx.author.id, "purge_attachments", f"Purged {len(deleted)} messages with attachments")
            await self.bot.grant(ctx, f"Purged **{len(deleted)}** messages with attachments | Case **#{case_num}**")
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to purge attachments: {str(e)}")

    @purge.command(name="embeds")
    async def purge_embeds(self, ctx, amount: int = 50):
        """Delete messages with embeds"""
        if amount < 1 or amount > 100:
            return await self.bot.warn(ctx, "You can only delete between 1 and 100 messages.")
        
        try:
            deleted = await ctx.channel.purge(limit=amount, check=lambda m: len(m.embeds) > 0)
            case_num = await self.save_case(ctx.guild.id, 0, ctx.author.id, "purge_embeds", f"Purged {len(deleted)} messages with embeds")
            await self.bot.grant(ctx, f"Purged **{len(deleted)}** messages with embeds | Case **#{case_num}**")
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to purge embeds: {str(e)}")

    @purge.command(name="links")
    async def purge_links(self, ctx, amount: int = 50):
        """Delete messages containing links"""
        if amount < 1 or amount > 100:
            return await self.bot.warn(ctx, "You can only delete between 1 and 100 messages.")
        
        try:
            import re
            url_pattern = r'https?://\S+'
            deleted = await ctx.channel.purge(limit=amount, check=lambda m: re.search(url_pattern, m.content))
            case_num = await self.save_case(ctx.guild.id, 0, ctx.author.id, "purge_links", f"Purged {len(deleted)} messages with links")
            await self.bot.grant(ctx, f"Purged **{len(deleted)}** messages with links | Case **#{case_num}**")
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to purge links: {str(e)}")

    @purge.command(name="invites")
    async def purge_invites(self, ctx, amount: int = 50):
        """Delete messages with Discord invites"""
        if amount < 1 or amount > 100:
            return await self.bot.warn(ctx, "You can only delete between 1 and 100 messages.")
        
        try:
            import re
            invite_pattern = r'discord(?:\.gg|app\.com/invite)/\S+'
            deleted = await ctx.channel.purge(limit=amount, check=lambda m: re.search(invite_pattern, m.content))
            case_num = await self.save_case(ctx.guild.id, 0, ctx.author.id, "purge_invites", f"Purged {len(deleted)} messages with invites")
            await self.bot.grant(ctx, f"Purged **{len(deleted)}** messages with invites | Case **#{case_num}**")
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to purge invites: {str(e)}")

    @purge.command(name="text")
    async def purge_text(self, ctx, text: str = None, amount: int = 50):
        """Delete messages containing specific text"""
        if not text:
            return await ctx.send_help(ctx.command)
        if amount < 1 or amount > 100:
            return await self.bot.warn(ctx, "You can only delete between 1 and 100 messages.")
        
        try:
            deleted = await ctx.channel.purge(limit=amount, check=lambda m: text.lower() in m.content.lower())
            case_num = await self.save_case(ctx.guild.id, 0, ctx.author.id, "purge_text", f"Purged {len(deleted)} messages containing '{text}'")
            await self.bot.grant(ctx, f"Purged **{len(deleted)}** messages containing **{text}** | Case **#{case_num}**")
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to purge text: {str(e)}")

    @purge.command(name="reactions")
    async def purge_reactions(self, ctx, amount: int = 50):
        """Delete all reactions from messages"""
        if amount < 1 or amount > 100:
            return await self.bot.warn(ctx, "You can only delete between 1 and 100 messages.")
        
        try:
            count = 0
            async for message in ctx.channel.history(limit=amount):
                if message.reactions:
                    await message.clear_reactions()
                    count += 1
            
            case_num = await self.save_case(ctx.guild.id, 0, ctx.author.id, "purge_reactions", f"Cleared reactions from {count} messages")
            await self.bot.grant(ctx, f"Cleared reactions from **{count}** messages | Case **#{case_num}**")
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to purge reactions: {str(e)}")

    @purge.command(name="pinned")
    async def purge_pinned(self, ctx, amount: int = 50):
        """Delete pinned messages"""
        if amount < 1 or amount > 100:
            return await self.bot.warn(ctx, "You can only delete between 1 and 100 messages.")
        
        try:
            deleted = await ctx.channel.purge(limit=amount, check=lambda m: m.pinned)
            case_num = await self.save_case(ctx.guild.id, 0, ctx.author.id, "purge_pinned", f"Purged {len(deleted)} pinned messages")
            await self.bot.grant(ctx, f"Purged **{len(deleted)}** pinned messages | Case **#{case_num}**")
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to purge pinned: {str(e)}")

    @purge.command(name="mentions")
    async def purge_mentions(self, ctx, amount: int = 50):
        """Delete messages with mentions"""
        if amount < 1 or amount > 100:
            return await self.bot.warn(ctx, "You can only delete between 1 and 100 messages.")
        
        try:
            deleted = await ctx.channel.purge(limit=amount, check=lambda m: len(m.mentions) > 0 or len(m.role_mentions) > 0)
            case_num = await self.save_case(ctx.guild.id, 0, ctx.author.id, "purge_mentions", f"Purged {len(deleted)} messages with mentions")
            await self.bot.grant(ctx, f"Purged **{len(deleted)}** messages with mentions | Case **#{case_num}**")
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to purge mentions: {str(e)}")

    @purge.command(name="dms")
    async def purge_dms(self, ctx, amount: int = 50):
        """Delete messages from DMs (bot DMs in channel)"""
        if amount < 1 or amount > 100:
            return await self.bot.warn(ctx, "You can only delete between 1 and 100 messages.")
        
        try:
            deleted = await ctx.channel.purge(limit=amount, check=lambda m: m.author.id == self.bot.user.id)
            case_num = await self.save_case(ctx.guild.id, 0, ctx.author.id, "purge_dms", f"Purged {len(deleted)} bot messages")
            await self.bot.grant(ctx, f"Purged **{len(deleted)}** bot messages | Case **#{case_num}**")
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to purge bot messages: {str(e)}")

    @commands.group(name="role", invoke_without_command=True)
    @commands.has_permissions(manage_roles=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def role(self, ctx, member: discord.Member = None, *, role: discord.Role = None):
        """
        Main role command. 
        Usage: ;role <member> <role>
        """
        if member is None or role is None:
            return await ctx.send_help(ctx.command)

        if role >= ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:
            return await self.bot.warn(ctx, "You cannot manage a role higher than or equal to your own.")
        
        if role >= ctx.guild.me.top_role:
            return await self.bot.warn(ctx, "I cannot manage a role higher than or equal to my own.")

        if role in member.roles:
            await member.remove_roles(role, reason=f"Role toggle by {ctx.author}")
            await self.bot.grant(ctx, f"Removed {role.mention} from **{member}**")
        else:
            await member.add_roles(role, reason=f"Role toggle by {ctx.author}")
            await self.bot.grant(ctx, f"Added {role.mention} to **{member}**")

    @role.command(name="humans")
    async def role_humans(self, ctx, *, role: discord.Role):
        """Add a role to every human in the server"""
        if role >= ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:
            return await self.bot.warn(ctx, "Hierarchy error.")
            
        async with ctx.typing():
            humans = [m for m in ctx.guild.members if not m.bot and role not in m.roles]
            for human in humans:
                await human.add_roles(role, reason=f"Mass role humans by {ctx.author}")
        
        await self.bot.grant(ctx, f"Added {role.mention} to **{len(humans)}** humans.")

    @role.command(name="bots")
    async def role_bots(self, ctx, *, role: discord.Role):
        """Add a role to every bot in the server"""
        if role >= ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:
            return await self.bot.warn(ctx, "Hierarchy error.")

        async with ctx.typing():
            bots = [m for m in ctx.guild.members if m.bot and role not in m.roles]
            for bot in bots:
                await bot.add_roles(role, reason=f"Mass role bots by {ctx.author}")

        await self.bot.grant(ctx, f"Added {role.mention} to **{len(bots)}** bots.")

    @role.command(name="rename")
    async def role_rename(self, ctx, role: discord.Role, *, new_name: str):
        """Rename an existing role"""
        if role >= ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:
            return await self.bot.warn(ctx, "Hierarchy error.")
            
        old_name = role.name
        await role.edit(name=new_name, reason=f"Renamed by {ctx.author}")
        await self.bot.grant(ctx, f"Renamed role **{old_name}** to **{new_name}**")

    @role.command(name="color")
    async def role_color(self, ctx, role: discord.Role, color: discord.Color):
        """Change a role's color"""
        if role >= ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:
            return await self.bot.warn(ctx, "Hierarchy error.")
            
        await role.edit(color=color, reason=f"Color changed by {ctx.author}")
        await self.bot.grant(ctx, f"Changed the color of {role.mention} to **{color}**")

    @commands.command(name="kick")
    @commands.has_permissions(kick_members=True)
    @commands.bot_has_permissions(kick_members=True)
    async def kick(self, ctx, member: discord.Member = None, *, reason: str = "No reason provided"):
        """Kick a member from the server"""
        if not member:
            return await ctx.send_help(ctx.command)
        if member.id == ctx.author.id:
            return await self.bot.warn(ctx, "You cannot kick yourself.")
            
        if member.top_role >= ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:
            return await self.bot.warn(ctx, "You cannot kick someone with a higher or equal role.")

        if not await self.check_booster(ctx, member, "kick"):
            return await ctx.send("Kick cancelled.")

        await self.dm_user(member, ctx.guild, "kicked", reason)
        await member.kick(reason=f"Kicked by {ctx.author}: {reason}")
        case_num = await self.save_case(ctx.guild.id, member.id, ctx.author.id, "kick", reason)
        sent = await self._dispatch_invoke(ctx, "message", "kick", target=member, reason=reason, case_number=case_num)
        if not sent:
            await self.bot.grant(ctx, f"**{member}** has been kicked | Case **#{case_num}** | {reason}")

    @commands.group(name="thread", invoke_without_command=True)
    @commands.has_permissions(manage_threads=True)
    @commands.bot_has_permissions(manage_threads=True)
    async def thread(self, ctx):
        """Manage threads and forum posts"""
        await ctx.send_help(ctx.command)

    @thread.command(name="lock")
    @commands.has_permissions(manage_threads=True)
    @commands.bot_has_permissions(manage_threads=True)
    async def thread_lock(self, ctx, thread: discord.Thread = None, *, reason: str = "No reason provided"):
        """Lock a thread or forum post"""
        thread = thread or ctx.channel
        if not isinstance(thread, discord.Thread):
            return await self.bot.warn(ctx, "This command can only be used in a thread or by specifying a thread.")
        try:
            await thread.edit(locked=True, reason=f"Locked by {ctx.author}: {reason}")
            await self.bot.grant(ctx, f"Thread **{thread.name}** has been locked")
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to lock thread: {str(e)}")

    @thread.command(name="unlock")
    @commands.has_permissions(manage_threads=True)
    @commands.bot_has_permissions(manage_threads=True)
    async def thread_unlock(self, ctx, thread: discord.Thread = None, *, reason: str = "No reason provided"):
        """Unlock a thread or forum post"""
        thread = thread or ctx.channel
        if not isinstance(thread, discord.Thread):
            return await self.bot.warn(ctx, "This command can only be used in a thread or by specifying a thread.")
        try:
            await thread.edit(locked=False, reason=f"Unlocked by {ctx.author}: {reason}")
            await self.bot.grant(ctx, f"Thread **{thread.name}** has been unlocked")
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to unlock thread: {str(e)}")

    @thread.command(name="add")
    @commands.has_permissions(manage_threads=True)
    @commands.bot_has_permissions(manage_threads=True)
    async def thread_add(self, ctx, member: discord.Member, thread: discord.Thread = None):
        """Add a member to the thread"""
        thread = thread or ctx.channel
        if not isinstance(thread, discord.Thread):
            return await self.bot.warn(ctx, "This command can only be used in a thread or by specifying a thread.")
        try:
            await thread.add_user(member)
            await self.bot.grant(ctx, f"**{member}** has been added to **{thread.name}**")
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to add member: {str(e)}")

    @thread.command(name="remove")
    @commands.has_permissions(manage_threads=True)
    @commands.bot_has_permissions(manage_threads=True)
    async def thread_remove(self, ctx, member: discord.Member, thread: discord.Thread = None):
        """Remove a member from the thread"""
        thread = thread or ctx.channel
        if not isinstance(thread, discord.Thread):
            return await self.bot.warn(ctx, "This command can only be used in a thread or by specifying a thread.")
        try:
            await thread.remove_user(member)
            await self.bot.grant(ctx, f"**{member}** has been removed from **{thread.name}**")
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to remove member: {str(e)}")

    @thread.command(name="rename")
    @commands.has_permissions(manage_threads=True)
    @commands.bot_has_permissions(manage_threads=True)
    async def thread_rename(self, ctx, thread: discord.Thread = None, *, name: str = None):
        """Rename a thread or forum post"""
        if not name:
            return await ctx.send_help(ctx.command)
        thread = thread or ctx.channel
        if not isinstance(thread, discord.Thread):
            return await self.bot.warn(ctx, "This command can only be used in a thread or by specifying a thread.")
        try:
            await thread.edit(name=name, reason=f"Renamed by {ctx.author}")
            await self.bot.grant(ctx, f"Thread has been renamed to **{name}**")
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to rename thread: {str(e)}")

    @commands.command(name="ban")
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def ban(self, ctx, member: discord.Member = None, *, reason: str = "No reason provided"):
        """Ban a member from the server"""
        if not member:
            return await ctx.send_help(ctx.command)
        if member.id == ctx.author.id:
            return await self.bot.warn(ctx, "You cannot ban yourself.")

        if member.top_role >= ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:
            return await self.bot.warn(ctx, "You cannot ban someone with a higher or equal role.")

        if not await self.check_booster(ctx, member, "ban"):
            return await ctx.send("Ban cancelled.")

        await self.dm_user(member, ctx.guild, "banned", reason)
        await member.ban(reason=f"Banned by {ctx.author}: {reason}")
        case_num = await self.save_case(ctx.guild.id, member.id, ctx.author.id, "ban", reason)
        sent = await self._dispatch_invoke(ctx, "message", "ban", target=member, reason=reason, case_number=case_num)
        if not sent:
            await self.bot.grant(ctx, f"**{member}** has been banned | Case **#{case_num}** | {reason}")

    @commands.command(name="bans")
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def bans(self, ctx):
        """List all banned users in the server"""
        try:
            bans = [ban async for ban in ctx.guild.bans()]
            if not bans:
                return await self.bot.neutral(ctx, "There are no banned users in this server.")
            
            pages = []
            bans_per_page = 10
            
            for i in range(0, len(bans), bans_per_page):
                page_bans = bans[i:i + bans_per_page]
                embed = discord.Embed(color=0xff0000, title="Banned Users")
                
                for ban in page_bans:
                    user = ban.user
                    reason = ban.reason or "No reason provided"
                    embed.add_field(name=str(user), value=reason, inline=False)
                
                embed.set_footer(text=f"Page {len(pages) + 1} of {(len(bans) + bans_per_page - 1) // bans_per_page}")
                pages.append(embed)
            
            paginator = WockPaginator(ctx, pages)
            await paginator.start()
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to fetch bans: {str(e)}")

    @commands.group(name="unban", invoke_without_command=True)
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def unban(self, ctx, user: discord.User = None, *, reason: str = "No reason provided"):
        """Unban a user from the server"""
        if not user:
            return await ctx.send_help(ctx.command)
        try:
            await ctx.guild.unban(user, reason=f"Unbanned by {ctx.author}: {reason}")
            case_num = await self.save_case(ctx.guild.id, user.id, ctx.author.id, "unban", reason)
            sent = await self._dispatch_invoke(ctx, "message", "unban", target=user, reason=reason, case_number=case_num)
            if not sent:
                await self.bot.grant(ctx, f"**{user}** has been unbanned | Case **#{case_num}**")
        except discord.NotFound:
            return await self.bot.warn(ctx, "That user is not banned.")

    @unban.command(name="all")
    async def unban_all(self, ctx):
        """Unban all users from the server"""
        try:
            bans = [ban async for ban in ctx.guild.bans()]
            if not bans:
                return await self.bot.neutral(ctx, "There are no banned users in this server.")
            
            async with ctx.typing():
                unbanned_count = 0
                for ban in bans:
                    try:
                        await ctx.guild.unban(ban.user, reason=f"Mass unban by {ctx.author}")
                        unbanned_count += 1
                    except:
                        pass
            
            await self.bot.grant(ctx, f"Unbanned **{unbanned_count}** users from the server")
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to unban all users: {str(e)}")

    @commands.command(name="softban")
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def softban(self, ctx, member: discord.Member = None, *, reason: str = "No reason provided"):
        """Ban and immediately unban a member (deletes recent messages)"""
        if not member:
            return await ctx.send_help(ctx.command)
        if member.id == ctx.author.id:
            return await self.bot.warn(ctx, "You cannot softban yourself.")

        if member.top_role >= ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:
            return await self.bot.warn(ctx, "You cannot softban someone with a higher or equal role.")

        if not await self.check_booster(ctx, member, "softban"):
            return await ctx.send("Softban cancelled.")

        await self.dm_user(member, ctx.guild, "softbanned", reason)
        await ctx.guild.ban(member, reason=f"Softbanned by {ctx.author}: {reason}", delete_message_seconds=604800)
        await ctx.guild.unban(member, reason=f"Softban cleanup")
        case_num = await self.save_case(ctx.guild.id, member.id, ctx.author.id, "softban", reason)
        await self.bot.grant(ctx, f"**{member}** has been softbanned | Case **#{case_num}** | {reason}")

    @commands.command(name="timeout", aliases=["tempmute", "tmo"])
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True)
    async def timeout(self, ctx, member: discord.Member = None, duration: str = None, *, reason: str = "No reason provided"):
        """Timeout a member (1m, 1h, 1d, etc.)"""
        if not member or not duration:
            return await ctx.send_help(ctx.command)
        unit = duration[-1].lower()
        try:
            amount = int(duration[:-1])
        except ValueError:
            return await self.bot.warn(ctx, "Invalid duration format (e.g., 10m, 1h, 1d)")
        
        units = {'m': 60, 'h': 3600, 'd': 86400}
        if unit not in units:
            return await self.bot.warn(ctx, "Use m (minutes), h (hours), or d (days)")
        
        seconds = amount * units[unit]
        timeout_until = datetime.utcnow() + timedelta(seconds=seconds)
        
        try:
            await member.timeout(timeout_until, reason=f"Timeout by {ctx.author}: {reason}")
            case_num = await self.save_case(ctx.guild.id, member.id, ctx.author.id, "timeout", reason, duration)
            await self.bot.grant(ctx, f"**{member}** timed out for **{duration}** | Case **#{case_num}** | {reason}")
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to timeout member: {str(e)}")

    @commands.command(name="reactionmute", aliases=["rmute"])
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def reactionmute(self, ctx, member: discord.Member = None, *, reason: str = "No reason provided"):
        """Reaction mute a user"""
        if not member:
            return await ctx.send_help(ctx.command)
        if not await self._can_moderate_target(ctx, member):
            return

        cfg = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        role = await self._apply_punishment_role(
            ctx,
            member,
            cfg.rmute_role_id if cfg else None,
            "reactionmute",
            reason
        )
        if not role:
            return

        case_num = await self.save_case(ctx.guild.id, member.id, ctx.author.id, "reactionmute", reason)
        sent = await self._dispatch_invoke(ctx, "message", "mute", target=member, reason=reason, case_number=case_num, role=role)
        if not sent:
            await self.bot.grant(ctx, f"**{member}** has been reaction-muted with {role.mention} | Case **#{case_num}**")

    @commands.command(name="unreactionmute", aliases=["urmute"])
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def unreactionmute(self, ctx, member: discord.Member = None, *, reason: str = "No reason provided"):
        """Remove reaction mute from a user"""
        if not member:
            return await ctx.send_help(ctx.command)
        if not await self._can_moderate_target(ctx, member):
            return

        cfg = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not cfg or not cfg.rmute_role_id:
            return await self.bot.warn(ctx, "No reaction mute role configured.")

        role = ctx.guild.get_role(cfg.rmute_role_id)
        if not role:
            return await self.bot.warn(ctx, "Configured reaction mute role no longer exists.")

        if role not in member.roles:
            return await self.bot.warn(ctx, f"**{member}** is not reaction-muted.")

        try:
            await member.remove_roles(role, reason=f"Unreaction-muted by {ctx.author}: {reason}")
        except Exception as e:
            return await self.bot.deny(ctx, f"Failed to remove reaction mute role: {str(e)}")

        await self._dispatch_invoke(ctx, "dm", "unmute", target=member, reason=reason, role=role)
        case_num = await self.save_case(ctx.guild.id, member.id, ctx.author.id, "unreactionmute", reason)
        sent = await self._dispatch_invoke(ctx, "message", "unmute", target=member, reason=reason, case_number=case_num, role=role)
        if not sent:
            await self.bot.grant(ctx, f"**{member}** has been unreaction-muted | Case **#{case_num}**")

    @commands.command(name="imagemute", aliases=["imute"])
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def imagemute(self, ctx, member: discord.Member = None, *, reason: str = "No reason provided"):
        """Image mute a user"""
        if not member:
            return await ctx.send_help(ctx.command)
        if not await self._can_moderate_target(ctx, member):
            return

        cfg = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        role = await self._apply_punishment_role(
            ctx,
            member,
            cfg.imute_role_id if cfg else None,
            "imagemute",
            reason
        )
        if not role:
            return

        case_num = await self.save_case(ctx.guild.id, member.id, ctx.author.id, "imagemute", reason)
        sent = await self._dispatch_invoke(ctx, "message", "mute", target=member, reason=reason, case_number=case_num, role=role)
        if not sent:
            await self.bot.grant(ctx, f"**{member}** has been image-muted with {role.mention} | Case **#{case_num}**")

    @commands.command(name="unimagemute", aliases=["uimute"])
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def unimagemute(self, ctx, member: discord.Member = None, *, reason: str = "No reason provided"):
        """Remove image mute from a user"""
        if not member:
            return await ctx.send_help(ctx.command)
        if not await self._can_moderate_target(ctx, member):
            return

        cfg = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not cfg or not cfg.imute_role_id:
            return await self.bot.warn(ctx, "No image mute role configured.")

        role = ctx.guild.get_role(cfg.imute_role_id)
        if not role:
            return await self.bot.warn(ctx, "Configured image mute role no longer exists.")

        if role not in member.roles:
            return await self.bot.warn(ctx, f"**{member}** is not image-muted.")

        try:
            await member.remove_roles(role, reason=f"Unimage-muted by {ctx.author}: {reason}")
        except Exception as e:
            return await self.bot.deny(ctx, f"Failed to remove image mute role: {str(e)}")

        await self._dispatch_invoke(ctx, "dm", "unmute", target=member, reason=reason, role=role)
        case_num = await self.save_case(ctx.guild.id, member.id, ctx.author.id, "unimagemute", reason)
        sent = await self._dispatch_invoke(ctx, "message", "unmute", target=member, reason=reason, case_number=case_num, role=role)
        if not sent:
            await self.bot.grant(ctx, f"**{member}** has been unimage-muted | Case **#{case_num}**")

    @commands.command(name="mute")
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def mute(self, ctx, member: discord.Member = None, *, reason: str = "No reason provided"):
        """Mute a user with the configured mute role"""
        if not member:
            return await ctx.send_help(ctx.command)
        if not await self._can_moderate_target(ctx, member):
            return

        cfg = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        role = await self._apply_punishment_role(
            ctx,
            member,
            cfg.mute_role_id if cfg else None,
            "mute",
            reason
        )
        if not role:
            return

        await self._dispatch_invoke(ctx, "dm", "mute", target=member, reason=reason, role=role)
        case_num = await self.save_case(ctx.guild.id, member.id, ctx.author.id, "mute", reason)
        sent = await self._dispatch_invoke(ctx, "message", "mute", target=member, reason=reason, case_number=case_num, role=role)
        if not sent:
            await self.bot.grant(ctx, f"**{member}** has been muted with {role.mention} | Case **#{case_num}**")

    @commands.command(name="unmute")
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def unmute(self, ctx, member: discord.Member = None, *, reason: str = "No reason provided"):
        """Remove mute from a user"""
        if not member:
            return await ctx.send_help(ctx.command)
        if not await self._can_moderate_target(ctx, member):
            return

        cfg = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not cfg or not cfg.mute_role_id:
            return await self.bot.warn(ctx, "No mute role configured.")

        role = ctx.guild.get_role(cfg.mute_role_id)
        if not role:
            return await self.bot.warn(ctx, "Configured mute role no longer exists.")

        if role not in member.roles:
            return await self.bot.warn(ctx, f"**{member}** is not muted.")

        try:
            await member.remove_roles(role, reason=f"Unmuted by {ctx.author}: {reason}")
        except Exception as e:
            return await self.bot.deny(ctx, f"Failed to remove mute role: {str(e)}")

        await self._dispatch_invoke(ctx, "dm", "unmute", target=member, reason=reason, role=role)
        case_num = await self.save_case(ctx.guild.id, member.id, ctx.author.id, "unmute", reason)
        sent = await self._dispatch_invoke(ctx, "message", "unmute", target=member, reason=reason, case_number=case_num, role=role)
        if not sent:
            await self.bot.grant(ctx, f"**{member}** has been unmuted | Case **#{case_num}**")

    @commands.command(name="jail")
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def jail(self, ctx, member: discord.Member = None, *, reason: str = "No reason provided"):
        """Jail a user"""
        if not member:
            return await ctx.send_help(ctx.command)
        if not await self._can_moderate_target(ctx, member):
            return

        cfg = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not cfg or not cfg.jail_role_id:
            return await self.bot.warn(ctx, "No jail role configured. Use `settings jail <role> [channel]` first.")

        if cfg.jail_remove_roles:
            removable_roles = [
                r for r in member.roles
                if r != ctx.guild.default_role
                and r.id != cfg.jail_role_id
                and r < ctx.guild.me.top_role
            ]
            if removable_roles:
                try:
                    self.jail_role_cache.setdefault(ctx.guild.id, {})[member.id] = [r.id for r in removable_roles]
                    await member.remove_roles(*removable_roles, reason=f"Jail cleanup by {ctx.author}")
                except Exception:
                    pass

        role = await self._apply_punishment_role(ctx, member, cfg.jail_role_id, "jail", reason)
        if not role:
            return

        await self._dispatch_invoke(ctx, "dm", "jail", target=member, reason=reason, role=role)
        case_num = await self.save_case(ctx.guild.id, member.id, ctx.author.id, "jail", reason)
        jail_channel = ctx.guild.get_channel(cfg.jail_channel_id) if cfg.jail_channel_id else None
        jail_channel_text = f" | Channel: {jail_channel.mention}" if jail_channel else ""
        if jail_channel:
            await self._dispatch_invoke(ctx, "message", "jailchannel", target=member, reason=reason, case_number=case_num, channel=jail_channel, role=role)
        sent = await self._dispatch_invoke(ctx, "message", "jail", target=member, reason=reason, case_number=case_num, channel=jail_channel or ctx.channel, role=role)
        if not sent:
            await self.bot.grant(ctx, f"**{member}** has been jailed with {role.mention}{jail_channel_text} | Case **#{case_num}**")

    @commands.command(name="unjail")
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def unjail(self, ctx, member: discord.Member = None, *, reason: str = "No reason provided"):
        """Remove jail from a user"""
        if not member:
            return await ctx.send_help(ctx.command)
        if not await self._can_moderate_target(ctx, member):
            return

        cfg = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not cfg or not cfg.jail_role_id:
            return await self.bot.warn(ctx, "No jail role configured. Use `settings jail <role> [channel]` first.")

        role = ctx.guild.get_role(cfg.jail_role_id)
        if not role:
            return await self.bot.warn(ctx, "Configured jail role no longer exists.")

        if role not in member.roles:
            return await self.bot.warn(ctx, f"**{member}** is not jailed.")

        try:
            await member.remove_roles(role, reason=f"Unjailed by {ctx.author}: {reason}")
        except Exception as e:
            return await self.bot.deny(ctx, f"Failed to remove jail role: {str(e)}")

        restored_roles = []
        cached_roles = self.jail_role_cache.get(ctx.guild.id, {}).pop(member.id, [])
        if cached_roles:
            restorable_roles = []
            for role_id in cached_roles:
                cached_role = ctx.guild.get_role(role_id)
                if not cached_role:
                    continue
                if cached_role >= ctx.guild.me.top_role:
                    continue
                if cached_role in member.roles:
                    continue
                restorable_roles.append(cached_role)

            if restorable_roles:
                try:
                    await member.add_roles(*restorable_roles, reason=f"Unjail restore by {ctx.author}")
                    restored_roles = restorable_roles
                except Exception:
                    restored_roles = []

        await self._dispatch_invoke(ctx, "dm", "unjail", target=member, reason=reason, role=role)
        case_num = await self.save_case(ctx.guild.id, member.id, ctx.author.id, "unjail", reason)
        sent = await self._dispatch_invoke(ctx, "message", "unjail", target=member, reason=reason, case_number=case_num, role=role)
        if not sent:
            restore_text = ""
            if restored_roles:
                restore_text = f" | Restored **{len(restored_roles)}** role{'s' if len(restored_roles) != 1 else ''}"
            await self.bot.grant(ctx, f"**{member}** has been unjailed{restore_text} | Case **#{case_num}**")

    @commands.group(name="untimeout", invoke_without_command=True)
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True)
    async def untimeout(self, ctx, member: discord.Member = None):
        """Remove a timeout from a member"""
        if not member:
            return await ctx.send_help(ctx.command)
        try:
            await member.timeout(None, reason=f"Timeout removed by {ctx.author}")
            case_num = await self.save_case(ctx.guild.id, member.id, ctx.author.id, "untimeout", "Timeout removed")
            await self._dispatch_invoke(ctx, "dm", "unmute", target=member, reason="Timeout removed", case_number=case_num)
            sent = await self._dispatch_invoke(ctx, "message", "unmute", target=member, reason="Timeout removed", case_number=case_num)
            if not sent:
                await self.bot.grant(ctx, f"**{member}** timeout removed | Case **#{case_num}**")
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to remove timeout: {str(e)}")

    @untimeout.command(name="all")
    async def untimeout_all(self, ctx):
        """Remove timeout from all members in the server"""
        try:
            async with ctx.typing():
                untimeouted_count = 0
                for member in ctx.guild.members:
                    if member.timed_out:
                        try:
                            await member.timeout(None, reason=f"Mass untimeout by {ctx.author}")
                            untimeouted_count += 1
                        except:
                            pass
            
            await self.bot.grant(ctx, f"Removed timeout from **{untimeouted_count}** members")
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to remove all timeouts: {str(e)}")

    @commands.command(name="warn")
    @commands.has_permissions(moderate_members=True)
    async def warn(self, ctx, member: discord.Member = None, *, reason: str = "No reason provided"):
        """Warn a member"""
        if not member:
            return await ctx.send_help(ctx.command)
        if member.id == ctx.author.id:
            return await self.bot.warn(ctx, "You cannot warn yourself.")
        
        if member.top_role >= ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:
            return await self.bot.warn(ctx, "You cannot warn someone with a higher or equal role.")
        
        try:
            case_num = await self.save_case(ctx.guild.id, member.id, ctx.author.id, "warn", reason)
            
            warning = Warning(
                guild_id=ctx.guild.id,
                user_id=member.id,
                moderator_id=ctx.author.id,
                reason=reason,
                timestamp=datetime.utcnow(),
                case_number=case_num
            )
            await warning.insert()
            
            warn_count = await Warning.find(
                Warning.guild_id == ctx.guild.id,
                Warning.user_id == member.id
            ).count()
            
            try:
                sent_dm = await self._dispatch_invoke(ctx, "dm", "warn", target=member, reason=reason, case_number=case_num)
                if not sent_dm:
                    embed = discord.Embed(
                        title="⚠️ Warning",
                        description=f"You have been warned in **{ctx.guild.name}**",
                        color=0xffcc00
                    )
                    embed.add_field(name="Reason", value=reason)
                    embed.add_field(name="Total Warnings", value=f"{warn_count}", inline=False)
                    await member.send(embed=embed)
            except discord.Forbidden:
                pass
            
            sent = await self._dispatch_invoke(ctx, "message", "warn", target=member, reason=reason, case_number=case_num)
            if not sent:
                await self.bot.grant(ctx, f"**{member}** warned | Case **#{case_num}** | **{warn_count}** total warnings | {reason}")
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to warn member: {str(e)}")

    @commands.command(name="warnings")
    @commands.has_permissions(moderate_members=True)
    async def warnings(self, ctx, member: discord.User = None):
        """View a user's warnings"""
        if not member:
            return await ctx.send_help(ctx.command)
        
        try:
            warnings = await Warning.find(
                Warning.guild_id == ctx.guild.id,
                Warning.user_id == member.id
            ).sort([("timestamp", 1)]).to_list(None)
            
            if not warnings:
                return await self.bot.neutral(ctx, f"**{member}** has no warnings")
            
            embed = discord.Embed(color=0x242429, title=f"Warnings - {member}")
            
            for warn in warnings[-10:]:
                try:
                    moderator = await self.bot.fetch_user(warn.moderator_id)
                    mod_name = str(moderator)
                except:
                    mod_name = f"Unknown ({warn.moderator_id})"
                
                timestamp = discord.utils.format_dt(warn.timestamp, style="R")
                warn_info = f"Moderator: {mod_name}\n"
                warn_info += f"Reason: {warn.reason}\n"
                warn_info += f"Time: {timestamp}"

                embed.add_field(
                    name=f"Warning #{warn.case_number}",
                    value=warn_info,
                    inline=False
                )
            
            embed.set_footer(text=f"Total Warnings: {len(warnings)}")
            await ctx.send(embed=embed)
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to fetch warnings: {str(e)}")

    @commands.command(name="clearwarnings", aliases=["removewarnings"])
    @commands.has_permissions(manage_guild=True)
    async def clearwarnings(self, ctx, member: discord.User = None):
        """Clear all warnings for a user"""
        if not member:
            return await ctx.send_help(ctx.command)
        
        try:
            result = await Warning.find(
                Warning.guild_id == ctx.guild.id,
                Warning.user_id == member.id
            ).delete()
            
            await self.bot.grant(ctx, f"Cleared **{result}** warnings for **{member}**")
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to clear warnings: {str(e)}")

    @commands.group(name="modhistory", aliases=["history", "cases"], invoke_without_command=True)
    @commands.has_permissions(moderate_members=True)
    async def modhistory(self, ctx, user_or_mod: str = None, mod: str = None):
        """View moderation history for a user
        Usage:
        modhistory @user - Actions against the user
        modhistory @user true - Actions done BY the user
        modhistory true - Actions done BY you
        """
        user = None
        is_mod = False
        
        if user_or_mod:
            if user_or_mod.lower() in ("true", "yes", "1"):
                is_mod = True
                user = ctx.author
            else:
                try:
                    user = await commands.UserConverter().convert(ctx, user_or_mod)
                except commands.UserNotFound:
                    return await self.bot.warn(ctx, f"User **{user_or_mod}** not found.")
        
        if mod and mod.lower() in ("true", "yes", "1"):
            is_mod = True
        
        if not user:
            return await ctx.send_help(ctx.command)
        
        try:
            if is_mod:
                cases = await ModCase.find(
                    ModCase.guild_id == ctx.guild.id,
                    ModCase.moderator_id == user.id
                ).sort([("case_number", 1)]).to_list(None)
                title = f"Moderation Actions by {user}"
            else:
                cases = await ModCase.find(
                    ModCase.guild_id == ctx.guild.id,
                    ModCase.user_id == user.id
                ).sort([("case_number", 1)]).to_list(None)
                title = f"Moderation History - {user}"
            
            if not cases:
                return await self.bot.neutral(ctx, f"No moderation cases for **{user}**")
            
            pages = []
            
            for case in cases:
                try:
                    moderator = await self.bot.fetch_user(case.moderator_id)
                    mod_name = str(moderator)
                except:
                    mod_name = f"Unknown ({case.moderator_id})"
                
                try:
                    target = await self.bot.fetch_user(case.user_id)
                    target_name = str(target)
                except:
                    target_name = f"Unknown ({case.user_id})"
                
                timestamp = discord.utils.format_dt(case.timestamp, style="F")
                action = case.action.upper()
                
                embed = discord.Embed(color=0x242429, title=f"{title}")
                embed.add_field(name="Case", value=f"**#{case.case_number}**", inline=True)
                embed.add_field(name="Action", value=action, inline=True)
                
                if is_mod:
                    embed.add_field(name="Target", value=target_name, inline=False)
                else:
                    embed.add_field(name="Moderator", value=mod_name, inline=False)
                
                embed.add_field(name="Reason", value=case.reason, inline=False)
                if case.duration:
                    embed.add_field(name="Duration", value=case.duration, inline=False)
                embed.add_field(name="Date", value=timestamp, inline=False)
                
                embed.set_footer(text=f"Page 1 of {len(cases)}")
                pages.append(embed)
            
            paginator = WockPaginator(ctx, pages)
            await paginator.start()
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to fetch history: {str(e)}")

    @modhistory.command(name="clear")
    async def modhistory_clear(self, ctx, user: discord.User = None):
        """Clear all moderation history for a user"""
        if not user:
            return await ctx.send_help(ctx.command)
        
        try:
            result = await ModCase.find(
                ModCase.guild_id == ctx.guild.id,
                ModCase.user_id == user.id
            ).delete()
            
            await self.bot.grant(ctx, f"Cleared **{result}** moderation cases for **{user}**")
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to clear history: {str(e)}")

    @commands.group(name="case", invoke_without_command=True)
    @commands.has_permissions(moderate_members=True)
    async def case(self, ctx, case_num: int = None):
        """View details about a specific moderation case"""
        if not case_num:
            return await ctx.send_help(ctx.command)
        
        try:
            case = await ModCase.find_one(
                ModCase.guild_id == ctx.guild.id,
                ModCase.case_number == case_num
            )
            
            if not case:
                return await self.bot.warn(ctx, f"Case **#{case_num}** not found")
            
            try:
                user = await self.bot.fetch_user(case.user_id)
                user_name = str(user)
            except:
                user_name = f"Unknown ({case.user_id})"
            
            try:
                moderator = await self.bot.fetch_user(case.moderator_id)
                mod_name = str(moderator)
            except:
                mod_name = f"Unknown ({case.moderator_id})"
            
            timestamp = discord.utils.format_dt(case.timestamp, style="F")
            
            embed = discord.Embed(color=0x242429, title=f"Case #{case_num}")
            embed.add_field(name="Action", value=case.action.upper(), inline=True)
            embed.add_field(name="User", value=user_name, inline=True)
            embed.add_field(name="Moderator", value=mod_name, inline=True)
            embed.add_field(name="Reason", value=case.reason, inline=False)
            if case.duration:
                embed.add_field(name="Duration", value=case.duration, inline=False)
            embed.add_field(name="Timestamp", value=timestamp, inline=False)
            
            await ctx.send(embed=embed)
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to fetch case: {str(e)}")

    @case.command(name="reason")
    async def case_reason(self, ctx, case_num: int = None, *, reason: str = None):
        """Edit the reason for a moderation case"""
        if not case_num or not reason:
            return await ctx.send_help(ctx.command)
        
        try:
            case = await ModCase.find_one(
                ModCase.guild_id == ctx.guild.id,
                ModCase.case_number == case_num
            )
            
            if not case:
                return await self.bot.warn(ctx, f"Case **#{case_num}** not found")
            
            old_reason = case.reason
            case.reason = reason
            await case.save()
            
            await self.bot.grant(ctx, f"Updated reason for Case **#{case_num}**\n**Old:** {old_reason}\n**New:** {reason}")
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to update case: {str(e)}")

    @case.command(name="delete")
    async def case_delete(self, ctx, case_num: int = None):
        """Delete a moderation case"""
        if not case_num:
            return await ctx.send_help(ctx.command)
        
        try:
            case = await ModCase.find_one(
                ModCase.guild_id == ctx.guild.id,
                ModCase.case_number == case_num
            )
            
            if not case:
                return await self.bot.warn(ctx, f"Case **#{case_num}** not found")
            
            await case.delete()
            await self.bot.grant(ctx, f"Deleted Case **#{case_num}**")
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to delete case: {str(e)}")

    @commands.group(name="emoji", invoke_without_command=True)
    @commands.has_permissions(manage_expressions=True)
    async def emoji(self, ctx):
        """Manage server emojis"""
        await ctx.send_help(ctx.command)

    @emoji.command(name="add")
    async def emoji_add(self, ctx, emoji_arg: str, *, name: str = None):
        """Add a new emoji to the server"""
        emoji_match = re.search(r'<a?:[a-zA-Z0-9_]+:(\d+)>', emoji_arg)
        
        if emoji_match:
            emoji_id = emoji_match.group(1)
            is_animated = emoji_arg.startswith('<a:')
            ext = 'gif' if is_animated else 'png'
            emoji_url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{ext}"
            
            if not name:
                name = emoji_arg.split(':')[1] if ':' in emoji_arg else "emoji"
            
            try:
                emoji_data = await ctx.bot.http._get_bytes(emoji_url)
                new_emoji = await ctx.guild.create_custom_emoji(name=name, image=emoji_data)
                await self.bot.grant(ctx, f"Emoji **{new_emoji.name}** added to the server")
            except Exception as e:
                await self.bot.deny(ctx, f"Failed to add emoji: {str(e)}")
        else:
            await self.bot.deny(ctx, "Invalid emoji format. Use custom emoji format like <:name:id>")

    @emoji.command(name="remove")
    async def emoji_remove(self, ctx, emoji_arg: str):
        """Remove an emoji from the server"""
        emoji_match = re.search(r'<a?:[a-zA-Z0-9_]+:(\d+)>', emoji_arg)
        
        if emoji_match:
            emoji_id = int(emoji_match.group(1))
            emoji_obj = discord.utils.get(ctx.guild.emojis, id=emoji_id)
            
            if not emoji_obj:
                return await self.bot.deny(ctx, "Emoji not found in this server.")
            
            try:
                await emoji_obj.delete()
                await self.bot.grant(ctx, f"Emoji **{emoji_obj.name}** removed from the server")
            except Exception as e:
                await self.bot.deny(ctx, f"Failed to remove emoji: {str(e)}")
        else:
            await self.bot.deny(ctx, "Invalid emoji format.")

    @emoji.command(name="info")
    async def emoji_info(self, ctx, emoji_arg: str):
        """Get information about a specific emoji"""
        emoji_match = re.search(r'<a?:[a-zA-Z0-9_]+:(\d+)>', emoji_arg)
        
        if emoji_match:
            emoji_id = int(emoji_match.group(1))
            emoji_obj = discord.utils.get(ctx.guild.emojis, id=emoji_id)
            
            if not emoji_obj:
                return await self.bot.deny(ctx, "Emoji not found in this server.")
            
            embed = discord.Embed(color=0x242429, title=f"Emoji Info: {emoji_obj.name}")
            embed.set_thumbnail(url=emoji_obj.url)
            embed.add_field(name="Name", value=emoji_obj.name, inline=True)
            embed.add_field(name="ID", value=emoji_obj.id, inline=True)
            embed.add_field(name="Animated", value="Yes" if emoji_obj.animated else "No", inline=True)
            embed.add_field(name="Created", value=discord.utils.format_dt(emoji_obj.created_at, style="F"), inline=False)
            embed.add_field(name="URL", value=f"[Link]({emoji_obj.url})", inline=False)
            
            await ctx.send(embed=embed)
        else:
            await self.bot.deny(ctx, "Invalid emoji format.")

    @emoji.command(name="list")
    async def emoji_list(self, ctx):
        """List all emojis in the server"""
        if not ctx.guild.emojis:
            return await self.bot.deny(ctx, "This server has no custom emojis.")
        
        emoji_list = " ".join(str(e) for e in ctx.guild.emojis)
        embed = discord.Embed(color=0x242429, title=f"Emojis ({len(ctx.guild.emojis)})")
        embed.description = emoji_list
        
        await ctx.send(embed=embed)

    @emoji.command(name="addmany")
    async def emoji_addmany(self, ctx, *emoji_args):
        """Add multiple emojis (up to 10)"""
        if len(emoji_args) > 10:
            return await self.bot.deny(ctx, "You can only add up to 10 emojis at once.")
        
        if not emoji_args:
            return await ctx.send_help(ctx.command)
        
        added = 0
        failed = 0
        
        for emoji_arg in emoji_args:
            emoji_match = re.search(r'<a?:[a-zA-Z0-9_]+:(\d+)>', emoji_arg)
            
            if emoji_match:
                emoji_id = emoji_match.group(1)
                is_animated = emoji_arg.startswith('<a:')
                ext = 'gif' if is_animated else 'png'
                emoji_url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{ext}"
                name = emoji_arg.split(':')[1]
                
                try:
                    emoji_data = await ctx.bot.http._get_bytes(emoji_url)
                    await ctx.guild.create_custom_emoji(name=name, image=emoji_data)
                    added += 1
                except:
                    failed += 1
            else:
                failed += 1
        
        await self.bot.grant(ctx, f"Added **{added}** emoji(s). Failed: **{failed}**")

    @emoji.command(name="zip")
    async def emoji_zip(self, ctx):
        """Zip all server emojis and send as a file"""
        if not ctx.guild.emojis:
            return await self.bot.deny(ctx, "This server has no custom emojis.")
        
        async with ctx.typing():
            zip_buffer = io.BytesIO()
            
            with zipfile.ZipFile(zip_buffer, 'w') as zip_file:
                for emoji in ctx.guild.emojis:
                    try:
                        emoji_data = await ctx.bot.http._get_bytes(emoji.url)
                        ext = 'gif' if emoji.animated else 'png'
                        zip_file.writestr(f"{emoji.name}.{ext}", emoji_data)
                    except:
                        pass
            
            zip_buffer.seek(0)
            await ctx.send(file=discord.File(zip_buffer, filename=f"{ctx.guild.name}-emojis.zip"))

    @emoji.command(name="deletemany")
    async def emoji_deletemany(self, ctx, *emoji_args):
        """Delete multiple emojis"""
        if not emoji_args:
            return await ctx.send_help(ctx.command)
        
        deleted = 0
        failed = 0
        
        for emoji_arg in emoji_args:
            emoji_match = re.search(r'<a?:[a-zA-Z0-9_]+:(\d+)>', emoji_arg)
            
            if emoji_match:
                emoji_id = int(emoji_match.group(1))
                emoji_obj = discord.utils.get(ctx.guild.emojis, id=emoji_id)
                
                if emoji_obj:
                    try:
                        await emoji_obj.delete()
                        deleted += 1
                    except:
                        failed += 1
                else:
                    failed += 1
            else:
                failed += 1
        
        await self.bot.grant(ctx, f"Deleted **{deleted}** emoji(s). Failed: **{failed}**")

    @commands.command(name="steal")
    @commands.has_permissions(manage_expressions=True)
    async def steal(self, ctx):
        """Steal a sticker or emoji from a replied message"""
        if not ctx.message.reference:
            return await self.bot.deny(ctx, "Reply to a message with a sticker or emoji to steal it.")
        
        replied_msg = await ctx.channel.fetch_message(ctx.message.reference.message_id)
        
        if replied_msg.stickers:
            sticker = replied_msg.stickers[0]
            try:
                sticker_data = await ctx.bot.http._get_bytes(sticker.url)
                await ctx.guild.create_sticker(
                    name=sticker.name[:100],
                    description=sticker.name,
                    emoji="⭐",
                    file=discord.File(sticker_data, filename="sticker.png")
                )
                await self.bot.grant(ctx, f"Sticker **{sticker.name}** added to the server")
            except Exception as e:
                await self.bot.deny(ctx, f"Failed to steal sticker: {str(e)}")
        
        elif replied_msg.content:
            emoji_match = re.search(r'<a?:[a-zA-Z0-9_]+:(\d+)>', replied_msg.content)
            if emoji_match:
                emoji_id = emoji_match.group(1)
                is_animated = '<a:' in replied_msg.content[emoji_match.start():emoji_match.end()]
                ext = 'gif' if is_animated else 'png'
                emoji_url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{ext}"
                name = replied_msg.content[emoji_match.start():emoji_match.end()].split(':')[1]
                
                try:
                    emoji_data = await ctx.bot.http._get_bytes(emoji_url)
                    new_emoji = await ctx.guild.create_custom_emoji(name=name, image=emoji_data)
                    await self.bot.grant(ctx, f"Emoji **{new_emoji.name}** stolen and added to the server")
                except Exception as e:
                    await self.bot.deny(ctx, f"Failed to steal emoji: {str(e)}")
            else:
                await self.bot.deny(ctx, "No stickers or emojis found in that message.")
        else:
            await self.bot.deny(ctx, "No stickers or emojis found in that message.")

    @commands.group(name="sticker", invoke_without_command=True)
    @commands.has_permissions(manage_expressions=True)
    async def sticker(self, ctx):
        """Manage server stickers"""
        await ctx.send_help(ctx.command)

    @sticker.command(name="add")
    async def sticker_add(self, ctx, *, name: str = None):
        """Add a new sticker to the server"""
        if not ctx.message.attachments:
            return await self.bot.deny(ctx, "Please attach an image to add as a sticker.")
        
        attachment = ctx.message.attachments[0]
        
        if not name:
            name = attachment.filename.split('.')[0]
        
        try:
            sticker_data = await attachment.read()
            await ctx.guild.create_sticker(
                name=name[:100],
                description=name,
                emoji="⭐",
                file=discord.File(io.BytesIO(sticker_data), filename="sticker.png")
            )
            await self.bot.grant(ctx, f"Sticker **{name}** added to the server")
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to add sticker: {str(e)}")

    @sticker.command(name="remove")
    async def sticker_remove(self, ctx, *, sticker_name: str):
        """Remove a sticker from the server"""
        sticker_obj = discord.utils.get(ctx.guild.stickers, name=sticker_name)
        
        if not sticker_obj:
            return await self.bot.deny(ctx, f"Sticker **{sticker_name}** not found.")
        
        try:
            await sticker_obj.delete()
            await self.bot.grant(ctx, f"Sticker **{sticker_obj.name}** removed from the server")
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to remove sticker: {str(e)}")

    @sticker.command(name="rename")
    async def sticker_rename(self, ctx, old_name: str, *, new_name: str):
        """Rename a sticker"""
        sticker_obj = discord.utils.get(ctx.guild.stickers, name=old_name)
        
        if not sticker_obj:
            return await self.bot.deny(ctx, f"Sticker **{old_name}** not found.")
        
        try:
            await sticker_obj.edit(name=new_name[:100])
            await self.bot.grant(ctx, f"Sticker renamed from **{old_name}** to **{new_name}**")
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to rename sticker: {str(e)}")

    @sticker.command(name="zip")
    async def sticker_zip(self, ctx):
        """Zip all server stickers and send as a file"""
        if not ctx.guild.stickers:
            return await self.bot.deny(ctx, "This server has no stickers.")
        
        async with ctx.typing():
            zip_buffer = io.BytesIO()
            
            with zipfile.ZipFile(zip_buffer, 'w') as zip_file:
                for sticker in ctx.guild.stickers:
                    try:
                        sticker_data = await ctx.bot.http._get_bytes(sticker.url)
                        zip_file.writestr(f"{sticker.name}.png", sticker_data)
                    except:
                        pass
            
            zip_buffer.seek(0)
            await ctx.send(file=discord.File(zip_buffer, filename=f"{ctx.guild.name}-stickers.zip"))

    @commands.group(name="slowmode", invoke_without_command=True)
    @commands.has_permissions(manage_channels=True)
    async def slowmode(self, ctx):
        """Manage slowmode settings for channels"""
        if ctx.invoked_subcommand is None:
            embed = discord.Embed(
                title="⏱️ Slowmode Management",
                description="Manage slowmode settings for your channels",
                color=0x5d92f2
            )
            embed.add_field(
                name="Commands",
                value=(
                    "`slowmode set <channel> <seconds>` - Set slowmode duration\n"
                    "`slowmode remove <channel>` - Remove slowmode\n"
                    "`slowmode view [channel]` - View slowmode status\n"
                    "`slowmode list` - List all channels with slowmode"
                ),
                inline=False
            )
            embed.set_footer(text="Use 'slowmode <subcommand>' for more info")
            await ctx.send(embed=embed)

    @slowmode.command(name="set")
    @commands.has_permissions(manage_channels=True)
    async def slowmode_set(self, ctx, channel: discord.TextChannel, seconds: int):
        """Set slowmode duration for a channel
        
        Args:
            channel: The channel to set slowmode for
            seconds: Duration in seconds (0-21600, max 6 hours)
        """
        try:
            if seconds < 0 or seconds > 21600:
                return await self.bot.warn(ctx, "Slowmode duration must be between 0 and 21600 seconds (6 hours)")
            
            await channel.edit(slowmode_delay=seconds, reason=f"Slowmode set by {ctx.author} ({ctx.author.id})")
            
            if seconds == 0:
                embed = discord.Embed(
                    description=f"✅ Slowmode removed from {channel.mention}",
                    color=0x43b581
                )
            else:
                minutes, secs = divmod(seconds, 60)
                hours, minutes = divmod(minutes, 60)
                
                time_str = ""
                if hours:
                    time_str += f"{hours}h "
                if minutes:
                    time_str += f"{minutes}m "
                if secs or not time_str:
                    time_str += f"{secs}s"
                
                embed = discord.Embed(
                    description=f"✅ Slowmode set to **{time_str.strip()}** in {channel.mention}",
                    color=0x43b581
                )
                embed.add_field(name="Duration", value=f"{seconds} seconds", inline=True)
            
            await ctx.send(embed=embed)
            
        except discord.Forbidden:
            await self.bot.warn(ctx, "I don't have permission to modify this channel")
        except Exception as e:
            await self.bot.warn(ctx, f"Failed to set slowmode: {str(e)}")

    @slowmode.command(name="remove", aliases=["off"])
    @commands.has_permissions(manage_channels=True)
    async def slowmode_remove(self, ctx, channel: discord.TextChannel):
        """Remove slowmode from a channel"""
        try:
            if channel.slowmode_delay == 0:
                return await self.bot.warn(ctx, f"{channel.mention} doesn't have slowmode enabled")
            
            await channel.edit(slowmode_delay=0, reason=f"Slowmode removed by {ctx.author} ({ctx.author.id})")
            
            embed = discord.Embed(
                description=f"✅ Slowmode removed from {channel.mention}",
                color=0x43b581
            )
            await ctx.send(embed=embed)
            
        except discord.Forbidden:
            await self.bot.warn(ctx, "I don't have permission to modify this channel")
        except Exception as e:
            await self.bot.warn(ctx, f"Failed to remove slowmode: {str(e)}")

    @slowmode.command(name="view")
    @commands.has_permissions(manage_channels=True)
    async def slowmode_view(self, ctx, channel: discord.TextChannel = None):
        """View slowmode status for a channel"""
        channel = channel or ctx.channel
        
        try:
            slowmode = channel.slowmode_delay
            
            embed = discord.Embed(
                title="⏱️ Slowmode Status",
                color=0x5d92f2
            )
            embed.add_field(name="Channel", value=channel.mention, inline=False)
            
            if slowmode == 0:
                embed.add_field(name="Status", value="❌ Disabled", inline=True)
            else:
                minutes, secs = divmod(slowmode, 60)
                hours, minutes = divmod(minutes, 60)
                
                time_str = ""
                if hours:
                    time_str += f"{hours}h "
                if minutes:
                    time_str += f"{minutes}m "
                if secs:
                    time_str += f"{secs}s"
                
                embed.add_field(name="Status", value="✅ Enabled", inline=True)
                embed.add_field(name="Duration", value=f"{time_str.strip()}\n({slowmode}s)", inline=True)
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            await self.bot.warn(ctx, f"Failed to view slowmode: {str(e)}")

    @slowmode.command(name="list")
    @commands.has_permissions(manage_channels=True)
    async def slowmode_list(self, ctx):
        """List all channels with slowmode enabled"""
        try:
            channels_with_slowmode = []
            
            for channel in ctx.guild.text_channels:
                if channel.slowmode_delay > 0:
                    channels_with_slowmode.append(channel)
            
            if not channels_with_slowmode:
                return await self.bot.warn(ctx, "No channels have slowmode enabled")
            
            channels_with_slowmode.sort(key=lambda c: c.slowmode_delay)
            
            pages = []
            for i in range(0, len(channels_with_slowmode), 10):
                batch = channels_with_slowmode[i:i+10]
                embed = discord.Embed(
                    title="⏱️ Channels with Slowmode",
                    description=f"Showing {min(i+10, len(channels_with_slowmode))} of {len(channels_with_slowmode)} channels",
                    color=0x5d92f2
                )
                
                for channel in batch:
                    slowmode = channel.slowmode_delay
                    minutes, secs = divmod(slowmode, 60)
                    hours, minutes = divmod(minutes, 60)
                    
                    time_str = ""
                    if hours:
                        time_str += f"{hours}h "
                    if minutes:
                        time_str += f"{minutes}m "
                    if secs:
                        time_str += f"{secs}s"
                    
                    embed.add_field(
                        name=channel.mention,
                        value=f"{time_str.strip()}\n({slowmode}s)",
                        inline=True
                    )
                
                pages.append(embed)
            
            if len(pages) == 1:
                await ctx.send(embed=pages[0])
            else:
                paginator = WockPaginator(pages, ctx.author.id)
                await paginator.start(ctx)
        
        except Exception as e:
            await self.bot.warn(ctx, f"Failed to list slowmode channels: {str(e)}")

    @commands.group(name="nuke", invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def nuke(self, ctx, channel: discord.TextChannel = None):
        """Clone a channel and delete the old one, preserving all database settings.
        
        This command will:
        - Clone the channel with all settings and permissions
        - Restore all scheduled messages, feeds, and configurations
        - Delete the old channel
        - Preserve the channel ID mapping in the database
        
        Args:
            channel: The channel to nuke (defaults to current channel)
        """
        if ctx.invoked_subcommand is not None:
            return
        
        channel = channel or ctx.channel
        
        embed = discord.Embed(
            title="⚠️ Channel Nuke",
            description=f"This will clone {channel.mention} and delete the old one.\n\nAll database settings will be restored automatically.",
            color=0xff0000
        )
        view = ConfirmAction(ctx, None, "nuke")
        msg = await ctx.send(embed=embed, view=view)
        
        await view.wait()
        
        if not view.value:
            return await self.bot.deny(ctx, "Channel nuke cancelled.")
        
        async with ctx.typing():
            try:
                guild_config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
                
                new_channel = await channel.clone(name=channel.name, reason=f"Channel nuked by {ctx.author}")
                
                await new_channel.move(beginning=False, offset=channel.position - new_channel.position)
                
                if guild_config:
                    updated = False
                    
                    if guild_config.modlog_channel_id == channel.id:
                        guild_config.modlog_channel_id = new_channel.id
                        updated = True
                    
                    if guild_config.voicemaster_interface_channel_id == channel.id:
                        guild_config.voicemaster_interface_channel_id = new_channel.id
                        updated = True
                    
                    if guild_config.level_channel_id == channel.id:
                        guild_config.level_channel_id = new_channel.id
                        updated = True
                    
                    if guild_config.ticket_transcript_channel_id == channel.id:
                        guild_config.ticket_transcript_channel_id = new_channel.id
                        updated = True
                    
                    for twitch_user, feed_channel_id in list(guild_config.twitch_feeds.items()):
                        if feed_channel_id == channel.id:
                            guild_config.twitch_feeds[twitch_user] = new_channel.id
                            updated = True
                    
                    for tumblr_user, feed_channel_id in list(guild_config.tumblr_feeds.items()):
                        if feed_channel_id == channel.id:
                            guild_config.tumblr_feeds[tumblr_user] = new_channel.id
                            updated = True
                    
                    for soundcloud_user, feed_channel_id in list(guild_config.soundcloud_feeds.items()):
                        if feed_channel_id == channel.id:
                            guild_config.soundcloud_feeds[soundcloud_user] = new_channel.id
                            updated = True
                    
                    if updated:
                        await guild_config.save()
                
                scheduled_messages = await ScheduledMessage.find(ScheduledMessage.channel_id == channel.id).to_list(None)
                for scheduled_msg in scheduled_messages:
                    scheduled_msg.channel_id = new_channel.id
                    await scheduled_msg.save()
                
                await channel.delete(reason=f"Channel nuked by {ctx.author}")
                
                embed = discord.Embed(
                    title="✅ Channel Nuked",
                    description=f"This channel has been nuked and recreated!\n\n"
                                f"✓ All permissions copied\n"
                                f"✓ All settings restored\n"
                                f"✓ All feeds updated\n"
                                f"✓ All scheduled messages migrated",
                    color=0x242429
                )
                await new_channel.send(embed=embed)
                
            except discord.Forbidden:
                await self.bot.deny(ctx, "I don't have permission to clone or delete this channel.")
            except Exception as e:
                await self.bot.deny(ctx, f"Failed to nuke channel: {str(e)}")

    @nuke.command(name="add")
    @commands.has_permissions(administrator=True)
    async def nuke_add(self, ctx, channel: discord.TextChannel, interval: str, *, message: str = None):
        """Schedule a nuke for a channel
        
        Args:
            channel: The channel to schedule nuke for
            interval: How often to nuke (e.g., '1d', '6h', '30m')
            message: Optional message to send before nuking
        """
        import re
        
        try:
            match = re.match(r'^(\d+)([dhm])$', interval.lower())
            if not match:
                return await self.bot.warn(ctx, "Invalid interval format. Use: `1d`, `6h`, `30m`, etc.")
            
            amount, unit = int(match.group(1)), match.group(2)
            
            conversions = {'d': 86400, 'h': 3600, 'm': 60}
            interval_seconds = amount * conversions[unit]
            
            guild_config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
            if not guild_config:
                guild_config = GuildConfig(guild_id=ctx.guild.id)
            
            if not hasattr(guild_config, 'scheduled_nukes'):
                guild_config.scheduled_nukes = {}
            
            from datetime import datetime, timedelta
            next_nuke = (datetime.utcnow() + timedelta(seconds=interval_seconds)).isoformat()
            
            guild_config.scheduled_nukes[str(channel.id)] = {
                "interval": interval_seconds,
                "message": message,
                "next_nuke_time": next_nuke,
                "archive_pins": False
            }
            
            await guild_config.save()
            
            embed = discord.Embed(
                description=f"✅ Scheduled nuke for {channel.mention}",
                color=0x43b581
            )
            embed.add_field(name="Interval", value=interval, inline=True)
            embed.add_field(name="First Nuke", value=discord.utils.format_dt(discord.utils.utcnow() + timedelta(seconds=interval_seconds), style='R'), inline=True)
            if message:
                embed.add_field(name="Message", value=message, inline=False)
            await ctx.send(embed=embed)
            
        except Exception as e:
            await self.bot.warn(ctx, f"Failed to schedule nuke: {str(e)}")

    @nuke.command(name="remove")
    @commands.has_permissions(administrator=True)
    async def nuke_remove(self, ctx, channel: discord.TextChannel):
        """Remove scheduled nuke for a channel"""
        try:
            guild_config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
            
            if not guild_config or not guild_config.scheduled_nukes or str(channel.id) not in guild_config.scheduled_nukes:
                return await self.bot.warn(ctx, f"No scheduled nuke found for {channel.mention}")
            
            del guild_config.scheduled_nukes[str(channel.id)]
            await guild_config.save()
            
            embed = discord.Embed(
                description=f"✅ Removed scheduled nuke for {channel.mention}",
                color=0x43b581
            )
            await ctx.send(embed=embed)
            
        except Exception as e:
            await self.bot.warn(ctx, f"Failed to remove nuke: {str(e)}")

    @nuke.command(name="list")
    @commands.has_permissions(administrator=True)
    async def nuke_list(self, ctx):
        """View all scheduled nukes"""
        try:
            guild_config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
            
            if not guild_config or not guild_config.scheduled_nukes:
                return await self.bot.warn(ctx, "No scheduled nukes in this server")
            
            embed = discord.Embed(
                title="📌 Scheduled Nukes",
                color=0x5d92f2
            )
            
            for channel_id, nuke_data in guild_config.scheduled_nukes.items():
                channel = ctx.guild.get_channel(int(channel_id))
                channel_name = channel.mention if channel else f"#deleted-{channel_id}"
                
                from datetime import datetime
                next_nuke = datetime.fromisoformat(nuke_data["next_nuke_time"])
                time_until = discord.utils.format_dt(next_nuke, style='R')
                
                value = f"Interval: `{nuke_data['interval']}s`\nNext Nuke: {time_until}"
                if nuke_data.get("archive_pins"):
                    value += "\nArchive Pins: ✅"
                
                embed.add_field(name=channel_name, value=value, inline=False)
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            await self.bot.warn(ctx, f"Failed to list nukes: {str(e)}")

    @nuke.command(name="archive")
    @commands.has_permissions(administrator=True)
    async def nuke_archive(self, ctx, channel: discord.TextChannel, setting: str):
        """Toggle pin archiving for scheduled nuke
        
        Args:
            channel: The channel to configure
            setting: 'on' or 'off'
        """
        try:
            if setting.lower() not in ['on', 'off']:
                return await self.bot.warn(ctx, "Setting must be 'on' or 'off'")
            
            guild_config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
            
            if not guild_config or not guild_config.scheduled_nukes or str(channel.id) not in guild_config.scheduled_nukes:
                return await self.bot.warn(ctx, f"No scheduled nuke found for {channel.mention}")
            
            enabled = setting.lower() == 'on'
            guild_config.scheduled_nukes[str(channel.id)]["archive_pins"] = enabled
            await guild_config.save()
            
            status = "✅ enabled" if enabled else "❌ disabled"
            embed = discord.Embed(
                description=f"Pin archiving {status} for {channel.mention}",
                color=0x43b581 if enabled else 0xf25d5d
            )
            await ctx.send(embed=embed)
            
        except Exception as e:
            await self.bot.warn(ctx, f"Failed to configure archive: {str(e)}")

    @commands.command(name="lockdown", aliases=["lock"])
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def lockdown(self, ctx, channel: discord.TextChannel = None):
        """Lock down a channel, preventing members from sending messages"""
        channel = channel or ctx.channel
        
        try:
            overwrite = channel.overwrites_for(ctx.guild.default_role)
            overwrite.send_messages = False
            await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite, reason=f"Lockdown by {ctx.author}")
            await self.bot.grant(ctx, f"**{channel.mention}** has been locked down")
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to lockdown channel: {str(e)}")

    @commands.command(name="unlockdown", aliases=["unlock"])
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def unlockdown(self, ctx, channel: discord.TextChannel = None):
        """Unlock a channel, allowing members to send messages again"""
        channel = channel or ctx.channel
        
        try:
            overwrite = channel.overwrites_for(ctx.guild.default_role)
            overwrite.send_messages = None
            await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite, reason=f"Unlockdown by {ctx.author}")
            await self.bot.grant(ctx, f"**{channel.mention}** has been unlocked")
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to unlock channel: {str(e)}")

    @commands.command(name="hide")
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def hide(self, ctx, channel: discord.TextChannel = None):
        """Hide a channel from members"""
        channel = channel or ctx.channel
        
        try:
            overwrite = channel.overwrites_for(ctx.guild.default_role)
            overwrite.view_channel = False
            await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite, reason=f"Hidden by {ctx.author}")
            await self.bot.grant(ctx, f"**{channel.mention}** has been hidden")
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to hide channel: {str(e)}")

    @commands.command(name="unhide")
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def unhide(self, ctx, channel: discord.TextChannel = None):
        """Unhide a channel from members"""
        channel = channel or ctx.channel
        
        try:
            overwrite = channel.overwrites_for(ctx.guild.default_role)
            overwrite.view_channel = None
            await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite, reason=f"Unhidden by {ctx.author}")
            await self.bot.grant(ctx, f"**{channel.mention}** has been unhidden")
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to unhide channel: {str(e)}")


    @commands.group(name="webhook", invoke_without_command=True)
    @commands.has_permissions(manage_webhooks=True)
    async def webhook(self, ctx):
        """Manage webhooks in your guild"""
        await ctx.send_help(ctx.command)

    @webhook.command(name="create")
    @commands.has_permissions(manage_webhooks=True)
    async def webhook_create(self, ctx, channel: discord.TextChannel = None, *, name: str = None):
        """Create a webhook in the specified channel"""
        channel = channel or ctx.channel
        name = name or f"{ctx.author.name}'s Webhook"

        try:
            webhook = await channel.create_webhook(name=name, reason=f"Created by {ctx.author}")
            webhook_url = webhook.url
            embed = discord.Embed(
                title="✅ Webhook Created",
                description=f"Your webhook has been created in {channel.mention}",
                color=0x2ecc71
            )
            embed.add_field(name="Webhook Name", value=f"`{webhook.name}`", inline=False)
            embed.add_field(name="Webhook ID", value=f"`{webhook.id}`", inline=False)
            embed.add_field(name="Webhook URL", value=f"[Click to copy](https://discord.com)", inline=False)
            embed.set_footer(text="Keep your webhook URL safe and never share it!")
            await ctx.author.send(embed=embed, content=f"**Webhook URL:**\n{webhook_url}")
            await self.bot.grant(ctx, f"Webhook created in {channel.mention} and sent to your DMs")
        except discord.Forbidden:
            await self.bot.warn(ctx, "I don't have permission to create webhooks in that channel")
        except Exception as e:
            await self.bot.warn(ctx, f"Failed to create webhook: {str(e)}")

    @webhook.command(name="delete", aliases=["rm"])
    @commands.has_permissions(manage_webhooks=True)
    async def webhook_delete(self, ctx, *, identifier: str):
        """Delete an existing webhook by its identifier"""
        try:
            webhook_id = int(identifier) if identifier.isdigit() else None
            webhooks = await ctx.guild.webhooks()
            
            found_webhook = None
            if webhook_id:
                found_webhook = discord.utils.find(lambda w: w.id == webhook_id, webhooks)
            else:
                found_webhook = discord.utils.find(lambda w: w.name.lower() == identifier.lower(), webhooks)
            
            if not found_webhook:
                return await self.bot.warn(ctx, f"Webhook `{identifier}` not found")
            
            await found_webhook.delete(reason=f"Deleted by {ctx.author}")
            await self.bot.grant(ctx, f"Deleted webhook `{found_webhook.name}`")
        except ValueError:
            await self.bot.warn(ctx, "Invalid webhook ID")
        except Exception as e:
            await self.bot.warn(ctx, f"Failed to delete webhook: {str(e)}")

    @webhook.command(name="list")
    @commands.has_permissions(manage_webhooks=True)
    async def webhook_list(self, ctx):
        """View all webhooks in the guild"""
        try:
            webhooks = await ctx.guild.webhooks()
            
            if not webhooks:
                return await self.bot.neutral(ctx, "No webhooks in this guild")
            
            entries = []
            for webhook in webhooks:
                channel = ctx.guild.get_channel(webhook.channel_id)
                channel_name = channel.mention if channel else f"Unknown ({webhook.channel_id})"
                entries.append(f"**{webhook.name}** (ID: `{webhook.id}`)\nChannel: {channel_name}")
            
            pages = []
            for i in range(0, len(entries), 5):
                chunk = entries[i:i + 5]
                embed = discord.Embed(
                    title="Guild Webhooks",
                    description="\n\n".join(chunk),
                    color=0x242429
                )
                embed.set_footer(text=f"Page {i//5 + 1} of {(len(entries)-1)//5 + 1} | Total: {len(webhooks)}")
                pages.append(embed)
            
            if len(pages) == 1:
                await ctx.send(embed=pages[0])
            else:
                await self.bot.paginator(ctx, pages)
        except Exception as e:
            await self.bot.warn(ctx, f"Failed to list webhooks: {str(e)}")

    @webhook.command(name="edit", aliases=["modify"])
    @commands.has_permissions(manage_webhooks=True)
    async def webhook_edit(self, ctx, message: discord.Message, *, script: str):
        """Update a message sent through a webhook"""
        try:
            if not message.webhook_id:
                return await self.bot.warn(ctx, "That message was not sent by a webhook")
            
            webhooks = await ctx.guild.webhooks()
            webhook = discord.utils.find(lambda w: w.id == message.webhook_id, webhooks)
            
            if not webhook:
                return await self.bot.warn(ctx, "The webhook for this message no longer exists")
            
            content = script.replace("{content}", message.content)
            
            await webhook.edit_message(message.id, content=content or "No content")
            await self.bot.grant(ctx, "Message updated")
        except Exception as e:
            await self.bot.warn(ctx, f"Failed to edit message: {str(e)}")

    @webhook.command(name="forward", aliases=["fwd"])
    @commands.has_permissions(manage_webhooks=True)
    async def webhook_forward(self, ctx, identifier: str, *, script: str):
        """Forward a message through a webhook"""
        try:
            webhook_id = int(identifier) if identifier.isdigit() else None
            webhooks = await ctx.guild.webhooks()
            
            webhook = None
            if webhook_id:
                webhook = discord.utils.find(lambda w: w.id == webhook_id, webhooks)
            else:
                webhook = discord.utils.find(lambda w: w.name.lower() == identifier.lower(), webhooks)
            
            if not webhook:
                return await self.bot.warn(ctx, f"Webhook `{identifier}` not found")
            
            try:
                parser = EmbedParser(ctx)
                embed = parser.parse(script)
                await webhook.send(embed=embed, username=ctx.author.name, avatar_url=ctx.author.display_avatar.url)
            except:
                message = script.replace("{author}", ctx.author.name)
                message = message.replace("{content}", ctx.message.content if ctx.message.content else "No content")
                message = message.replace("{timestamp}", discord.utils.utcnow().strftime("%m/%d/%Y %H:%M:%S"))
                await webhook.send(message, username=ctx.author.name, avatar_url=ctx.author.display_avatar.url)
            
            await self.bot.grant(ctx, "Message forwarded through webhook")
        except ValueError:
            await self.bot.warn(ctx, "Invalid webhook ID")
        except Exception as e:
            await self.bot.warn(ctx, f"Failed to forward message: {str(e)}")

    @commands.group(name="pin", invoke_without_command=True)
    @commands.has_permissions(manage_messages=True)
    async def pin(self, ctx):
        """Manage pinned messages in the server"""
        if ctx.invoked_subcommand is None:
            embed = discord.Embed(
                title="📌 Pin Management",
                description="Manage pinned messages in your server",
                color=0x5d92f2
            )
            embed.add_field(
                name="Commands",
                value=(
                    "`pin add <message_id>` - Pin a message\n"
                    "`pin remove <message_id>` - Unpin a message\n"
                    "`pin view [channel]` - View all pinned messages in a channel\n"
                    "`pin clear [channel]` - Unpin all messages in a channel\n"
                    "`pin list` - List all pinned messages in the server"
                ),
                inline=False
            )
            embed.set_footer(text="Use 'pin <subcommand>' for more info")
            await ctx.send(embed=embed)

    @pin.command(name="add")
    @commands.has_permissions(manage_messages=True)
    async def pin_add(self, ctx, message_id: int):
        """Pin a message by ID"""
        try:
            message = None
            for channel in ctx.guild.text_channels:
                try:
                    message = await channel.fetch_message(message_id)
                    if message:
                        break
                except discord.NotFound:
                    continue
                except discord.Forbidden:
                    continue
            
            if not message:
                return await self.bot.warn(ctx, f"Message with ID `{message_id}` not found")
            
            if message.pinned:
                return await self.bot.warn(ctx, "This message is already pinned")
            
            pinned_messages = await message.channel.pins()
            if len(pinned_messages) >= 50:
                return await self.bot.warn(ctx, "This channel has reached the maximum pin limit (50)")
            
            await message.pin(reason=f"Pinned by {ctx.author} ({ctx.author.id})")
            embed = discord.Embed(
                description=f"✅ Message pinned in {message.channel.mention}",
                color=0x43b581
            )
            embed.add_field(name="Author", value=str(message.author), inline=False)
            embed.add_field(name="Content", value=message.content[:100] + "..." if len(message.content) > 100 else message.content or "*No content*", inline=False)
            await ctx.send(embed=embed)
        except discord.Forbidden:
            await self.bot.warn(ctx, "I don't have permission to pin messages in that channel")
        except Exception as e:
            await self.bot.warn(ctx, f"Failed to pin message: {str(e)}")

    @pin.command(name="remove", aliases=["unpin"])
    @commands.has_permissions(manage_messages=True)
    async def pin_remove(self, ctx, message_id: int):
        """Unpin a message by ID"""
        try:
            message = None
            for channel in ctx.guild.text_channels:
                try:
                    message = await channel.fetch_message(message_id)
                    if message:
                        break
                except discord.NotFound:
                    continue
                except discord.Forbidden:
                    continue
            
            if not message:
                return await self.bot.warn(ctx, f"Message with ID `{message_id}` not found")
            
            if not message.pinned:
                return await self.bot.warn(ctx, "This message is not pinned")
            
            await message.unpin(reason=f"Unpinned by {ctx.author} ({ctx.author.id})")
            embed = discord.Embed(
                description=f"✅ Message unpinned from {message.channel.mention}",
                color=0x43b581
            )
            await ctx.send(embed=embed)
        except discord.Forbidden:
            await self.bot.warn(ctx, "I don't have permission to unpin messages in that channel")
        except Exception as e:
            await self.bot.warn(ctx, f"Failed to unpin message: {str(e)}")

    @pin.command(name="view")
    @commands.has_permissions(manage_messages=True)
    async def pin_view(self, ctx, channel: discord.TextChannel = None):
        """View all pinned messages in a channel"""
        if channel is None:
            channel = ctx.channel
        
        try:
            pinned_messages = await channel.pins()
            
            if not pinned_messages:
                return await self.bot.warn(ctx, f"No pinned messages in {channel.mention}")
            
            pages = []
            for i in range(0, len(pinned_messages), 5):
                batch = pinned_messages[i:i+5]
                embed = discord.Embed(
                    title=f"📌 Pinned Messages in {channel.name}",
                    description=f"Showing {min(i+5, len(pinned_messages))} of {len(pinned_messages)} pinned messages",
                    color=0x5d92f2
                )
                
                for msg in batch:
                    timestamp = discord.utils.format_dt(msg.created_at, style='R')
                    author = msg.author.mention if msg.author else "*Unknown*"
                    content = msg.content[:50] + "..." if msg.content and len(msg.content) > 50 else msg.content or "*No text content*"
                    
                    embed.add_field(
                        name=f"Message ID: {msg.id}",
                        value=f"**Author:** {author}\n**Posted:** {timestamp}\n**Content:** {content}",
                        inline=False
                    )
                
                pages.append(embed)
            
            if len(pages) == 1:
                await ctx.send(embed=pages[0])
            else:
                paginator = WockPaginator(pages, ctx.author.id)
                await paginator.start(ctx)
        
        except discord.Forbidden:
            await self.bot.warn(ctx, "I don't have permission to view pins in that channel")
        except Exception as e:
            await self.bot.warn(ctx, f"Failed to view pinned messages: {str(e)}")

    @pin.command(name="clear")
    @commands.has_permissions(manage_messages=True)
    async def pin_clear(self, ctx, channel: discord.TextChannel = None):
        """Clear all pinned messages in a channel"""
        if channel is None:
            channel = ctx.channel
        
        try:
            pinned_messages = await channel.pins()
            
            if not pinned_messages:
                return await self.bot.warn(ctx, f"No pinned messages in {channel.mention}")
            
            embed = discord.Embed(
                title="⚠️ Confirm Unpin All",
                description=f"Are you sure you want to unpin all **{len(pinned_messages)}** messages in {channel.mention}?",
                color=0xffcc00
            )
            
            view = ConfirmAction(ctx, None, "unpin all pins")
            msg = await ctx.send(embed=embed, view=view)
            
            await view.wait()
            
            if view.value:
                unpinned_count = 0
                for message in pinned_messages:
                    try:
                        await message.unpin(reason=f"Bulk unpin by {ctx.author} ({ctx.author.id})")
                        unpinned_count += 1
                    except:
                        pass
                
                embed = discord.Embed(
                    description=f"✅ Unpinned **{unpinned_count}** messages from {channel.mention}",
                    color=0x43b581
                )
                await ctx.send(embed=embed)
            else:
                embed = discord.Embed(description="❌ Cancelled", color=0xf25d5d)
                await ctx.send(embed=embed)
        
        except discord.Forbidden:
            await self.bot.warn(ctx, "I don't have permission to unpin messages in that channel")
        except Exception as e:
            await self.bot.warn(ctx, f"Failed to clear pins: {str(e)}")

    @pin.command(name="list")
    @commands.has_permissions(manage_messages=True)
    async def pin_list(self, ctx):
        """List all pinned messages across the entire server"""
        try:
            all_pins = []
            
            for channel in ctx.guild.text_channels:
                try:
                    pinned = await channel.pins()
                    for msg in pinned:
                        all_pins.append((channel, msg))
                except discord.Forbidden:
                    continue
            
            if not all_pins:
                return await self.bot.warn(ctx, "No pinned messages found in this server")
            
            all_pins.sort(key=lambda x: x[1].created_at, reverse=True)
            
            pages = []
            for i in range(0, len(all_pins), 8):
                batch = all_pins[i:i+8]
                embed = discord.Embed(
                    title="📌 All Pinned Messages",
                    description=f"Showing {min(i+8, len(all_pins))} of {len(all_pins)} pinned messages",
                    color=0x5d92f2
                )
                
                for channel, msg in batch:
                    timestamp = discord.utils.format_dt(msg.created_at, style='R')
                    author = msg.author.mention if msg.author else "*Unknown*"
                    content = msg.content[:40] + "..." if msg.content and len(msg.content) > 40 else msg.content or "*No text*"
                    
                    embed.add_field(
                        name=f"{channel.mention} • {msg.id}",
                        value=f"{author} • {timestamp}\n*{content}*",
                        inline=False
                    )
                
                pages.append(embed)
            
            if len(pages) == 1:
                await ctx.send(embed=pages[0])
            else:
                paginator = WockPaginator(pages, ctx.author.id)
                await paginator.start(ctx)
        
        except Exception as e:
            await self.bot.warn(ctx, f"Failed to list pinned messages: {str(e)}")

async def setup(bot):
    await bot.add_cog(Moderation(bot))