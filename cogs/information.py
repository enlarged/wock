import discord
from discord.ext import commands
from utils.logger import logger
from utils.paginator import WockPaginator, Paginator
from models.configs import UserConfig
from cogs.events import get_xp_for_level, get_level_from_xp
from discord import Embed
from typing import Optional, Set
import re
import aiohttp
from datetime import datetime

color = 0x242429


class Help(commands.MinimalHelpCommand):
    def get_command_signature(self, command):
        return f'{self.context.clean_prefix}{command.qualified_name}'

    def _format_parameters(self, command: commands.Command) -> str:
        if command.brief:
            return command.brief

        params = []
        for name, param in command.clean_params.items():
            if param.default is param.empty:
                params.append(f"<{name}>")
            else:
                params.append(f"[{name}]")
        return " ".join(params) if params else "None"

    def _format_usage(self, command: commands.Command) -> str:
        if command.usage:
            usage = command.usage
            if "Syntax: " in usage:
                if "Syntax: ," in usage:
                    usage = usage.replace("Syntax: ,", f"Syntax: {self.context.clean_prefix}{command.qualified_name} ")
                else:
                    usage = usage.replace("Syntax: ", f"Syntax: {self.context.clean_prefix}{command.qualified_name} ")
            if "Example: " in usage:
                if "Example: ," in usage:
                    usage = usage.replace("Example: ,", f"Example: {self.context.clean_prefix}{command.qualified_name} ")
                else:
                    usage = usage.replace("Example: ", f"Example: {self.context.clean_prefix}{command.qualified_name} ")
            return usage

        params = self._format_parameters(command)
        if params == "None":
            return f"Syntax: {self.context.clean_prefix}{command.qualified_name}"
        return f"Syntax: {self.context.clean_prefix}{command.qualified_name} {params}"

    def _infer_permissions(self, command: commands.Command) -> str:
        if command.extras and command.extras.get("perms"):
            return str(command.extras.get("perms")).replace("_", " ")

        discovered = []
        for check in command.checks:
            name = getattr(check, "__name__", "")
            qualname = getattr(check, "__qualname__", "")

            if "guild_only" in name or "guild_only" in qualname:
                discovered.append("Guild Only")

            closure = getattr(check, "__closure__", None) or []
            for cell in closure:
                value = getattr(cell, "cell_contents", None)
                if isinstance(value, dict):
                    keys = [k.replace("_", " ") for k, v in value.items() if v]
                    discovered.extend(keys)

        if discovered:
            unique = []
            for item in discovered:
                if item not in unique:
                    unique.append(item)
            return ", ".join(unique)

        return "None"

    def _get_module_commands(self, cog: commands.Cog) -> list[str]:
        commands_list = []
        for command in sorted(cog.get_commands(), key=lambda x: x.name):
            label = f"{command.name}*" if isinstance(command, commands.Group) else command.name
            commands_list.append(label)
        return commands_list

    async def _build_command_embed(self, command: commands.Command, group_view: bool = False) -> discord.Embed:
        description = command.description or command.help or command.short_doc or "No description provided."
        title = f"Group Command: {command.qualified_name}" if group_view else f"Command: {command.qualified_name}"

        e = await self.help_embed(
            title=title,
            description=description
        )
        e.set_author(
            name=self.context.bot.user.name,
            icon_url=self.context.bot.user.display_avatar
        )
        e.set_footer(text=f"Module: {command.cog_name}")

        aliases = ', '.join(alias for alias in command.aliases if alias != command.name)
        e.add_field(name="Aliases", value=aliases if aliases else 'None')
        e.add_field(name='Parameters', value=self._format_parameters(command), inline=True)
        e.add_field(name='Permissions', value=self._infer_permissions(command), inline=True)
        e.add_field(name='Usage', value=f'```{self._format_usage(command)}```', inline=False)
        return e
    
    async def help_embed(self, title: Optional[str] = None, description: Optional[str] = None, mapping: Optional[dict] = None, command_set: Optional[Set[commands.Command]] = None, horseshit: Optional[bool] = False):
            helpdesc = ''
            e = Embed(color=color, description=helpdesc)  
            if title:
                e.title = title
            if description:
                e.description = description
            if command_set:
                filtered = await self.filter_commands(command_set, sort=True)
                for command in filtered:
                    e.add_field(name=command.qualified_name, value=command.help, inline=False)
            elif mapping:
                if horseshit:
                    embeds = []
                    cogs = ''
                    module_pages = []
                    hidden_cogs = {'Jishaku', 'Developer'}

                    for cog, command_set in mapping.items():
                        if cog is None or cog.qualified_name in hidden_cogs:
                            continue

                        filtered = await self.filter_commands(command_set, sort=True)
                        if not filtered:
                            continue

                        cog_commands = self._get_module_commands(cog)
                        if not cog_commands:
                            continue

                        cogs += f"> [{cog.qualified_name}](https://discord.gg/wockbot)\n"

                        for chunk in discord.utils.as_chunks(cog_commands, 10):
                            module_pages.append((cog.qualified_name, len(cog_commands), list(chunk)))

                    totalcommands = [command for command in self.context.bot.walk_commands() if command.cog_name != 'Jishaku']
                    maxpages = len(module_pages) + 1

                    mainEmbed = discord.Embed(
                        color=color,
                        description=f"""
                        ```ini\n[ {len(totalcommands)} commands ]```
                        {cogs}

                        """).set_thumbnail(url=self.context.bot.user.display_avatar).set_footer(text=f"Page 1/{maxpages}")
                    embeds.insert(0, mainEmbed)

                    for index, (module_name, total_module_commands, chunk) in enumerate(module_pages, start=2):
                        module_embed = discord.Embed(color=color)
                        module_embed.description = (
                            f"```ini\n[ {module_name} ]\n[ {total_module_commands} commands ]```\n"
                            + "\n".join(f"> [{cmd}](https://discord.gg/wockbot)" for cmd in chunk)
                        )
                        module_embed.set_footer(text=f"Page {index}/{maxpages}")
                        module_embed.set_thumbnail(url=self.context.bot.user.display_avatar)
                        embeds.append(module_embed)

                    pag = Paginator(self.context.bot, embeds, self.context, invoker=self.context.author.id)
                    pag.add_button('prev', emoji='<:void_previous:1082283002207424572>')
                    pag.add_button('goto', emoji='<:void_goto:1082282999187517490>')
                    pag.add_button('next', emoji='<:void_next:1082283004321341511>')
                    pag.add_button('delete', emoji='<:void_cross:1082283006649188435>')
                    await pag.start()
                    return
                for cog, command_set in sorted(mapping.items(), key=lambda item: item[0].qualified_name if item[0] else ''):
                    if cog is None or cog.qualified_name in {'Jishaku', 'Developer'}:
                        continue
                    filtered = await self.filter_commands(command_set, sort=True)
                    if not filtered:
                        continue
                    name = cog.qualified_name if cog else 'no category'
                    command_list = ', '.join(f'`{command.name}`' for command in sorted(filtered, key=lambda x: x.name))
                    for command in filtered:
                        if isinstance(command, commands.Group):
                            command_list = command_list.replace(f'`{command.name}`', f'`{command.name}*`')
                    value = (
                        f'{command_list}'
                    )
                    e.add_field(name=name, value=value, inline=False)
            return e

    async def send_bot_help(self, mapping: dict):
        e = await self.help_embed(
            mapping=mapping,
            horseshit=True
        )
        if e:
            await self.get_destination().send(embed=e)
        else:
            pass

    async def send_command_help(self, command: commands.Command):
        e = await self._build_command_embed(command, group_view=False)
        await self.get_destination().send(embed=e)

    async def send_group_help(self, group: commands.Group):
        pages = [await self._build_command_embed(group, group_view=True)]

        all_commands = await self.filter_commands(group.walk_commands(), sort=True)
        for cmd in all_commands:
            pages.append(await self._build_command_embed(cmd, group_view=False))

        if len(pages) == 1:
            await self.get_destination().send(embed=pages[0])
            return

        pag = Paginator(self.context.bot, pages, self.context, invoker=self.context.author.id)
        pag.add_button('prev', emoji='<:void_previous:1082283002207424572>')
        pag.add_button('goto', emoji='<:void_goto:1082282999187517490>')
        pag.add_button('next', emoji='<:void_next:1082283004321341511>')
        pag.add_button('delete', emoji='<:void_cross:1082283006649188435>')
        await pag.start()

