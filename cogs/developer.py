import discord
from discord.ext import commands
import aiohttp
import traceback
import uuid
import secrets
from datetime import datetime
from models.configs import UserConfig

class Developer(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.dev_ids = {1266104182625013786, 1065363954597113896, 1143469481612562554, 931757446609907762}
        self.error_traces = {}

    async def cog_check(self, ctx):
        return ctx.author.id in self.dev_ids

    @commands.group(name="bot", invoke_without_command=True)
    async def dev_bot(self, ctx):
        """Base command for bot management"""
        await ctx.send_help(ctx.command)

    @dev_bot.command(name="icon", aliases=["avatar"])
    async def dev_icon(self, ctx, url: str = None):
        """Change the bot's avatar via attachment or URL"""
        if not url and not ctx.message.attachments:
            return await self.bot.warn(ctx, "Please provide an image URL or attach a file.")

        async with ctx.typing():
            target = ctx.message.attachments[0].url if ctx.message.attachments else url
            async with aiohttp.ClientSession() as session:
                async with session.get(target) as resp:
                    if resp.status != 200:
                        return await self.bot.warn(ctx, "Failed to download the image.")
                    
                    data = await resp.read()
                    try:
                        await self.bot.user.edit(avatar=data)
                        await self.bot.grant(ctx, "Successfully updated the bot's **avatar**.")
                    except discord.HTTPException as e:
                        await self.bot.warn(ctx, f"Failed to update: {e}")

    @dev_bot.command(name="banner")
    async def dev_banner(self, ctx, url: str = None):
        """Change the bot's banner (Requires Nitro on the bot account)"""
        if not url and not ctx.message.attachments:
            return await self.bot.warn(ctx, "Please provide an image URL or attach a file.")

        async with ctx.typing():
            target = ctx.message.attachments[0].url if ctx.message.attachments else url
            async with aiohttp.ClientSession() as session:
                async with session.get(target) as resp:
                    if resp.status != 200:
                        return await self.bot.warn(ctx, "Failed to download the image.")
                    
                    data = await resp.read()
                    try:
                        await self.bot.user.edit(banner=data)
                        await self.bot.grant(ctx, "Successfully updated the bot's **banner**.")
                    except discord.HTTPException as e:
                        await self.bot.warn(ctx, f"Failed to update: {e}")

    @dev_bot.command(name="status")
    async def dev_status(self, ctx, *, text: str):
        """Change the bot's presence/status"""
        activity = discord.Game(name=text)
        await self.bot.change_presence(activity=activity)
        await self.bot.grant(ctx, f"Status updated to: **{text}**")

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            return
        
        trace_code = str(uuid.uuid4())[:8].upper()
        tb_str = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        
        self.error_traces[trace_code] = {
            "timestamp": datetime.now(),
            "command": ctx.command.qualified_name if ctx.command else "Unknown",
            "author": f"{ctx.author} ({ctx.author.id})",
            "traceback": tb_str
        }
        
        embed = discord.Embed(color=0xf04747, description=f"@{ctx.author}: An error occurred")
        embed.add_field(name="Error Code", value=f"`{trace_code}`", inline=False)
        embed.add_field(name="Command", value=f"`{ctx.command.qualified_name if ctx.command else 'Unknown'}`", inline=False)
        embed.set_footer(text="Use `,trace <code>` to view details")
        
        await ctx.send(embed=embed)

    @commands.command(name="trace")
    async def trace(self, ctx, code: str = None):
        """View error traceback by code"""
        try:
            if not code:
                return await ctx.send_help(ctx.command)
            
            code = code.upper()
            if code not in self.error_traces:
                return await self.bot.warn(ctx, f"Error code `{code}` not found.")
            
            error_info = self.error_traces[code]
            tb = error_info["traceback"]
            
            # Limit to 950 chars to account for markdown code blocks
            if len(tb) > 950:
                tb = tb[-950:]
                tb = "...\n" + tb
            
            embed = discord.Embed(color=0x242429, title=f"Error Trace: {code}")
            embed.add_field(name="Command", value=f"`{error_info['command']}`", inline=True)
            embed.add_field(name="Author", value=error_info['author'], inline=True)
            embed.add_field(name="Traceback", value=f"```py\n{tb}\n```", inline=False)
            embed.set_footer(text=error_info['timestamp'].strftime("%Y-%m-%d %H:%M:%S"))
            
            await ctx.send(embed=embed)
        except Exception as e:
            print(f"ERROR in trace command: {e}")
            import traceback as tb_module
            tb_module.print_exc()
            await ctx.send(f"Error viewing trace: {str(e)}")

    @commands.command(name="authorize", aliases=["authapi", "apikey"])
    async def authorize(self, ctx, user: discord.User = None):
        """Authorize a user for API access and DM them their API key"""
        if not user:
            return await self.bot.warn(ctx, "Please provide a user to authorize.")

        try:
            api_key = f"wock_{secrets.token_hex(24)}"

            user_cfg = await UserConfig.find_one(UserConfig.user_id == user.id)
            if not user_cfg:
                user_cfg = UserConfig(user_id=user.id)

            user_cfg.api_authorized = True
            user_cfg.api_key = api_key
            await user_cfg.save()

            dm_embed = discord.Embed(
                color=0x242429,
                title="Wock API Access Authorized",
                description="You have been authorized to use the Wock API."
            )
            dm_embed.add_field(name="API Key", value=f"`{api_key}`", inline=False)
            dm_embed.add_field(name="Header", value="Use this header: `x-api-key: <your-key>`", inline=False)
            dm_embed.set_footer(text="Keep your key private. Regenerate if exposed.")

            await user.send(embed=dm_embed)
            await self.bot.grant(ctx, f"Authorized **{user}** and sent their API key via DM.")
        except discord.Forbidden:
            await self.bot.warn(ctx, f"Couldn't DM **{user}**. They need to enable DMs first.")
        except Exception as e:
            await self.bot.warn(ctx, f"Failed to authorize user: {e}")

async def setup(bot):
    await bot.add_cog(Developer(bot))