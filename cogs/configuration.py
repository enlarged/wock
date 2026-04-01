import discord
from discord.ext import commands
from models.configs import GuildConfig, UserConfig
from models.starboard import StarboardConfig, StarboardPost
from utils.parser import EmbedParser
from utils.command_meta import command_meta
import re
import secrets
import aiohttp
from datetime import datetime

class AddWordModal(discord.ui.Modal, title="Add Filtered Word"):
    def __init__(self, cog):
        super().__init__()
        self.cog = cog
    
    word = discord.ui.TextInput(label="Word", placeholder="Enter word to filter", max_length=100)
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            word_to_add = self.word.value.strip()
            if not word_to_add:
                return await interaction.response.send_message(f"@{interaction.user}: Word cannot be empty!", ephemeral=True)
            
            res = await GuildConfig.find_one(GuildConfig.guild_id == interaction.guild.id)
            if not res:
                res = GuildConfig(guild_id=interaction.guild.id, filtered_words=[word_to_add])
            else:
                if word_to_add.lower() not in [w.lower() for w in res.filtered_words]:
                    res.filtered_words.append(word_to_add)
                else:
                    return await interaction.response.send_message(f"@{interaction.user}: **{word_to_add}** is already in the filter list", ephemeral=True)
            
            await res.save()
            await interaction.response.send_message(f"@{interaction.user}: Added **{word_to_add}** to the filter list", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"@{interaction.user}: Error: {str(e)}", ephemeral=True)


class RemoveWordModal(discord.ui.Modal, title="Remove Filtered Word"):
    def __init__(self, cog):
        super().__init__()
        self.cog = cog
    
    word = discord.ui.TextInput(label="Word", placeholder="Enter word to remove", max_length=100)
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            word_to_remove = self.word.value.strip()
            if not word_to_remove:
                return await interaction.response.send_message(f"@{interaction.user}: Word cannot be empty!", ephemeral=True)
            
            res = await GuildConfig.find_one(GuildConfig.guild_id == interaction.guild.id)
            if not res or word_to_remove.lower() not in [w.lower() for w in res.filtered_words]:
                return await interaction.response.send_message(f"@{interaction.user}: **{word_to_remove}** is not in the filter list", ephemeral=True)
            
            res.filtered_words = [w for w in res.filtered_words if w.lower() != word_to_remove.lower()]
            await res.save()
            await interaction.response.send_message(f"@{interaction.user}: Removed **{word_to_remove}** from the filter list", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"@{interaction.user}: Error: {str(e)}", ephemeral=True)


class FilterWordsView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog
    
    @discord.ui.button(emoji="➕", style=discord.ButtonStyle.green, label="Add Word")
    async def add_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddWordModal(self.cog))
    
    @discord.ui.button(emoji="➖", style=discord.ButtonStyle.red, label="Remove Word")
    async def remove_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RemoveWordModal(self.cog))

class TicketCreateView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot
    
    @discord.ui.button(emoji="🎫", style=discord.ButtonStyle.grey, label="Create Ticket")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        
        config = await GuildConfig.find_one(GuildConfig.guild_id == interaction.guild.id)
        if not config or not config.ticket_category_id:
            return await interaction.followup.send("❌ Ticket system not configured", ephemeral=True)
        
        category = interaction.guild.get_channel(config.ticket_category_id)
        if not category:
            return await interaction.followup.send("❌ Ticket category not found", ephemeral=True)
        
        for channel in category.text_channels:
            if f"ticket-{interaction.user.id}" in channel.name:
                embed = discord.Embed(color=0xED4245, title="❌ Ticket Limit Exceeded")
                embed.description = f"You already have an open ticket."
                embed.add_field(name="Your Ticket", value=channel.mention, inline=False)
                return await interaction.followup.send(embed=embed, ephemeral=True)
        
        config.ticket_counter += 1
        ticket_number = config.ticket_counter
        
        channel_name = f"ticket-{ticket_number}"
        ticket_channel = await category.create_text_channel(
            channel_name,
            topic=f"Ticket for {interaction.user.display_name} (ID: {interaction.user.id})"
        )
        
        await ticket_channel.set_permissions(interaction.guild.default_role, view_channel=False)
        await ticket_channel.set_permissions(interaction.user, view_channel=True, send_messages=True, read_message_history=True)
        
        await config.save()
        
        embed = discord.Embed(color=0x242429, title=f"🎫 Ticket #{ticket_number}")
        embed.description = f"Welcome {interaction.user.mention}! A staff member will be with you shortly.\n\n**Please describe your issue below.**"
        embed.add_field(name="Created", value=discord.utils.format_dt(discord.utils.utcnow(), style='f'), inline=True)
        embed.add_field(name="User", value=interaction.user.mention, inline=True)
        embed.set_footer(text="React or respond to this thread to notify staff")
        
        await ticket_channel.send(embed=embed)
        
        confirm_embed = discord.Embed(color=0x57F287, title="✅ Ticket Created")
        confirm_embed.description = f"Your support ticket has been successfully created."
        confirm_embed.add_field(name="Ticket", value=ticket_channel.mention, inline=False)
        confirm_embed.add_field(name="Ticket ID", value=f"#{ticket_number}", inline=False)
        confirm_embed.set_footer(text="A staff member will respond soon")
        
        await interaction.followup.send(embed=confirm_embed, ephemeral=True)