class Information(commands.Cog):

    def __init__(self, bot):
        attrs = {
            'help': '',
            'description': 'View the bot commands',
            'usage': 'Syntax: <command>',
            'aliases': ['commands', 'cmds']
            }
        self.bot = bot
        self._original_help_command = bot.help_command
        bot.help_command = Help(command_attrs=attrs)
        bot.help_command.cog = self

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"{self.__class__.__name__} cog has been loaded\n-----")

    @commands.command(aliases=['ri'])
    async def roleinfo(self, ctx, *, role: discord.Role = None):
        """View information about a specific role"""
        role = role or ctx.author.top_role
        
        permissions = [p[0].replace('_', ' ').title() for p in role.permissions if p[1]]
        if len(permissions) > 10:
            perms_str = f"{', '.join(permissions[:10])} (+{len(permissions) - 10} more)"
        else:
            perms_str = ", ".join(permissions) if permissions else "None"

        e = discord.Embed(
            color=role.color,
            title=f'{role.name} ({role.id})'
        )
        
        e.add_field(name='Created', value=f'{discord.utils.format_dt(role.created_at, style="D")}\n'
                                          f'{discord.utils.format_dt(role.created_at, style="R")}')
        
        e.add_field(name='Color', value=f'**Hex:** {role.color}\n'
                                        f'**RGB:** {role.color.to_rgb()}')
        
        e.add_field(name='Statistics', value=f'**Members:** {len(role.members)}\n'
                                             f'**Position:** {role.position}\n'
                                             f'**Hoisted:** {role.hoist}', inline=False)
        
        e.add_field(name='Permissions', value=f'```{perms_str}```', inline=False)

        e.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
        e.set_footer(text=f'{role.id}')
        
        await ctx.send(embed=e)

    @commands.command(aliases=['rs'])
    @commands.guild_only()
    async def roles(self, ctx):
        """View a list of all server roles"""
        await ctx.typing()

        roles = [r for r in reversed(ctx.guild.roles) if not r.is_default()]
        
        if not roles:
            return await ctx.send("This server has no custom roles.")

        role_list = [f"{r.mention} (`{r.id}`) - **{len(r.members)}**" for r in roles]
        
        pages = []
        chunks = [role_list[i:i + 15] for i in range(0, len(role_list), 15)]

        for i, chunk in enumerate(chunks):
            e = discord.Embed(
                color=0x242429,
                description="\n".join(chunk)
            )
            e.set_author(
                name=f"Roles in {ctx.guild.name} ({len(roles)})", 
                icon_url=ctx.guild.icon.url if ctx.guild.icon else None
            )
            e.set_footer(text=f"Page {i+1} of {len(chunks)}")
            pages.append(e)

        if len(pages) == 1:
            await ctx.send(embed=pages[0])
        else:
            paginator = WockPaginator(ctx, pages)
            await paginator.start()

    @commands.command(aliases=['vars'])
    async def variables(self, ctx):
        """View available variables for custom commands and embeds"""
        pages = []
        
        # User Variables
        user_embed = discord.Embed(color=0x242429, title="Available Variables")
        user_embed.add_field(
            name="👤 User Variables",
            value=(
                "`{user}` - Your username\n"
                "`{user.mention}` - Your mention\n"
                "`{user.id}` - Your user ID\n"
                "`{level}` - Your current level"
            ),
            inline=False
        )
        user_embed.set_footer(text="Page 1 of 3")
        pages.append(user_embed)
        
        guild_embed = discord.Embed(color=0x242429, title="Available Variables")
        guild_embed.add_field(
            name="🏛️ Guild Variables",
            value=(
                "`{guild}` - Guild name\n"
                "`{guild.id}` - Guild ID"
            ),
            inline=False
        )
        guild_embed.set_footer(text="Page 2 of 3")
        pages.append(guild_embed)
        
        channel_embed = discord.Embed(color=0x242429, title="Available Variables")
        channel_embed.add_field(
            name="📝 Channel Variables",
            value=(
                "`{channel}` - Channel name\n"
                "`{channel.id}` - Channel ID"
            ),
            inline=False
        )
        channel_embed.set_footer(text="Page 3 of 3")
        pages.append(channel_embed)
        
        example_embed = discord.Embed(color=0x242429, title="Usage Example")
        example_embed.add_field(
            name="In Custom Messages",
            value="Use variables wrapped in curly braces: `Hello {user.mention}, welcome to {guild}!`",
            inline=False
        )
        example_embed.add_field(
            name="In Embeds (Parser Format)",
            value=(
                "`{title: Welcome {user}}`\n"
                "`{description: You are in {guild}}`\n"
                "`{color: #FF0000}`"
            ),
            inline=False
        )
        example_embed.set_footer(text="Page 4 of 4")
        pages.append(example_embed)
        
        paginator = WockPaginator(ctx, pages)
        await paginator.start()

    @commands.command(aliases=['about', 'info', 'bi'])
    async def botinfo(self, ctx):
        """View information about the bot"""
        await ctx.typing()
        
        invite = f"https://discord.com/api/oauth2/authorize?client_id={self.bot.user.id}&permissions=8&scope=bot"
        servercount = len(self.bot.guilds)
        usercount = sum(g.member_count for g in self.bot.guilds)
        
        startup = getattr(self.bot, "startup_time", discord.utils.utcnow())
        difference = discord.utils.utcnow() - startup
        hours, remainder = divmod(int(difference.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        days, hours = divmod(hours, 24)
        
        totalcommands = [c for c in self.bot.walk_commands() if c.cog_name not in ['Jishaku', 'Developer']]

        e = discord.Embed(
            color=0x242429,
            description=f'**[Support](https://discord.gg/wockbot)** | **[Invite]({invite})**\n'
                        f'Multipurpose bot'
        )
        
        e.add_field(name='Statistics', value=f'**Guilds:** {servercount}\n'
                                             f'**Users:** {usercount:,}\n'
                                             f'**Commands:** {len(totalcommands)}', inline=True)
        
        e.add_field(name='Uptime', value=f'**Days:** {days}\n'
                                         f'**Hours:** {hours}\n'
                                         f'**Minutes:** {minutes}', inline=True)

        e.set_author(name=f'{self.bot.user.name} ({self.bot.user.id})', icon_url=self.bot.user.display_avatar.url)
        e.set_thumbnail(url=self.bot.user.display_avatar.url)
        
        version = getattr(self.bot, 'version', '1.0.0')
        e.set_footer(text=f'{self.bot.user.name} v{version}')
        
        await ctx.send(embed=e)

    @commands.command(aliases=["inv"])
    async def invite(self, ctx):
        """Get the bot's invite link"""
        invite_url = discord.utils.oauth_url(
            self.bot.user.id,
            permissions=discord.Permissions(
                send_messages=True,
                embed_links=True,
                read_messages=True,
                read_message_history=True,
                external_emojis=True,
                add_reactions=True,
                manage_messages=True,
                speak=True,
                connect=True,
            )
        )
        embed = discord.Embed(
            color=0x242429,
            description=f"Click **[here]({invite_url})** to invite me to your server"
        )
        await ctx.send(embed=embed)

    @commands.group(name="server", invoke_without_command=True)
    async def server_group(self, ctx):
        """Base command for server media. Usage: server [icon|banner|splash]"""
        await ctx.send_help(ctx.command)

    @server_group.command(name="icon")
    async def server_icon(self, ctx, invite_code: str = None):
        """View the icon of the current server or a provided invite code"""
        if invite_code:
            try:
                invite = await self.bot.fetch_invite(invite_code)
                guild_name = invite.guild.name
                url = invite.guild.icon.url if invite.guild.icon else None
            except discord.NotFound:
                return await self.bot.warn(ctx, "Invalid invite code provided.")
        else:
            guild_name = ctx.guild.name
            url = ctx.guild.icon.url if ctx.guild.icon else None

        if not url:
            return await self.bot.warn(ctx, f"**{guild_name}** does not have an icon.")

        embed = discord.Embed(color=0x242429, title=f"{guild_name}'s icon")
        embed.set_image(url=url)
        await ctx.send(embed=embed)

    @server_group.command(name="banner")
    async def server_banner(self, ctx, invite_code: str = None):
        """View the banner of the current server or a provided invite code"""
        if invite_code:
            try:
                invite = await self.bot.fetch_invite(invite_code)
                guild_name = invite.guild.name
                url = invite.guild.banner.url if invite.guild.banner else None
            except discord.NotFound:
                return await self.bot.warn(ctx, "Invalid invite code provided.")
        else:
            guild_name = ctx.guild.name
            url = ctx.guild.banner.url if ctx.guild.banner else None

        if not url:
            return await self.bot.warn(ctx, f"**{guild_name}** does not have a banner.")

        embed = discord.Embed(color=0x242429, title=f"{guild_name}'s banner")
        embed.set_image(url=url)
        await ctx.send(embed=embed)


    @server_group.command(name="splash")
    async def server_splash(self, ctx, invite_code: str = None):
        """View the invite splash of the current server or a provided invite code"""
        if invite_code:
            try:
                invite = await self.bot.fetch_invite(invite_code)
                guild_name = invite.guild.name
                url = invite.guild.splash.url if invite.guild.splash else None
            except discord.NotFound:
                return await self.bot.warn(ctx, "Invalid invite code provided.")
        else:
            guild_name = ctx.guild.name
            url = ctx.guild.splash.url if ctx.guild.splash else None

        if not url:
            return await self.bot.warn(ctx, f"**{guild_name}** does not have an invite splash.")

        embed = discord.Embed(color=0x242429, title=f"{guild_name}'s invite splash")
        embed.set_image(url=url)
        await ctx.send(embed=embed)

    @commands.command(name="userinfo", aliases=["ui", "whois"])
    async def userinfo(self, ctx, *, member: discord.Member = None):
        """Display information about a specific member"""
        user = member or ctx.author
        
        members = sorted(ctx.guild.members, key=lambda m: m.joined_at or ctx.guild.created_at)
        position = members.index(user) + 1

        embed = discord.Embed(color=0x242429)
        embed.set_author(name=f"{user} ({user.id})", icon_url=user.display_avatar.url)
        embed.set_thumbnail(url=user.display_avatar.url)

        embed.add_field(
            name="Created", 
            value=f"{discord.utils.format_dt(user.created_at, style='D')}\n{discord.utils.format_dt(user.created_at, style='R')}",
            inline=True
        )
        embed.add_field(
            name="Joined", 
            value=f"{discord.utils.format_dt(user.joined_at, style='D')}\n{discord.utils.format_dt(user.joined_at, style='R')}",
            inline=True
        )

        if user.premium_since:
            embed.add_field(
                name="Boosted", 
                value=f"{discord.utils.format_dt(user.premium_since, style='D')}\n{discord.utils.format_dt(user.premium_since, style='R')}",
                inline=True
            )

        roles = [r.mention for r in reversed(user.roles[1:])] 
        if roles:
            role_str = ", ".join(roles)
            if len(role_str) > 1024:
                role_str = "Too many roles to display."
            embed.add_field(name=f"Roles ({len(roles)})", value=role_str, inline=False)
        else:
            embed.add_field(name="Roles (0)", value="No roles", inline=False)

        embed.set_footer(text=f"Join position: {position}")
        
        await ctx.send(embed=embed)

    @commands.command(name="serverinfo", aliases=["si"])
    async def serverinfo(self, ctx):
        """Display information about the server"""
        await ctx.typing()
        
        guild = ctx.guild
        humans = len([m for m in guild.members if not m.bot])
        bots = len([m for m in guild.members if m.bot])
        
        text_channels = len(guild.text_channels)
        voice_channels = len(guild.voice_channels)
        categories = len(guild.categories)
        
        vanity = f"[{guild.vanity_url_code}]({guild.vanity_url})" if guild.vanity_url else "N/A"

        embed = discord.Embed(
            color=0x242429,
            description=f"Created {discord.utils.format_dt(guild.created_at, style='F')} ({discord.utils.format_dt(guild.created_at, style='R')})"
        )
        
        embed.set_author(
            name=f"{guild.name} ({guild.id})", 
            icon_url=guild.icon.url if guild.icon else None
        )

        embed.add_field(
            name="Members", 
            value=f"**Total:** {guild.member_count}\n**Humans:** {humans}\n**Bots:** {bots}", 
            inline=True
        )
        embed.add_field(
            name="Channels", 
            value=f"**Total:** {text_channels + voice_channels}\n**Text:** {text_channels}\n**Voice:** {voice_channels}", 
            inline=True
        )
        embed.add_field(
            name="Other", 
            value=f"**Categories:** {categories}\n**Roles:** {len(guild.roles)}\n**Emotes:** {len(guild.emojis)}", 
            inline=True
        )
        embed.add_field(
            name="Boost", 
            value=f"**Level:** {guild.premium_tier}\n**Boosts:** {guild.premium_subscription_count}", 
            inline=True
        )
        embed.add_field(
            name="Information", 
            value=f"**Verification:** {str(guild.verification_level).title()}\n**Vanity:** {vanity}", 
            inline=True
        )

        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
            
        embed.set_footer(text=f"Owner: {guild.owner} ({guild.owner_id})")
        
        await ctx.reply(embed=embed, mention_author=False)

    @commands.command(name="membercount", aliases=["mc"])
    async def membercount(self, ctx):
        """Display the total number of members, humans, and bots"""
        total = ctx.guild.member_count
        humans = len([m for m in ctx.guild.members if not m.bot])
        bots = len([m for m in ctx.guild.members if m.bot])

        embed = discord.Embed(color=0x242429)
        
        embed.set_author(
            name=ctx.guild.name, 
            icon_url=ctx.guild.icon.url if ctx.guild.icon else None
        )
        
        embed.add_field(name="Total", value=f"**{total}**", inline=True)
        embed.add_field(name="Humans", value=f"**{humans}**", inline=True)
        embed.add_field(name="Bots", value=f"**{bots}**", inline=True)

        await ctx.send(embed=embed)

    @commands.command()
    async def ping(self, ctx):
        """Check the bot's response time"""
        await ctx.send(f"🏓 Pong! Latency: {round(self.bot.latency * 1000)}ms")

    @commands.command(aliases=["av"])
    async def avatar(self, ctx, *, member: discord.Member = None):
        """View a member's avatar"""
        member = member or ctx.author
        embed = discord.Embed(color=0x242429, title=f"{member.display_name}'s avatar")
        embed.set_image(url=member.display_avatar.url)
        await ctx.send(embed=embed)

    @commands.command(aliases=["bn"])
    async def banner(self, ctx, *, member: discord.Member = None):
        """View a member's banner"""
        member = member or ctx.author
        user = await self.bot.fetch_user(member.id)
        if not user.banner:
            return await ctx.send(f"**{member.display_name}** does not have a banner.")
        embed = discord.Embed(color=0x242429, title=f"{member.display_name}'s banner")
        embed.set_image(url=user.banner.url)
        await ctx.send(embed=embed)

    @commands.group(invoke_without_command=True)
    async def boosters(self, ctx):
        """View the server's boosters"""
        boosters = [m for m in ctx.guild.members if m.premium_since]
        if not boosters:
            return await ctx.send("This server has no boosters.")
        
        boosters = sorted(boosters, key=lambda m: m.premium_since, reverse=True)
        pages = []
        chunks = [boosters[i:i + 10] for i in range(0, len(boosters), 10)]

        for i, chunk in enumerate(chunks):
            content = "\n".join(f"{m.mention} - <t:{int(m.premium_since.timestamp())}:R>" for m in chunk)
            embed = discord.Embed(color=0x242429, title=f"Boosters in {ctx.guild.name}", description=content)
            embed.set_footer(text=f"Page {i+1}/{len(chunks)} • Total: {len(boosters)}")
            pages.append(embed)

        paginator = WockPaginator(ctx, pages)
        await paginator.start()

    @boosters.command(name="lost")
    async def boosters_lost(self, ctx):
        """Track boosters who have left or stopped boosting"""
        await ctx.send("Lost boosters tracking requires a database.")

    @commands.command(aliases=["xp", "progress"])
    async def rank(self, ctx, *, member: discord.Member = None):
        """View a user's rank and XP progress"""
        member = member or ctx.author
        
        user_config = await UserConfig.find_one(UserConfig.user_id == member.id)
        if not user_config:
            user_config = UserConfig(user_id=member.id)
        
        xp = user_config.xp
        level = user_config.level
        
        xp_for_current_level = get_xp_for_level(level)
        xp_for_next_level = get_xp_for_level(level + 1)
        xp_in_level = xp - xp_for_current_level
        xp_needed_for_next = xp_for_next_level - xp_for_current_level
        
        filled = round((xp_in_level / xp_needed_for_next) * 10)
        filled = max(0, min(filled, 10))
        progress_bar = "🟦" * filled + "⬜" * (10 - filled)
        
        embed = discord.Embed(color=0x242429, title=f"{member.display_name}'s Rank")
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Level", value=str(level), inline=True)
        embed.add_field(
            name="Progress",
            value=f"{progress_bar}\n`{xp_in_level}/{xp_needed_for_next} XP`",
            inline=False
        )
        
        await ctx.send(embed=embed)

    @commands.command(name="accounts", aliases=['acc', 'creation'])
    async def accounts(self, ctx, sub: str = None, is_top: str = None):
        """View the oldest or youngest accounts in the server"""
        if not sub or sub.lower() not in ['oldest', 'youngest']:
            return await ctx.send_help(ctx.command)

        sub = sub.lower()
        is_top_flag = is_top and is_top.lower() == 'top'

        members = sorted(ctx.guild.members, key=lambda m: m.created_at)
        
        if sub == 'youngest':
            members = list(reversed(members))

        if is_top_flag:
            pages = []
            per_page = 10
            
            for i in range(0, len(members), per_page):
                current_batch = members[i:i + per_page]
                description = ""
                
                for index, m in enumerate(current_batch):
                    description += f"**{i + index + 1}.** {m.mention} {discord.utils.format_dt(m.created_at, style='R')}\n"

                embed = discord.Embed(
                    title=f"{sub.capitalize()} Accounts",
                    description=description,
                    color=0x242429
                )
                embed.set_footer(text=f"Page {i // per_page + 1} of {(len(members) - 1) // per_page + 1}")
                pages.append(embed)

            paginator = WockPaginator(ctx, pages)
            return await paginator.start()

        target_member = members[0]

        embed = discord.Embed(color=0x242429)
        embed.set_author(name=f"{target_member} ({target_member.id})", icon_url=target_member.display_avatar.url)
        embed.set_thumbnail(url=target_member.display_avatar.url)

        embed.add_field(
            name="Created", 
            value=f"{discord.utils.format_dt(target_member.created_at, style='D')}\n{discord.utils.format_dt(target_member.created_at, style='R')}",
            inline=False
        )
        embed.add_field(
            name="Joined", 
            value=f"{discord.utils.format_dt(target_member.joined_at, style='D')}\n{discord.utils.format_dt(target_member.joined_at, style='R')}",
            inline=False
        )
        
        embed.set_footer(text=f"{sub.capitalize()} Account in {ctx.guild.name}")

        await ctx.send(embed=embed)

    @commands.command(name="bots")
    async def bots(self, ctx, sub: str = None):
        """View bot information in the server"""
        if not sub or sub.lower() not in ['oldest', 'youngest', 'count']:
            return await ctx.send_help(ctx.command)

        sub = sub.lower()
        bots_list = [m for m in ctx.guild.members if m.bot]

        if sub == 'count':
            embed = discord.Embed(color=0x242429)
            embed.set_author(name=f"{ctx.guild.name}", icon_url=ctx.guild.icon.url if ctx.guild.icon else None)
            embed.add_field(name="Bot Count", value=str(len(bots_list)), inline=False)
            embed.set_footer(text=f"Total bots in {ctx.guild.name}")
            return await ctx.send(embed=embed)

        bots_sorted = sorted(bots_list, key=lambda m: m.created_at)
        
        if sub == 'youngest':
            bots_sorted = list(reversed(bots_sorted))

        if not bots_sorted:
            return await self.bot.neutral(ctx, "There are no bots in this server.")

        target_bot = bots_sorted[0]

        embed = discord.Embed(color=0x242429)
        embed.set_author(name=f"{target_bot} ({target_bot.id})", icon_url=target_bot.display_avatar.url)
        embed.set_thumbnail(url=target_bot.display_avatar.url)

        embed.add_field(
            name="Created", 
            value=f"{discord.utils.format_dt(target_bot.created_at, style='D')}\n{discord.utils.format_dt(target_bot.created_at, style='R')}",
            inline=False
        )
        embed.add_field(
            name="Joined", 
            value=f"{discord.utils.format_dt(target_bot.joined_at, style='D')}\n{discord.utils.format_dt(target_bot.joined_at, style='R')}",
            inline=False
        )
        
        embed.set_footer(text=f"{sub.capitalize()} Bot in {ctx.guild.name}")

        await ctx.send(embed=embed)

    @commands.command(name="inviteinfo", aliases=['ii'])
    async def inviteinfo(self, ctx, *, invite_input: str = None):
        """Fetches information about a provided invite link"""
        if not invite_input:
            return await ctx.send_help(ctx.command)

        invite_code = invite_input.split('/')[-1]

        try:
            invite = await self.bot.fetch_invite(invite_code, with_counts=True, with_expiration=True)
            
            async with aiohttp.ClientSession() as session:
                async with session.get(f'https://discord.com/api/v10/invites/{invite_code}') as resp:
                    if resp.status != 200:
                        return await self.bot.deny(ctx, "Invite not found or API error.")
                    invite_data = await resp.json()

            guild = invite.guild
            channel = invite.channel
            inviter = invite.inviter

            guild_created = int(guild.created_at.timestamp())
            channel_created = int(channel.created_at.timestamp()) if channel else None
            expires_at = f"<t:{int(invite.expires_at.timestamp())}:R>" if invite.expires_at else "Never"

            embed = discord.Embed(
                title=f"{guild.name} (/{invite_code})",
                url=f"https://discord.gg/{invite_code}",
                description=invite_data.get('guild', {}).get('description') or 'No description available',
                color=0x242429
            )

            if guild.icon:
                embed.set_thumbnail(url=guild.icon.url)
            
            if invite_data.get('guild', {}).get('banner'):
                banner_id = invite_data['guild']['banner']
                embed.set_image(url=f"https://cdn.discordapp.com/banners/{guild.id}/{banner_id}.png")

            channel_val = (
                f"**Name:** {channel.name if channel else 'Unknown'}\n"
                f"**ID:** {channel.id if channel else 'Unknown'}\n"
                f"**Created:** <t:{channel_created}:R>\n" if channel_created else "None\n"
                f"**Expires:** {expires_at}\n"
                f"**Inviter:** {inviter.mention if inviter else 'None'}"
            )

            guild_val = (
                f"**Name:** {guild.name}\n"
                f"**ID:** {guild.id}\n"
                f"**Created:** <t:{guild_created}:R>\n"
                f"**Members:** {invite.approximate_member_count:,}\n"
                f"**Verification:** {str(guild.verification_level).title()}"
            )

            embed.add_field(name="Channel & Invite", value=channel_val, inline=True)
            embed.add_field(name="Guild", value=guild_val, inline=True)
            
            await ctx.send(embed=embed)

        except discord.NotFound:
            await self.bot.warn(ctx, "Invalid or expired invite.")
        except Exception as e:
            logger.error(f"Invite info error: {e}")
            await self.bot.deny(ctx, "An error occurred while fetching the invite.")

async def setup(bot):
    await bot.add_cog(Information(bot))