class Configuration(commands.Cog, name="Configuration"):
    def __init__(self, bot):
        self.bot = bot

    def _normalize_permission_name(self, permission: str):
        if not permission:
            return None
        normalized = permission.lower().replace(" ", "_")
        if normalized in discord.Permissions.VALID_FLAGS:
            return normalized
        return None

    def _invoke_actions(self):
        return {
            "ban": "Ban",
            "jail": "Jail",
            "jailchannel": "Jail Channel",
            "kick": "Kick",
            "mute": "Mute",
            "unban": "Unban",
            "unjail": "Unjail",
            "unmute": "Unmute",
            "warn": "Warn",
        }

    async def _get_or_create_guild_config(self, guild_id: int):
        config = await GuildConfig.find_one(GuildConfig.guild_id == guild_id)
        if not config:
            config = GuildConfig(guild_id=guild_id)
        if not getattr(config, "invoke_messages", None):
            config.invoke_messages = {"dm": {}, "message": {}}
        else:
            config.invoke_messages.setdefault("dm", {})
            config.invoke_messages.setdefault("message", {})
        return config

    def _render_invoke_template(self, ctx, template: str, action: str, mode: str):
        replacements = {
            "{user}": ctx.author.mention,
            "{user.name}": ctx.author.name,
            "{user.display_name}": ctx.author.display_name,
            "{user.id}": str(ctx.author.id),
            "{moderator}": ctx.author.mention,
            "{moderator.name}": ctx.author.name,
            "{moderator.display_name}": ctx.author.display_name,
            "{guild}": ctx.guild.name,
            "{guild.member_count}": str(ctx.guild.member_count),
            "{reason}": "No reason provided",
            "{action}": action,
            "{mode}": mode,
            "{channel}": ctx.channel.mention if hasattr(ctx.channel, "mention") else str(ctx.channel),
        }

        rendered = template
        for key, value in replacements.items():
            rendered = rendered.replace(key, value)
        return rendered

    async def _send_invoke_preview(self, ctx, mode: str, action: str, template: str):
        rendered = self._render_invoke_template(ctx, template, action, mode)
        parser_markers = ["{title:", "{description:", "{color:", "{field_name", "{image:", "{thumbnail:", "{author:", "{footer:"]
        target = ctx.author if mode == "dm" else ctx.channel

        if any(marker in rendered.lower() for marker in parser_markers):
            parser = EmbedParser(ctx)
            embed = parser.parse(rendered)
            await target.send(embed=embed)
            return

        await target.send(rendered)

    async def _invoke_action_dispatch(self, ctx, mode: str, action: str = None, operation: str = None, *, code: str = None):
        actions = self._invoke_actions()
        if not action:
            return await ctx.send_help(ctx.command)

        action = action.lower()
        if action not in actions:
            return await self.bot.warn(ctx, f"Invalid invoke action. Available: {', '.join(actions.keys())}")

        if not operation:
            return await ctx.send_help(ctx.command)

        operation = operation.lower()
        if operation not in {"add", "remove", "test", "view"}:
            return await self.bot.warn(ctx, "Operation must be one of: add, remove, test, or view")

        config = await self._get_or_create_guild_config(ctx.guild.id)
        stored = config.invoke_messages.setdefault(mode, {})
        current = stored.get(action)

        if operation == "add":
            if not code:
                return await ctx.send_help(ctx.command)
            stored[action] = code
            await config.save()
            return await self.bot.grant(ctx, f"{actions[action]} {mode} invoke message updated")

        if operation == "remove":
            if action not in stored:
                return await self.bot.warn(ctx, f"No {actions[action].lower()} {mode} invoke message is configured.")
            del stored[action]
            await config.save()
            return await self.bot.grant(ctx, f"Removed {actions[action].lower()} {mode} invoke message")

        if operation == "view":
            if not current:
                return await self.bot.neutral(ctx, f"No {actions[action].lower()} {mode} invoke message is configured.")
            embed = discord.Embed(
                color=0x242429,
                title=f"Invoke {mode.title()} {actions[action]}",
                description=current[:4000]
            )
            await ctx.send(embed=embed)
            return

        if operation == "test":
            if not current:
                return await self.bot.warn(ctx, f"No {actions[action].lower()} {mode} invoke message is configured.")
            try:
                await self._send_invoke_preview(ctx, mode, action, current)
            except Exception as e:
                return await self.bot.warn(ctx, f"Failed to test invoke message: {str(e)}")
            destination = "DMs" if mode == "dm" else "this channel"
            return await self.bot.grant(ctx, f"Sent test {actions[action].lower()} invoke message to {destination}")

    @commands.group(name="prefix", invoke_without_command=True)
    @command_meta(
        description="Get or set custom prefix for this server.",
        syntax=",prefix [set <prefix>]",
        example=",prefix set !",
        permissions="administrator",
    )
    async def prefix(self, ctx):
        """View or set the server's prefix"""
        res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        current = res.prefix if res else "!"
        await self.bot.neutral(ctx, f"The current prefix for **{ctx.guild.name}** is `{current}`")

    @prefix.command(name="set")
    @commands.has_permissions(administrator=True)
    async def prefix_set(self, ctx, *, new_prefix: str = None):
        """Set a custom prefix for this server"""
        if not new_prefix:
            return await ctx.send_help(ctx.command)

        if len(new_prefix) > 5:
            return await self.bot.warn(ctx, "Prefix cannot exceed **5** characters.")

        res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if res:
            res.prefix = new_prefix
            await res.save()
        else:
            await GuildConfig(guild_id=ctx.guild.id, prefix=new_prefix).insert()

        await self.bot.grant(ctx, f"Server prefix updated to `{new_prefix}`")

    @commands.group(name="selfprefix", aliases=["selfp"], invoke_without_command=True)
    @command_meta(
        description="View or set your personal bot prefix.",
        syntax="selfprefix [set <prefix>|reset]",
        example="selfprefix set ?",
        permissions="none",
    )
    async def selfprefix(self, ctx):
        """View or set your personal prefix"""
        res = await UserConfig.find_one(UserConfig.user_id == ctx.author.id)

        if not res or not res.prefix:
            return await self.bot.neutral(ctx, "You don't have a personal prefix set.")

        await self.bot.neutral(ctx, f"Your personal prefix is `{res.prefix}`")

    @selfprefix.command(name="set")
    async def selfprefix_set(self, ctx, *, new_prefix: str = None):
        """Set a personal prefix that works for you everywhere"""
        if not new_prefix:
            return await ctx.send_help(ctx.command)

        if len(new_prefix) > 5:
            return await self.bot.warn(ctx, "Personal prefix cannot exceed **5** characters.")

        res = await UserConfig.find_one(UserConfig.user_id == ctx.author.id)
        if res:
            res.prefix = new_prefix
            await res.save()
        else:
            await UserConfig(user_id=ctx.author.id, prefix=new_prefix).insert()

        await self.bot.grant(ctx, f"Your personal prefix is now `{new_prefix}`")

    @selfprefix.command(name="reset")
    async def selfprefix_reset(self, ctx):
        """Remove your personal prefix"""
        res = await UserConfig.find_one(UserConfig.user_id == ctx.author.id)
        if res and res.prefix:
            res.prefix = None
            await res.save()
            return await self.bot.grant(ctx, "Your personal prefix has been **reset**.")

        await self.bot.warn(ctx, "You do not have a personal prefix set.")

    @commands.group(name="alias", invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def alias(self, ctx):
        """Create your own shortcuts for commands"""
        await ctx.send_help(ctx.command)

    @alias.command(name="view")
    @commands.has_permissions(manage_guild=True)
    async def alias_view(self, ctx, shortcut: str = None):
        """View command execution for alias"""
        if not shortcut:
            return await ctx.send_help(ctx.command)

        res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not res or not res.aliases or shortcut not in res.aliases:
            return await self.bot.warn(ctx, f"Alias `{shortcut}` not found.")

        command = res.aliases[shortcut]
        embed = discord.Embed(
            color=0x242429,
            title="Alias Information",
            description=f"**Shortcut:** `{shortcut}`\n**Command:** `{command}`"
        )
        await ctx.send(embed=embed)

    @alias.command(name="list")
    @commands.has_permissions(manage_guild=True)
    async def alias_list(self, ctx):
        """List every alias for all commands"""
        res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        
        if not res or not res.aliases:
            return await self.bot.neutral(ctx, "No aliases configured for this server.")

        embed = discord.Embed(color=0x242429, title="Server Aliases")
        
        alias_text = []
        for shortcut, command in sorted(res.aliases.items()):
            alias_text.append(f"`{shortcut}` → `{command}`")
        
        if len(alias_text) <= 25:
            embed.description = "\n".join(alias_text)
        else:
            for i in range(0, len(alias_text), 25):
                chunk = alias_text[i:i+25]
                embed.add_field(name=f"Aliases ({i+1}-{min(i+25, len(alias_text))})", value="\n".join(chunk), inline=False)
        
        embed.set_footer(text=f"Total: {len(res.aliases)} aliases")
        await ctx.send(embed=embed)

    @alias.command(name="add")
    @commands.has_permissions(manage_guild=True)
    async def alias_add(self, ctx, shortcut: str = None, *, command: str = None):
        """Create an alias for command"""
        if not shortcut or not command:
            return await ctx.send_help(ctx.command)

        if " " in shortcut or len(shortcut) > 20:
            return await self.bot.warn(ctx, "Shortcut must be under 20 characters and contain no spaces.")

        if shortcut.lower() == command.lower():
            return await self.bot.warn(ctx, "Shortcut cannot be the same as the command.")

        res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not res:
            res = GuildConfig(guild_id=ctx.guild.id)

        if not res.aliases:
            res.aliases = {}

        if shortcut in res.aliases:
            old_command = res.aliases[shortcut]
            res.aliases[shortcut] = command
            await res.save()
            return await self.bot.grant(ctx, f"Updated alias `{shortcut}` from `{old_command}` to `{command}`")

        res.aliases[shortcut] = command
        await res.save()
        await self.bot.grant(ctx, f"Created alias `{shortcut}` for command `{command}`")

    @alias.command(name="remove")
    @commands.has_permissions(manage_guild=True)
    async def alias_remove(self, ctx, shortcut: str = None):
        """Remove an alias for command"""
        if not shortcut:
            return await ctx.send_help(ctx.command)

        res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not res or not res.aliases or shortcut not in res.aliases:
            return await self.bot.warn(ctx, f"Alias `{shortcut}` not found.")

        command = res.aliases.pop(shortcut)
        await res.save()
        await self.bot.grant(ctx, f"Removed alias `{shortcut}` (was set to `{command}`)")

    @alias.command(name="removeall")
    @commands.has_permissions(manage_guild=True)
    async def alias_removeall(self, ctx, *, command: str = None):
        """Remove all aliases for a command"""
        if not command:
            return await ctx.send_help(ctx.command)

        res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not res or not res.aliases:
            return await self.bot.warn(ctx, f"No aliases found for command `{command}`.")

        removed_aliases = []
        for shortcut, cmd in list(res.aliases.items()):
            if cmd.lower() == command.lower():
                removed_aliases.append(shortcut)
                res.aliases.pop(shortcut)

        if not removed_aliases:
            return await self.bot.warn(ctx, f"No aliases found for command `{command}`.")

        await res.save()
        aliases_str = ", ".join([f"`{a}`" for a in removed_aliases])
        await self.bot.grant(ctx, f"Removed {len(removed_aliases)} alias/aliases for `{command}`: {aliases_str}")

    @alias.command(name="reset")
    @commands.has_permissions(manage_guild=True)
    async def alias_reset(self, ctx):
        """Reset every alias for all commands"""
        res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        
        if not res or not res.aliases:
            return await self.bot.warn(ctx, "No aliases to reset.")

        count = len(res.aliases)
        res.aliases = {}
        await res.save()
        await self.bot.grant(ctx, f"Reset all **{count}** aliases.")

    @commands.group(name="invoke", invoke_without_command=True)
    async def invoke(self, ctx):
        """Change punishment messages for DM or command response"""
        await ctx.send_help(ctx.command)

    @invoke.group(name="dm", invoke_without_command=True)
    async def invoke_dm(self, ctx):
        """Change punishment messages for DM"""
        await ctx.send_help(ctx.command)

    @invoke.group(name="message", invoke_without_command=True)
    async def invoke_message(self, ctx):
        """Change punishment messages for command response"""
        await ctx.send_help(ctx.command)

    @invoke_dm.group(name="ban", invoke_without_command=True)
    async def invoke_dm_ban(self, ctx):
        """Change ban message for DM"""
        await ctx.send_help(ctx.command)

    @invoke_dm.group(name="jail", invoke_without_command=True)
    async def invoke_dm_jail(self, ctx):
        """Change jail message for DM"""
        await ctx.send_help(ctx.command)

    @invoke_dm.group(name="kick", invoke_without_command=True)
    async def invoke_dm_kick(self, ctx):
        """Change kick message for DM"""
        await ctx.send_help(ctx.command)

    @invoke_dm.group(name="mute", invoke_without_command=True)
    async def invoke_dm_mute(self, ctx):
        """Change mute message for DM"""
        await ctx.send_help(ctx.command)

    @invoke_dm.group(name="unban", invoke_without_command=True)
    async def invoke_dm_unban(self, ctx):
        """Change unban message for DM"""
        await ctx.send_help(ctx.command)

    @invoke_dm.group(name="unjail", invoke_without_command=True)
    async def invoke_dm_unjail(self, ctx):
        """Change unjail message for DM"""
        await ctx.send_help(ctx.command)

    @invoke_dm.group(name="unmute", invoke_without_command=True)
    async def invoke_dm_unmute(self, ctx):
        """Change unmute message for DM"""
        await ctx.send_help(ctx.command)

    @invoke_dm.group(name="warn", invoke_without_command=True)
    async def invoke_dm_warn(self, ctx):
        """Change warn message for DM"""
        await ctx.send_help(ctx.command)

    @invoke_message.group(name="ban", invoke_without_command=True)
    async def invoke_message_ban(self, ctx):
        """Change ban message for command response"""
        await ctx.send_help(ctx.command)

    @invoke_message.group(name="jail", invoke_without_command=True)
    async def invoke_message_jail(self, ctx):
        """Change jail message for command response"""
        await ctx.send_help(ctx.command)

    @invoke_message.group(name="jailchannel", invoke_without_command=True)
    async def invoke_message_jailchannel(self, ctx):
        """Change jail channel message for command response"""
        await ctx.send_help(ctx.command)

    @invoke_message.group(name="kick", invoke_without_command=True)
    async def invoke_message_kick(self, ctx):
        """Change kick message for command response"""
        await ctx.send_help(ctx.command)

    @invoke_message.group(name="mute", invoke_without_command=True)
    async def invoke_message_mute(self, ctx):
        """Change mute message for command response"""
        await ctx.send_help(ctx.command)

    @invoke_message.group(name="unban", invoke_without_command=True)
    async def invoke_message_unban(self, ctx):
        """Change unban message for command response"""
        await ctx.send_help(ctx.command)

    @invoke_message.group(name="unjail", invoke_without_command=True)
    async def invoke_message_unjail(self, ctx):
        """Change unjail message for command response"""
        await ctx.send_help(ctx.command)

    @invoke_message.group(name="unmute", invoke_without_command=True)
    async def invoke_message_unmute(self, ctx):
        """Change unmute message for command response"""
        await ctx.send_help(ctx.command)

    @invoke_message.group(name="warn", invoke_without_command=True)
    async def invoke_message_warn(self, ctx):
        """Change warn message for command response"""
        await ctx.send_help(ctx.command)

    @invoke_dm_ban.command(name="add")
    @commands.has_permissions(manage_guild=True)
    async def invoke_dm_ban_add(self, ctx, *, code: str = None):
        """Add ban message for DM"""
        await self._invoke_action_dispatch(ctx, "dm", "ban", "add", code=code)

    @invoke_dm_ban.command(name="remove")
    @commands.has_permissions(manage_guild=True)
    async def invoke_dm_ban_remove(self, ctx):
        """Remove ban message for DM"""
        await self._invoke_action_dispatch(ctx, "dm", "ban", "remove")

    @invoke_dm_ban.command(name="test")
    @commands.has_permissions(manage_guild=True)
    async def invoke_dm_ban_test(self, ctx):
        """Test ban message for DM"""
        await self._invoke_action_dispatch(ctx, "dm", "ban", "test")

    @invoke_dm_ban.command(name="view")
    @commands.has_permissions(manage_guild=True)
    async def invoke_dm_ban_view(self, ctx):
        """View ban message for DM"""
        await self._invoke_action_dispatch(ctx, "dm", "ban", "view")

    @invoke_dm_jail.command(name="add")
    @commands.has_permissions(manage_guild=True)
    async def invoke_dm_jail_add(self, ctx, *, code: str = None):
        """Add jail message for DM"""
        await self._invoke_action_dispatch(ctx, "dm", "jail", "add", code=code)

    @invoke_dm_jail.command(name="remove")
    @commands.has_permissions(manage_guild=True)
    async def invoke_dm_jail_remove(self, ctx):
        """Remove jail message for DM"""
        await self._invoke_action_dispatch(ctx, "dm", "jail", "remove")

    @invoke_dm_jail.command(name="test")
    @commands.has_permissions(manage_guild=True)
    async def invoke_dm_jail_test(self, ctx):
        """Test jail message for DM"""
        await self._invoke_action_dispatch(ctx, "dm", "jail", "test")

    @invoke_dm_jail.command(name="view")
    @commands.has_permissions(manage_guild=True)
    async def invoke_dm_jail_view(self, ctx):
        """View jail message for DM"""
        await self._invoke_action_dispatch(ctx, "dm", "jail", "view")

    @invoke_dm_kick.command(name="add")
    @commands.has_permissions(manage_guild=True)
    async def invoke_dm_kick_add(self, ctx, *, code: str = None):
        """Add kick message for DM"""
        await self._invoke_action_dispatch(ctx, "dm", "kick", "add", code=code)

    @invoke_dm_kick.command(name="remove")
    @commands.has_permissions(manage_guild=True)
    async def invoke_dm_kick_remove(self, ctx):
        """Remove kick message for DM"""
        await self._invoke_action_dispatch(ctx, "dm", "kick", "remove")

    @invoke_dm_kick.command(name="test")
    @commands.has_permissions(manage_guild=True)
    async def invoke_dm_kick_test(self, ctx):
        """Test kick message for DM"""
        await self._invoke_action_dispatch(ctx, "dm", "kick", "test")

    @invoke_dm_kick.command(name="view")
    @commands.has_permissions(manage_guild=True)
    async def invoke_dm_kick_view(self, ctx):
        """View kick message for DM"""
        await self._invoke_action_dispatch(ctx, "dm", "kick", "view")

    @invoke_dm_mute.command(name="add")
    @commands.has_permissions(manage_guild=True)
    async def invoke_dm_mute_add(self, ctx, *, code: str = None):
        """Add mute message for DM"""
        await self._invoke_action_dispatch(ctx, "dm", "mute", "add", code=code)

    @invoke_dm_mute.command(name="remove")
    @commands.has_permissions(manage_guild=True)
    async def invoke_dm_mute_remove(self, ctx):
        """Remove mute message for DM"""
        await self._invoke_action_dispatch(ctx, "dm", "mute", "remove")

    @invoke_dm_mute.command(name="test")
    @commands.has_permissions(manage_guild=True)
    async def invoke_dm_mute_test(self, ctx):
        """Test mute message for DM"""
        await self._invoke_action_dispatch(ctx, "dm", "mute", "test")

    @invoke_dm_mute.command(name="view")
    @commands.has_permissions(manage_guild=True)
    async def invoke_dm_mute_view(self, ctx):
        """View mute message for DM"""
        await self._invoke_action_dispatch(ctx, "dm", "mute", "view")

    @invoke_dm_unban.command(name="add")
    @commands.has_permissions(manage_guild=True)
    async def invoke_dm_unban_add(self, ctx, *, code: str = None):
        """Add unban message for DM"""
        await self._invoke_action_dispatch(ctx, "dm", "unban", "add", code=code)

    @invoke_dm_unban.command(name="remove")
    @commands.has_permissions(manage_guild=True)
    async def invoke_dm_unban_remove(self, ctx):
        """Remove unban message for DM"""
        await self._invoke_action_dispatch(ctx, "dm", "unban", "remove")

    @invoke_dm_unban.command(name="test")
    @commands.has_permissions(manage_guild=True)
    async def invoke_dm_unban_test(self, ctx):
        """Test unban message for DM"""
        await self._invoke_action_dispatch(ctx, "dm", "unban", "test")

    @invoke_dm_unban.command(name="view")
    @commands.has_permissions(manage_guild=True)
    async def invoke_dm_unban_view(self, ctx):
        """View unban message for DM"""
        await self._invoke_action_dispatch(ctx, "dm", "unban", "view")

    @invoke_dm_unjail.command(name="add")
    @commands.has_permissions(manage_guild=True)
    async def invoke_dm_unjail_add(self, ctx, *, code: str = None):
        """Add unjail message for DM"""
        await self._invoke_action_dispatch(ctx, "dm", "unjail", "add", code=code)

    @invoke_dm_unjail.command(name="remove")
    @commands.has_permissions(manage_guild=True)
    async def invoke_dm_unjail_remove(self, ctx):
        """Remove unjail message for DM"""
        await self._invoke_action_dispatch(ctx, "dm", "unjail", "remove")

    @invoke_dm_unjail.command(name="test")
    @commands.has_permissions(manage_guild=True)
    async def invoke_dm_unjail_test(self, ctx):
        """Test unjail message for DM"""
        await self._invoke_action_dispatch(ctx, "dm", "unjail", "test")

    @invoke_dm_unjail.command(name="view")
    @commands.has_permissions(manage_guild=True)
    async def invoke_dm_unjail_view(self, ctx):
        """View unjail message for DM"""
        await self._invoke_action_dispatch(ctx, "dm", "unjail", "view")

    @invoke_dm_unmute.command(name="add")
    @commands.has_permissions(manage_guild=True)
    async def invoke_dm_unmute_add(self, ctx, *, code: str = None):
        """Add unmute message for DM"""
        await self._invoke_action_dispatch(ctx, "dm", "unmute", "add", code=code)

    @invoke_dm_unmute.command(name="remove")
    @commands.has_permissions(manage_guild=True)
    async def invoke_dm_unmute_remove(self, ctx):
        """Remove unmute message for DM"""
        await self._invoke_action_dispatch(ctx, "dm", "unmute", "remove")

    @invoke_dm_unmute.command(name="test")
    @commands.has_permissions(manage_guild=True)
    async def invoke_dm_unmute_test(self, ctx):
        """Test unmute message for DM"""
        await self._invoke_action_dispatch(ctx, "dm", "unmute", "test")

    @invoke_dm_unmute.command(name="view")
    @commands.has_permissions(manage_guild=True)
    async def invoke_dm_unmute_view(self, ctx):
        """View unmute message for DM"""
        await self._invoke_action_dispatch(ctx, "dm", "unmute", "view")

    @invoke_dm_warn.command(name="add")
    @commands.has_permissions(manage_guild=True)
    async def invoke_dm_warn_add(self, ctx, *, code: str = None):
        """Add warn message for DM"""
        await self._invoke_action_dispatch(ctx, "dm", "warn", "add", code=code)

    @invoke_dm_warn.command(name="remove")
    @commands.has_permissions(manage_guild=True)
    async def invoke_dm_warn_remove(self, ctx):
        """Remove warn message for DM"""
        await self._invoke_action_dispatch(ctx, "dm", "warn", "remove")

    @invoke_dm_warn.command(name="test")
    @commands.has_permissions(manage_guild=True)
    async def invoke_dm_warn_test(self, ctx):
        """Test warn message for DM"""
        await self._invoke_action_dispatch(ctx, "dm", "warn", "test")

    @invoke_dm_warn.command(name="view")
    @commands.has_permissions(manage_guild=True)
    async def invoke_dm_warn_view(self, ctx):
        """View warn message for DM"""
        await self._invoke_action_dispatch(ctx, "dm", "warn", "view")

    @invoke_message_ban.command(name="add")
    @commands.has_permissions(manage_guild=True)
    async def invoke_message_ban_add(self, ctx, *, code: str = None):
        """Add ban message for command response"""
        await self._invoke_action_dispatch(ctx, "message", "ban", "add", code=code)

    @invoke_message_ban.command(name="remove")
    @commands.has_permissions(manage_guild=True)
    async def invoke_message_ban_remove(self, ctx):
        """Remove ban message for command response"""
        await self._invoke_action_dispatch(ctx, "message", "ban", "remove")

    @invoke_message_ban.command(name="test")
    @commands.has_permissions(manage_guild=True)
    async def invoke_message_ban_test(self, ctx):
        """Test ban message for command response"""
        await self._invoke_action_dispatch(ctx, "message", "ban", "test")

    @invoke_message_ban.command(name="view")
    @commands.has_permissions(manage_guild=True)
    async def invoke_message_ban_view(self, ctx):
        """View ban message for command response"""
        await self._invoke_action_dispatch(ctx, "message", "ban", "view")

    @invoke_message_jail.command(name="add")
    @commands.has_permissions(manage_guild=True)
    async def invoke_message_jail_add(self, ctx, *, code: str = None):
        """Add jail message for command response"""
        await self._invoke_action_dispatch(ctx, "message", "jail", "add", code=code)

    @invoke_message_jail.command(name="remove")
    @commands.has_permissions(manage_guild=True)
    async def invoke_message_jail_remove(self, ctx):
        """Remove jail message for command response"""
        await self._invoke_action_dispatch(ctx, "message", "jail", "remove")

    @invoke_message_jail.command(name="test")
    @commands.has_permissions(manage_guild=True)
    async def invoke_message_jail_test(self, ctx):
        """Test jail message for command response"""
        await self._invoke_action_dispatch(ctx, "message", "jail", "test")

    @invoke_message_jail.command(name="view")
    @commands.has_permissions(manage_guild=True)
    async def invoke_message_jail_view(self, ctx):
        """View jail message for command response"""
        await self._invoke_action_dispatch(ctx, "message", "jail", "view")

    @invoke_message_jailchannel.command(name="add")
    @commands.has_permissions(manage_guild=True)
    async def invoke_message_jailchannel_add(self, ctx, *, code: str = None):
        """Add jail channel message for command response"""
        await self._invoke_action_dispatch(ctx, "message", "jailchannel", "add", code=code)

    @invoke_message_jailchannel.command(name="remove")
    @commands.has_permissions(manage_guild=True)
    async def invoke_message_jailchannel_remove(self, ctx):
        """Remove jail channel message for command response"""
        await self._invoke_action_dispatch(ctx, "message", "jailchannel", "remove")

    @invoke_message_jailchannel.command(name="test")
    @commands.has_permissions(manage_guild=True)
    async def invoke_message_jailchannel_test(self, ctx):
        """Test jail channel message for command response"""
        await self._invoke_action_dispatch(ctx, "message", "jailchannel", "test")

    @invoke_message_jailchannel.command(name="view")
    @commands.has_permissions(manage_guild=True)
    async def invoke_message_jailchannel_view(self, ctx):
        """View jail channel message for command response"""
        await self._invoke_action_dispatch(ctx, "message", "jailchannel", "view")

    @invoke_message_kick.command(name="add")
    @commands.has_permissions(manage_guild=True)
    async def invoke_message_kick_add(self, ctx, *, code: str = None):
        """Add kick message for command response"""
        await self._invoke_action_dispatch(ctx, "message", "kick", "add", code=code)

    @invoke_message_kick.command(name="remove")
    @commands.has_permissions(manage_guild=True)
    async def invoke_message_kick_remove(self, ctx):
        """Remove kick message for command response"""
        await self._invoke_action_dispatch(ctx, "message", "kick", "remove")

    @invoke_message_kick.command(name="test")
    @commands.has_permissions(manage_guild=True)
    async def invoke_message_kick_test(self, ctx):
        """Test kick message for command response"""
        await self._invoke_action_dispatch(ctx, "message", "kick", "test")

    @invoke_message_kick.command(name="view")
    @commands.has_permissions(manage_guild=True)
    async def invoke_message_kick_view(self, ctx):
        """View kick message for command response"""
        await self._invoke_action_dispatch(ctx, "message", "kick", "view")

    @invoke_message_mute.command(name="add")
    @commands.has_permissions(manage_guild=True)
    async def invoke_message_mute_add(self, ctx, *, code: str = None):
        """Add mute message for command response"""
        await self._invoke_action_dispatch(ctx, "message", "mute", "add", code=code)

    @invoke_message_mute.command(name="remove")
    @commands.has_permissions(manage_guild=True)
    async def invoke_message_mute_remove(self, ctx):
        """Remove mute message for command response"""
        await self._invoke_action_dispatch(ctx, "message", "mute", "remove")

    @invoke_message_mute.command(name="test")
    @commands.has_permissions(manage_guild=True)
    async def invoke_message_mute_test(self, ctx):
        """Test mute message for command response"""
        await self._invoke_action_dispatch(ctx, "message", "mute", "test")

    @invoke_message_mute.command(name="view")
    @commands.has_permissions(manage_guild=True)
    async def invoke_message_mute_view(self, ctx):
        """View mute message for command response"""
        await self._invoke_action_dispatch(ctx, "message", "mute", "view")

    @invoke_message_unban.command(name="add")
    @commands.has_permissions(manage_guild=True)
    async def invoke_message_unban_add(self, ctx, *, code: str = None):
        """Add unban message for command response"""
        await self._invoke_action_dispatch(ctx, "message", "unban", "add", code=code)

    @invoke_message_unban.command(name="remove")
    @commands.has_permissions(manage_guild=True)
    async def invoke_message_unban_remove(self, ctx):
        """Remove unban message for command response"""
        await self._invoke_action_dispatch(ctx, "message", "unban", "remove")

    @invoke_message_unban.command(name="test")
    @commands.has_permissions(manage_guild=True)
    async def invoke_message_unban_test(self, ctx):
        """Test unban message for command response"""
        await self._invoke_action_dispatch(ctx, "message", "unban", "test")

    @invoke_message_unban.command(name="view")
    @commands.has_permissions(manage_guild=True)
    async def invoke_message_unban_view(self, ctx):
        """View unban message for command response"""
        await self._invoke_action_dispatch(ctx, "message", "unban", "view")

    @invoke_message_unjail.command(name="add")
    @commands.has_permissions(manage_guild=True)
    async def invoke_message_unjail_add(self, ctx, *, code: str = None):
        """Add unjail message for command response"""
        await self._invoke_action_dispatch(ctx, "message", "unjail", "add", code=code)

    @invoke_message_unjail.command(name="remove")
    @commands.has_permissions(manage_guild=True)
    async def invoke_message_unjail_remove(self, ctx):
        """Remove unjail message for command response"""
        await self._invoke_action_dispatch(ctx, "message", "unjail", "remove")

    @invoke_message_unjail.command(name="test")
    @commands.has_permissions(manage_guild=True)
    async def invoke_message_unjail_test(self, ctx):
        """Test unjail message for command response"""
        await self._invoke_action_dispatch(ctx, "message", "unjail", "test")

    @invoke_message_unjail.command(name="view")
    @commands.has_permissions(manage_guild=True)
    async def invoke_message_unjail_view(self, ctx):
        """View unjail message for command response"""
        await self._invoke_action_dispatch(ctx, "message", "unjail", "view")

    @invoke_message_unmute.command(name="add")
    @commands.has_permissions(manage_guild=True)
    async def invoke_message_unmute_add(self, ctx, *, code: str = None):
        """Add unmute message for command response"""
        await self._invoke_action_dispatch(ctx, "message", "unmute", "add", code=code)

    @invoke_message_unmute.command(name="remove")
    @commands.has_permissions(manage_guild=True)
    async def invoke_message_unmute_remove(self, ctx):
        """Remove unmute message for command response"""
        await self._invoke_action_dispatch(ctx, "message", "unmute", "remove")

    @invoke_message_unmute.command(name="test")
    @commands.has_permissions(manage_guild=True)
    async def invoke_message_unmute_test(self, ctx):
        """Test unmute message for command response"""
        await self._invoke_action_dispatch(ctx, "message", "unmute", "test")

    @invoke_message_unmute.command(name="view")
    @commands.has_permissions(manage_guild=True)
    async def invoke_message_unmute_view(self, ctx):
        """View unmute message for command response"""
        await self._invoke_action_dispatch(ctx, "message", "unmute", "view")

    @invoke_message_warn.command(name="add")
    @commands.has_permissions(manage_guild=True)
    async def invoke_message_warn_add(self, ctx, *, code: str = None):
        """Add warn message for command response"""
        await self._invoke_action_dispatch(ctx, "message", "warn", "add", code=code)

    @invoke_message_warn.command(name="remove")
    @commands.has_permissions(manage_guild=True)
    async def invoke_message_warn_remove(self, ctx):
        """Remove warn message for command response"""
        await self._invoke_action_dispatch(ctx, "message", "warn", "remove")

    @invoke_message_warn.command(name="test")
    @commands.has_permissions(manage_guild=True)
    async def invoke_message_warn_test(self, ctx):
        """Test warn message for command response"""
        await self._invoke_action_dispatch(ctx, "message", "warn", "test")

    @invoke_message_warn.command(name="view")
    @commands.has_permissions(manage_guild=True)
    async def invoke_message_warn_view(self, ctx):
        """View warn message for command response"""
        await self._invoke_action_dispatch(ctx, "message", "warn", "view")

    @commands.group(name="settings", invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def settings(self, ctx):
        """Manage server settings configuration"""
        await ctx.send_help(ctx.command)

    @settings.command(name="imute")
    @commands.has_permissions(manage_guild=True)
    async def settings_imute(self, ctx, role: discord.Role = None):
        """Set an image mute role"""
        if not role:
            return await ctx.send_help(ctx.command)

        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config:
            config = GuildConfig(guild_id=ctx.guild.id)

        config.imute_role_id = role.id
        await config.save()
        await self.bot.grant(ctx, f"Image mute role set to {role.mention}")

    @settings.command(name="rmute")
    @commands.has_permissions(manage_guild=True)
    async def settings_rmute(self, ctx, role: discord.Role = None):
        """Set a reaction mute role"""
        if not role:
            return await ctx.send_help(ctx.command)

        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config:
            config = GuildConfig(guild_id=ctx.guild.id)

        config.rmute_role_id = role.id
        await config.save()
        await self.bot.grant(ctx, f"Reaction mute role set to {role.mention}")

    @settings.command(name="mute")
    @commands.has_permissions(manage_guild=True)
    async def settings_mute(self, ctx, role: discord.Role = None):
        """Set a mute role"""
        if not role:
            return await ctx.send_help(ctx.command)

        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config:
            config = GuildConfig(guild_id=ctx.guild.id)

        config.mute_role_id = role.id
        await config.save()
        await self.bot.grant(ctx, f"Mute role set to {role.mention}")

    @commands.group(name="modlogs", invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def modlogs(self, ctx):
        """Configure moderation logs channel"""
        await ctx.send_help(ctx.command)

    @modlogs.command(name="enable")
    @commands.has_permissions(manage_guild=True)
    async def modlogs_enable(self, ctx, channel: discord.TextChannel = None):
        """Enable moderation logs in a channel"""
        if channel is None:
            return await ctx.send_help(ctx.command)

        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config:
            config = GuildConfig(guild_id=ctx.guild.id)

        config.modlog_channel_id = channel.id
        await config.save()
        await self.bot.grant(ctx, f"Modlogs enabled in {channel.mention}")

    @modlogs.command(name="disable")
    @commands.has_permissions(manage_guild=True)
    async def modlogs_disable(self, ctx):
        """Disable moderation logs"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config or not config.modlog_channel_id:
            return await self.bot.warn(ctx, "Modlogs are not enabled.")

        config.modlog_channel_id = None
        await config.save()
        await self.bot.grant(ctx, "Modlogs disabled")

    @commands.group(name="fakepermissions", aliases=["fakepermission", "fakeperms", "fakeperm", "fp"], invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def fakepermissions(self, ctx):
        """Manage fake permissions for roles"""
        await ctx.send_help(ctx.command)

    @fakepermissions.command(name="add")
    @commands.has_permissions(manage_guild=True)
    async def fakepermissions_add(self, ctx, role: discord.Role = None, permission: str = None):
        """Add a fake permission to a role"""
        if role is None or permission is None:
            return await ctx.send_help(ctx.command)

        perm = self._normalize_permission_name(permission)
        if not perm:
            sample = ", ".join(list(discord.Permissions.VALID_FLAGS.keys())[:8])
            return await self.bot.warn(ctx, f"Invalid permission. Example valid values: {sample}")

        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config:
            config = GuildConfig(guild_id=ctx.guild.id)

        if not getattr(config, "fake_permissions", None):
            config.fake_permissions = {}

        key = str(role.id)
        current = config.fake_permissions.get(key, [])
        if perm in current:
            return await self.bot.warn(ctx, f"{role.mention} already has fake `{perm}`")

        current.append(perm)
        current = sorted(set(current))
        config.fake_permissions[key] = current
        await config.save()
        await self.bot.grant(ctx, f"Added fake `{perm}` to {role.mention}")

    @fakepermissions.command(name="list")
    @commands.has_permissions(manage_guild=True)
    async def fakepermissions_list(self, ctx):
        """List all fake permissions in the server"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config or not getattr(config, "fake_permissions", None):
            return await self.bot.neutral(ctx, "No fake permissions are configured for this server.")

        lines = []
        for role_id, perms in config.fake_permissions.items():
            role = ctx.guild.get_role(int(role_id))
            if not role or not perms:
                continue
            pretty = ", ".join(f"`{p}`" for p in sorted(set(perms)))
            lines.append(f"{role.mention}: {pretty}")

        if not lines:
            return await self.bot.neutral(ctx, "No valid fake permissions are configured for this server.")

        embed = discord.Embed(color=0x242429, title="Fake Permissions", description="\n".join(lines))
        await ctx.send(embed=embed)

    @fakepermissions.command(name="remove")
    @commands.has_permissions(manage_guild=True)
    async def fakepermissions_remove(self, ctx, role: discord.Role = None, permission: str = None):
        """Remove a fake permission from a role"""
        if role is None or permission is None:
            return await ctx.send_help(ctx.command)

        perm = self._normalize_permission_name(permission)
        if not perm:
            return await self.bot.warn(ctx, "Invalid permission name.")

        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config or not getattr(config, "fake_permissions", None):
            return await self.bot.warn(ctx, "No fake permissions are configured.")

        key = str(role.id)
        current = config.fake_permissions.get(key, [])
        if perm not in current:
            return await self.bot.warn(ctx, f"{role.mention} does not have fake `{perm}`")

        current.remove(perm)
        if current:
            config.fake_permissions[key] = sorted(set(current))
        else:
            config.fake_permissions.pop(key, None)

        await config.save()
        await self.bot.grant(ctx, f"Removed fake `{perm}` from {role.mention}")

    @fakepermissions.command(name="reset")
    @commands.has_permissions(manage_guild=True)
    async def fakepermissions_reset(self, ctx):
        """Clear all fake permissions from the server"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config or not getattr(config, "fake_permissions", None):
            return await self.bot.warn(ctx, "No fake permissions are configured.")

        config.fake_permissions = {}
        await config.save()
        await self.bot.grant(ctx, "Cleared all fake permissions for this server")

    @settings.group(name="fakepermissions", aliases=["fakepermission", "fakeperms", "fakeperm", "fp"], invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def settings_fakepermissions(self, ctx):
        """Manage fake permissions for roles"""
        await ctx.send_help(ctx.command)

    @settings_fakepermissions.command(name="add")
    @commands.has_permissions(manage_guild=True)
    async def settings_fakepermissions_add(self, ctx, role: discord.Role = None, permission: str = None):
        """Add a fake permission to a role"""
        await self.fakepermissions_add(ctx, role, permission)

    @settings_fakepermissions.command(name="list")
    @commands.has_permissions(manage_guild=True)
    async def settings_fakepermissions_list(self, ctx):
        """List all fake permissions in the server"""
        await self.fakepermissions_list(ctx)

    @settings_fakepermissions.command(name="remove")
    @commands.has_permissions(manage_guild=True)
    async def settings_fakepermissions_remove(self, ctx, role: discord.Role = None, permission: str = None):
        """Remove a fake permission from a role"""
        await self.fakepermissions_remove(ctx, role, permission)

    @settings_fakepermissions.command(name="reset")
    @commands.has_permissions(manage_guild=True)
    async def settings_fakepermissions_reset(self, ctx):
        """Clear all fake permissions from the server"""
        await self.fakepermissions_reset(ctx)

    @settings.group(name="jail", invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def settings_jail(self, ctx):
        """View jail configuration"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config:
            config = GuildConfig(guild_id=ctx.guild.id)

        jail_category = ctx.guild.get_channel(config.jail_category_id) if getattr(config, "jail_category_id", None) else None
        jail_role = ctx.guild.get_role(config.jail_role_id) if config.jail_role_id else None
        jail_channel = ctx.guild.get_channel(config.jail_channel_id) if config.jail_channel_id else None
        await self.bot.neutral(
            ctx,
            f"Jail role: {jail_role.mention if jail_role else '`not set`'} | Jail category: {jail_category.mention if jail_category else '`not set`'} | Jail channel: {jail_channel.mention if jail_channel else '`not set`'}"
        )

    @settings_jail.command(name="setup")
    @commands.has_permissions(manage_guild=True)
    @commands.bot_has_permissions(manage_roles=True, manage_channels=True)
    async def settings_jail_setup(self, ctx):
        """Create and configure the jailed role, category, and jail channel"""

        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config:
            config = GuildConfig(guild_id=ctx.guild.id)

        jail_role = ctx.guild.get_role(config.jail_role_id) if config.jail_role_id else None
        if jail_role is None:
            jail_role = discord.utils.get(ctx.guild.roles, name="jailed")
        if jail_role is None:
            jail_role = await ctx.guild.create_role(name="jailed", reason=f"Jail setup by {ctx.author}")

        bot_member = ctx.guild.me
        overwrites = {
            ctx.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            jail_role: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                add_reactions=False,
                attach_files=False,
                embed_links=False,
            ),
            bot_member: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                manage_channels=True,
                manage_messages=True,
                read_message_history=True,
            ),
        }

        jail_category = ctx.guild.get_channel(getattr(config, "jail_category_id", None)) if getattr(config, "jail_category_id", None) else None
        if jail_category is None or not isinstance(jail_category, discord.CategoryChannel):
            existing_category = discord.utils.get(ctx.guild.categories, name="jail")
            if existing_category:
                jail_category = existing_category
                await jail_category.edit(overwrites=overwrites, reason=f"Jail setup by {ctx.author}")
            else:
                jail_category = await ctx.guild.create_category("jail", overwrites=overwrites, reason=f"Jail setup by {ctx.author}")
        else:
            await jail_category.edit(overwrites=overwrites, reason=f"Jail setup by {ctx.author}")

        jail_channel = ctx.guild.get_channel(config.jail_channel_id) if config.jail_channel_id else None
        if jail_channel is None or not isinstance(jail_channel, discord.TextChannel):
            existing_channel = discord.utils.get(ctx.guild.text_channels, name="jail")
            if existing_channel:
                jail_channel = existing_channel
                await jail_channel.edit(category=jail_category, sync_permissions=True, reason=f"Jail setup by {ctx.author}")
            else:
                jail_channel = await ctx.guild.create_text_channel("jail", category=jail_category, overwrites=overwrites, reason=f"Jail setup by {ctx.author}")
        else:
            await jail_channel.edit(category=jail_category, sync_permissions=True, reason=f"Jail setup by {ctx.author}")

        config.jail_role_id = jail_role.id
        config.jail_category_id = jail_category.id
        config.jail_channel_id = jail_channel.id

        await config.save()
        await self.bot.grant(
            ctx,
            f"Jail configured — role: {jail_role.mention}, category: {jail_category.mention}, channel: {jail_channel.mention}"
        )

    @settings_jail.command(name="remove")
    @commands.has_permissions(manage_guild=True)
    @commands.bot_has_permissions(manage_roles=True, manage_channels=True)
    async def settings_jail_remove(self, ctx):
        """Remove jail role, category, and channel configuration"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config or (not config.jail_role_id and not config.jail_channel_id and not getattr(config, "jail_category_id", None)):
            return await self.bot.warn(ctx, "Jail is not configured for this server.")

        jail_channel = ctx.guild.get_channel(config.jail_channel_id) if config.jail_channel_id else None
        jail_category = ctx.guild.get_channel(getattr(config, "jail_category_id", None)) if getattr(config, "jail_category_id", None) else None
        jail_role = ctx.guild.get_role(config.jail_role_id) if config.jail_role_id else None

        if jail_channel:
            try:
                await jail_channel.delete(reason=f"Jail removed by {ctx.author}")
            except Exception:
                pass

        if jail_category and isinstance(jail_category, discord.CategoryChannel):
            try:
                await jail_category.delete(reason=f"Jail removed by {ctx.author}")
            except Exception:
                pass

        if jail_role:
            try:
                await jail_role.delete(reason=f"Jail removed by {ctx.author}")
            except Exception:
                pass

        config.jail_role_id = None
        config.jail_category_id = None
        config.jail_channel_id = None
        await config.save()
        await self.bot.grant(ctx, "Removed jail role, category, and channel configuration")

    @settings.command(name="autonick")
    @commands.has_permissions(manage_guild=True)
    async def settings_autonick(self, ctx, *, nickname: str = None):
        """Set a nickname assigned to members when they join"""
        if nickname is None:
            return await ctx.send_help(ctx.command)

        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config:
            config = GuildConfig(guild_id=ctx.guild.id)

        lowered = nickname.lower().strip()
        if lowered in ["off", "disable", "none", "reset", "remove"]:
            config.autonick = None
            await config.save()
            return await self.bot.grant(ctx, "Auto-nickname has been disabled")

        if len(nickname) > 32:
            return await self.bot.warn(ctx, "Nickname cannot be more than 32 characters")

        config.autonick = nickname
        await config.save()
        await self.bot.grant(ctx, f"Auto-nickname set to `{nickname}`")

    @settings.command(name="config")
    @commands.has_permissions(manage_guild=True)
    async def settings_config(self, ctx):
        """View settings configuration for guild"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config:
            return await self.bot.neutral(ctx, "No settings configured for this server yet.")

        imute = ctx.guild.get_role(config.imute_role_id) if config.imute_role_id else None
        rmute = ctx.guild.get_role(config.rmute_role_id) if config.rmute_role_id else None
        mute = ctx.guild.get_role(config.mute_role_id) if config.mute_role_id else None
        jail_category = ctx.guild.get_channel(getattr(config, "jail_category_id", None)) if getattr(config, "jail_category_id", None) else None
        jail_role = ctx.guild.get_role(config.jail_role_id) if config.jail_role_id else None
        jail_channel = ctx.guild.get_channel(config.jail_channel_id) if config.jail_channel_id else None
        staff_roles = [ctx.guild.get_role(rid) for rid in (config.staff_roles or [])]
        staff_roles = [r.mention for r in staff_roles if r]

        embed = discord.Embed(color=0x242429, title=f"Settings Configuration — {ctx.guild.name}")
        embed.add_field(name="Image Mute Role", value=imute.mention if imute else "Not set", inline=False)
        embed.add_field(name="Reaction Mute Role", value=rmute.mention if rmute else "Not set", inline=False)
        embed.add_field(name="Mute Role", value=mute.mention if mute else "Not set", inline=False)
        embed.add_field(name="Jail Role", value=jail_role.mention if jail_role else "Not set", inline=False)
        embed.add_field(name="Jail Category", value=jail_category.mention if jail_category else "Not set", inline=False)
        embed.add_field(name="Jail Channel", value=jail_channel.mention if jail_channel else "Not set", inline=False)
        embed.add_field(name="Auto Nickname", value=config.autonick if config.autonick else "Disabled", inline=False)
        embed.add_field(name="Jail Roles Removal", value="Enabled" if config.jail_remove_roles else "Disabled", inline=False)
        embed.add_field(name="Staff Roles", value=", ".join(staff_roles) if staff_roles else "None", inline=False)
        await ctx.send(embed=embed)

    @settings.command(name="jailroles")
    @commands.has_permissions(manage_guild=True)
    async def settings_jailroles(self, ctx, state: str = None):
        """Enable or disable removal of roles for jail"""
        if not state:
            return await ctx.send_help(ctx.command)

        state = state.lower()
        if state not in ["on", "off", "enable", "disable", "true", "false"]:
            return await self.bot.warn(ctx, "State must be one of: on, off, enable, disable, true, false")

        enabled = state in ["on", "enable", "true"]
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config:
            config = GuildConfig(guild_id=ctx.guild.id)

        config.jail_remove_roles = enabled
        await config.save()
        await self.bot.grant(ctx, f"Jail role removal has been **{'enabled' if enabled else 'disabled'}**")

    @settings.group(name="staff", invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def settings_staff(self, ctx, *roles: discord.Role):
        """Set staff role(s)"""
        if not roles:
            return await ctx.send_help(ctx.command)

        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config:
            config = GuildConfig(guild_id=ctx.guild.id)

        unique_ids = []
        for role in roles:
            if role.id not in unique_ids:
                unique_ids.append(role.id)

        config.staff_roles = unique_ids
        await config.save()
        mentions = [ctx.guild.get_role(rid).mention for rid in unique_ids if ctx.guild.get_role(rid)]
        await self.bot.grant(ctx, f"Staff roles set: {', '.join(mentions) if mentions else 'None'}")

    @settings_staff.command(name="list")
    @commands.has_permissions(manage_guild=True)
    async def settings_staff_list(self, ctx):
        """View a list of all staff roles"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config or not config.staff_roles:
            return await self.bot.neutral(ctx, "No staff roles set for this server.")

        roles = [ctx.guild.get_role(rid) for rid in config.staff_roles]
        roles = [role.mention for role in roles if role]
        if not roles:
            return await self.bot.neutral(ctx, "No valid staff roles set for this server.")

        embed = discord.Embed(color=0x242429, title="Staff Roles", description="\n".join(roles))
        await ctx.send(embed=embed)

    @commands.group(name="stickymessage", invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def stickymessage(self, ctx):
        """Set up a sticky message in one or multiple channels"""
        await ctx.send_help(ctx.command)

    @stickymessage.command(name="add")
    @commands.has_permissions(manage_guild=True)
    async def stickymessage_add(self, ctx, channel: discord.TextChannel = None, *, message: str = None):
        """Add a sticky message to a channel"""
        if not channel or not message:
            return await ctx.send_help(ctx.command)

        res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not res:
            res = GuildConfig(guild_id=ctx.guild.id)

        if not res.sticky_messages:
            res.sticky_messages = {}

        try:
            sent_message = await channel.send(message)
            res.sticky_messages[str(channel.id)] = {
                "message_id": sent_message.id,
                "content": message
            }
            await res.save()
            await self.bot.grant(ctx, f"Sticky message added to {channel.mention}")
        except Exception as e:
            await self.bot.warn(ctx, f"Failed to send sticky message: {str(e)}")

    @stickymessage.command(name="remove")
    @commands.has_permissions(manage_guild=True)
    async def stickymessage_remove(self, ctx, channel: discord.TextChannel = None):
        """Remove a sticky message from a channel"""
        if not channel:
            return await ctx.send_help(ctx.command)

        res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not res or not res.sticky_messages or str(channel.id) not in res.sticky_messages:
            return await self.bot.warn(ctx, f"No sticky message found in {channel.mention}")

        try:
            message_id = res.sticky_messages[str(channel.id)]["message_id"]
            message = await channel.fetch_message(message_id)
            await message.delete()
        except:
            pass

        res.sticky_messages.pop(str(channel.id))
        await res.save()
        await self.bot.grant(ctx, f"Sticky message removed from {channel.mention}")

    @stickymessage.command(name="view")
    @commands.has_permissions(manage_guild=True)
    async def stickymessage_view(self, ctx, channel: discord.TextChannel = None):
        """View the sticky message for a channel"""
        if not channel:
            return await ctx.send_help(ctx.command)

        res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not res or not res.sticky_messages or str(channel.id) not in res.sticky_messages:
            return await self.bot.warn(ctx, f"No sticky message found in {channel.mention}")

        sticky = res.sticky_messages[str(channel.id)]
        embed = discord.Embed(
            color=0x242429,
            title="Sticky Message",
            description=sticky["content"]
        )
        embed.add_field(name="Channel", value=channel.mention, inline=False)
        embed.add_field(name="Message ID", value=sticky["message_id"], inline=False)
        await ctx.send(embed=embed)

    @stickymessage.command(name="list")
    @commands.has_permissions(manage_guild=True)
    async def stickymessage_list(self, ctx):
        """View all sticky messages"""
        res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        
        if not res or not res.sticky_messages:
            return await self.bot.neutral(ctx, "No sticky messages configured for this server.")

        embed = discord.Embed(color=0x242429, title="Sticky Messages")
        
        sticky_text = []
        for channel_id, data in res.sticky_messages.items():
            channel = ctx.guild.get_channel(int(channel_id))
            channel_name = channel.mention if channel else f"Unknown ({channel_id})"
            content = data["content"][:50] + "..." if len(data["content"]) > 50 else data["content"]
            sticky_text.append(f"{channel_name}: {content}")

        if len(sticky_text) <= 20:
            embed.description = "\n".join(sticky_text)
        else:
            for i in range(0, len(sticky_text), 20):
                chunk = sticky_text[i:i+20]
                embed.add_field(name=f"Sticky Messages ({i+1}-{min(i+20, len(sticky_text))})", value="\n".join(chunk), inline=False)

        embed.set_footer(text=f"Total: {len(res.sticky_messages)} sticky messages")
        await ctx.send(embed=embed)

    @commands.group(name="autoresponder", invoke_without_command=True)
    @commands.has_permissions(manage_channels=True)
    async def autoresponder(self, ctx):
        """Set up automatic replies to messages matching a trigger"""
        await ctx.send_help(ctx.command)

    @autoresponder.command(name="variables")
    @commands.has_permissions(manage_channels=True)
    async def autoresponder_variables(self, ctx):
        """View a list of available variables"""
        embed = discord.Embed(color=0x242429, title="Autoresponder Variables")
        embed.description = "Use these variables in your autoresponder replies:\n\n"
        
        variables_list = [
            ("{user}", "Mentions the user"),
            ("{user.name}", "User's username"),
            ("{user.display_name}", "User's display name"),
            ("{user.id}", "User's ID"),
            ("{guild}", "Server name"),
            ("{guild.member_count}", "Total server members"),
            ("{channel}", "Channel name"),
            ("{channel.mention}", "Mention the channel"),
        ]
        
        for var, desc in variables_list:
            embed.add_field(name=f"`{var}`", value=desc, inline=False)
        
        await ctx.send(embed=embed)

    @autoresponder.command(name="add")
    @commands.has_permissions(manage_channels=True)
    async def autoresponder_add(self, ctx, trigger: str = None, *, response: str = None):
        """Create a reply for a trigger word"""
        if not trigger or not response:
            return await ctx.send_help(ctx.command)

        res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not res:
            res = GuildConfig(guild_id=ctx.guild.id)

        if not res.autoresponders:
            res.autoresponders = {}

        if trigger in res.autoresponders:
            res.autoresponders[trigger]["response"] = response
            await res.save()
            return await self.bot.grant(ctx, f"Updated autoresponder for trigger `{trigger}`")

        res.autoresponders[trigger] = {
            "response": response,
            "add_roles": [],
            "remove_roles": [],
            "exclusive_channels": [],
            "exclusive_roles": []
        }
        await res.save()
        await self.bot.grant(ctx, f"Created autoresponder for trigger `{trigger}`")

    @autoresponder.command(name="remove")
    @commands.has_permissions(manage_channels=True)
    async def autoresponder_remove(self, ctx, trigger: str = None):
        """Remove a reply for a trigger word"""
        if not trigger:
            return await ctx.send_help(ctx.command)

        res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not res or not res.autoresponders or trigger not in res.autoresponders:
            return await self.bot.warn(ctx, f"Autoresponder for trigger `{trigger}` not found.")

        res.autoresponders.pop(trigger)
        await res.save()
        await self.bot.grant(ctx, f"Removed autoresponder for trigger `{trigger}`")

    @autoresponder.command(name="update")
    @commands.has_permissions(manage_channels=True)
    async def autoresponder_update(self, ctx, trigger: str = None, *, response: str = None):
        """Update a reply for a trigger word"""
        if not trigger or not response:
            return await ctx.send_help(ctx.command)

        res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not res or not res.autoresponders or trigger not in res.autoresponders:
            return await self.bot.warn(ctx, f"Autoresponder for trigger `{trigger}` not found.")

        res.autoresponders[trigger]["response"] = response
        await res.save()
        await self.bot.grant(ctx, f"Updated autoresponder for trigger `{trigger}`")

    @autoresponder.command(name="list")
    @commands.has_permissions(manage_channels=True)
    async def autoresponder_list(self, ctx):
        """View a list of auto-reply triggers in guild"""
        res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        
        if not res or not res.autoresponders:
            return await self.bot.neutral(ctx, "No autoresponders configured for this server.")

        embed = discord.Embed(color=0x242429, title="Autoresponders")
        
        trigger_text = []
        for trigger, data in sorted(res.autoresponders.items()):
            response = data["response"][:40] + "..." if len(data["response"]) > 40 else data["response"]
            trigger_text.append(f"`{trigger}` → {response}")

        if len(trigger_text) <= 20:
            embed.description = "\n".join(trigger_text)
        else:
            for i in range(0, len(trigger_text), 20):
                chunk = trigger_text[i:i+20]
                embed.add_field(name=f"Triggers ({i+1}-{min(i+20, len(trigger_text))})", value="\n".join(chunk), inline=False)

        embed.set_footer(text=f"Total: {len(res.autoresponders)} autoresponders")
        await ctx.send(embed=embed)

    @autoresponder.command(name="reset")
    @commands.has_permissions(manage_channels=True)
    async def autoresponder_reset(self, ctx):
        """Remove every auto response"""
        res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        
        if not res or not res.autoresponders:
            return await self.bot.warn(ctx, "No autoresponders to reset.")

        count = len(res.autoresponders)
        res.autoresponders = {}
        await res.save()
        await self.bot.grant(ctx, f"Reset all **{count}** autoresponders.")

    @commands.group(name="autoresponder-role", invoke_without_command=True)
    @commands.has_permissions(manage_channels=True)
    async def autoresponder_role(self, ctx):
        """Assign or remove roles on messages matching a trigger"""
        await ctx.send_help(ctx.command)

    @autoresponder_role.command(name="add")
    @commands.has_permissions(manage_channels=True)
    async def autoresponder_role_add(self, ctx, role: discord.Role = None, trigger: str = None):
        """Add a role to be given when an autoresponder is triggered"""
        if not role or not trigger:
            return await ctx.send_help(ctx.command)

        res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not res or not res.autoresponders or trigger not in res.autoresponders:
            return await self.bot.warn(ctx, f"Autoresponder for trigger `{trigger}` not found.")

        if "add_roles" not in res.autoresponders[trigger]:
            res.autoresponders[trigger]["add_roles"] = []

        if role.id not in res.autoresponders[trigger]["add_roles"]:
            res.autoresponders[trigger]["add_roles"].append(role.id)
            await res.save()
            return await self.bot.grant(ctx, f"Added role `{role.name}` to be given for trigger `{trigger}`")

        await self.bot.warn(ctx, f"Role `{role.name}` is already set to be given for this trigger.")

    @autoresponder_role.command(name="add-list")
    @commands.has_permissions(manage_channels=True)
    async def autoresponder_role_add_list(self, ctx, trigger: str = None):
        """View roles assigned upon messages matching a trigger"""
        if not trigger:
            return await ctx.send_help(ctx.command)

        res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not res or not res.autoresponders or trigger not in res.autoresponders:
            return await self.bot.warn(ctx, f"Autoresponder for trigger `{trigger}` not found.")

        add_roles = res.autoresponders[trigger].get("add_roles", [])
        if not add_roles:
            return await self.bot.neutral(ctx, f"No roles assigned to be given for trigger `{trigger}`.")

        embed = discord.Embed(color=0x242429, title=f"Roles to Add - Trigger: {trigger}")
        
        roles_text = []
        for role_id in add_roles:
            role = ctx.guild.get_role(role_id)
            roles_text.append(f"• {role.mention if role else f'Unknown ({role_id})'}")

        embed.description = "\n".join(roles_text)
        await ctx.send(embed=embed)

    @autoresponder_role.command(name="remove")
    @commands.has_permissions(manage_channels=True)
    async def autoresponder_role_remove(self, ctx, role: discord.Role = None, trigger: str = None):
        """Add a role to be removed when an autoresponder is triggered"""
        if not role or not trigger:
            return await ctx.send_help(ctx.command)

        res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not res or not res.autoresponders or trigger not in res.autoresponders:
            return await self.bot.warn(ctx, f"Autoresponder for trigger `{trigger}` not found.")

        if "remove_roles" not in res.autoresponders[trigger]:
            res.autoresponders[trigger]["remove_roles"] = []

        if role.id not in res.autoresponders[trigger]["remove_roles"]:
            res.autoresponders[trigger]["remove_roles"].append(role.id)
            await res.save()
            return await self.bot.grant(ctx, f"Added role `{role.name}` to be removed for trigger `{trigger}`")

        await self.bot.warn(ctx, f"Role `{role.name}` is already set to be removed for this trigger.")

    @autoresponder_role.command(name="remove-list")
    @commands.has_permissions(manage_channels=True)
    async def autoresponder_role_remove_list(self, ctx, trigger: str = None):
        """View roles removed upon messages matching a trigger"""
        if not trigger:
            return await ctx.send_help(ctx.command)

        res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not res or not res.autoresponders or trigger not in res.autoresponders:
            return await self.bot.warn(ctx, f"Autoresponder for trigger `{trigger}` not found.")

        remove_roles = res.autoresponders[trigger].get("remove_roles", [])
        if not remove_roles:
            return await self.bot.neutral(ctx, f"No roles assigned to be removed for trigger `{trigger}`.")

        embed = discord.Embed(color=0x242429, title=f"Roles to Remove - Trigger: {trigger}")
        
        roles_text = []
        for role_id in remove_roles:
            role = ctx.guild.get_role(role_id)
            roles_text.append(f"• {role.mention if role else f'Unknown ({role_id})'}")

        embed.description = "\n".join(roles_text)
        await ctx.send(embed=embed)

    @commands.group(name="autoresponder-exclusive", invoke_without_command=True)
    @commands.has_permissions(manage_channels=True)
    async def autoresponder_exclusive(self, ctx, role_or_channel: str = None, trigger: str = None):
        """Toggle exclusive access for an autoresponder to a role or channel"""
        if not role_or_channel or not trigger:
            return await ctx.send_help(ctx.command)

        res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not res or not res.autoresponders or trigger not in res.autoresponders:
            return await self.bot.warn(ctx, f"Autoresponder for trigger `{trigger}` not found.")

        try:
            role = await commands.RoleConverter().convert(ctx, role_or_channel)
            if "exclusive_roles" not in res.autoresponders[trigger]:
                res.autoresponders[trigger]["exclusive_roles"] = []
            
            if role.id in res.autoresponders[trigger]["exclusive_roles"]:
                res.autoresponders[trigger]["exclusive_roles"].remove(role.id)
                await res.save()
                return await self.bot.grant(ctx, f"Removed exclusive access for role `{role.name}` on trigger `{trigger}`")
            else:
                res.autoresponders[trigger]["exclusive_roles"].append(role.id)
                await res.save()
                return await self.bot.grant(ctx, f"Added exclusive access for role `{role.name}` on trigger `{trigger}`")
        except:
            pass

        try:
            channel = await commands.TextChannelConverter().convert(ctx, role_or_channel)
            if "exclusive_channels" not in res.autoresponders[trigger]:
                res.autoresponders[trigger]["exclusive_channels"] = []
            
            if channel.id in res.autoresponders[trigger]["exclusive_channels"]:
                res.autoresponders[trigger]["exclusive_channels"].remove(channel.id)
                await res.save()
                return await self.bot.grant(ctx, f"Removed exclusive access for channel {channel.mention} on trigger `{trigger}`")
            else:
                res.autoresponders[trigger]["exclusive_channels"].append(channel.id)
                await res.save()
                return await self.bot.grant(ctx, f"Added exclusive access for channel {channel.mention} on trigger `{trigger}`")
        except:
            pass

        await self.bot.warn(ctx, "Could not parse as a role or channel.")

    @autoresponder_exclusive.command(name="list")
    @commands.has_permissions(manage_channels=True)
    async def autoresponder_exclusive_list(self, ctx, trigger: str = None):
        """View a list of roles and channels that have exclusive access to an autoresponder"""
        if not trigger:
            return await ctx.send_help(ctx.command)

        res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not res or not res.autoresponders or trigger not in res.autoresponders:
            return await self.bot.warn(ctx, f"Autoresponder for trigger `{trigger}` not found.")

        embed = discord.Embed(color=0x242429, title=f"Exclusive Access - Trigger: {trigger}")
        
        exclusive_roles = res.autoresponders[trigger].get("exclusive_roles", [])
        exclusive_channels = res.autoresponders[trigger].get("exclusive_channels", [])

        if not exclusive_roles and not exclusive_channels:
            embed.description = "No exclusive access restrictions for this trigger."
        else:
            if exclusive_roles:
                roles_text = []
                for role_id in exclusive_roles:
                    role = ctx.guild.get_role(role_id)
                    roles_text.append(f"• {role.mention if role else f'Unknown ({role_id})'}")
                embed.add_field(name="Exclusive Roles", value="\n".join(roles_text), inline=False)

            if exclusive_channels:
                channels_text = []
                for channel_id in exclusive_channels:
                    channel = ctx.guild.get_channel(channel_id)
                    channels_text.append(f"• {channel.mention if channel else f'Unknown ({channel_id})'}")
                embed.add_field(name="Exclusive Channels", value="\n".join(channels_text), inline=False)

        await ctx.send(embed=embed)

    @commands.group(name="ignore", invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def ignore(self, ctx, target: str = None):
        """No description given"""
        if target:
            try:
                member = await commands.MemberConverter().convert(ctx, target)
                res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
                if not res:
                    res = GuildConfig(guild_id=ctx.guild.id)

                if not res.ignored_members:
                    res.ignored_members = []

                if member.id in res.ignored_members:
                    res.ignored_members.remove(member.id)
                    await res.save()
                    return await self.bot.grant(ctx, f"Stopped ignoring {member.mention}")
                else:
                    res.ignored_members.append(member.id)
                    await res.save()
                    return await self.bot.grant(ctx, f"Now ignoring {member.mention}")
            except:
                pass

            try:
                channel = await commands.TextChannelConverter().convert(ctx, target)
                res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
                if not res:
                    res = GuildConfig(guild_id=ctx.guild.id)

                if not res.ignored_channels:
                    res.ignored_channels = []

                if channel.id in res.ignored_channels:
                    res.ignored_channels.remove(channel.id)
                    await res.save()
                    return await self.bot.grant(ctx, f"Stopped ignoring {channel.mention}")
                else:
                    res.ignored_channels.append(channel.id)
                    await res.save()
                    return await self.bot.grant(ctx, f"Now ignoring {channel.mention}")
            except:
                pass

            return await self.bot.warn(ctx, "Could not parse as a member or channel.")

        await ctx.send_help(ctx.command)

    @ignore.command(name="add")
    @commands.has_permissions(administrator=True)
    async def ignore_add(self, ctx, target: str = None):
        """Ignore a member or channel"""
        if not target:
            return await ctx.send_help(ctx.command)

        try:
            member = await commands.MemberConverter().convert(ctx, target)
            res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
            if not res:
                res = GuildConfig(guild_id=ctx.guild.id)

            if not res.ignored_members:
                res.ignored_members = []

            if member.id in res.ignored_members:
                return await self.bot.warn(ctx, f"{member.mention} is already ignored.")

            res.ignored_members.append(member.id)
            await res.save()
            return await self.bot.grant(ctx, f"Now ignoring {member.mention}")
        except:
            pass

        try:
            channel = await commands.TextChannelConverter().convert(ctx, target)
            res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
            if not res:
                res = GuildConfig(guild_id=ctx.guild.id)

            if not res.ignored_channels:
                res.ignored_channels = []

            if channel.id in res.ignored_channels:
                return await self.bot.warn(ctx, f"{channel.mention} is already ignored.")

            res.ignored_channels.append(channel.id)
            await res.save()
            return await self.bot.grant(ctx, f"Now ignoring {channel.mention}")
        except:
            pass

        await self.bot.warn(ctx, "Could not parse as a member or channel.")

    @ignore.command(name="remove")
    @commands.has_permissions(administrator=True)
    async def ignore_remove(self, ctx, target: str = None):
        """Remove ignoring for a member or channel"""
        if not target:
            return await ctx.send_help(ctx.command)

        try:
            member = await commands.MemberConverter().convert(ctx, target)
            res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
            if not res or not res.ignored_members or member.id not in res.ignored_members:
                return await self.bot.warn(ctx, f"{member.mention} is not ignored.")

            res.ignored_members.remove(member.id)
            await res.save()
            return await self.bot.grant(ctx, f"Stopped ignoring {member.mention}")
        except:
            pass

        try:
            channel = await commands.TextChannelConverter().convert(ctx, target)
            res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
            if not res or not res.ignored_channels or channel.id not in res.ignored_channels:
                return await self.bot.warn(ctx, f"{channel.mention} is not ignored.")

            res.ignored_channels.remove(channel.id)
            await res.save()
            return await self.bot.grant(ctx, f"Stopped ignoring {channel.mention}")
        except:
            pass

        await self.bot.warn(ctx, "Could not parse as a member or channel.")

    @ignore.command(name="list")
    @commands.has_permissions(administrator=True)
    async def ignore_list(self, ctx):
        """View a list of ignored members or channels"""
        res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        
        if not res or (not res.ignored_members and not res.ignored_channels):
            return await self.bot.neutral(ctx, "No ignored members or channels.")

        embed = discord.Embed(color=0x242429, title="Ignored Members & Channels")
        
        if res.ignored_members:
            members_text = []
            for member_id in res.ignored_members:
                member = ctx.guild.get_member(member_id)
                members_text.append(f"• {member.mention if member else f'Unknown ({member_id})'}")
            embed.add_field(name=f"Ignored Members ({len(res.ignored_members)})", value="\n".join(members_text), inline=False)

        if res.ignored_channels:
            channels_text = []
            for channel_id in res.ignored_channels:
                channel = ctx.guild.get_channel(channel_id)
                channels_text.append(f"• {channel.mention if channel else f'Unknown ({channel_id})'}")
            embed.add_field(name=f"Ignored Channels ({len(res.ignored_channels)})", value="\n".join(channels_text), inline=False)

        embed.set_footer(text=f"Total: {len(res.ignored_members or [])} members, {len(res.ignored_channels or [])} channels")
        await ctx.send(embed=embed)

    @commands.group(name="voicemaster", aliases=["vm"])
    @commands.has_permissions(administrator=True)
    async def voicemaster(self, ctx):
        """VoiceMaster system commands"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @voicemaster.command(name="setup")
    async def voicemaster_setup(self, ctx):
        """Set up the VoiceMaster system"""
        try:
            await ctx.defer()
            
            res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
            if res and res.voicemaster_enabled:
                return await self.bot.warn(ctx, "VoiceMaster is already setup in this server!")
            
            category = await ctx.guild.create_category("voice")
            
            lobby_channel = await ctx.guild.create_voice_channel(
                "Join to Create",
                category=category
            )
            
            interface_channel = await ctx.guild.create_text_channel(
                "interface",
                category=category
            )
            
            if not res:
                res = GuildConfig(guild_id=ctx.guild.id)

            res.voicemaster_enabled = True
            res.voicemaster_channel_id = lobby_channel.id
            res.voicemaster_interface_channel_id = interface_channel.id
            res.voicemaster_user_channels = {}
            await res.insert() if not res.id else await res.save()

            embed = discord.Embed(
                color=0x242429,
                title="VoiceMaster Interface",
                description="Use the buttons below to control your voice channel."
            )
            embed.add_field(
                name="Button Usage",
                value="🔒 — Lock the voice channel\n"
                      "🔓 — Unlock the voice channel\n"
                      "👻 — Ghost the voice channel\n"
                      "👁️ — Reveal the voice channel\n"
                      "🎙️ — Claim the voice channel\n"
                      "🔌 — Disconnect a member\n"
                      "🎮 — Start an activity\n"
                      "ℹ️ — View channel information\n"
                      "➕ — Increase the user limit\n"
                      "➖ — Decrease the user limit",
                inline=False
            )

            view = VoiceMasterView(self.bot, ctx.guild.id)
            message = await interface_channel.send(embed=embed, view=view)
            
            res.voicemaster_interface_message_id = message.id
            await res.save()

            await self.bot.grant(ctx, f"VoiceMaster setup complete in {category.mention}")
        except Exception as e:
            await self.bot.warn(ctx, f"Error: {str(e)}")

    @voicemaster.command(name="rename")
    async def voicemaster_rename(self, ctx, *, name: str = None):
        """Rename your voice channel"""
        if not name:
            return await ctx.send_help(ctx.command)
        
        if not ctx.author.voice or not ctx.author.voice.channel:
            return await self.bot.warn(ctx, "You must be in a voice channel!")
        
        try:
            channel = ctx.author.voice.channel
            old_name = channel.name
            await channel.edit(name=name)
            await self.bot.grant(ctx, f"Renamed from **{old_name}** to **{name}**")
        except Exception as e:
            await self.bot.warn(ctx, f"Error: {str(e)}")

    @voicemaster.command(name="limit")
    async def voicemaster_limit(self, ctx, limit: int = None):
        """Set a user limit for your voice channel"""
        if limit is None:
            return await ctx.send_help(ctx.command)
        
        if not ctx.author.voice or not ctx.author.voice.channel:
            return await self.bot.warn(ctx, "You must be in a voice channel!")
        
        if limit < 0 or limit > 99:
            return await self.bot.warn(ctx, "Limit must be between 0 and 99!")
        
        try:
            channel = ctx.author.voice.channel
            await channel.edit(user_limit=limit)
            await self.bot.grant(ctx, f"Limit set to **{limit}**")
        except Exception as e:
            await self.bot.warn(ctx, f"Error: {str(e)}")

    @voicemaster.command(name="transfer")
    async def voicemaster_transfer(self, ctx, member: discord.Member = None):
        """Transfer ownership to another member"""
        if not member:
            return await ctx.send_help(ctx.command)
        
        if not ctx.author.voice or not ctx.author.voice.channel:
            return await self.bot.warn(ctx, "You must be in a voice channel!")
        
        try:
            channel = ctx.author.voice.channel
            await channel.set_permissions(ctx.author, overwrite=None)
            await channel.set_permissions(member, manage_channel=True, manage_permissions=True, move_members=True)
            await self.bot.grant(ctx, f"Ownership transferred to **{member.name}**")
        except Exception as e:
            await self.bot.warn(ctx, f"Error: {str(e)}")

    @voicemaster.command(name="claim")
    async def voicemaster_claim(self, ctx):
        """Claim ownership of an empty voice channel"""
        if not ctx.author.voice or not ctx.author.voice.channel:
            return await self.bot.warn(ctx, "You must be in a voice channel!")
        
        try:
            channel = ctx.author.voice.channel
            await channel.set_permissions(ctx.author, manage_channel=True, manage_permissions=True, move_members=True)
            await self.bot.grant(ctx, f"Ownership claimed!")
        except Exception as e:
            await self.bot.warn(ctx, f"Error: {str(e)}")

    @voicemaster.command(name="remove")
    async def voicemaster_remove(self, ctx):
        """Delete VoiceMaster configuration"""
        try:
            res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
            if res and res.voicemaster_enabled:
                lobby_channel = ctx.guild.get_channel(res.voicemaster_channel_id)
                
                if lobby_channel and lobby_channel.category:
                    category = lobby_channel.category
                    await category.delete()
                
                res.voicemaster_enabled = False
                res.voicemaster_channel_id = None
                res.voicemaster_interface_channel_id = None
                res.voicemaster_interface_message_id = None
                res.voicemaster_user_channels = {}
                await res.save()
                await self.bot.grant(ctx, "VoiceMaster configuration removed and channels deleted")
            else:
                await self.bot.warn(ctx, "VoiceMaster is not configured")
        except Exception as e:
            await self.bot.warn(ctx, f"Error: {str(e)}")

    @commands.group(name="filter", invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def filter(self, ctx):
        """Configure message filters for the server"""
        await ctx.send_help(ctx.command)

    @filter.group(name="invites", invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def filter_invites(self, ctx, enabled: str = None):
        """Configure invite link filtering"""
        if enabled is None:
            return await ctx.send_help(ctx.command)
        
        enabled = enabled.lower() in ['true', '1', 'yes', 'on']
        res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        
        if res:
            res.filter_invites = enabled
            await res.save()
        else:
            await GuildConfig(guild_id=ctx.guild.id, filter_invites=enabled).save()
        
        status = "enabled" if enabled else "disabled"
        await self.bot.grant(ctx, f"Invite filtering has been **{status}**")

    @filter_invites.command(name="action")
    @commands.has_permissions(administrator=True)
    async def filter_invites_action(self, ctx, action: str = None):
        """Set action for invite filtering (kick, ban, timeout)"""
        if action is None:
            return await ctx.send_help(ctx.command)
        
        action = action.lower()
        if action not in ['kick', 'ban', 'timeout']:
            return await self.bot.warn(ctx, "Action must be **kick**, **ban**, or **timeout**")
        
        res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        
        if res:
            res.filter_invites_action = action
            await res.save()
        else:
            await GuildConfig(guild_id=ctx.guild.id, filter_invites_action=action).save()
        
        await self.bot.grant(ctx, f"Invite filter action set to **{action}**")

    @filter.group(name="words", invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def filter_words(self, ctx):
        """Manage filtered words"""
        await ctx.send_help(ctx.command)

    @filter_words.command(name="action")
    @commands.has_permissions(administrator=True)
    async def filter_words_action(self, ctx, action: str = None):
        """Set action for word filtering (kick, ban, timeout)"""
        if action is None:
            return await ctx.send_help(ctx.command)
        
        action = action.lower()
        if action not in ['kick', 'ban', 'timeout']:
            return await self.bot.warn(ctx, "Action must be **kick**, **ban**, or **timeout**")
        
        res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        
        if res:
            res.filter_words_action = action
            await res.save()
        else:
            await GuildConfig(guild_id=ctx.guild.id, filter_words_action=action).save()
        
        await self.bot.grant(ctx, f"Word filter action set to **{action}**")

    @filter_words.command(name="add")
    @commands.has_permissions(administrator=True)
    async def filter_words_add(self, ctx, *, word: str = None):
        """Add a word to the filter list"""
        if not word:
            return await ctx.send_help(ctx.command)
        
        res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not res:
            res = GuildConfig(guild_id=ctx.guild.id, filtered_words=[word])
        else:
            if word.lower() not in [w.lower() for w in res.filtered_words]:
                res.filtered_words.append(word)
            else:
                return await self.bot.warn(ctx, f"**{word}** is already in the filter list")
        
        await res.save()
        await self.bot.grant(ctx, f"Added **{word}** to the filter list")

    @filter_words.command(name="remove")
    @commands.has_permissions(administrator=True)
    async def filter_words_remove(self, ctx, *, word: str = None):
        """Remove a word from the filter list"""
        if not word:
            return await ctx.send_help(ctx.command)
        
        res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not res or word.lower() not in [w.lower() for w in res.filtered_words]:
            return await self.bot.warn(ctx, f"**{word}** is not in the filter list")
        
        res.filtered_words = [w for w in res.filtered_words if w.lower() != word.lower()]
        await res.save()
        await self.bot.grant(ctx, f"Removed **{word}** from the filter list")

    @filter_words.command(name="list")
    async def filter_words_list(self, ctx):
        """View all filtered words"""
        res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        
        if not res or not res.filtered_words:
            description = "No words are currently filtered"
        else:
            description = "\n".join(res.filtered_words)
        
        embed = discord.Embed(color=0x242429, title="Filtered Words", description=description)
        embed.set_footer(text=f"Total words: {len(res.filtered_words) if res else 0}")
        await ctx.send(embed=embed, view=FilterWordsView(self))

    @filter_words.command(name="clear")
    @commands.has_permissions(administrator=True)
    async def filter_words_clear(self, ctx):
        """Clear all filtered words"""
        res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        
        if not res or not res.filtered_words:
            return await self.bot.warn(ctx, "There are no words to clear")
        
        res.filtered_words = []
        await res.save()
        await self.bot.grant(ctx, "All filtered words have been cleared")

    @filter.group(name="spam", invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def filter_spam(self, ctx, enabled: str = None):
        """Configure spam filtering"""
        if enabled is None:
            return await ctx.send_help(ctx.command)
        
        enabled = enabled.lower() in ['true', '1', 'yes', 'on']
        res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        
        if res:
            res.filter_spam = enabled
            await res.save()
        else:
            await GuildConfig(guild_id=ctx.guild.id, filter_spam=enabled).save()
        
        status = "enabled" if enabled else "disabled"
        await self.bot.grant(ctx, f"Spam filtering has been **{status}**")

    @filter_spam.command(name="action")
    @commands.has_permissions(administrator=True)
    async def filter_spam_action(self, ctx, action: str = None):
        """Set action for spam filtering (kick, ban, timeout)"""
        if action is None:
            return await ctx.send_help(ctx.command)
        
        action = action.lower()
        if action not in ['kick', 'ban', 'timeout']:
            return await self.bot.warn(ctx, "Action must be **kick**, **ban**, or **timeout**")
        
        res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        
        if res:
            res.filter_spam_action = action
            await res.save()
        else:
            await GuildConfig(guild_id=ctx.guild.id, filter_spam_action=action).save()
        
        await self.bot.grant(ctx, f"Spam filter action set to **{action}**")

    @filter.command(name="settings")
    async def filter_settings(self, ctx):
        """View all filter settings"""
        res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        
        invite_filter = res.filter_invites if res else False
        invite_action = res.filter_invites_action if res else "kick"
        spam_filter = res.filter_spam if res else False
        spam_action = res.filter_spam_action if res else "kick"
        words_filter = res.filter_words if res else False
        words_action = res.filter_words_action if res else "kick"
        word_count = len(res.filtered_words) if res and res.filtered_words else 0
        
        embed = discord.Embed(color=0x242429, title="Filter Settings")
        embed.add_field(name="Invite Filtering", value="✅ Enabled" if invite_filter else "❌ Disabled", inline=True)
        embed.add_field(name="Invite Action", value=f"`{invite_action}`", inline=True)
        embed.add_field(name="Spam Filtering", value="✅ Enabled" if spam_filter else "❌ Disabled", inline=True)
        embed.add_field(name="Spam Action", value=f"`{spam_action}`", inline=True)
        embed.add_field(name="Words Filtering", value="✅ Enabled" if words_filter else "❌ Disabled", inline=True)
        embed.add_field(name="Words Action", value=f"`{words_action}`", inline=True)
        embed.add_field(name="Filtered Words", value=str(word_count), inline=False)
        embed.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon.url if ctx.guild.icon else None)
        
        await ctx.send(embed=embed)

    def _generate_preset_id(self):
        """Generate a random 8-character hex ID for presets"""
        return secrets.token_hex(4)

    async def _save_preset(self, ctx, preset_id, nickname, avatar, banner):
        """Save a customization preset"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config:
            config = GuildConfig(guild_id=ctx.guild.id)
        
        config.customization_presets[preset_id] = {
            'nickname': nickname,
            'avatar': avatar,
            'banner': banner,
            'created_by': ctx.author.id,
            'created_at': discord.utils.utcnow().isoformat()
        }
        await config.save()

    @commands.group(name="customize", aliases=["custom"], invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def customize(self, ctx):
        """Customize the bot appearance in your server"""
        await ctx.send_help(ctx.command)

    @customize.command(name="nickname")
    async def customize_nickname(self, ctx, *, name: str = None):
        """Set the bot nickname in your server"""
        if not name:
            return await ctx.send_help(ctx.command)

        try:
            if name.lower() == 'reset':
                await ctx.guild.me.edit(nick=None)
                return await ctx.send("Bot nickname has been reset to default")
            
            if len(name) > 32:
                return await ctx.send("Nickname must be **32 characters or less**.")

            await ctx.guild.me.edit(nick=name)
            await ctx.send(f"Bot nickname set to **{name}**")
        except discord.Forbidden:
            await ctx.send("I don't have permission to change my nickname.")

    @customize.command(name="avatar")
    async def customize_avatar(self, ctx, *, reset: str = None):
        """Set the bot server avatar (requires an attachment)"""
        if reset and reset.lower() == 'reset':
            await ctx.guild.me.edit(avatar=None)
            return await ctx.send("Bot avatar has been reset")

        if not ctx.message.attachments:
            return await ctx.send("Please attach an image.")

        attachment = ctx.message.attachments[0]
        if not attachment.content_type or not attachment.content_type.startswith('image/'):
            return await ctx.send("Attachment must be an image.")

        image_bytes = await attachment.read()
        try:
            await ctx.guild.me.edit(avatar=image_bytes)
            await ctx.send("Bot avatar updated successfully")
        except Exception as e:
            await ctx.send(f"Failed to set avatar: {e}")

    @customize.command(name="banner")
    async def customize_banner(self, ctx, *, reset: str = None):
        """Set the bot server banner (requires an attachment)"""
        if reset and reset.lower() == 'reset':
            await ctx.guild.me.edit(banner=None)
            return await ctx.send("Bot banner has been reset")

        if not ctx.message.attachments:
            return await ctx.send("Please attach an image.")

        attachment = ctx.message.attachments[0]
        image_bytes = await attachment.read()
        try:
            await ctx.guild.me.edit(banner=image_bytes)
            await ctx.send("Bot banner updated successfully")
        except Exception as e:
            await ctx.send(f"Failed to set banner: {e}")

    @customize.command(name="view")
    async def customize_view(self, ctx):
        """View current customizations"""
        me = ctx.guild.me
        nickname = me.nick or 'Default (none set)'
        avatar = me.display_avatar.url
        
        embed = discord.Embed(title='Bot Customization', color=0x242429)
        embed.add_field(name='Nickname', value=f"`{nickname}`", inline=True)
        embed.add_field(name='Avatar', value=f"[Link]({avatar})", inline=True)
        
        await ctx.send(embed=embed)

    @customize.command(name="accents")
    async def customize_accents(self, ctx):
        """View available accent colors"""
        accents = {
            'red': '#FF0000', 'blue': '#0000FF', 'green': '#00FF00',
            'yellow': '#FFFF00', 'purple': '#800080', 'cyan': '#00FFFF',
            'magenta': '#FF00FF', 'orange': '#FFA500', 'pink': '#FFC0CB',
            'gold': '#FFD700', 'silver': '#C0C0C0', 'navy': '#000080',
            'teal': '#008080', 'maroon': '#800000', 'olive': '#808000',
            'coral': '#FF7F50', 'salmon': '#FA8072', 'indigo': '#4B0082',
            'violet': '#EE82EE'
        }
        accent_list = "\n".join([f"`{name}` {hex_val}" for name, hex_val in accents.items()])
        embed = discord.Embed(title='Available Accents', color=0x242429, description=accent_list)
        await ctx.send(embed=embed)

    @customize.command(name="reset")
    async def customize_reset(self, ctx):
        """Reset all customizations to default"""
        try:
            await ctx.guild.me.edit(nick=None, avatar=None, banner=None)
            await ctx.send("All customizations have been reset to default")
        except Exception as e:
            await ctx.send(f"Failed to reset: {e}")

    @customize.group(name="presets", invoke_without_command=True)
    async def customize_presets(self, ctx):
        """View all saved customization presets"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config or not config.customization_presets:
            return await ctx.send("No presets saved yet.")
        
        embed = discord.Embed(title='Customization Presets', color=0x242429)
        for preset_id, preset_data in config.customization_presets.items():
            nickname = preset_data.get('nickname') or 'None'
            created_at = preset_data.get('created_at', 'Unknown')
            embed.add_field(
                name=f"`{preset_id}`",
                value=f"**Nickname:** {nickname}\n**Created:** {created_at[:10]}",
                inline=False
            )
        
        await ctx.send(embed=embed)

    @customize_presets.command(name="save")
    async def customize_presets_save(self, ctx):
        """Save current customizations as a preset"""
        me = ctx.guild.me
        items_saved = 0
        
        nickname = me.nick if me.nick else None
        avatar = me.display_avatar.url if me.display_avatar else None
        banner = None
        
        if nickname:
            items_saved += 1
        if avatar and avatar != ctx.bot.user.display_avatar.url:
            items_saved += 1
        
        if items_saved < 2:
            return await ctx.send("You need to customize **at least 2 items** (nickname, avatar, banner) to save as a preset.")
        
        preset_id = self._generate_preset_id()
        await self._save_preset(ctx, preset_id, nickname, avatar, banner)
        
        await ctx.send(f"✅ Preset saved with ID: `{preset_id}`\nRestore with: `,customize presets restore {preset_id}`")

    @customize_presets.command(name="restore")
    async def customize_presets_restore(self, ctx, preset_id: str):
        """Restore a saved customization preset"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config or preset_id not in config.customization_presets:
            return await ctx.send(f"Preset `{preset_id}` not found.")
        
        preset = config.customization_presets[preset_id]
        try:
            if preset.get('nickname'):
                await ctx.guild.me.edit(nick=preset['nickname'])
            
            if preset.get('avatar'):
                async with aiohttp.ClientSession() as session:
                    async with session.get(preset['avatar']) as resp:
                        if resp.status == 200:
                            avatar_bytes = await resp.read()
                            await ctx.guild.me.edit(avatar=avatar_bytes)
            
            if preset.get('banner'):
                async with aiohttp.ClientSession() as session:
                    async with session.get(preset['banner']) as resp:
                        if resp.status == 200:
                            banner_bytes = await resp.read()
                            await ctx.guild.me.edit(banner=banner_bytes)
            
            await ctx.send(f"✅ Restored preset `{preset_id}`")
        except Exception as e:
            await ctx.send(f"Failed to restore preset: {e}")

    @commands.group(name="reactionrole", aliases=["rr"], invoke_without_command=True)
    @commands.has_permissions(manage_roles=True)
    async def reactionrole(self, ctx):
        """Manage reaction role messages"""
        await ctx.send_help(ctx.command)

    @reactionrole.command(name="add")
    async def reactionrole_add(self, ctx, message: discord.Message, emoji: str, role: discord.Role):
        """Add a reaction role to a message
        
        Usage: reactionrole add <message_id/link> <emoji> <@role>
        """
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config:
            config = GuildConfig(guild_id=ctx.guild.id)
        
        message_id = str(message.id)
        
        if message_id not in config.reaction_roles:
            config.reaction_roles[message_id] = {}
        
        emoji_str = str(emoji)
        config.reaction_roles[message_id][emoji_str] = role.id
        
        await config.save()
        
        try:
            await message.add_reaction(emoji)
        except:
            pass
        
        await self.bot.grant(ctx, f"Added {emoji} → **{role.name}** reaction role")

    @reactionrole.command(name="remove")
    async def reactionrole_remove(self, ctx, message: discord.Message, emoji: str):
        """Remove a reaction role from a message"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config or str(message.id) not in config.reaction_roles:
            return await self.bot.deny(ctx, "No reaction roles found for this message.")
        
        message_id = str(message.id)
        emoji_str = str(emoji)
        
        if emoji_str not in config.reaction_roles[message_id]:
            return await self.bot.deny(ctx, f"Reaction {emoji} not found on this message.")
        
        del config.reaction_roles[message_id][emoji_str]
        if not config.reaction_roles[message_id]:
            del config.reaction_roles[message_id]
        
        await config.save()
        await self.bot.grant(ctx, f"Removed {emoji} reaction role")

    @reactionrole.command(name="list")
    async def reactionrole_list(self, ctx):
        """List all reaction roles in this server"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config or not config.reaction_roles:
            return await ctx.send("No reaction roles configured in this server.")
        
        embed = discord.Embed(title="Reaction Roles", color=0x242429)
        for message_id, reactions in config.reaction_roles.items():
            role_mappings = []
            for emoji, role_id in reactions.items():
                role = ctx.guild.get_role(role_id)
                role_name = role.name if role else f"Unknown ({role_id})"
                role_mappings.append(f"{emoji} → {role_name}")
            
            if role_mappings:
                embed.add_field(
                    name=f"Message `{message_id}`",
                    value="\n".join(role_mappings),
                    inline=False
                )
        
        await ctx.send(embed=embed)

    @commands.group(name="command", invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def command(self, ctx):
        """Manage command and module settings"""
        await ctx.send_help(ctx.command)

    @command.command(name="enable")
    async def command_enable(self, ctx, *, target: str = None):
        """Enable a command or module
        
        Usage: command enable <command_name>
               command enable <command_name> @user|@role|#channel|all
               command enable module <module_name>
               command enable module <module_name> @user|@role|#channel|all
        """
        if not target:
            return await ctx.send_help(ctx.command)
        
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config:
            config = GuildConfig(guild_id=ctx.guild.id)
        
        parts = target.split()
        
        is_module = len(parts) > 1 and parts[0].lower() == "module"
        
        if is_module:
            module_name = parts[1].lower()
            target_str = " ".join(parts[2:]) if len(parts) > 2 else "all"
            
            if module_name not in config.disabled_modules:
                return await self.bot.deny(ctx, f"Module **{module_name}** is not disabled.")
            
            if not target_str or target_str.lower() == "all":
                del config.disabled_modules[module_name]
                await config.save()
                await self.bot.grant(ctx, f"Enabled module **{module_name}** for everyone")
            else:
                await self._update_restrictions(ctx, config, "modules", module_name, target_str, "enable")
        else:
            cmd_name = parts[0].lower()
            target_str = " ".join(parts[1:]) if len(parts) > 1 else "all"
            
            if cmd_name not in config.disabled_commands:
                return await self.bot.deny(ctx, f"Command **{cmd_name}** is not disabled.")
            
            if not target_str or target_str.lower() == "all":
                del config.disabled_commands[cmd_name]
                await config.save()
                await self.bot.grant(ctx, f"Enabled command **{cmd_name}** for everyone")
            else:
                await self._update_restrictions(ctx, config, "commands", cmd_name, target_str, "enable")
    
    async def _update_restrictions(self, ctx, config, cmd_type, name, target_str, action):
        """Update command/module restrictions for users, roles, or channels"""
        restrictions = config.disabled_commands if cmd_type == "commands" else config.disabled_modules
        
        if name not in restrictions:
            restrictions[name] = {"all": False, "users": [], "roles": [], "channels": []}
        
        users = ctx.message.mentions
        roles = ctx.message.role_mentions
        channels = []
        
        for match in re.finditer(r'<#(\d+)>', target_str):
            channel = ctx.guild.get_channel(int(match.group(1)))
            if channel:
                channels.append(channel)
        
        if action == "enable":
            restrictions[name]["users"] = [u.id for u in restrictions[name].get("users", []) if u not in [m.id for m in users]]
            restrictions[name]["roles"] = [r.id for r in restrictions[name].get("roles", []) if r not in [r.id for r in roles]]
            restrictions[name]["channels"] = [c.id for c in restrictions[name].get("channels", []) if c not in [c.id for c in channels]]
        else:
            for user in users:
                if user.id not in restrictions[name]["users"]:
                    restrictions[name]["users"].append(user.id)
            for role in roles:
                if role.id not in restrictions[name]["roles"]:
                    restrictions[name]["roles"].append(role.id)
            for channel in channels:
                if channel.id not in restrictions[name]["channels"]:
                    restrictions[name]["channels"].append(channel.id)
        
        if not restrictions[name]["users"] and not restrictions[name]["roles"] and not restrictions[name]["channels"]:
            del restrictions[name]
        
        await config.save()
        await self.bot.grant(ctx, f"Updated {cmd_type} **{name}** restrictions for {action}")


    @command.command(name="disable")
    async def command_disable(self, ctx, *, target: str = None):
        """Disable a command or module
        
        Usage: command disable <command_name>
               command disable <command_name> @user|@role|#channel|all
               command disable module <module_name>
               command disable module <module_name> @user|@role|#channel|all
        """
        if not target:
            return await ctx.send_help(ctx.command)
        
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config:
            config = GuildConfig(guild_id=ctx.guild.id)
        
        parts = target.split()
        
        is_module = len(parts) > 1 and parts[0].lower() == "module"
        
        if is_module:
            module_name = parts[1].lower()
            target_str = " ".join(parts[2:]) if len(parts) > 2 else "all"
            
            if module_name not in config.disabled_modules:
                config.disabled_modules[module_name] = {"all": False, "users": [], "roles": [], "channels": []}
            
            if not target_str or target_str.lower() == "all":
                if module_name.lower() == "configuration":
                    return await self.bot.deny(ctx, "You cannot disable the **Configuration** module.")
                
                config.disabled_modules[module_name]["all"] = True
                await config.save()
                await self.bot.grant(ctx, f"Disabled module **{module_name}** for everyone")
            else:
                await self._update_restrictions(ctx, config, "modules", module_name, target_str, "disable")
        else:
            cmd_name = parts[0].lower()
            target_str = " ".join(parts[1:]) if len(parts) > 1 else "all"
            
            if cmd_name == "command":
                return await self.bot.deny(ctx, "You cannot disable the **command** command.")
            
            if cmd_name not in config.disabled_commands:
                config.disabled_commands[cmd_name] = {"all": False, "users": [], "roles": [], "channels": []}
            
            if not target_str or target_str.lower() == "all":
                config.disabled_commands[cmd_name]["all"] = True
                await config.save()
                await self.bot.grant(ctx, f"Disabled command **{cmd_name}** for everyone")
            else:
                await self._update_restrictions(ctx, config, "commands", cmd_name, target_str, "disable")

    @command.command(name="list")
    async def command_list(self, ctx):
        """List disabled commands and modules"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        
        if not config or (not config.disabled_commands and not config.disabled_modules):
            return await ctx.send("No commands or modules are disabled in this server.")
        
        embed = discord.Embed(title="Disabled Commands & Modules", color=0x242429)
        
        if config.disabled_commands:
            cmd_list = []
            for cmd, restrictions in config.disabled_commands.items():
                if restrictions.get("all"):
                    cmd_list.append(f"`{cmd}` - Disabled for everyone")
                else:
                    scope = []
                    if restrictions.get("users"):
                        scope.append(f"{len(restrictions['users'])} user(s)")
                    if restrictions.get("roles"):
                        scope.append(f"{len(restrictions['roles'])} role(s)")
                    if restrictions.get("channels"):
                        scope.append(f"{len(restrictions['channels'])} channel(s)")
                    cmd_list.append(f"`{cmd}` - Disabled for: {', '.join(scope)}")
            
            if cmd_list:
                embed.add_field(name="Disabled Commands", value="\n".join(cmd_list), inline=False)
        
        if config.disabled_modules:
            mod_list = []
            for mod, restrictions in config.disabled_modules.items():
                if restrictions.get("all"):
                    mod_list.append(f"`{mod}` - Disabled for everyone")
                else:
                    scope = []
                    if restrictions.get("users"):
                        scope.append(f"{len(restrictions['users'])} user(s)")
                    if restrictions.get("roles"):
                        scope.append(f"{len(restrictions['roles'])} role(s)")
                    if restrictions.get("channels"):
                        scope.append(f"{len(restrictions['channels'])} channel(s)")
                    mod_list.append(f"`{mod}` - Disabled for: {', '.join(scope)}")
            
            if mod_list:
                embed.add_field(name="Disabled Modules", value="\n".join(mod_list), inline=False)
        
        await ctx.send(embed=embed)

    @commands.group(name="restrict", aliases=["restrictcommand"], invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def restrict(self, ctx):
        """restrict a command for specific perms"""
        await ctx.send_help(ctx.command)

    @restrict.command(name="add", aliases=["create", "c", "a"])
    @commands.has_permissions(manage_guild=True)
    async def restrict_add(self, ctx, command_name: str = None, *, permissions: str = None):
        """add a command restriction"""
        if not command_name or not permissions:
            return await ctx.send_help(ctx.command)

        command_name = command_name.replace(".", " ").lower().strip()
        target_command = self.bot.get_command(command_name)
        if not target_command:
            return await self.bot.warn(ctx, f"Command `{command_name}` was not found.")

        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config:
            config = GuildConfig(guild_id=ctx.guild.id)

        if not getattr(config, "command_restrictions", None):
            config.command_restrictions = {}

        normalized_perms = []
        for raw in [p.strip() for p in permissions.split(",") if p.strip()]:
            perm = self._normalize_permission_name(raw)
            if not perm:
                return await self.bot.warn(ctx, f"Invalid permission: `{raw}`")
            normalized_perms.append(perm)

        cmd_key = target_command.qualified_name.lower()
        existing = set(config.command_restrictions.get(cmd_key, []))
        existing.update(normalized_perms)
        config.command_restrictions[cmd_key] = sorted(existing)

        await config.save()
        pretty = ", ".join(f"`{p}`" for p in config.command_restrictions[cmd_key])
        await self.bot.grant(ctx, f"Restricted `{cmd_key}` to: {pretty}")

    @restrict.command(name="list", aliases=["show"])
    @commands.has_permissions(manage_guild=True)
    async def restrict_list(self, ctx):
        """show command restrictions"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        restrictions = config.command_restrictions if config and getattr(config, "command_restrictions", None) else {}
        if not restrictions:
            return await self.bot.neutral(ctx, "No command restrictions are configured.")

        lines = []
        for cmd_name, perms in sorted(restrictions.items()):
            if not perms:
                continue
            pretty = ", ".join(f"`{p}`" for p in sorted(set(perms)))
            lines.append(f"`{cmd_name}` → {pretty}")

        if not lines:
            return await self.bot.neutral(ctx, "No command restrictions are configured.")

        embed = discord.Embed(color=0x242429, title="Command Restrictions", description="\n".join(lines))
        await ctx.send(embed=embed)

    @restrict.command(name="remove", aliases=["rem", "delete", "del", "d", "r"])
    @commands.has_permissions(manage_guild=True)
    async def restrict_remove(self, ctx, command_name: str = None, permission: str = None):
        """Delete a command restriction"""
        if not command_name:
            return await ctx.send_help(ctx.command)

        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        restrictions = config.command_restrictions if config and getattr(config, "command_restrictions", None) else {}
        if not restrictions:
            return await self.bot.warn(ctx, "No command restrictions are configured.")

        command_name = command_name.replace(".", " ").lower().strip()
        target_command = self.bot.get_command(command_name)
        cmd_key = target_command.qualified_name.lower() if target_command else command_name

        if cmd_key not in restrictions:
            return await self.bot.warn(ctx, f"No restriction found for `{cmd_key}`.")

        if permission:
            perm = self._normalize_permission_name(permission)
            if not perm:
                return await self.bot.warn(ctx, f"Invalid permission: `{permission}`")

            perms = restrictions.get(cmd_key, [])
            if perm not in perms:
                return await self.bot.warn(ctx, f"`{cmd_key}` is not restricted by `{perm}`.")

            restrictions[cmd_key] = [p for p in perms if p != perm]
            if not restrictions[cmd_key]:
                restrictions.pop(cmd_key, None)

            await config.save()
            return await self.bot.grant(ctx, f"Removed `{perm}` restriction from `{cmd_key}`")

        restrictions.pop(cmd_key, None)
        await config.save()
        await self.bot.grant(ctx, f"Removed all restrictions from `{cmd_key}`")

    @restrict.command(name="reset", aliases=["clear"])
    @commands.has_permissions(manage_guild=True)
    async def restrict_reset(self, ctx):
        """reset all command restrictions"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        restrictions = config.command_restrictions if config and getattr(config, "command_restrictions", None) else {}
        if not restrictions:
            return await self.bot.warn(ctx, "No command restrictions are configured.")

        config.command_restrictions = {}
        await config.save()
        await self.bot.grant(ctx, "Reset all command restrictions")

    @commands.group(name="level", invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def level(self, ctx):
        """Manage leveling system"""
        await ctx.send_help(ctx.command)

    @level.command(name="enable")
    async def level_enable(self, ctx):
        """Enable the leveling system"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config:
            config = GuildConfig(guild_id=ctx.guild.id)
        
        if config.leveling_enabled:
            return await self.bot.deny(ctx, "Leveling is already enabled.")
        
        config.leveling_enabled = True
        await config.save()
        await self.bot.grant(ctx, "Leveling system has been **enabled**")

    @level.command(name="disable")
    async def level_disable(self, ctx):
        """Disable the leveling system"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config:
            return await self.bot.deny(ctx, "Leveling is already disabled.")
        
        if not config.leveling_enabled:
            return await self.bot.deny(ctx, "Leveling is already disabled.")
        
        config.leveling_enabled = False
        await config.save()
        await self.bot.grant(ctx, "Leveling system has been **disabled**")

    @level.command(name="role")
    async def level_role(self, ctx, role: discord.Role, level: int):
        """Assign a role to be given at a specific level
        
        Usage: level role @role 5
        """
        if level < 1:
            return await self.bot.deny(ctx, "Level must be at least 1.")
        
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config:
            config = GuildConfig(guild_id=ctx.guild.id)
        
        config.level_roles[str(level)] = role.id
        await config.save()
        await self.bot.grant(ctx, f"Users reaching level **{level}** will receive **{role.name}**")

    @level.command(name="channel")
    async def level_channel(self, ctx, channel: discord.TextChannel = None):
        """Set the channel for level up notifications
        
        Usage: level channel
               level channel (clears)
        """
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config:
            config = GuildConfig(guild_id=ctx.guild.id)
        
        if not channel:
            config.level_channel_id = None
            await config.save()
            return await self.bot.grant(ctx, "Level up channel cleared")
        
        config.level_channel_id = channel.id
        await config.save()
        await self.bot.grant(ctx, f"Level up notifications will be sent to {channel.mention}")

    @level.command(name="message")
    async def level_message(self, ctx, *, message: str = None):
        """Set a custom level up message
        
        Usage: level message {user} reached level {level}!
               level message (clears)
        
        Available variables: {user}, {user.mention}, {level}, {old_level}
        """
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config:
            config = GuildConfig(guild_id=ctx.guild.id)
        
        if not message:
            config.level_message = None
            await config.save()
            return await self.bot.grant(ctx, "Custom level message cleared")
        
        config.level_message = message
        await config.save()
        await self.bot.grant(ctx, "Custom level message updated")

    @level.group(name="settings", invoke_without_command=True)
    async def level_settings(self, ctx):
        """View leveling settings"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        
        embed = discord.Embed(title="Leveling Settings", color=0x242429)
        
        if not config:
            embed.description = "No leveling settings configured."
            return await ctx.send(embed=embed)
        
        status = "✅ Enabled" if config.leveling_enabled else "❌ Disabled"
        embed.add_field(name="Status", value=status, inline=False)
        
        if config.level_roles:
            role_list = []
            for level, role_id in sorted(config.level_roles.items(), key=lambda x: int(x[0])):
                role = ctx.guild.get_role(role_id)
                role_name = role.name if role else f"Unknown ({role_id})"
                role_list.append(f"Level **{level}** → {role_name}")
            embed.add_field(name="Level Roles", value="\n".join(role_list), inline=False)
        
        if config.level_channel_id:
            channel = ctx.guild.get_channel(config.level_channel_id)
            channel_name = channel.mention if channel else f"Unknown ({config.level_channel_id})"
            embed.add_field(name="Notification Channel", value=channel_name, inline=False)
        
        if config.level_message:
            embed.add_field(name="Custom Message", value=f"`{config.level_message}`", inline=False)
        
        await ctx.send(embed=embed)

    @level_settings.command(name="clear")
    async def level_settings_clear(self, ctx):
        """Clear all leveling settings"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config:
            return await self.bot.deny(ctx, "No leveling settings to clear.")
        
        config.leveling_enabled = False
        config.level_roles = {}
        config.level_channel_id = None
        config.level_message = None
        await config.save()
        await self.bot.grant(ctx, "Leveling settings cleared")

    @level.command(name="reset")
    @commands.has_permissions(manage_guild=True)
    async def level_reset(self, ctx, member: discord.Member):
        """Reset a user's XP and level to 0"""
        user_config = await UserConfig.find_one(UserConfig.user_id == member.id)
        if not user_config:
            return await self.bot.deny(ctx, f"**{member.display_name}** has no level data to reset.")
        
        user_config.xp = 0
        user_config.level = 1
        await user_config.save()
        await self.bot.grant(ctx, f"**{member.display_name}** has been reset to level **1** with **0 XP**")

    @level.command(name="set")
    @commands.has_permissions(manage_guild=True)
    async def level_set(self, ctx, member: discord.Member, level: int):
        """Set a user to a specific level"""
        if level < 1:
            return await self.bot.deny(ctx, "Level must be at least 1.")
        
        user_config = await UserConfig.find_one(UserConfig.user_id == member.id)
        if not user_config:
            user_config = UserConfig(user_id=member.id)
        
        from cogs.events import get_xp_for_level
        user_config.level = level
        user_config.xp = get_xp_for_level(level)
        await user_config.save()
        await self.bot.grant(ctx, f"**{member.display_name}** has been set to level **{level}** with **{user_config.xp} XP**")

    @commands.group(name="tickets", invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def tickets(self, ctx):
        """Manage the ticket system"""
        await ctx.send_help(ctx.command)

    @tickets.command(name="setup")
    async def tickets_setup(self, ctx):
        """Initialize the ticket system"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config:
            config = GuildConfig(guild_id=ctx.guild.id)
        
        if config.ticket_category_id:
            category = ctx.guild.get_channel(config.ticket_category_id)
            if category:
                embed = discord.Embed(color=0xFAA61A, title="⚠️ Already Configured")
                embed.description = f"Ticket system is already set up in {category.mention}\n\nUse `,tickets reset` to reconfigure."
                return await ctx.send(embed=embed)
        
        try:
            category = await ctx.guild.create_category("tickets")
        except Exception as e:
            return await self.bot.deny(ctx, f"Failed to create category: {str(e)}")
        
        try:
            channel = await category.create_text_channel("tickets")
        except Exception as e:
            await category.delete()
            return await self.bot.deny(ctx, f"Failed to create channel: {str(e)}")
        
        config.ticket_category_id = category.id
        config.ticket_counter = 0
        await config.save()
        
        embed = discord.Embed(color=0x242429, title="🎫 Ticket System")
        embed.description = "Welcome to our support system! Click the button below to create a ticket if you need assistance.\n\n**Rules:**\n• One ticket per user\n• Be respectful to staff members"
        embed.add_field(name="📋 How it works", value="1. Click the **Create Ticket** button\n2. A private channel will be created\n3. Staff will respond as soon as possible", inline=False)
        embed.set_footer(text="Only you and staff can see your ticket")
        embed.set_thumbnail(url=ctx.guild.icon.url if ctx.guild.icon else None)
        
        await channel.send(embed=embed, view=TicketCreateView(self.bot))
        
        setup_embed = discord.Embed(color=0x57F287, title="✅ Ticket System Initialized")
        setup_embed.description = f"Ticket system has been successfully set up!"
        setup_embed.add_field(name="Category", value=category.mention, inline=True)
        setup_embed.add_field(name="Channel", value=channel.mention, inline=True)
        setup_embed.add_field(name="Configuration", value="1 ticket per user | Auto-numbered | Admin managed", inline=False)
        
        await ctx.send(embed=setup_embed)

    @tickets.command(name="create")
    async def tickets_create(self, ctx, member: discord.Member):
        """Open a ticket for a member"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config or not config.ticket_category_id:
            return await self.bot.deny(ctx, "Ticket system not set up. Use `,tickets setup <category>`")
        
        category = ctx.guild.get_channel(config.ticket_category_id)
        if not category:
            return await self.bot.deny(ctx, "Ticket category not found.")
        
        config.ticket_counter += 1
        ticket_number = config.ticket_counter
        
        channel_name = f"ticket-{ticket_number}"
        ticket_channel = await category.create_text_channel(
            channel_name,
            topic=f"Ticket for {member.display_name} (ID: {member.id})"
        )
        
        await ticket_channel.set_permissions(ctx.guild.default_role, view_channel=False)
        await ticket_channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True)
        await ticket_channel.set_permissions(ctx.author, view_channel=True, send_messages=True, read_message_history=True, manage_messages=True)
        
        await config.save()
        
        embed = discord.Embed(color=0x242429, title=f"Ticket #{ticket_number}")
        embed.description = f"Ticket opened for {member.mention}"
        embed.add_field(name="Created by", value=ctx.author.mention, inline=False)
        
        await ticket_channel.send(embed=embed)
        await self.bot.grant(ctx, f"Ticket #{ticket_number} created for {member.mention}: {ticket_channel.mention}")

    @tickets.command(name="add")
    async def tickets_add(self, ctx, member: discord.Member):
        """Add a user to a ticket"""
        if not isinstance(ctx.channel, discord.TextChannel):
            return await self.bot.deny(ctx, "This command only works in ticket channels.")
        
        if "ticket-" not in ctx.channel.name:
            return await self.bot.deny(ctx, "This is not a ticket channel.")
        
        try:
            await ctx.channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True)
            await self.bot.grant(ctx, f"Added {member.mention} to the ticket")
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to add user: {str(e)}")

    @tickets.command(name="remove")
    async def tickets_remove(self, ctx, member: discord.Member):
        """Remove a user from a ticket"""
        if not isinstance(ctx.channel, discord.TextChannel):
            return await self.bot.deny(ctx, "This command only works in ticket channels.")
        
        if "ticket-" not in ctx.channel.name:
            return await self.bot.deny(ctx, "This is not a ticket channel.")
        
        try:
            await ctx.channel.set_permissions(member, view_channel=False)
            await self.bot.grant(ctx, f"Removed {member.mention} from the ticket")
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to remove user: {str(e)}")

    @tickets.command(name="transcript")
    async def tickets_transcript(self, ctx, channel: discord.TextChannel):
        """Set the log channel for transcripts"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config:
            config = GuildConfig(guild_id=ctx.guild.id)
        
        config.ticket_transcript_channel_id = channel.id
        await config.save()
        await self.bot.grant(ctx, f"Ticket transcripts will be sent to {channel.mention}")

    @tickets.command(name="reset")
    async def tickets_reset(self, ctx):
        """Delete all tickets and reset ticket system"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config or not config.ticket_category_id:
            return await self.bot.deny(ctx, "No ticket system configured.")
        
        category = ctx.guild.get_channel(config.ticket_category_id)
        if category:
            for channel in category.text_channels:
                try:
                    await channel.delete()
                except Exception as e:
                    print(f"Failed to delete channel {channel.name}: {e}")
            
            try:
                await category.delete()
            except Exception as e:
                print(f"Failed to delete category: {e}")
        
        config.ticket_category_id = None
        config.ticket_transcript_channel_id = None
        config.ticket_counter = 0
        config.ticket_roles = []
        await config.save()
        
        embed = discord.Embed(color=0x57F287, title="✅ Ticket System Removed")
        embed.description = "All tickets, channels, and categories have been successfully deleted."
        await ctx.send(embed=embed)

    @commands.group(name="autorole", invoke_without_command=True)
    @commands.has_permissions(manage_guild=True, manage_roles=True)
    async def autorole(self, ctx):
        """Set up automatic role assign on member join"""
        await ctx.send_help(ctx.command)

    @autorole.command(name="add")
    @commands.has_permissions(manage_guild=True, manage_roles=True)
    async def autorole_add(self, ctx, role: discord.Role):
        """Adds a autorole and assigns on join to member"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config:
            config = GuildConfig(guild_id=ctx.guild.id)
        
        if role.id not in config.autoroles:
            config.autoroles.append(role.id)
            await config.save()
            await self.bot.grant(ctx, f"Added **{role.name}** to autoroles")
        else:
            await self.bot.warn(ctx, f"**{role.name}** is already in autoroles")

    @autorole.command(name="remove")
    @commands.has_permissions(manage_guild=True, manage_roles=True)
    async def autorole_remove(self, ctx, role: discord.Role):
        """Removes a autorole and stops assigning on join"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config or role.id not in config.autoroles:
            return await self.bot.warn(ctx, f"**{role.name}** is not in autoroles")
        
        config.autoroles.remove(role.id)
        await config.save()
        await self.bot.grant(ctx, f"Removed **{role.name}** from autoroles")

    @autorole.command(name="list")
    async def autorole_list(self, ctx):
        """View a list of every auto role"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        
        if not config or not config.autoroles:
            return await self.bot.neutral(ctx, "No autoroles configured")
        
        roles_text = []
        for role_id in config.autoroles:
            role = ctx.guild.get_role(role_id)
            roles_text.append(f"• {role.mention if role else f'Unknown ({role_id})'}")
        
        embed = discord.Embed(title="Autoroles", color=0x242429, description="\n".join(roles_text))
        embed.set_footer(text=f"Total: {len(config.autoroles)}")
        await ctx.send(embed=embed)

    @autorole.command(name="reset")
    @commands.has_permissions(manage_guild=True, manage_roles=True)
    async def autorole_reset(self, ctx):
        """Clears every autorole for guild"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config or not config.autoroles:
            return await self.bot.warn(ctx, "No autoroles to clear")
        
        config.autoroles = []
        await config.save()
        await self.bot.grant(ctx, "All autoroles have been cleared")

    @commands.group(name="buttonrole", invoke_without_command=True)
    @commands.has_permissions(manage_guild=True, manage_roles=True)
    async def buttonrole(self, ctx):
        """Set up self-assignable roles with buttons"""
        await ctx.send_help(ctx.command)

    @buttonrole.command(name="add")
    @commands.has_permissions(manage_guild=True, manage_roles=True)
    async def buttonrole_add(self, ctx, message: discord.Message, role: discord.Role, style: str = "primary", emoji: str = None, *, label: str = None):
        """Add a button role to a message
        
        Usage: buttonrole add <message_link> @role primary emoji label
        Styles: primary (blue), secondary (gray), success (green), danger (red)
        """
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config:
            config = GuildConfig(guild_id=ctx.guild.id)
        
        style_map = {
            "primary": discord.ButtonStyle.primary,
            "secondary": discord.ButtonStyle.secondary,
            "success": discord.ButtonStyle.success,
            "danger": discord.ButtonStyle.danger
        }
        button_style = style_map.get(style.lower(), discord.ButtonStyle.primary)
        
        message_id = str(message.id)
        if message_id not in config.button_roles:
            config.button_roles[message_id] = []
        
        button_data = {
            "role_id": role.id,
            "style": style.lower(),
            "emoji": emoji,
            "label": label or role.name
        }
        config.button_roles[message_id].append(button_data)
        await config.save()
        
        await self.bot.grant(ctx, f"Added button role **{role.name}** to [message]({message.jump_url})")

    @buttonrole.command(name="remove")
    @commands.has_permissions(manage_guild=True, manage_roles=True)
    async def buttonrole_remove(self, ctx, message: discord.Message, index: int):
        """Remove a button role from a message
        
        Usage:buttonrole remove <message_link> <index>
        Use 'buttonrole list' to see indices
        """
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        message_id = str(message.id)
        
        if not config or message_id not in config.button_roles or not config.button_roles[message_id]:
            return await self.bot.warn(ctx, "No button roles found for this message")
        
        if index < 1 or index > len(config.button_roles[message_id]):
            return await self.bot.warn(ctx, f"Invalid index. Use a number between 1 and {len(config.button_roles[message_id])}")
        
        removed = config.button_roles[message_id].pop(index - 1)
        if not config.button_roles[message_id]:
            del config.button_roles[message_id]
        
        await config.save()
        await self.bot.grant(ctx, f"Removed button role from [message]({message.jump_url})")

    @buttonrole.command(name="list")
    async def buttonrole_list(self, ctx):
        """View a list of every button role"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        
        if not config or not config.button_roles:
            return await self.bot.neutral(ctx, "No button roles configured")
        
        embed = discord.Embed(title="Button Roles", color=0x242429)
        
        for message_id, buttons in config.button_roles.items():
            roles_text = []
            for i, button in enumerate(buttons, 1):
                role = ctx.guild.get_role(button["role_id"])
                role_name = role.mention if role else f"Unknown ({button['role_id']})"
                label = button.get("label", "No label")
                roles_text.append(f"{i}. {role_name} - `{label}`")
            
            if roles_text:
                embed.add_field(
                    name=f"Message ID: {message_id}",
                    value="\n".join(roles_text),
                    inline=False
                )
        
        await ctx.send(embed=embed)

    @buttonrole.command(name="reset")
    @commands.has_permissions(manage_guild=True, manage_roles=True)
    async def buttonrole_reset(self, ctx):
        """Clears every button role from guild"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config or not config.button_roles:
            return await self.bot.warn(ctx, "No button roles to clear")
        
        config.button_roles = {}
        await config.save()
        await self.bot.grant(ctx, "All button roles have been cleared")

    @buttonrole.command(name="removeall")
    @commands.has_permissions(manage_guild=True, manage_roles=True)
    async def buttonrole_removeall(self, ctx, message: discord.Message):
        """Remove all button roles from a message"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        message_id = str(message.id)
        
        if not config or message_id not in config.button_roles:
            return await self.bot.warn(ctx, "No button roles found for this message")
        
        del config.button_roles[message_id]
        await config.save()
        await self.bot.grant(ctx, f"Removed all button roles from [message]({message.jump_url})")

    @commands.group(name="reaction", invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def reaction_group(self, ctx):
        """Manage reaction triggers"""
        await ctx.send_help(ctx.command)

    @reaction_group.command(name="quick")
    @commands.has_permissions(manage_messages=True)
    async def reaction_quick(self, ctx, message: discord.Message, *, emoji: str):
        """Add a reaction(s) to a message"""
        try:
            await message.add_reaction(emoji)
            await self.bot.grant(ctx, f"Added reaction {emoji} to [message]({message.jump_url})")
        except Exception as e:
            await self.bot.warn(ctx, f"Failed to add reaction: {e}")

    @reaction_group.command(name="add")
    @commands.has_permissions(manage_guild=True)
    async def reaction_add(self, ctx, emoji: str, *, trigger_word: str):
        """Adds a reaction trigger to guild"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config:
            config = GuildConfig(guild_id=ctx.guild.id)
        
        if trigger_word.lower() not in config.reaction_triggers:
            config.reaction_triggers[trigger_word.lower()] = {
                "emoji": emoji,
                "created_by": ctx.author.id,
                "created_at": discord.utils.utcnow().isoformat()
            }
            await config.save()
            await self.bot.grant(ctx, f"Added reaction trigger: **{trigger_word}** → {emoji}")
        else:
            await self.bot.warn(ctx, f"Reaction trigger **{trigger_word}** already exists")

    @reaction_group.command(name="delete")
    @commands.has_permissions(manage_guild=True)
    async def reaction_delete(self, ctx, emoji: str, *, trigger_word: str):
        """Removes a reaction trigger in guild"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config or trigger_word.lower() not in config.reaction_triggers:
            return await self.bot.warn(ctx, f"Reaction trigger **{trigger_word}** not found")
        
        del config.reaction_triggers[trigger_word.lower()]
        await config.save()
        await self.bot.grant(ctx, f"Removed reaction trigger **{trigger_word}**")

    @reaction_group.command(name="deleteall")
    @commands.has_permissions(manage_guild=True)
    async def reaction_deleteall(self, ctx, *, trigger_word: str):
        """Removes every reaction trigger for a specific word"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config or trigger_word.lower() not in config.reaction_triggers:
            return await self.bot.warn(ctx, f"Reaction trigger **{trigger_word}** not found")
        
        del config.reaction_triggers[trigger_word.lower()]
        await config.save()
        await self.bot.grant(ctx, f"Removed all reactions for **{trigger_word}**")

    @reaction_group.command(name="clear")
    @commands.has_permissions(manage_guild=True)
    async def reaction_clear(self, ctx):
        """Removes every reaction trigger in guild"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config or not config.reaction_triggers:
            return await self.bot.warn(ctx, "No reaction triggers to clear")
        
        config.reaction_triggers = {}
        await config.save()
        await self.bot.grant(ctx, "All reaction triggers have been cleared")

    @reaction_group.command(name="list")
    async def reaction_list(self, ctx):
        """View a list of every reaction trigger in guild"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config or not config.reaction_triggers:
            return await self.bot.neutral(ctx, "No reaction triggers configured")
        
        embed = discord.Embed(title="Reaction Triggers", color=0x242429)
        for trigger, data in config.reaction_triggers.items():
            emoji = data.get("emoji", "Unknown")
            embed.add_field(name=f"{emoji} - {trigger}", value="\u200b", inline=False)
        
        embed.set_footer(text=f"Total: {len(config.reaction_triggers)}")
        await ctx.send(embed=embed)

    @reaction_group.command(name="owner")
    async def reaction_owner(self, ctx, *, trigger_word: str):
        """Gets the author of a trigger word"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config or trigger_word.lower() not in config.reaction_triggers:
            return await self.bot.warn(ctx, f"Reaction trigger **{trigger_word}** not found")
        
        data = config.reaction_triggers[trigger_word.lower()]
        creator_id = data.get("created_by")
        creator = ctx.guild.get_member(creator_id) if creator_id else None
        
        embed = discord.Embed(title=f"Reaction Trigger Owner", color=0x242429)
        embed.add_field(name="Trigger", value=trigger_word, inline=False)
        embed.add_field(name="Created by", value=creator.mention if creator else f"Unknown ({creator_id})", inline=False)
        embed.add_field(name="Created at", value=data.get("created_at", "Unknown"), inline=False)
        
        await ctx.send(embed=embed)

    @reaction_group.group(name="messages", invoke_without_command=True)
    async def reaction_messages(self, ctx, channel: discord.TextChannel = None, first: str = None, second: str = None, third: str = None):
        """Add or remove auto reaction on messages"""
        if channel is None or first is None:
            return await ctx.send_help(ctx.command)
        
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config:
            config = GuildConfig(guild_id=ctx.guild.id)
        
        channel_id = str(channel.id)
        if channel_id not in config.auto_reactions:
            config.auto_reactions[channel_id] = []
        
        emojis = [first]
        if second:
            emojis.append(second)
        if third:
            emojis.append(third)
        
        config.auto_reactions[channel_id] = emojis
        await config.save()
        await self.bot.grant(ctx, f"Set auto reactions for {channel.mention}")

    @reaction_messages.command(name="list")
    async def reaction_messages_list(self, ctx):
        """List auto reactions for all channels"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config or not config.auto_reactions:
            return await self.bot.neutral(ctx, "No auto reactions configured")
        
        embed = discord.Embed(title="Auto Reactions", color=0x242429)
        for channel_id, emojis in config.auto_reactions.items():
            channel = ctx.guild.get_channel(int(channel_id))
            channel_name = channel.mention if channel else f"Unknown ({channel_id})"
            embed.add_field(name=channel_name, value=" ".join(emojis), inline=False)
        
        await ctx.send(embed=embed)

    @commands.group(name="previousreact", invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def previousreact(self, ctx):
        """Manage previous reaction triggers"""
        await ctx.send_help(ctx.command)

    @previousreact.command(name="add")
    @commands.has_permissions(manage_guild=True)
    async def previousreact_add(self, ctx, emoji: str, *, trigger_word: str):
        """Adds a reaction trigger to guild"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config:
            config = GuildConfig(guild_id=ctx.guild.id)
        
        if trigger_word.lower() not in config.previous_react_triggers:
            config.previous_react_triggers[trigger_word.lower()] = {
                "emoji": emoji,
                "created_by": ctx.author.id,
                "created_at": discord.utils.utcnow().isoformat()
            }
            await config.save()
            await self.bot.grant(ctx, f"Added previous reaction trigger: **{trigger_word}** → {emoji}")
        else:
            await self.bot.warn(ctx, f"Previous reaction trigger **{trigger_word}** already exists")

    @previousreact.command(name="delete")
    @commands.has_permissions(manage_guild=True)
    async def previousreact_delete(self, ctx, emoji: str, *, trigger_word: str):
        """Removes a previous reaction trigger in guild"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config or trigger_word.lower() not in config.previous_react_triggers:
            return await self.bot.warn(ctx, f"Previous reaction trigger **{trigger_word}** not found")
        
        del config.previous_react_triggers[trigger_word.lower()]
        await config.save()
        await self.bot.grant(ctx, f"Removed previous reaction trigger **{trigger_word}**")

    @previousreact.command(name="deleteall")
    @commands.has_permissions(manage_guild=True)
    async def previousreact_deleteall(self, ctx, *, trigger_word: str):
        """Removes every reaction trigger for a specific word"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config or trigger_word.lower() not in config.previous_react_triggers:
            return await self.bot.warn(ctx, f"Previous reaction trigger **{trigger_word}** not found")
        
        del config.previous_react_triggers[trigger_word.lower()]
        await config.save()
        await self.bot.grant(ctx, f"Removed all previous reactions for **{trigger_word}**")

    @previousreact.command(name="clear")
    @commands.has_permissions(manage_guild=True)
    async def previousreact_clear(self, ctx):
        """Removes every previous reaction trigger in guild"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config or not config.previous_react_triggers:
            return await self.bot.warn(ctx, "No previous reaction triggers to clear")
        
        config.previous_react_triggers = {}
        await config.save()
        await self.bot.grant(ctx, "All previous reaction triggers have been cleared")

    @previousreact.command(name="list")
    async def previousreact_list(self, ctx):
        """View a list of every previous reaction trigger in guild"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config or not config.previous_react_triggers:
            return await self.bot.neutral(ctx, "No previous reaction triggers configured")
        
        embed = discord.Embed(title="Previous Reaction Triggers", color=0x242429)
        for trigger, data in config.previous_react_triggers.items():
            emoji = data.get("emoji", "Unknown")
            embed.add_field(name=f"{emoji} - {trigger}", value="\u200b", inline=False)
        
        embed.set_footer(text=f"Total: {len(config.previous_react_triggers)}")
        await ctx.send(embed=embed)

    @previousreact.command(name="owner")
    async def previousreact_owner(self, ctx, *, trigger_word: str):
        """Gets the author of a previous reaction trigger"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config or trigger_word.lower() not in config.previous_react_triggers:
            return await self.bot.warn(ctx, f"Previous reaction trigger **{trigger_word}** not found")
        
        data = config.previous_react_triggers[trigger_word.lower()]
        creator_id = data.get("created_by")
        creator = ctx.guild.get_member(creator_id) if creator_id else None
        
        embed = discord.Embed(title=f"Previous Reaction Trigger Owner", color=0x242429)
        embed.add_field(name="Trigger", value=trigger_word, inline=False)
        embed.add_field(name="Created by", value=creator.mention if creator else f"Unknown ({creator_id})", inline=False)
        embed.add_field(name="Created at", value=data.get("created_at", "Unknown"), inline=False)
        
        await ctx.send(embed=embed)


    @commands.group(name="noselfreact", invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def noselfreact(self, ctx):
        """Prevent self reactions on messages"""
        await ctx.send_help(ctx.command)

    @noselfreact.command(name="toggle")
    @commands.has_permissions(administrator=True)
    async def noselfreact_toggle(self, ctx, setting: str = None):
        """Toggle self-react monitoring"""
        if setting is None:
            return await ctx.send_help(ctx.command)
        
        setting = setting.lower()
        if setting not in ["on", "off", "true", "false", "enable", "disable"]:
            return await self.bot.warn(ctx, "Setting must be: on, off, true, false, enable, or disable")
        
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config:
            config = GuildConfig(guild_id=ctx.guild.id)
        
        is_enabled = setting in ["on", "true", "enable"]
        config.noselfreact_enabled = is_enabled
        await config.save()
        
        status = "enabled" if is_enabled else "disabled"
        await self.bot.grant(ctx, f"Self-react monitoring has been **{status}**")

    @noselfreact.command(name="bypass")
    @commands.is_owner()
    async def noselfreact_bypass(self, ctx, setting: str = None):
        """Allow staff to bypass self-react monitoring"""
        if setting is None:
            return await ctx.send_help(ctx.command)
        
        setting = setting.lower()
        if setting not in ["on", "off", "true", "false", "enable", "disable"]:
            return await self.bot.warn(ctx, "Setting must be: on, off, true, false, enable, or disable")
        
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config:
            config = GuildConfig(guild_id=ctx.guild.id)
        
        is_enabled = setting in ["on", "true", "enable"]
        config.noselfreact_staff_bypass = is_enabled
        await config.save()
        
        status = "enabled" if is_enabled else "disabled"
        await self.bot.grant(ctx, f"Staff bypass for self-react has been **{status}**")

    @noselfreact.command(name="exempt")
    @commands.has_permissions(administrator=True)
    async def noselfreact_exempt(self, ctx, *, target: str = None):
        """Exempt a member, channel or role from self-react punishments"""
        if not target:
            return await ctx.send_help(ctx.command)
        
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config:
            config = GuildConfig(guild_id=ctx.guild.id)
        
        if ctx.message.mentions:
            for member in ctx.message.mentions:
                if member.id not in config.noselfreact_exempt_members:
                    config.noselfreact_exempt_members.append(member.id)
            await config.save()
            await self.bot.grant(ctx, f"Exempted {len(ctx.message.mentions)} member(s)")
        
        elif ctx.message.channel_mentions:
            for channel in ctx.message.channel_mentions:
                if channel.id not in config.noselfreact_exempt_channels:
                    config.noselfreact_exempt_channels.append(channel.id)
            await config.save()
            await self.bot.grant(ctx, f"Exempted {len(ctx.message.channel_mentions)} channel(s)")
        
        elif ctx.message.role_mentions:
            for role in ctx.message.role_mentions:
                if role.id not in config.noselfreact_exempt_roles:
                    config.noselfreact_exempt_roles.append(role.id)
            await config.save()
            await self.bot.grant(ctx, f"Exempted {len(ctx.message.role_mentions)} role(s)")
        
        else:
            await self.bot.warn(ctx, "Please mention a member, channel, or role to exempt")

    @noselfreact.command(name="exempt list")
    async def noselfreact_exempt_list(self, ctx):
        """View all exempted members, channels and roles"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        
        if not config or (not config.noselfreact_exempt_members and not config.noselfreact_exempt_channels and not config.noselfreact_exempt_roles):
            return await self.bot.neutral(ctx, "No exemptions configured")
        
        embed = discord.Embed(title="Self-React Exemptions", color=0x242429)
        
        if config.noselfreact_exempt_members:
            members_text = []
            for mid in config.noselfreact_exempt_members:
                member = ctx.guild.get_member(mid)
                members_text.append(member.mention if member else f"Unknown ({mid})")
            embed.add_field(name="Members", value="\n".join(members_text), inline=False)
        
        if config.noselfreact_exempt_channels:
            channels_text = []
            for cid in config.noselfreact_exempt_channels:
                channel = ctx.guild.get_channel(cid)
                channels_text.append(channel.mention if channel else f"Unknown ({cid})")
            embed.add_field(name="Channels", value="\n".join(channels_text), inline=False)
        
        if config.noselfreact_exempt_roles:
            roles_text = []
            for rid in config.noselfreact_exempt_roles:
                role = ctx.guild.get_role(rid)
                roles_text.append(role.mention if role else f"Unknown ({rid})")
            embed.add_field(name="Roles", value="\n".join(roles_text), inline=False)
        
        await ctx.send(embed=embed)

    @noselfreact.command(name="emoji")
    @commands.has_permissions(administrator=True)
    async def noselfreact_emoji(self, ctx, *, emoji: str = None):
        """Add or remove emoji from self-react monitoring"""
        if emoji is None:
            return await ctx.send_help(ctx.command)
        
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config:
            config = GuildConfig(guild_id=ctx.guild.id)
        
        if emoji in config.noselfreact_monitored_emojis:
            config.noselfreact_monitored_emojis.remove(emoji)
            await config.save()
            await self.bot.grant(ctx, f"Removed {emoji} from self-react monitoring")
        else:
            config.noselfreact_monitored_emojis.append(emoji)
            await config.save()
            await self.bot.grant(ctx, f"Added {emoji} to self-react monitoring")

    @noselfreact.command(name="emoji list")
    async def noselfreact_emoji_list(self, ctx):
        """View list of monitored self-react emojis"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        
        if not config or not config.noselfreact_monitored_emojis:
            return await self.bot.neutral(ctx, "No emojis are being monitored for self-react")
        
        embed = discord.Embed(title="Monitored Self-React Emojis", color=0x242429)
        embed.description = " ".join(config.noselfreact_monitored_emojis)
        embed.set_footer(text=f"Total: {len(config.noselfreact_monitored_emojis)}")
        await ctx.send(embed=embed)

    @noselfreact.command(name="punishment")
    @commands.has_permissions(administrator=True)
    async def noselfreact_punishment(self, ctx, *, punishment: str = None):
        """Set the default punishment for self-reacts"""
        if punishment is None:
            return await ctx.send_help(ctx.command)
        
        punishment = punishment.lower()
        if punishment not in ["kick", "ban", "timeout", "warn"]:
            return await self.bot.warn(ctx, "Punishment must be: kick, ban, timeout, or warn")
        
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config:
            config = GuildConfig(guild_id=ctx.guild.id)
        
        config.noselfreact_punishment = punishment
        await config.save()
        await self.bot.grant(ctx, f"Self-react punishment set to **{punishment}**")

    @commands.group(name="gallery", invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def gallery(self, ctx):
        """Restrict channels to only allow attachments"""
        await ctx.send_help(ctx.command)

    @gallery.command(name="add")
    @commands.has_permissions(manage_guild=True)
    async def gallery_add(self, ctx, channel: discord.TextChannel):
        """Add a channel which only allows attachments"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config:
            config = GuildConfig(guild_id=ctx.guild.id)
        
        if channel.id not in config.gallery_channels:
            config.gallery_channels.append(channel.id)
            await config.save()
            await self.bot.grant(ctx, f"Added {channel.mention} to gallery restrictions")
        else:
            await self.bot.warn(ctx, f"{channel.mention} is already a gallery channel")

    @gallery.command(name="remove", aliases=["rm"])
    @commands.has_permissions(manage_guild=True)
    async def gallery_remove(self, ctx, channel: discord.TextChannel):
        """Remove a channel from the gallery restriction"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config or channel.id not in config.gallery_channels:
            return await self.bot.warn(ctx, f"{channel.mention} is not a gallery channel")
        
        config.gallery_channels.remove(channel.id)
        await config.save()
        await self.bot.grant(ctx, f"Removed {channel.mention} from gallery restrictions")

    @gallery.command(name="clear")
    @commands.has_permissions(manage_guild=True)
    async def gallery_clear(self, ctx):
        """Remove all channels from the gallery restriction"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config or not config.gallery_channels:
            return await self.bot.warn(ctx, "No gallery channels to clear")
        
        config.gallery_channels = []
        await config.save()
        await self.bot.grant(ctx, "All gallery restrictions have been cleared")

    @gallery.command(name="list")
    async def gallery_list(self, ctx):
        """View all gallery channels"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        
        if not config or not config.gallery_channels:
            return await self.bot.neutral(ctx, "No gallery channels configured")
        
        channels_text = []
        for channel_id in config.gallery_channels:
            channel = ctx.guild.get_channel(channel_id)
            channels_text.append(f"• {channel.mention if channel else f'Unknown ({channel_id})'}")
        
        embed = discord.Embed(title="Gallery Channels", color=0x242429, description="\n".join(channels_text))
        embed.set_footer(text=f"Total: {len(config.gallery_channels)}")
        await ctx.send(embed=embed)

    @commands.group(name="starboard", aliases=["sb"], invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    async def starboard(self, ctx):
        """Showcase the best messages in your server"""
        await ctx.send_help(ctx.command)

    @starboard.command(name="set")
    @commands.has_permissions(manage_guild=True)
    async def starboard_set(self, ctx, channel: discord.TextChannel = None):
        """Sets the channel where starboard messages will be sent to"""
        if not channel:
            return await ctx.send_help(ctx.command)
        
        try:
            config = await StarboardConfig.find_one(StarboardConfig.guild_id == ctx.guild.id)
            if not config:
                config = StarboardConfig(guild_id=ctx.guild.id, starboard_channel_id=channel.id)
            else:
                config.starboard_channel_id = channel.id
            
            await config.save()
            await self.bot.grant(ctx, f"Starboard channel set to {channel.mention}")
        except Exception as e:
            await self.bot.warn(ctx, f"Failed to set starboard channel: {e}")

    @starboard.command(name="unlock")
    @commands.has_permissions(manage_guild=True)
    async def starboard_unlock(self, ctx):
        """Enables/unlocks starboard from operating"""
        try:
            config = await StarboardConfig.find_one(StarboardConfig.guild_id == ctx.guild.id)
            if not config:
                config = StarboardConfig(guild_id=ctx.guild.id, locked=False)
            else:
                config.locked = False
            
            await config.save()
            await self.bot.grant(ctx, "Starboard unlocked!")
        except Exception as e:
            await self.bot.warn(ctx, f"Failed to unlock starboard: {e}")

    @starboard.command(name="lock")
    @commands.has_permissions(manage_guild=True)
    async def starboard_lock(self, ctx):
        """Disables/locks starboard from operating"""
        try:
            config = await StarboardConfig.find_one(StarboardConfig.guild_id == ctx.guild.id)
            if not config:
                config = StarboardConfig(guild_id=ctx.guild.id, locked=True)
            else:
                config.locked = True
            
            await config.save()
            await self.bot.grant(ctx, "Starboard locked!")
        except Exception as e:
            await self.bot.warn(ctx, f"Failed to lock starboard: {e}")

    @starboard.command(name="selfstar")
    @commands.has_permissions(manage_guild=True)
    async def starboard_selfstar(self, ctx, setting: str = None):
        """Allow an author to star their own message"""
        if setting is None:
            return await ctx.send_help(ctx.command)
        
        setting = setting.lower()
        if setting not in ["on", "off", "true", "false", "enable", "disable"]:
            return await self.bot.warn(ctx, "Setting must be: on, off, true, false, enable, or disable")
        
        try:
            is_enabled = setting in ["on", "true", "enable"]
            config = await StarboardConfig.find_one(StarboardConfig.guild_id == ctx.guild.id)
            if not config:
                config = StarboardConfig(guild_id=ctx.guild.id, allow_self_star=is_enabled)
            else:
                config.allow_self_star = is_enabled
            
            await config.save()
            status = "enabled" if is_enabled else "disabled"
            await self.bot.grant(ctx, f"Self-starring {status}!")
        except Exception as e:
            await self.bot.warn(ctx, f"Failed to update selfstar: {e}")

    @starboard.command(name="emoji")
    @commands.has_permissions(manage_guild=True)
    async def starboard_emoji(self, ctx, emoji: str = None):
        """Sets the emoji that triggers the starboard messages"""
        if not emoji:
            return await ctx.send_help(ctx.command)
        
        try:
            config = await StarboardConfig.find_one(StarboardConfig.guild_id == ctx.guild.id)
            if not config:
                config = StarboardConfig(guild_id=ctx.guild.id, emoji=emoji)
            else:
                config.emoji = emoji
            
            await config.save()
            await self.bot.grant(ctx, f"Starboard emoji set to {emoji}")
        except Exception as e:
            await self.bot.warn(ctx, f"Failed to set emoji: {e}")

    @starboard.command(name="color")
    @commands.has_permissions(manage_guild=True)
    async def starboard_color(self, ctx, color: str = None):
        """Set default color for starboard posts"""
        if not color:
            return await ctx.send_help(ctx.command)
        
        try:
            if color.startswith('#'):
                color = color[1:]
            
            color_int = int(color, 16)
            config = await StarboardConfig.find_one(StarboardConfig.guild_id == ctx.guild.id)
            if not config:
                config = StarboardConfig(guild_id=ctx.guild.id, color=color_int)
            else:
                config.color = color_int
            
            await config.save()
            await self.bot.grant(ctx, f"Starboard color set to `#{color.upper()}`")
        except ValueError:
            await self.bot.warn(ctx, "Invalid color format. Use HEX (#RRGGBB)")
        except Exception as e:
            await self.bot.warn(ctx, f"Failed to set color: {e}")

    @starboard.command(name="jumpurl")
    @commands.has_permissions(manage_guild=True)
    async def starboard_jumpurl(self, ctx, setting: str = None):
        """Allow the jump URL to appear on a Starboard post"""
        if setting is None:
            return await ctx.send_help(ctx.command)
        
        setting = setting.lower()
        if setting not in ["on", "off", "true", "false", "enable", "disable"]:
            return await self.bot.warn(ctx, "Setting must be: on, off, true, false, enable, or disable")
        
        try:
            is_enabled = setting in ["on", "true", "enable"]
            config = await StarboardConfig.find_one(StarboardConfig.guild_id == ctx.guild.id)
            if not config:
                config = StarboardConfig(guild_id=ctx.guild.id, allow_jump_url=is_enabled)
            else:
                config.allow_jump_url = is_enabled
            
            await config.save()
            status = "enabled" if is_enabled else "disabled"
            await self.bot.grant(ctx, f"Jump URL {status}!")
        except Exception as e:
            await self.bot.warn(ctx, f"Failed to update jumpurl: {e}")

    @starboard.command(name="threshold")
    async def starboard_threshold(self, ctx, threshold: int = None):
        """Sets the default amount stars needed to post"""
        if threshold is None:
            return await ctx.send_help(ctx.command)
        
        if threshold < 1:
            return await self.bot.warn(ctx, "Threshold must be at least 1")
        
        try:
            config = await StarboardConfig.find_one(StarboardConfig.guild_id == ctx.guild.id)
            if not config:
                config = StarboardConfig(guild_id=ctx.guild.id, threshold=threshold)
            else:
                config.threshold = threshold
            
            await config.save()
            await self.bot.grant(ctx, f"Starboard threshold set to {threshold}")
        except Exception as e:
            await self.bot.warn(ctx, f"Failed to set threshold: {e}")

    @starboard.command(name="timestamp")
    @commands.has_permissions(manage_guild=True)
    async def starboard_timestamp(self, ctx, setting: str = None):
        """Allow a timestamp to appear on a Starboard post"""
        if setting is None:
            return await ctx.send_help(ctx.command)
        
        setting = setting.lower()
        if setting not in ["on", "off", "true", "false", "enable", "disable"]:
            return await self.bot.warn(ctx, "Setting must be: on, off, true, false, enable, or disable")
        
        try:
            is_enabled = setting in ["on", "true", "enable"]
            config = await StarboardConfig.find_one(StarboardConfig.guild_id == ctx.guild.id)
            if not config:
                config = StarboardConfig(guild_id=ctx.guild.id, allow_timestamp=is_enabled)
            else:
                config.allow_timestamp = is_enabled
            
            await config.save()
            status = "enabled" if is_enabled else "disabled"
            await self.bot.grant(ctx, f"Timestamp {status}!")
        except Exception as e:
            await self.bot.warn(ctx, f"Failed to update timestamp: {e}")

    @starboard.command(name="attachments")
    @commands.has_permissions(manage_guild=True)
    async def starboard_attachments(self, ctx, setting: str = None):
        """Allow attachments to appear on Starboard posts"""
        if setting is None:
            return await ctx.send_help(ctx.command)
        
        setting = setting.lower()
        if setting not in ["on", "off", "true", "false", "enable", "disable"]:
            return await self.bot.warn(ctx, "Setting must be: on, off, true, false, enable, or disable")
        
        try:
            is_enabled = setting in ["on", "true", "enable"]
            config = await StarboardConfig.find_one(StarboardConfig.guild_id == ctx.guild.id)
            if not config:
                config = StarboardConfig(guild_id=ctx.guild.id, allow_attachments=is_enabled)
            else:
                config.allow_attachments = is_enabled
            
            await config.save()
            status = "enabled" if is_enabled else "disabled"
            await self.bot.grant(ctx, f"Attachments {status}!")
        except Exception as e:
            await self.bot.warn(ctx, f"Failed to update attachments: {e}")

    @starboard.command(name="ignore")
    @commands.has_permissions(manage_guild=True)
    async def starboard_ignore(self, ctx, target: str = None):
        """Ignore a channel, members or roles for new stars"""
        if not target:
            return await ctx.send_help(ctx.command)
        
        try:
            config = await StarboardConfig.find_one(StarboardConfig.guild_id == ctx.guild.id)
            if not config:
                config = StarboardConfig(guild_id=ctx.guild.id)
            
            if ctx.message.channel_mentions:
                channel = ctx.message.channel_mentions[0]
                if channel.id not in config.ignored_channels:
                    config.ignored_channels.append(channel.id)
                    await config.save()
                    await self.bot.grant(ctx, f"Ignored channel {channel.mention}")
                else:
                    await self.bot.warn(ctx, f"Channel {channel.mention} is already ignored")
            
            elif ctx.message.mentions:
                for member in ctx.message.mentions:
                    if member.id not in config.ignored_members:
                        config.ignored_members.append(member.id)
                await config.save()
                await self.bot.grant(ctx, f"Ignored {len(ctx.message.mentions)} member(s)")
            
            elif ctx.message.role_mentions:
                for role in ctx.message.role_mentions:
                    if role.id not in config.ignored_roles:
                        config.ignored_roles.append(role.id)
                await config.save()
                await self.bot.grant(ctx, f"Ignored {len(ctx.message.role_mentions)} role(s)")
            
            else:
                await self.bot.warn(ctx, "Please mention a channel, member, or role to ignore")
        
        except Exception as e:
            await self.bot.warn(ctx, f"Failed to ignore: {e}")

    @starboard.command(name="ignore list")
    @commands.has_permissions(manage_guild=True)
    async def starboard_ignore_list(self, ctx):
        """View ignored roles, members and channels for Starboard"""
        try:
            config = await StarboardConfig.find_one(StarboardConfig.guild_id == ctx.guild.id)
            
            if not config or (not config.ignored_channels and not config.ignored_members and not config.ignored_roles):
                return await self.bot.neutral(ctx, "No ignored channels, members, or roles")
            
            embed = discord.Embed(title="Starboard Ignore List", color=0xffd700)
            
            if config.ignored_channels:
                channels_text = ", ".join([f"<#{cid}>" for cid in config.ignored_channels])
                embed.add_field(name="Ignored Channels", value=channels_text, inline=False)
            
            if config.ignored_members:
                members_text = ", ".join([f"<@{mid}>" for mid in config.ignored_members])
                embed.add_field(name="Ignored Members", value=members_text, inline=False)
            
            if config.ignored_roles:
                roles_text = ", ".join([f"<@&{rid}>" for rid in config.ignored_roles])
                embed.add_field(name="Ignored Roles", value=roles_text, inline=False)
            
            await ctx.send(embed=embed)
        
        except Exception as e:
            await self.bot.warn(ctx, f"Failed to get ignore list: {e}")

    @starboard.command(name="reset")
    @commands.has_permissions(manage_guild=True)
    async def starboard_reset(self, ctx):
        """Resets guild's configuration for starboard"""
        try:
            config = await StarboardConfig.find_one(StarboardConfig.guild_id == ctx.guild.id)
            if config:
                await config.delete()
            
            await self.bot.grant(ctx, "Starboard configuration reset!")
        except Exception as e:
            await self.bot.warn(ctx, f"Failed to reset: {e}")

    @starboard.command(name="config")
    async def starboard_config(self, ctx):
        """View the settings for starboard in guild"""
        try:
            config = await StarboardConfig.find_one(StarboardConfig.guild_id == ctx.guild.id)
            
            if not config:
                return await self.bot.neutral(ctx, "Starboard not configured. Use `;starboard set <channel>`")
            
            channel = ctx.guild.get_channel(config.starboard_channel_id) if config.starboard_channel_id else None
            
            embed = discord.Embed(title="Starboard Configuration", color=config.color)
            embed.add_field(name="Channel", value=channel.mention if channel else "Not set", inline=True)
            embed.add_field(name="Emoji", value=config.emoji, inline=True)
            embed.add_field(name="Threshold", value=str(config.threshold), inline=True)
            embed.add_field(name="Status", value="🔒 Locked" if config.locked else "🔓 Unlocked", inline=True)
            embed.add_field(name="Self-Star", value="✅ Allowed" if config.allow_self_star else "❌ Not allowed", inline=True)
            embed.add_field(name="Jump URL", value="✅ Shown" if config.allow_jump_url else "❌ Hidden", inline=True)
            embed.add_field(name="Timestamp", value="✅ Shown" if config.allow_timestamp else "❌ Hidden", inline=True)
            embed.add_field(name="Attachments", value="✅ Shown" if config.allow_attachments else "❌ Hidden", inline=True)
            
            await ctx.send(embed=embed)
        
        except Exception as e:
            await self.bot.warn(ctx, f"Failed to get config: {e}")


    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        """Handle reaction additions for starboard"""
        try:
            guild = self.bot.get_guild(payload.guild_id)
            if not guild:
                return
            
            config = await StarboardConfig.find_one(StarboardConfig.guild_id == guild.id)
            if not config or config.locked or not config.starboard_channel_id:
                return
            
            # Check if reaction emoji matches starboard emoji
            if str(payload.emoji) != config.emoji:
                return
            
            # Check if channel is ignored
            if payload.channel_id in config.ignored_channels:
                return
            
            channel = guild.get_channel(payload.channel_id)
            if not channel:
                return
            
            try:
                message = await channel.fetch_message(payload.message_id)
            except:
                return
            
            if payload.user_id in config.ignored_members:
                return
            
            author = message.author
            if author and hasattr(author, 'roles'):
                for role in author.roles:
                    if role.id in config.ignored_roles:
                        return
            
            if not config.allow_self_star and payload.user_id == message.author.id:
                return
            
            reaction_count = sum(1 for reaction in message.reactions if str(reaction.emoji) == config.emoji)
            
            starboard_post = await StarboardPost.find_one(StarboardPost.original_message_id == message.id)
            
            starboard_channel = guild.get_channel(config.starboard_channel_id)
            if not starboard_channel:
                return
            
            if not starboard_post:
                if reaction_count >= config.threshold:
                    embed = self._create_starboard_embed(message, config, reaction_count)
                    content = f"{config.emoji} - #{reaction_count}"
                    view = StarboardJumpView(message.jump_url) if config.allow_jump_url else None
                    starboard_message = await starboard_channel.send(content=content, embed=embed, view=view)
                    starboard_post = StarboardPost(
                        guild_id=guild.id,
                        original_message_id=message.id,
                        starboard_message_id=starboard_message.id,
                        original_channel_id=message.channel.id,
                        author_id=message.author.id,
                        reaction_count=reaction_count
                    )
                    await starboard_post.save()
            else:
                if reaction_count >= config.threshold:
                    try:
                        starboard_message = await starboard_channel.fetch_message(starboard_post.starboard_message_id)
                        embed = self._create_starboard_embed(message, config, reaction_count)
                        content = f"{config.emoji} - #{reaction_count}"
                        view = StarboardJumpView(message.jump_url) if config.allow_jump_url else None
                        await starboard_message.edit(content=content, embed=embed, view=view)
                        starboard_post.reaction_count = reaction_count
                        await starboard_post.save()
                    except:
                        pass
                elif reaction_count < config.threshold:
                    # Delete from starboard if below threshold
                    try:
                        starboard_message = await starboard_channel.fetch_message(starboard_post.starboard_message_id)
                        await starboard_message.delete()
                        await starboard_post.delete()
                    except:
                        pass
        
        except Exception as e:
            pass 

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        """Handle reaction removals for starboard"""
        try:
            guild = self.bot.get_guild(payload.guild_id)
            if not guild:
                return
            
            config = await StarboardConfig.find_one(StarboardConfig.guild_id == guild.id)
            if not config or not config.starboard_channel_id:
                return
            
            if str(payload.emoji) != config.emoji:
                return
            
            channel = guild.get_channel(payload.channel_id)
            if not channel:
                return
            
            try:
                message = await channel.fetch_message(payload.message_id)
            except:
                return

            reaction_count = sum(1 for reaction in message.reactions if str(reaction.emoji) == config.emoji)
            
            starboard_post = await StarboardPost.find_one(StarboardPost.original_message_id == message.id)
            
            if starboard_post:
                starboard_channel = guild.get_channel(config.starboard_channel_id)
                if not starboard_channel:
                    return
                
                if reaction_count >= config.threshold:
                    try:
                        starboard_message = await starboard_channel.fetch_message(starboard_post.starboard_message_id)
                        embed = self._create_starboard_embed(message, config, reaction_count)
                        content = f"{config.emoji} **{reaction_count}** | {message.channel.mention}"
                        view = StarboardJumpView(message.jump_url) if config.allow_jump_url else None
                        await starboard_message.edit(content=content, embed=embed, view=view)
                        starboard_post.reaction_count = reaction_count
                        await starboard_post.save()
                    except:
                        pass
                else:
                    try:
                        starboard_message = await starboard_channel.fetch_message(starboard_post.starboard_message_id)
                        await starboard_message.delete()
                        await starboard_post.delete()
                    except:
                        await starboard_post.delete()
        
        except Exception as e:
            pass 

    def _create_starboard_embed(self, message, config, reaction_count):
        """Create a starboard embed for a message"""
        embed = discord.Embed(
            description=message.content or "No content",
            color=config.color,
            timestamp=message.created_at if config.allow_timestamp else None
        )
        embed.set_author(
            name=message.author.name,
            icon_url=message.author.avatar.url if message.author.avatar else None
        )
        
        if config.allow_attachments and message.attachments:
            first_image = next((a for a in message.attachments if a.content_type and a.content_type.startswith('image')), None)
            if first_image:
                embed.set_image(url=first_image.url)
        
        timestamp_str = f" - {message.created_at.strftime('%m/%d/%Y %H:%M:%S')}" if config.allow_timestamp else ""
        embed.set_footer(text=f"#{message.channel.name}{timestamp_str}")
        
        return embed

    @commands.group(name="antinuke", aliases=["an"], invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def antinuke(self, ctx):
        """Anti-nuke command group for protecting your server"""
        await ctx.send_help(ctx.command)

    @antinuke.command(name="status")
    @commands.has_permissions(administrator=True)
    async def antinuke_status(self, ctx):
        """Show antinuke status"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config:
            config = GuildConfig(guild_id=ctx.guild.id)
            await config.save()
        
        enabled = "✅ Enabled" if config.antinuke_enabled else "❌ Disabled"
        action = config.antinuke_action.capitalize() if config.antinuke_action else "Ban"
        threshold = config.antinuke_threshold
        
        embed = discord.Embed(
            title="Antinuke System",
            color=0x57F287 if config.antinuke_enabled else 0xED4245,
            description=f"**Status:** {enabled}\n**Action:** {action}\n**Threshold:** {threshold} violations"
        )
        await ctx.send(embed=embed)

    @antinuke.command(name="toggle", aliases=["t", "setup", "switch"])
    @commands.has_permissions(administrator=True)
    async def antinuke_toggle(self, ctx, state: str = None):
        """Toggle antinuke on/off"""
        if not state:
            return await ctx.send_help(ctx.command)
        
        enabled = state.lower() in ["on", "enable", "true"]
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id) or GuildConfig(guild_id=ctx.guild.id)
        config.antinuke_enabled = enabled
        await config.save()
        
        e = discord.Embed(description=f"⚔️ Antinuke **{'enabled' if enabled else 'disabled'}**", color=0x57F287 if enabled else 0xED4245)
        await ctx.send(embed=e)

    @antinuke.command(name="all", aliases=["everything"])
    @commands.has_permissions(administrator=True)
    async def antinuke_all(self, ctx, state: str = None):
        """Toggle all antinuke protections"""
        if not state:
            return await ctx.send_help(ctx.command)
        
        enabled = state.lower() in ["on", "enable", "true"]
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id) or GuildConfig(guild_id=ctx.guild.id)
        
        for module in config.antinuke_modules:
            config.antinuke_modules[module] = enabled
        
        await config.save()
        e = discord.Embed(description=f"🔐 All modules **{'enabled' if enabled else 'disabled'}**", color=0x57F287 if enabled else 0xED4245)
        await ctx.send(embed=e)

    @antinuke.command(name="ban")
    @commands.has_permissions(administrator=True)
    async def antinuke_ban(self, ctx, state: str = None):
        """Toggle ban protection"""
        if not state:
            return await ctx.send_help(ctx.command)
        
        enabled = state.lower() in ["on", "enable", "true"]
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id) or GuildConfig(guild_id=ctx.guild.id)
        config.antinuke_modules["ban"] = enabled
        await config.save()
        
        e = discord.Embed(description=f"🚫 Ban protection **{'enabled' if enabled else 'disabled'}**", color=0x57F287 if enabled else 0xED4245)
        await ctx.send(embed=e)

    @antinuke.command(name="kick")
    @commands.has_permissions(administrator=True)
    async def antinuke_kick(self, ctx, state: str = None):
        """Toggle kick protection"""
        if not state:
            return await ctx.send_help(ctx.command)
        
        enabled = state.lower() in ["on", "enable", "true"]
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id) or GuildConfig(guild_id=ctx.guild.id)
        config.antinuke_modules["kick"] = enabled
        await config.save()
        
        e = discord.Embed(description=f"🥵 Kick protection **{'enabled' if enabled else 'disabled'}**", color=0x57F287 if enabled else 0xED4245)
        await ctx.send(embed=e)

    @antinuke.command(name="prune")
    @commands.has_permissions(administrator=True)
    async def antinuke_prune(self, ctx, state: str = None):
        """Toggle prune protection"""
        if not state:
            return await ctx.send_help(ctx.command)
        
        enabled = state.lower() in ["on", "enable", "true"]
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id) or GuildConfig(guild_id=ctx.guild.id)
        config.antinuke_modules["prune"] = enabled
        await config.save()
        
        e = discord.Embed(description=f"🗑️ Prune protection **{'enabled' if enabled else 'disabled'}**", color=0x57F287 if enabled else 0xED4245)
        await ctx.send(embed=e)

    @antinuke.command(name="channelcreate", aliases=["channel_create"])
    @commands.has_permissions(administrator=True)
    async def antinuke_channelcreate(self, ctx, state: str = None):
        """Toggle channel create protection"""
        if not state:
            return await ctx.send_help(ctx.command)
        
        enabled = state.lower() in ["on", "enable", "true"]
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id) or GuildConfig(guild_id=ctx.guild.id)
        config.antinuke_modules["channelcreate"] = enabled
        await config.save()
        
        e = discord.Embed(description=f"🌟 Channel create **{'enabled' if enabled else 'disabled'}**", color=0x57F287 if enabled else 0xED4245)
        await ctx.send(embed=e)

    @antinuke.command(name="channel", aliases=["channels", "channel_update"])
    @commands.has_permissions(administrator=True)
    async def antinuke_channel(self, ctx, state: str = None):
        """Toggle channel edit protection"""
        if not state:
            return await ctx.send_help(ctx.command)
        
        enabled = state.lower() in ["on", "enable", "true"]
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id) or GuildConfig(guild_id=ctx.guild.id)
        config.antinuke_modules["channel"] = enabled
        await config.save()
        
        e = discord.Embed(description=f"📁 Channel edit **{'enabled' if enabled else 'disabled'}**", color=0x57F287 if enabled else 0xED4245)
        await ctx.send(embed=e)

    @antinuke.command(name="rolecreate", aliases=["role_create"])
    @commands.has_permissions(administrator=True)
    async def antinuke_rolecreate(self, ctx, state: str = None):
        """Toggle role create protection"""
        if not state:
            return await ctx.send_help(ctx.command)
        
        enabled = state.lower() in ["on", "enable", "true"]
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id) or GuildConfig(guild_id=ctx.guild.id)
        config.antinuke_modules["rolecreate"] = enabled
        await config.save()
        
        e = discord.Embed(description=f"🗝️ Role create **{'enabled' if enabled else 'disabled'}**", color=0x57F287 if enabled else 0xED4245)
        await ctx.send(embed=e)

    @antinuke.command(name="role", aliases=["roles", "role_update"])
    @commands.has_permissions(administrator=True)
    async def antinuke_role(self, ctx, state: str = None):
        """Toggle role edit protection"""
        if not state:
            return await ctx.send_help(ctx.command)
        
        enabled = state.lower() in ["on", "enable", "true"]
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id) or GuildConfig(guild_id=ctx.guild.id)
        config.antinuke_modules["role"] = enabled
        await config.save()
        
        e = discord.Embed(description=f"📄 Role edit **{'enabled' if enabled else 'disabled'}**", color=0x57F287 if enabled else 0xED4245)
        await ctx.send(embed=e)

    @antinuke.command(name="botadd", aliases=["bot", "ba"])
    @commands.has_permissions(administrator=True)
    async def antinuke_botadd(self, ctx, state: str = None):
        """Toggle bot add protection"""
        if not state:
            return await ctx.send_help(ctx.command)
        
        enabled = state.lower() in ["on", "enable", "true"]
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id) or GuildConfig(guild_id=ctx.guild.id)
        config.antinuke_modules["botadd"] = enabled
        await config.save()
        
        e = discord.Embed(description=f"🤖 Bot add **{'enabled' if enabled else 'disabled'}**", color=0x57F287 if enabled else 0xED4245)
        await ctx.send(embed=e)

    @antinuke.command(name="emoji", aliases=["emojis", "emoji_update", "emoji_create", "sticker", "stickers", "sticker_update", "sticker_create"])
    @commands.has_permissions(administrator=True)
    async def antinuke_emoji(self, ctx, state: str = None):
        """Toggle emoji protection"""
        if not state:
            return await ctx.send_help(ctx.command)
        
        enabled = state.lower() in ["on", "enable", "true"]
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id) or GuildConfig(guild_id=ctx.guild.id)
        config.antinuke_modules["emoji"] = enabled
        await config.save()
        
        e = discord.Embed(description=f"😀 Emoji **{'enabled' if enabled else 'disabled'}**", color=0x57F287 if enabled else 0xED4245)
        await ctx.send(embed=e)

    @antinuke.command(name="integration", aliases=["integrations"])
    @commands.has_permissions(administrator=True)
    async def antinuke_integration(self, ctx, state: str = None):
        """Toggle integration protection"""
        if not state:
            return await ctx.send_help(ctx.command)
        
        enabled = state.lower() in ["on", "enable", "true"]
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id) or GuildConfig(guild_id=ctx.guild.id)
        config.antinuke_modules["integration"] = enabled
        await config.save()
        
        e = discord.Embed(description=f"🔗 Integration **{'enabled' if enabled else 'disabled'}**", color=0x57F287 if enabled else 0xED4245)
        await ctx.send(embed=e)

    @antinuke.command(name="webhooks")
    @commands.has_permissions(administrator=True)
    async def antinuke_webhooks(self, ctx, state: str = None):
        """Toggle webhook protection"""
        if not state:
            return await ctx.send_help(ctx.command)
        
        enabled = state.lower() in ["on", "enable", "true"]
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id) or GuildConfig(guild_id=ctx.guild.id)
        config.antinuke_modules["webhooks"] = enabled
        await config.save()
        
        e = discord.Embed(description=f"🪣 Webhooks **{'enabled' if enabled else 'disabled'}**", color=0x57F287 if enabled else 0xED4245)
        await ctx.send(embed=e)

    @antinuke.command(name="guild", aliases=["guild_update"])
    @commands.has_permissions(administrator=True)
    async def antinuke_guild(self, ctx, state: str = None):
        """Toggle guild edit protection"""
        if not state:
            return await ctx.send_help(ctx.command)
        
        enabled = state.lower() in ["on", "enable", "true"]
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id) or GuildConfig(guild_id=ctx.guild.id)
        config.antinuke_modules["guild"] = enabled
        await config.save()
        
        e = discord.Embed(description=f"🏛️ Guild edit **{'enabled' if enabled else 'disabled'}**", color=0x57F287 if enabled else 0xED4245)
        await ctx.send(embed=e)

    @antinuke.command(name="vanity", aliases=["vanityurl", "url"])
    @commands.has_permissions(administrator=True)
    async def antinuke_vanity(self, ctx, state: str = None):
        """Toggle vanity URL protection"""
        if not state:
            return await ctx.send_help(ctx.command)
        
        enabled = state.lower() in ["on", "enable", "true"]
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id) or GuildConfig(guild_id=ctx.guild.id)
        config.antinuke_modules["vanity"] = enabled
        await config.save()
        
        e = discord.Embed(description=f"🔗 Vanity URL **{'enabled' if enabled else 'disabled'}**", color=0x57F287 if enabled else 0xED4245)
        await ctx.send(embed=e)

    @antinuke.command(name="punishment", aliases=["punish"])
    @commands.has_permissions(administrator=True)
    async def antinuke_punishment(self, ctx, punishment: str = None):
        """Set the punishment for antinuke violations"""
        if not punishment or punishment.lower() not in ["ban", "kick", "timeout"]:
            return await ctx.send_help(ctx.command)
        
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id) or GuildConfig(guild_id=ctx.guild.id)
        config.antinuke_action = punishment.lower()
        await config.save()
        
        e = discord.Embed(description=f"🔨 Punishment set to **{punishment.lower()}**", color=0x57F287)
        await ctx.send(embed=e)

    @antinuke.command(name="threshold")
    @commands.has_permissions(administrator=True)
    async def antinuke_threshold(self, ctx, threshold: int = None):
        """Set the violation threshold"""
        if threshold is None or threshold < 1 or threshold > 100:
            return await ctx.send_help(ctx.command)
        
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id) or GuildConfig(guild_id=ctx.guild.id)
        config.antinuke_threshold = threshold
        await config.save()
        
        e = discord.Embed(description=f"📊 Threshold set to **{threshold}**", color=0x57F287)
        await ctx.send(embed=e)

    @antinuke.command(name="whitelist", aliases=["wl"])
    @commands.has_permissions(administrator=True)
    async def antinuke_whitelist(self, ctx, user: discord.User = None):
        """Whitelist a user from antinuke"""
        if not user:
            return await ctx.send_help(ctx.command)
        
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id) or GuildConfig(guild_id=ctx.guild.id)
        
        if user.id in config.antinuke_whitelist:
            e = discord.Embed(description=f"⚠️ **{user.display_name}** already whitelisted", color=0xED4245)
            return await ctx.send(embed=e)
        
        config.antinuke_whitelist.append(user.id)
        await config.save()
        
        e = discord.Embed(description=f"🤍 **{user.display_name}** whitelisted", color=0x57F287)
        await ctx.send(embed=e)

    @antinuke.command(name="whitelisted", aliases=["whitelists", "wld"])
    @commands.has_permissions(administrator=True)
    async def antinuke_whitelisted(self, ctx):
        """List whitelisted users"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        
        if not config or not config.antinuke_whitelist:
            e = discord.Embed(description="No users whitelisted", color=0xED4245)
            return await ctx.send(embed=e)
        
        desc = f"**{len(config.antinuke_whitelist)} whitelisted**\n\n"
        for uid in config.antinuke_whitelist[:10]:
            try:
                user = await self.bot.fetch_user(uid)
                desc += f"• {user.mention}\n"
            except:
                desc += f"• Unknown ({uid})\n"
        
        if len(config.antinuke_whitelist) > 10:
            desc += f"... and {len(config.antinuke_whitelist) - 10} more"
        
        e = discord.Embed(title="🤍 Whitelisted", description=desc, color=0x57F287)
        await ctx.send(embed=e)

    @antinuke.command(name="trust", aliases=["admin"])
    @commands.has_permissions(administrator=True)
    async def antinuke_trust(self, ctx, user: discord.User = None):
        """Permit a user to use antinuke commands as an admin"""
        if not user:
            return await ctx.send_help(ctx.command)
        
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id) or GuildConfig(guild_id=ctx.guild.id)
        
        if user.id in config.antinuke_trusted:
            e = discord.Embed(description=f"⚠️ **{user.display_name}** already trusted", color=0xED4245)
            return await ctx.send(embed=e)
        
        config.antinuke_trusted.append(user.id)
        await config.save()
        
        e = discord.Embed(description=f"🛡️ **{user.display_name}** is now trusted", color=0x57F287)
        await ctx.send(embed=e)

    @antinuke.command(name="trusted", aliases=["admins"])
    @commands.has_permissions(administrator=True)
    async def antinuke_trusted(self, ctx):
        """List trusted admins"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        
        if not config or not config.antinuke_trusted:
            e = discord.Embed(description="No trusted admins", color=0xED4245)
            return await ctx.send(embed=e)
        
        desc = f"**{len(config.antinuke_trusted)} trusted**\n\n"
        for uid in config.antinuke_trusted[:10]:
            try:
                user = await self.bot.fetch_user(uid)
                desc += f"• {user.mention}\n"
            except:
                desc += f"• Unknown ({uid})\n"
        
        if len(config.antinuke_trusted) > 10:
            desc += f"... and {len(config.antinuke_trusted) - 10} more"
        
        e = discord.Embed(title="🛡️ Trusted", description=desc, color=0x57F287)
        await ctx.send(embed=e)

    @antinuke.command(name="list")
    @commands.has_permissions(administrator=True)
    async def antinuke_list(self, ctx):
        """List all antinuke modules and whitelisted users"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config:
            config = GuildConfig(guild_id=ctx.guild.id)
            await config.save()
        
        modules_text = ""
        for module, enabled in config.antinuke_modules.items():
            icon = "✅" if enabled else "❌"
            modules_text += f"{icon} {module.capitalize()}\n"
        
        whitelisted_count = len(config.antinuke_whitelist)
        trusted_count = len(config.antinuke_trusted)
        
        embed = discord.Embed(
            title="⚡ Antinuke Config",
            color=0x57F287 if config.antinuke_enabled else 0xED4245
        )
        embed.add_field(name="🔏 Status", value=f"Enabled: {'Yes' if config.antinuke_enabled else 'No'}\nAction: {config.antinuke_action}\nThreshold: {config.antinuke_threshold}", inline=False)
        embed.add_field(name="📊 Modules", value=modules_text if modules_text else "None", inline=False)
        embed.add_field(name="👥 Whitelisted", value=f"{whitelisted_count} users", inline=True)
        embed.add_field(name="👥 Trusted", value=f"{trusted_count} users", inline=True)
        await ctx.send(embed=embed)

    @antinuke.command(name="modules", aliases=["features", "events"])
    @commands.has_permissions(administrator=True)
    async def antinuke_modules(self, ctx):
        """List all available antinuke modules"""
        modules_list = [
            ("ban", "Prevents unauthorized member bans"),
            ("kick", "Prevents unauthorized member kicks"),
            ("prune", "Prevents mass member pruges"),
            ("channelcreate", "Prevents channel creation"),
            ("channel", "Prevents channel updates/deletions"),
            ("rolecreate", "Prevents role creation"),
            ("role", "Prevents role updates/deletions"),
            ("botadd", "Prevents unauthorized bot additions"),
            ("emoji", "Prevents emoji/sticker modifications"),
            ("integration", "Prevents integration modifications"),
            ("webhooks", "Prevents webhook creation/deletion"),
            ("guild", "Prevents guild settings changes"),
            ("vanity", "Prevents vanity URL changes"),
        ]
        
        embed = discord.Embed(
            title="🜯 Available Modules",
            color=0x57F287,
            description="Use `,antinuke [module] [on/off]` to toggle"
        )
        
        for module, desc in modules_list:
            embed.add_field(name=f"• {module}", value=desc, inline=False)
        
        await ctx.send(embed=embed)

    @antinuke.command(name="settings", aliases=["config"])
    @commands.has_permissions(administrator=True)
    async def antinuke_settings(self, ctx):
        """List your antinuke settings"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config:
            config = GuildConfig(guild_id=ctx.guild.id)
            await config.save()
        
        embed = discord.Embed(
            title="⚙️ Antinuke Settings",
            color=0x57F287 if config.antinuke_enabled else 0xED4245
        )
        
        enabled_modules = [m for m, e in config.antinuke_modules.items() if e]
        disabled_modules = [m for m, e in config.antinuke_modules.items() if not e]
        
        embed.add_field(name="System", value=f"Enabled: {'Yes' if config.antinuke_enabled else 'No'}\nPunishment: {config.antinuke_action}\nThreshold: {config.antinuke_threshold}", inline=False)
        embed.add_field(name="✅ Enabled", value=", ".join(enabled_modules) if enabled_modules else "None", inline=False)
        embed.add_field(name="❌ Disabled", value=", ".join(disabled_modules) if disabled_modules else "All enabled!", inline=False)
        embed.add_field(name="👥 Protection", value=f"Whitelisted: {len(config.antinuke_whitelist)}\nTrusted: {len(config.antinuke_trusted)}", inline=False)
        
        await ctx.send(embed=embed)

    @antinuke.command(name="cleanup", aliases=["clean"])
    @commands.has_permissions(administrator=True)
    async def antinuke_cleanup(self, ctx):
        """Clean up antinuke settings"""
        config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not config:
            e = discord.Embed(description="No configuration found", color=0xED4245)
            return await ctx.send(embed=e)
        
        config.antinuke_whitelist = [uid for uid in config.antinuke_whitelist if uid]
        config.antinuke_trusted = [uid for uid in config.antinuke_trusted if uid]
        await config.save()
        
        e = discord.Embed(description=f"🦟 Settings cleaned", color=0x57F287)
        await ctx.send(embed=e)

class StarboardJumpView(discord.ui.View):
    def __init__(self, message_url):
        super().__init__(timeout=None)
        self.message_url = message_url
        self.add_item(discord.ui.Button(label="Jump to Message", url=message_url, style=discord.ButtonStyle.link))

class VoiceMasterView(discord.ui.View):
    def __init__(self, bot, guild_id):
        super().__init__(timeout=None)
        self.bot = bot
        self.guild_id = guild_id

    @discord.ui.button(emoji="🔒", style=discord.ButtonStyle.secondary, row=0)
    async def lock_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("You must be in a voice channel!", ephemeral=True)
        try:
            channel = interaction.user.voice.channel
            await channel.edit(user_limit=len(channel.members))
            await interaction.response.send_message(f"🔒 Locked", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Error: {e}", ephemeral=True)

    @discord.ui.button(emoji="🔓", style=discord.ButtonStyle.secondary, row=0)
    async def unlock_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("You must be in a voice channel!", ephemeral=True)
        try:
            channel = interaction.user.voice.channel
            await channel.edit(user_limit=0)
            await interaction.response.send_message(f"🔓 Unlocked", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Error: {e}", ephemeral=True)

    @discord.ui.button(emoji="👻", style=discord.ButtonStyle.secondary, row=0)
    async def ghost_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("You must be in a voice channel!", ephemeral=True)
        try:
            channel = interaction.user.voice.channel
            await channel.set_permissions(interaction.guild.default_role, view_channel=False)
            await interaction.response.send_message(f"👻 Ghosted", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Error: {e}", ephemeral=True)

    @discord.ui.button(emoji="👁️", style=discord.ButtonStyle.secondary, row=0)
    async def reveal_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("You must be in a voice channel!", ephemeral=True)
        try:
            channel = interaction.user.voice.channel
            await channel.set_permissions(interaction.guild.default_role, view_channel=True)
            await interaction.response.send_message(f"👁️ Revealed", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Error: {e}", ephemeral=True)

    @discord.ui.button(emoji="🎙️", style=discord.ButtonStyle.secondary, row=1)
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("You must be in a voice channel!", ephemeral=True)
        try:
            channel = interaction.user.voice.channel
            await channel.set_permissions(interaction.user, manage_channel=True, manage_permissions=True, move_members=True)
            await interaction.response.send_message(f"🎙️ Claimed", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Error: {e}", ephemeral=True)

    @discord.ui.button(emoji="🔌", style=discord.ButtonStyle.secondary, row=1)
    async def disconnect_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("You must be in a voice channel!", ephemeral=True)
        channel = interaction.user.voice.channel
        members = [m for m in channel.members if m != interaction.user]
        if not members:
            return await interaction.response.send_message("No other members!", ephemeral=True)
        await interaction.response.send_modal(VoiceDisconnectModal(interaction.user, members))

    @discord.ui.button(emoji="🎮", style=discord.ButtonStyle.secondary, row=1)
    async def activity_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("You must be in a voice channel!", ephemeral=True)
        try:
            channel = interaction.user.voice.channel
            invite = await channel.create_invite(target_type=discord.InviteTarget.stream, target_user=interaction.user)
            await interaction.response.send_message(f"🎮 Activity created!", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Error: {e}", ephemeral=True)

    @discord.ui.button(emoji="ℹ️", style=discord.ButtonStyle.secondary, row=1)
    async def info_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("You must be in a voice channel!", ephemeral=True)
        channel = interaction.user.voice.channel
        embed = discord.Embed(color=0x242429, title=channel.name)
        embed.add_field(name="Members", value=len(channel.members))
        embed.add_field(name="Limit", value=channel.user_limit or "Unlimited")
        embed.add_field(name="Bitrate", value=f"{channel.bitrate // 1000}kbps")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(emoji="➕", style=discord.ButtonStyle.secondary, row=2)
    async def increase_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("You must be in a voice channel!", ephemeral=True)
        try:
            channel = interaction.user.voice.channel
            new_limit = (channel.user_limit or 0) + 1
            if new_limit > 99:
                return await interaction.response.send_message("Max limit is 99!", ephemeral=True)
            await channel.edit(user_limit=new_limit)
            await interaction.response.send_message(f"➕ Limit: {new_limit}", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Error: {e}", ephemeral=True)

    @discord.ui.button(emoji="➖", style=discord.ButtonStyle.secondary, row=2)
    async def decrease_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("You must be in a voice channel!", ephemeral=True)
        try:
            channel = interaction.user.voice.channel
            current = channel.user_limit or 0
            if current <= 1:
                return await interaction.response.send_message("Minimum limit is 1!", ephemeral=True)
            new_limit = current - 1
            await channel.edit(user_limit=new_limit)
            await interaction.response.send_message(f"➖ Limit: {new_limit}", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Error: {e}", ephemeral=True)

class VoiceDisconnectModal(discord.ui.Modal, title="Disconnect Member"):
    def __init__(self, user: discord.Member, members: list):
        super().__init__()
        self.user = user
        self.members = members

    member_id = discord.ui.TextInput(label="Member ID or mention", placeholder="123456789 or @user", max_length=100)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            input_str = self.member_id.value.strip()
            member = None
            guild = interaction.guild

            if input_str.startswith("<@") and input_str.endswith(">"):
                mid = int(input_str[2:-1])
                member = guild.get_member(mid)
            else:
                try:
                    mid = int(input_str)
                    member = guild.get_member(mid)
                except ValueError:
                    return await interaction.response.send_message("Invalid ID!", ephemeral=True)

            if not member or member not in self.members:
                return await interaction.response.send_message("Member not found!", ephemeral=True)

            await member.move_to(None)
            await interaction.response.send_message(f"🔌 Disconnected!", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Error: {e}", ephemeral=True)



async def setup(bot):
    await bot.add_cog(Configuration(bot))