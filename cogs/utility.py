import discord
from discord.ext import commands, tasks
from discord import ui
from utils.logger import logger
from utils.parser import EmbedParser
from models.afk import AFK
from models.configs import ScheduledMessage, Reminder
from models.giveaway import Giveaway
from datetime import datetime, timedelta
import colorsys
from PIL import Image, ImageOps, ImageFilter, ImageDraw, ImageChops
import io
import aiohttp
import asyncio
from io import BytesIO
import secrets
import random
import re
from typing import Optional
import math
import os
import google.generativeai as genai

try:
    import phonenumbers
except ImportError:
    phonenumbers = None

try:
    from wand.image import Image as WandImage
except ImportError:
    WandImage = None


class GiveawayView(ui.View):
    def __init__(self, giveaway_id: str):
        super().__init__(timeout=None)
        self.giveaway_id = giveaway_id

    @ui.button(label="🎉 Enter", style=discord.ButtonStyle.blurple)
    async def enter_giveaway(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer(ephemeral=True)
        
        try:
            giveaway = await Giveaway.get(self.giveaway_id)
            
            if interaction.user.id in giveaway.entries:
                return await interaction.followup.send("You've already entered this giveaway!", ephemeral=True)
            
            if not giveaway.is_active:
                return await interaction.followup.send("This giveaway has ended.", ephemeral=True)
            
            member = interaction.guild.get_member(interaction.user.id)
            if not member:
                return await interaction.followup.send("You must be a member of this server to enter.", ephemeral=True)
            
            if giveaway.min_level or giveaway.max_level:
                try:
                    from models.configs import UserConfig
                    user_config = await UserConfig.find_one(UserConfig.user_id == interaction.user.id)
                    level = user_config.level if user_config else 0
                    
                    if giveaway.min_level and level < giveaway.min_level:
                        return await interaction.followup.send(f"You need to be at least level {giveaway.min_level} to enter.", ephemeral=True)
                    if giveaway.max_level and level > giveaway.max_level:
                        return await interaction.followup.send(f"You can't be above level {giveaway.max_level} to enter.", ephemeral=True)
                except:
                    pass
            
            if giveaway.min_account_age_days:
                account_age = datetime.utcnow() - interaction.user.created_at
                if account_age.days < giveaway.min_account_age_days:
                    return await interaction.followup.send(
                        f"Your account must be at least {giveaway.min_account_age_days} days old.", 
                        ephemeral=True
                    )
            
            if giveaway.min_server_stay_days and member:
                server_stay = datetime.utcnow() - member.joined_at
                if server_stay.days < giveaway.min_server_stay_days:
                    return await interaction.followup.send(
                        f"You must have been in this server for at least {giveaway.min_server_stay_days} days.", 
                        ephemeral=True
                    )
            
            if giveaway.required_roles:
                has_role = False
                for role_id in giveaway.required_roles:
                    if member and member.get_role(role_id):
                        has_role = True
                        break
                if not has_role:
                    return await interaction.followup.send("You don't have the required roles to enter.", ephemeral=True)
            
            giveaway.entries.append(interaction.user.id)
            await giveaway.save()
            
            await interaction.followup.send(f"✅ You've entered the giveaway for **{giveaway.prize}**!", ephemeral=True)
        
        except Exception as e:
            logger("error", f"Error entering giveaway: {e}")
            await interaction.followup.send("An error occurred while entering the giveaway.", ephemeral=True)


class Utility(commands.Cog, name="Utility"):
    def __init__(self, bot):
        self.bot = bot
        self.message_snipes = {}
        self.edit_snipes = {}
        self.reaction_snipes = {}
        self.check_giveaways.start()

    def cog_unload(self):
        self.check_giveaways.cancel()

    @tasks.loop(seconds=5)
    async def check_giveaways(self):
        """Check for giveaways that should end"""
        try:
            giveaways = await Giveaway.find(Giveaway.is_active == True).to_list()
            
            for giveaway in giveaways:
                if datetime.utcnow() >= giveaway.end_time:
                    await self.end_giveaway_internal(giveaway)
        except Exception as e:
            logger("error", f"Error checking giveaways: {e}")

    @check_giveaways.before_loop
    async def before_check_giveaways(self):
        await self.bot.wait_until_ready()

    def _push_snipe(self, store: dict, channel_id: int, payload: dict, limit: int = 25):
        bucket = store.setdefault(channel_id, [])
        bucket.append(payload)
        if len(bucket) > limit:
            store[channel_id] = bucket[-limit:]

    def _format_snipe_time(self, dt: datetime) -> str:
        now = datetime.utcnow()
        if dt.date() == now.date():
            day = "Today"
        elif (now.date() - dt.date()).days == 1:
            day = "Yesterday"
        else:
            day = dt.strftime("%m/%d/%Y")

        time_text = dt.strftime("%I:%M %p").lstrip("0")
        return f"{day} at {time_text}"

    def _resolve_member_display(self, guild: discord.Guild, user_id: int):
        member = guild.get_member(user_id)
        if member:
            return member.display_name, member.display_avatar.url
        user = self.bot.get_user(user_id)
        if user:
            return getattr(user, "display_name", user.name), user.display_avatar.url
        return f"User {user_id}", None

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return

        content = message.content.strip() if message.content else ""
        if not content and not message.attachments and not message.stickers:
            return

        self._push_snipe(self.message_snipes, message.channel.id, {
            "author_id": message.author.id,
            "content": content,
            "attachments": [a.url for a in message.attachments],
            "stickers": [s.name for s in message.stickers],
            "timestamp": datetime.utcnow()
        })

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if not before.guild or before.author.bot:
            return
        if before.content == after.content:
            return
        if not (before.content or after.content):
            return

        self._push_snipe(self.edit_snipes, before.channel.id, {
            "author_id": before.author.id,
            "before": before.content or "*No content*",
            "after": after.content or "*No content*",
            "timestamp": datetime.utcnow(),
            "jump_url": after.jump_url
        })

    @commands.Cog.listener()
    async def on_reaction_remove(self, reaction: discord.Reaction, user: discord.User):
        message = reaction.message
        if not message.guild or user.bot:
            return

        self._push_snipe(self.reaction_snipes, message.channel.id, {
            "user_id": user.id,
            "author_id": message.author.id,
            "emoji": str(reaction.emoji),
            "message_jump": message.jump_url,
            "message_content": (message.content or "*No content*")[:500],
            "timestamp": datetime.utcnow()
        })

    @commands.command(name="snipe")
    async def snipe(self, ctx, index: int = 1):
        """View recently deleted messages"""
        if index < 1:
            return await self.bot.warn(ctx, "Index must be 1 or higher.")

        entries = self.message_snipes.get(ctx.channel.id, [])
        if not entries or index > len(entries):
            return await self.bot.warn(ctx, "No deleted messages to snipe.")

        data = entries[-index]
        author_text, author_icon = self._resolve_member_display(ctx.guild, data["author_id"])

        embed = discord.Embed(
            color=0x242429,
            description=data["content"] or "*No text content*",
        )
        embed.set_author(name=author_text, icon_url=author_icon)

        if data.get("attachments"):
            attachments_value = "\n".join(data["attachments"][:3])
            if embed.description:
                embed.description += f"\n\n{attachments_value}"
            else:
                embed.description = attachments_value
            if data["attachments"]:
                first = data["attachments"][0]
                if any(first.lower().endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".gif", ".webp"]):
                    embed.set_image(url=first)

        if data.get("stickers"):
            sticker_text = ", ".join(data["stickers"][:8])
            if embed.description:
                embed.description += f"\n\nStickers: {sticker_text}"
            else:
                embed.description = f"Stickers: {sticker_text}"

        when = self._format_snipe_time(data["timestamp"])
        embed.set_footer(text=f"Deleted in #{ctx.channel.name} • {when}")
        await ctx.send(embed=embed)

    @commands.command(name="editsnipe")
    async def editsnipe(self, ctx, index: int = 1):
        """View recently edited messages"""
        if index < 1:
            return await self.bot.warn(ctx, "Index must be 1 or higher.")

        entries = self.edit_snipes.get(ctx.channel.id, [])
        if not entries or index > len(entries):
            return await self.bot.warn(ctx, "No edited messages to snipe.")

        data = entries[-index]
        author_text, author_icon = self._resolve_member_display(ctx.guild, data["author_id"])

        before_text = (data["before"] or "*No content*")[:450]
        after_text = (data["after"] or "*No content*")[:450]
        embed = discord.Embed(
            color=0x242429,
            description=f"**Before**\n{before_text}\n\n**After**\n{after_text}"
        )
        embed.set_author(name=author_text, icon_url=author_icon)
        when = self._format_snipe_time(data["timestamp"])
        embed.set_footer(text=f"Edited in #{ctx.channel.name} • {when}")
        await ctx.send(embed=embed)

    @commands.command(name="reactionsnipe")
    async def reactionsnipe(self, ctx, index: int = 1):
        """View recently removed reactions"""
        if index < 1:
            return await self.bot.warn(ctx, "Index must be 1 or higher.")

        entries = self.reaction_snipes.get(ctx.channel.id, [])
        if not entries or index > len(entries):
            return await self.bot.warn(ctx, "No removed reactions to snipe.")

        data = entries[-index]
        user_text, user_icon = self._resolve_member_display(ctx.guild, data["user_id"])
        author_text, _ = self._resolve_member_display(ctx.guild, data["author_id"])

        embed = discord.Embed(
            color=0x242429,
            description=(
                f"Removed **{data['emoji']}** from {author_text}'s message\n"
                f"{data['message_content'][:700]}\n\n"
            )
        )
        embed.set_author(name=user_text, icon_url=user_icon)
        when = self._format_snipe_time(data["timestamp"])
        embed.set_footer(text=f"Reaction removed in #{ctx.channel.name} • {when}")
        await ctx.send(embed=embed)

    @commands.command(name="clearsnipe")
    @commands.has_permissions(manage_messages=True)
    async def clearsnipe(self, ctx):
        """Clear all snipes for this server"""
        guild_channel_ids = {c.id for c in ctx.guild.channels}

        for store in (self.message_snipes, self.edit_snipes, self.reaction_snipes):
            for channel_id in list(store.keys()):
                if channel_id in guild_channel_ids:
                    del store[channel_id]

        await self.bot.grant(ctx, "Cleared all snipes for this server.")

    async def get_dominant_color(self, image_url):
        """Extract dominant color from an image"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(image_url) as resp:
                    if resp.status == 200:
                        img = Image.open(io.BytesIO(await resp.read()))
                        img = img.convert('RGB')
                        img.thumbnail((100, 100))
                        
                        pixels = img.getdata()
                        r, g, b = 0, 0, 0
                        count = 0
                        
                        for pixel in pixels:
                            r += pixel[0]
                            g += pixel[1]
                            b += pixel[2]
                            count += 1
                        
                        r, g, b = r // count, g // count, b // count
                        return (r, g, b)
        except Exception as e:
            logger("error", f"Failed to extract dominant color: {e}")
        return None

    @commands.command(name="dominant")
    async def dominant(self, ctx, user: discord.User = None):
        """Get the dominant color from a user's avatar"""
        user = user or ctx.author
        
        async with ctx.typing():
            color_rgb = await self.get_dominant_color(str(user.display_avatar.url))
        
        if not color_rgb:
            return await self.bot.deny(ctx, "Failed to extract dominant color.")
        
        r, g, b = color_rgb
        hex_color = f"#{r:02x}{g:02x}{b:02x}".upper()
        
        embed = discord.Embed(color=discord.Color.from_rgb(r, g, b), title=f"Dominant Color - {user}")
        embed.add_field(name="RGB", value=f"{r}, {g}, {b}", inline=True)
        embed.add_field(name="HEX", value=hex_color, inline=True)
        embed.add_field(name="DEC", value=f"{int(hex_color[1:], 16)}", inline=True)
        
        embed.set_thumbnail(url=user.display_avatar.url)
        await ctx.send(embed=embed)

    @commands.command(name="color")
    async def color(self, ctx, color_input: str = None):
        """Look up color information"""
        if not color_input:
            return await ctx.send_help(ctx.command)
        
        try:
            if color_input.startswith('#'):
                hex_color = color_input
            else:
                hex_color = f"#{color_input}"
            
            if len(hex_color) not in [4, 7]:
                return await self.bot.warn(ctx, "Invalid color format. Use HEX (#RRGGBB or #RGB).")
            
            hex_color = hex_color.lstrip('#')
            if len(hex_color) == 3:
                hex_color = ''.join([c*2 for c in hex_color])
            
            r = int(hex_color[0:2], 16)
            g = int(hex_color[2:4], 16)
            b = int(hex_color[4:6], 16)
            
            h, s, l = colorsys.rgb_to_hls(r/255, g/255, b/255)
            h = int(h * 360)
            s = int(s * 100)
            l = int(l * 100)
            
            color_hex = f"#{hex_color}".upper()
            dec_color = int(hex_color, 16)
            
            embed = discord.Embed(color=discord.Color.from_rgb(r, g, b), title=f"Color: {color_hex}")
            embed.add_field(name="RGB", value=f"({r}, {g}, {b})", inline=True)
            embed.add_field(name="HEX", value=color_hex, inline=True)
            embed.add_field(name="DEC", value=dec_color, inline=True)
            embed.add_field(name="HSL", value=f"({h}°, {s}%, {l}%)", inline=True)
            
            img = Image.new('RGB', (200, 200), (r, g, b))
            with io.BytesIO() as image_binary:
                img.save(image_binary, 'PNG')
                image_binary.seek(0)
                embed.set_image(url="attachment://color.png")
                await ctx.send(embed=embed, file=discord.File(fp=image_binary, filename="color.png"))
        except ValueError:
            return await self.bot.warn(ctx, "Invalid color format. Use HEX (#RRGGBB or #RGB).")

    @commands.command(name="afk")
    async def afk(self, ctx, *, message: str = None):
        """Set your AFK status"""
        message = message or "I'm AFK"
        
        await AFK.find(
            AFK.guild_id == ctx.guild.id,
            AFK.user_id == ctx.author.id
        ).delete()
        
        afk_record = AFK(
            guild_id=ctx.guild.id,
            user_id=ctx.author.id,
            message=message,
            timestamp=datetime.utcnow()
        )
        await afk_record.insert()
        
        await self.bot.grant(ctx, f"You're now AFK: **{message}**")

    async def download_image(self, url: str) -> bytes:
        """Download image from URL"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        return await resp.read()
        except Exception as e:
            logger("error", f"Failed to download image: {e}")
        return None

    @commands.group(name="set", invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    @commands.bot_has_permissions(manage_guild=True)
    async def set_group(self, ctx):
        """Manage server settings"""
        await ctx.send_help(ctx.command)

    @set_group.command(name="icon")
    async def set_icon(self, ctx, image_url: str = None):
        """Set the server icon"""
        image_url = image_url or (ctx.message.attachments[0].url if ctx.message.attachments else None)
        
        if not image_url:
            return await ctx.send_help(ctx.command)
        
        async with ctx.typing():
            image_data = await self.download_image(image_url)
        
        if not image_data:
            return await self.bot.deny(ctx, "Failed to download image.")
        
        try:
            await ctx.guild.edit(icon=image_data)
            await self.bot.grant(ctx, "Server icon updated.")
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to update icon: {str(e)}")

    @set_group.command(name="banner")
    async def set_banner(self, ctx, image_url: str = None):
        """Set the server banner"""
        image_url = image_url or (ctx.message.attachments[0].url if ctx.message.attachments else None)
        
        if not image_url:
            return await ctx.send_help(ctx.command)
        
        async with ctx.typing():
            image_data = await self.download_image(image_url)
        
        if not image_data:
            return await self.bot.deny(ctx, "Failed to download image.")
        
        try:
            await ctx.guild.edit(banner=image_data)
            await self.bot.grant(ctx, "Server banner updated.")
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to update banner: {str(e)}")

    @set_group.command(name="splash")
    async def set_splash(self, ctx, image_url: str = None):
        """Set the server splash (invite background)"""
        image_url = image_url or (ctx.message.attachments[0].url if ctx.message.attachments else None)
        
        if not image_url:
            return await ctx.send_help(ctx.command)
        
        async with ctx.typing():
            image_data = await self.download_image(image_url)
        
        if not image_data:
            return await self.bot.deny(ctx, "Failed to download image.")
        
        try:
            await ctx.guild.edit(splash=image_data)
            await self.bot.grant(ctx, "Server splash updated.")
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to update splash: {str(e)}")

    @commands.group(name="remove", invoke_without_command=True)
    @commands.has_permissions(manage_guild=True)
    @commands.bot_has_permissions(manage_guild=True)
    async def remove_group(self, ctx):
        """Remove server settings"""
        await ctx.send_help(ctx.command)

    @remove_group.command(name="icon")
    async def remove_icon(self, ctx):
        """Remove the server icon"""
        try:
            await ctx.guild.edit(icon=None)
            await self.bot.grant(ctx, "Server icon removed.")
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to remove icon: {str(e)}")

    @remove_group.command(name="banner")
    async def remove_banner(self, ctx):
        """Remove the server banner"""
        try:
            await ctx.guild.edit(banner=None)
            await self.bot.grant(ctx, "Server banner removed.")
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to remove banner: {str(e)}")

    @remove_group.command(name="splash")
    async def remove_splash(self, ctx):
        """Remove the server splash"""
        try:
            await ctx.guild.edit(splash=None)
            await self.bot.grant(ctx, "Server splash removed.")
        except Exception as e:
            await self.bot.deny(ctx, f"Failed to remove splash: {str(e)}")

    async def fetch_crypto_data(self, crypto_id: str):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"https://api.coingecko.com/api/v3/coins/{crypto_id}") as resp:
                    if resp.status == 200:
                        return await resp.json()
        except Exception as e:
            return None

    def create_crypto_embed(self, data):
        name = data.get('name', 'Unknown')
        symbol = data.get('symbol', '').upper()
        market_data = data.get('market_data', {})
        
        current_price = market_data.get('current_price', {}).get('usd', 0)
        market_cap = market_data.get('market_cap', {}).get('usd', 0)
        volume = market_data.get('total_volume', {}).get('usd', 0)
        ath = market_data.get('ath', {}).get('usd', 0)
        atl = market_data.get('atl', {}).get('usd', 0)
        price_change_24h = market_data.get('price_change_percentage_24h', 0)
        price_change_7d = market_data.get('price_change_percentage_7d', 0)
        circulating_supply = market_data.get('circulating_supply', 0)
        max_supply = market_data.get('max_supply', 0)

        embed = discord.Embed(
            title=f"{name} ({symbol})",
            url=data.get('links', {}).get('homepage', [''])[0],
            color=0x242429,
            description=data.get('description', {}).get('en', 'No description available')[:300]
        )

        embed.add_field(
            name="Price",
            value=f"${current_price:,.2f}" if current_price else "N/A",
            inline=True
        )
        
        price_change_color = "📈" if price_change_24h >= 0 else "📉"
        embed.add_field(
            name="24h Change",
            value=f"{price_change_color} {price_change_24h:.2f}%",
            inline=True
        )

        embed.add_field(
            name="7d Change",
            value=f"{price_change_7d:.2f}%",
            inline=True
        )

        if market_cap:
            embed.add_field(
                name="Market Cap",
                value=f"${market_cap:,.0f}",
                inline=True
            )

        if volume:
            embed.add_field(
                name="24h Volume",
                value=f"${volume:,.0f}",
                inline=True
            )

        if ath and atl:
            embed.add_field(
                name="All-Time High/Low",
                value=f"H: ${ath:,.2f} / L: ${atl:,.2f}",
                inline=True
            )

        if circulating_supply:
            supply_text = f"Circulating: {circulating_supply:,.0f}"
            if max_supply:
                supply_text += f" / Max: {max_supply:,.0f}"
            embed.add_field(
                name="Supply",
                value=supply_text,
                inline=False
            )

        if data.get('image', {}).get('large'):
            embed.set_thumbnail(url=data['image']['large'])

        embed.set_footer(text="Data from CoinGecko")
        return embed

    @commands.group(name="transaction", aliases=["tx"], invoke_without_command=True)
    async def transaction(self, ctx):
        """Cryptocurrency transaction lookup"""
        await ctx.send_help(ctx.command)

    @transaction.command(name="lookup", aliases=["search"])
    async def transaction_lookup(self, ctx, hash_id: str = None):
        """Lookup a transaction by hash"""
        if not hash_id:
            return await ctx.send_help(ctx.command)

        coin = 'eth' if hash_id.startswith('0x') else 'btc'

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"https://api.blockcypher.com/v1/{coin}/main/txs/{hash_id}") as resp:
                    if resp.status != 200:
                        raise Exception()
                    data = await resp.json()

                    confirmations = data.get('confirmations', 0)
                    raw_value = data.get('total', 0)
                    value = raw_value / 1e18 if coin == 'eth' else raw_value / 1e8
                    
                    status = '✅ Confirmed' if confirmations > 0 else '⏳ Pending'
                    dt = datetime.fromisoformat(data['received'].replace('Z', '+00:00'))
                    timestamp = int(dt.timestamp())

                    embed = discord.Embed(
                        title=f"🔍 {coin.upper()} Transaction",
                        url=f"https://live.blockcypher.com/{coin}/tx/{data['hash']}",
                        color=0x43b581 if confirmations > 0 else 0xfaa61a
                    )
                    embed.add_field(name='Status', value=status, inline=True)
                    embed.add_field(name='Confirmations', value=f"`{confirmations}`", inline=True)
                    embed.add_field(name='Value', value=f"`{value:.6f} {coin.upper()}`", inline=False)
                    embed.add_field(name='Time', value=f"<t:{timestamp}:R>", inline=True)
                    await ctx.send(embed=embed)
        except:
            await self.bot.warn(ctx, "Transaction not found or API error.")

    @transaction.command(name="track", aliases=["watch"])
    async def transaction_track(self, ctx, hash_id: str = None):
        """Track a pending transaction"""
        if not hash_id:
            return await ctx.send_help(ctx.command)
        
        await self.bot.warn(ctx, "Transaction tracking coming soon")

    @transaction.command(name="cancel", aliases=["stop"])
    async def transaction_cancel(self, ctx, hash_id: str = None):
        """Cancel tracking a transaction"""
        if not hash_id:
            return await ctx.send_help(ctx.command)
        
        await self.bot.warn(ctx, "No active tracking for this transaction.")

    @commands.command(name="crypto")
    async def crypto(self, ctx, *, crypto_name: str = None):
        """View advanced information about a cryptocurrency"""
        if not crypto_name:
            return await ctx.send_help(ctx.command)

        async with ctx.typing():
            data = await self.fetch_crypto_data(crypto_name.lower())

        if not data:
            return await self.bot.warn(ctx, f"Cryptocurrency '{crypto_name}' not found.")

        embed = self.create_crypto_embed(data)
        await ctx.send(embed=embed)

    async def get_target_bytes(self, ctx, url: str = None):
        """Fetches the image and returns it as BytesIO for processing"""
        if not url:
            if ctx.message.attachments:
                url = ctx.message.attachments[0].url
            elif ctx.message.reference and ctx.message.reference.resolved.attachments:
                url = ctx.message.reference.resolved.attachments[0].url
            else:
                url = str(ctx.author.display_avatar.url)

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200: return None
                return BytesIO(await resp.read())

    async def _fetch_image_bytes(self, ctx, url: str = None, attachment_index: int = 0):
        if url:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return None
                    return await resp.read()

        if ctx.message.attachments and len(ctx.message.attachments) > attachment_index:
            return await ctx.message.attachments[attachment_index].read()

        if attachment_index == 0:
            data = await self.get_target_bytes(ctx, None)
            return data.getvalue() if data else None
        return None

    def _open_rgba_image(self, raw: bytes, size: int = 320):
        if not raw:
            return None

        with Image.open(BytesIO(raw)).convert("RGBA") as source:
            img = source.copy()

        img.thumbnail((size, size), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        x = (size - img.width) // 2
        y = (size - img.height) // 2
        canvas.paste(img, (x, y), img)
        return canvas

    def _gif_bytes(self, frames, duration: int = 70):
        if not frames:
            return None

        converted = [f.convert("RGBA") for f in frames]
        out = BytesIO()
        converted[0].save(
            out,
            format="GIF",
            save_all=True,
            append_images=converted[1:],
            loop=0,
            duration=duration,
            disposal=2,
            optimize=False,
        )
        out.seek(0)
        return out

    def _wave_distort(self, img: Image.Image, phase: float, amplitude: int = 8, wavelength: float = 24.0):
        w, h = img.size
        out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        for y in range(h):
            dx = int(amplitude * math.sin((2 * math.pi * y / wavelength) + phase))
            row = img.crop((0, y, w, y + 1))
            out.paste(row, (dx, y))
        return out

    def _build_animation_frames(self, effect: str, base: Image.Image, secondary: Image.Image = None):
        frames = []
        count = 18
        w, h = base.size

        for i in range(count):
            t = i / count

            if effect == "spin":
                frame = base.rotate(i * (360 / count), resample=Image.Resampling.BICUBIC)

            elif effect == "roll":
                frame = base.rotate(i * (360 / count), resample=Image.Resampling.BICUBIC)
                frame = ImageChops.offset(frame, int((i * 10) % w), 0)

            elif effect == "wiggle":
                frame = ImageChops.offset(base, int(math.sin(t * math.pi * 2) * 12), 0)

            elif effect == "earthquake":
                frame = ImageChops.offset(base, random.randint(-14, 14), random.randint(-10, 10))

            elif effect == "ripple":
                frame = self._wave_distort(base, t * math.pi * 2, amplitude=6, wavelength=16)

            elif effect == "wave":
                frame = self._wave_distort(base, t * math.pi * 2, amplitude=12, wavelength=30)

            elif effect == "glitch":
                r, g, b, a = base.split()
                r = ImageChops.offset(r, random.randint(-8, 8), 0)
                b = ImageChops.offset(b, random.randint(-8, 8), 0)
                frame = Image.merge("RGBA", (r, g, b, a))

            elif effect == "triggered":
                jitter = ImageChops.offset(base, random.randint(-10, 10), random.randint(-6, 6))
                red = Image.new("RGBA", (w, h), (255, 0, 0, 90))
                frame = Image.alpha_composite(jitter, red)
                draw = ImageDraw.Draw(frame)
                draw.rectangle((0, h - 48, w, h), fill=(0, 0, 0, 180))
                draw.text((10, h - 38), "TRIGGERED", fill=(255, 60, 60, 255))

            elif effect == "wasted":
                gray = ImageOps.grayscale(base).convert("RGBA")
                draw = ImageDraw.Draw(gray)
                draw.rectangle((0, h // 2 - 24, w, h // 2 + 24), fill=(0, 0, 0, 170))
                draw.text((w // 2 - 34, h // 2 - 8), "WASTED", fill=(255, 255, 255, 255))
                frame = ImageChops.offset(gray, random.randint(-2, 2), random.randint(-2, 2))

            elif effect == "boil":
                frame = ImageChops.offset(base.filter(ImageFilter.GaussianBlur(radius=1.2)), random.randint(-6, 6), random.randint(-6, 6))

            elif effect == "magik":
                squish = 0.85 + 0.2 * math.sin(t * math.pi * 2)
                nw = max(16, int(w * squish))
                nh = max(16, int(h * (2 - squish)))
                tmp = base.resize((nw, nh), Image.Resampling.BICUBIC).resize((w, h), Image.Resampling.BICUBIC)
                frame = ImageChops.offset(tmp, random.randint(-4, 4), random.randint(-4, 4))

            elif effect == "fan":
                frame = base.rotate(i * (720 / count), resample=Image.Resampling.BICUBIC)

            elif effect == "3d":
                left = ImageChops.offset(base, -3, 0)
                right = ImageChops.offset(base, 3, 0)
                r, _, _, a = right.split()
                _, g, b, _ = left.split()
                frame = Image.merge("RGBA", (r, g, b, a))

            elif effect == "shine":
                frame = base.copy()
                overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                draw = ImageDraw.Draw(overlay)
                x = int((w + 80) * t) - 80
                draw.polygon([(x, 0), (x + 28, 0), (x - 28, h), (x - 56, h)], fill=(255, 255, 255, 90))
                frame = Image.alpha_composite(frame, overlay)

            elif effect == "fire":
                frame = base.copy()
                overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                draw = ImageDraw.Draw(overlay)
                for _ in range(22):
                    fx = random.randint(0, w)
                    fy = random.randint(int(h * 0.6), h)
                    r = random.randint(8, 20)
                    draw.ellipse((fx - r, fy - r, fx + r, fy + r), fill=(255, random.randint(80, 180), 0, 120))
                frame = Image.alpha_composite(frame, overlay)

            elif effect == "rain":
                frame = base.copy()
                overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                draw = ImageDraw.Draw(overlay)
                for _ in range(60):
                    rx = random.randint(0, w)
                    ry = (random.randint(0, h) + i * 18) % (h + 20)
                    draw.line((rx, ry, rx - 4, ry + 14), fill=(130, 170, 255, 150), width=1)
                frame = Image.alpha_composite(frame, overlay)

            elif effect == "hearts":
                frame = base.copy()
                overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                draw = ImageDraw.Draw(overlay)
                for _ in range(12):
                    hx = random.randint(0, w)
                    hy = (h - random.randint(0, h) - i * 10) % (h + 30)
                    draw.text((hx, hy), "♥", fill=(255, random.randint(80, 180), random.randint(150, 220), 170))
                frame = Image.alpha_composite(frame, overlay)

            elif effect == "shoot":
                frame = base.copy()
                overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                draw = ImageDraw.Draw(overlay)
                sx = int((w + 40) * t) - 20
                sy = int(h * 0.2 + math.sin(t * 8) * 18)
                draw.line((sx - 35, sy + 10, sx, sy), fill=(255, 255, 180, 160), width=3)
                draw.ellipse((sx - 7, sy - 7, sx + 7, sy + 7), fill=(255, 255, 230, 230))
                frame = Image.alpha_composite(frame, overlay)

            elif effect == "shock":
                frame = base.copy()
                overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                draw = ImageDraw.Draw(overlay)
                for _ in range(4):
                    x1, y1 = random.randint(0, w), random.randint(0, h)
                    x2, y2 = x1 + random.randint(-50, 50), y1 + random.randint(-50, 50)
                    draw.line((x1, y1, x2, y2), fill=(120, 200, 255, 180), width=2)
                frame = Image.alpha_composite(frame, overlay)

            elif effect == "bomb":
                frame = base.copy()
                overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                draw = ImageDraw.Draw(overlay)
                cx, cy = w // 2, h // 2
                radius = int(10 + t * (w // 2))
                alpha = max(0, 180 - i * 10)
                draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=(255, 170, 40, alpha), width=6)
                frame = Image.alpha_composite(frame, overlay)

            elif effect == "patpat":
                squeeze = 1 - (0.12 if (i % 3 == 0) else 0.02)
                nh = max(16, int(h * squeeze))
                compressed = base.resize((w, nh), Image.Resampling.BICUBIC)
                frame = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                frame.paste(compressed, (0, h - nh), compressed)
                draw = ImageDraw.Draw(frame)
                draw.ellipse((w // 2 - 52, 4, w // 2 + 52, 46), fill=(245, 245, 245, 230))

            elif effect == "heart":
                other = secondary if secondary is not None else base
                blend = Image.blend(base, other, (math.sin(t * math.pi * 2) + 1) / 2)
                mask = Image.new("L", (w, h), 0)
                d = ImageDraw.Draw(mask)
                d.ellipse((w * 0.2, h * 0.18, w * 0.55, h * 0.5), fill=255)
                d.ellipse((w * 0.45, h * 0.18, w * 0.8, h * 0.5), fill=255)
                d.polygon([(w * 0.15, h * 0.38), (w * 0.85, h * 0.38), (w * 0.5, h * 0.9)], fill=255)
                frame = Image.new("RGBA", (w, h), (35, 0, 35, 255))
                frame.paste(blend, (0, 0), mask)

            elif effect == "burn":
                frame = ImageChops.offset(base, random.randint(-2, 2), random.randint(-2, 2))
                overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                draw = ImageDraw.Draw(overlay)
                for _ in range(18):
                    bx = random.randint(0, w)
                    by = random.randint(int(h * 0.45), h)
                    br = random.randint(10, 30)
                    draw.ellipse((bx - br, by - br, bx + br, by + br), fill=(255, random.randint(80, 180), 0, 110))
                frame = Image.alpha_composite(frame, overlay)

            elif effect == "dizzy":
                angle = math.sin(t * math.pi * 2) * 22 + (i * 6)
                scale = 0.86 + 0.16 * (math.sin(t * math.pi * 2) + 1) / 2
                nw = max(16, int(w * scale))
                nh = max(16, int(h * scale))
                spin = base.rotate(angle, resample=Image.Resampling.BICUBIC)
                scaled = spin.resize((nw, nh), Image.Resampling.BICUBIC)
                frame = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                frame.paste(scaled, ((w - nw) // 2, (h - nh) // 2), scaled)

            elif effect == "endless":
                frame = Image.new("RGBA", (w, h), (0, 0, 0, 255))
                for k in range(8):
                    frac = (k + (i / count)) / 8
                    scale = max(0.08, 1 - frac)
                    nw = max(8, int(w * scale))
                    nh = max(8, int(h * scale))
                    layer = base.resize((nw, nh), Image.Resampling.BICUBIC)
                    alpha = int(255 * (1 - frac * 0.75))
                    layer.putalpha(alpha)
                    frame.paste(layer, ((w - nw) // 2, (h - nh) // 2), layer)

            elif effect == "infinity":
                mirror = ImageOps.mirror(base)
                zoom = 0.92 - 0.22 * (math.sin(t * math.pi * 2) + 1) / 2
                zw = max(16, int(w * zoom))
                zh = max(16, int(h * zoom))
                left = base.resize((zw, zh), Image.Resampling.BICUBIC)
                right = mirror.resize((zw, zh), Image.Resampling.BICUBIC)
                frame = Image.new("RGBA", (w, h), (0, 0, 0, 255))
                frame.paste(left, (w // 2 - zw, (h - zh) // 2), left)
                frame.paste(right, (w // 2, (h - zh) // 2), right)

            elif effect == "melt":
                frame = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                for x in range(0, w, 4):
                    strip_w = min(4, w - x)
                    strip = base.crop((x, 0, x + strip_w, h))
                    dy = int((math.sin((x / 18) + t * math.pi * 2) + 1) * 6)
                    frame.paste(strip, (x, dy), strip)

            elif effect == "phase":
                wave_x = self._wave_distort(base, t * math.pi * 2, amplitude=8, wavelength=26)
                wave_y = self._wave_distort(base.rotate(90, expand=False), t * math.pi * 2, amplitude=5, wavelength=22).rotate(-90, expand=False)
                frame = Image.blend(wave_x, wave_y, 0.5)

            elif effect == "poly":
                cell = max(6, int(24 - (math.sin(t * math.pi * 2) + 1) * 6))
                small = base.resize((max(8, w // cell), max(8, h // cell)), Image.Resampling.NEAREST)
                frame = small.resize((w, h), Image.Resampling.NEAREST)

            elif effect == "pyramid":
                frame = Image.new("RGBA", (w, h), (0, 0, 0, 255))
                levels = 6
                for lv in range(levels):
                    frac = 1 - (lv / (levels + 1))
                    pulse = 0.92 + 0.08 * math.sin(t * math.pi * 2 + lv)
                    scale = max(0.1, frac * pulse)
                    nw = max(12, int(w * scale))
                    nh = max(12, int(h * scale))
                    layer = base.resize((nw, nh), Image.Resampling.BICUBIC)
                    alpha = int(220 - lv * 28)
                    layer.putalpha(alpha)
                    yoff = int((h - nh) // 2 + lv * 3)
                    frame.paste(layer, ((w - nw) // 2, yoff), layer)

            elif effect == "shear":
                dx = int(math.sin(t * math.pi * 2) * 26)
                frame = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                for y in range(h):
                    row = base.crop((0, y, w, y + 1))
                    shift = int(dx * (y / max(1, h - 1)))
                    frame.paste(row, (shift, y))

            elif effect == "shred":
                frame = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                strips = 12
                sw = max(1, w // strips)
                for s in range(strips):
                    x0 = s * sw
                    x1 = w if s == strips - 1 else x0 + sw
                    part = base.crop((x0, 0, x1, h))
                    dx = int(math.sin(t * math.pi * 2 + s * 0.7) * 14)
                    frame.paste(part, (x0 + dx, 0), part)

            elif effect == "slice":
                frame = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                slices = 10
                sh = max(1, h // slices)
                for s in range(slices):
                    y0 = s * sh
                    y1 = h if s == slices - 1 else y0 + sh
                    part = base.crop((0, y0, w, y1))
                    dx = int(math.sin(t * math.pi * 2 + s) * 18)
                    frame.paste(part, (dx, y0), part)

            elif effect == "stretch":
                sx = 0.82 + 0.28 * ((math.sin(t * math.pi * 2) + 1) / 2)
                sy = 2 - sx
                nw = max(16, int(w * sx))
                nh = max(16, int(h * sy))
                scaled = base.resize((nw, nh), Image.Resampling.BICUBIC)
                frame = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                frame.paste(scaled, ((w - nw) // 2, (h - nh) // 2), scaled)

            elif effect == "ads":
                frame = base.copy()
                draw = ImageDraw.Draw(frame)
                draw.rectangle((0, 0, w, 28), fill=(15, 15, 15, 220))
                draw.rectangle((0, h - 42, w, h), fill=(0, 0, 0, 210))
                draw.text((10, 8), "SPONSORED", fill=(255, 215, 70, 255))
                blink = 180 if i % 2 == 0 else 80
                draw.text((10, h - 30), "BUY NOW", fill=(255, 80, 80, blink))

            elif effect == "bayer":
                gray = ImageOps.grayscale(base).convert("RGB")
                dithered = gray.convert("1", dither=Image.Dither.FLOYDSTEINBERG).convert("RGBA")
                frame = dithered

            elif effect == "bevel":
                emboss = base.filter(ImageFilter.EMBOSS)
                frame = Image.blend(base, emboss.convert("RGBA"), 0.45)

            elif effect == "billboard":
                bg = Image.new("RGBA", (w, h), (20, 20, 24, 255))
                board_w, board_h = int(w * 0.78), int(h * 0.55)
                panel = base.resize((board_w, board_h), Image.Resampling.BICUBIC)
                x = (w - board_w) // 2 + int(math.sin(t * math.pi * 2) * 6)
                y = int(h * 0.14)
                bg.paste(panel, (x, y), panel)
                draw = ImageDraw.Draw(bg)
                draw.rectangle((x - 6, y - 6, x + board_w + 6, y + board_h + 6), outline=(210, 210, 210, 255), width=3)
                draw.rectangle((x + board_w // 2 - 7, y + board_h + 6, x + board_w // 2 + 7, h), fill=(110, 110, 115, 255))
                frame = bg

            elif effect == "cube":
                s = int(min(w, h) * 0.42)
                face = base.resize((s, s), Image.Resampling.BICUBIC)
                top = face.resize((s, int(s * 0.55)), Image.Resampling.BICUBIC)
                side = face.resize((int(s * 0.55), s), Image.Resampling.BICUBIC)
                frame = Image.new("RGBA", (w, h), (10, 10, 14, 255))
                cx, cy = w // 2, h // 2
                front = face.rotate(math.sin(t * math.pi * 2) * 4, resample=Image.Resampling.BICUBIC)
                frame.paste(front, (cx - s // 2, cy - s // 2 + 18), front)
                top_overlay = Image.new("RGBA", top.size, (255, 255, 255, 40))
                top = Image.alpha_composite(top.convert("RGBA"), top_overlay)
                frame.paste(top, (cx - s // 2, cy - s // 2 - top.height + 18), top)
                side_overlay = Image.new("RGBA", side.size, (0, 0, 0, 55))
                side = Image.alpha_composite(side.convert("RGBA"), side_overlay)
                frame.paste(side, (cx + s // 2, cy - s // 2 + 18), side)

            elif effect == "emojify":
                tiny = base.resize((32, 32), Image.Resampling.BILINEAR)
                frame = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                draw = ImageDraw.Draw(frame)
                cell = w // 32
                for yy in range(32):
                    for xx in range(32):
                        r, g, b, a = tiny.getpixel((xx, yy))
                        if a < 20:
                            continue
                        ch = "🟨" if (r + g) > (b + 150) else ("🟦" if b > r else "🟥")
                        draw.text((xx * cell, yy * cell), ch, fill=(r, g, b, 230))

            elif effect == "flag2":
                frame = self._wave_distort(base, t * math.pi * 2, amplitude=10, wavelength=34)
                draw = ImageDraw.Draw(frame)
                draw.rectangle((0, 0, 8, h), fill=(120, 120, 120, 255))

            elif effect == "gameboy":
                gray = ImageOps.grayscale(base)
                pal = [(15, 56, 15), (48, 98, 48), (139, 172, 15), (155, 188, 15)]
                def gb(px):
                    idx = min(3, px // 64)
                    return pal[idx]
                rgb = Image.new("RGB", gray.size)
                px_in = gray.load(); px_out = rgb.load()
                for yy in range(h):
                    for xx in range(w):
                        px_out[xx, yy] = gb(px_in[xx, yy])
                frame = rgb.convert("RGBA")

            elif effect == "half_invert":
                rgb = base.convert("RGB")
                inv = ImageOps.invert(rgb).convert("RGBA")
                split = int((math.sin(t * math.pi * 2) + 1) * 0.5 * w)
                frame = base.copy()
                frame.paste(inv.crop((0, 0, split, h)), (0, 0))

            elif effect == "letters":
                gray = ImageOps.grayscale(base)
                frame = Image.new("RGBA", (w, h), (0, 0, 0, 255))
                draw = ImageDraw.Draw(frame)
                chars = "@#W$9876543210?!abc;:+=-,._ "
                step = 10
                for yy in range(0, h, step):
                    for xx in range(0, w, step):
                        val = gray.getpixel((xx, yy))
                        ch = chars[min(len(chars) - 1, int(val / 255 * (len(chars) - 1)))]
                        draw.text((xx, yy), ch, fill=(200, 220, 255, 255))

            elif effect == "lines":
                frame = base.copy()
                draw = ImageDraw.Draw(frame)
                for yy in range(0, h, 6):
                    alpha = 90 if ((yy + i) // 6) % 2 == 0 else 30
                    draw.line((0, yy, w, yy), fill=(0, 0, 0, alpha), width=1)

            elif effect == "lsd":
                r, g, b, a = base.split()
                r = ImageChops.offset(r, int(8 * math.sin(t * math.pi * 2)), 0)
                g = ImageChops.offset(g, 0, int(8 * math.cos(t * math.pi * 2)))
                b = ImageChops.offset(b, int(6 * math.cos(t * math.pi * 2)), int(6 * math.sin(t * math.pi * 2)))
                frame = Image.merge("RGBA", (r, g, b, a))

            elif effect == "matrix":
                frame = ImageOps.grayscale(base).convert("RGBA")
                green = Image.new("RGBA", (w, h), (0, 200, 70, 80))
                frame = Image.alpha_composite(frame, green)
                draw = ImageDraw.Draw(frame)
                glyphs = "01アイウエオカキクケコ"
                for col in range(0, w, 16):
                    y0 = int((i * 16 + col * 0.3) % (h + 40)) - 40
                    for r0 in range(6):
                        y = y0 + r0 * 16
                        if 0 <= y < h:
                            draw.text((col, y), random.choice(glyphs), fill=(120, 255, 160, max(60, 220 - r0 * 30)))

            elif effect == "minecraft":
                tiny = base.resize((20, 20), Image.Resampling.NEAREST)
                frame = tiny.resize((w, h), Image.Resampling.NEAREST)
                draw = ImageDraw.Draw(frame)
                cell = w // 20
                for k in range(0, w, cell):
                    draw.line((k, 0, k, h), fill=(0, 0, 0, 40), width=1)
                    draw.line((0, k, w, k), fill=(0, 0, 0, 40), width=1)

            elif effect == "neon":
                edge = base.convert("RGB").filter(ImageFilter.FIND_EDGES).convert("RGBA")
                glow = edge.filter(ImageFilter.GaussianBlur(radius=2.0))
                tint = Image.new("RGBA", (w, h), (80, 255, 220, 90 if i % 2 == 0 else 140))
                frame = Image.alpha_composite(base, glow)
                frame = Image.alpha_composite(frame, tint)

            elif effect == "optics":
                zoom = 0.88 + 0.2 * ((math.sin(t * math.pi * 2) + 1) / 2)
                nw = max(16, int(w * zoom))
                nh = max(16, int(h * zoom))
                z = base.resize((nw, nh), Image.Resampling.BICUBIC)
                frame = Image.new("RGBA", (w, h), (0, 0, 0, 255))
                frame.paste(z, ((w - nw) // 2, (h - nh) // 2), z)
                frame = self._wave_distort(frame, t * math.pi * 2, amplitude=4, wavelength=20)

            elif effect == "pattern":
                tile = base.resize((max(24, w // 4), max(24, h // 4)), Image.Resampling.BICUBIC)
                frame = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                ox = int((i * 8) % tile.width)
                oy = int((i * 6) % tile.height)
                for yy in range(-tile.height, h + tile.height, tile.height):
                    for xx in range(-tile.width, w + tile.width, tile.width):
                        frame.paste(tile, (xx + ox, yy + oy), tile)

            elif effect == "sensitive":
                frame = base.filter(ImageFilter.GaussianBlur(radius=2.4)).convert("RGBA")
                draw = ImageDraw.Draw(frame)
                draw.rectangle((0, h // 2 - 24, w, h // 2 + 24), fill=(0, 0, 0, 200))
                draw.text((12, h // 2 - 10), "SENSITIVE CONTENT", fill=(255, 255, 255, 255))

            elif effect == "soap":
                frame = base.copy()
                overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                draw = ImageDraw.Draw(overlay)
                for _ in range(22):
                    bx = random.randint(0, w)
                    by = (random.randint(0, h) - i * 7) % (h + 20)
                    br = random.randint(6, 22)
                    draw.ellipse((bx - br, by - br, bx + br, by + br), outline=(190, 220, 255, 170), width=2)
                frame = Image.alpha_composite(frame, overlay)

            elif effect == "stereo":
                left = ImageChops.offset(base, -4, 0)
                right = ImageChops.offset(base, 4, 0)
                r, _, _, a = right.split()
                _, g, b, _ = left.split()
                frame = Image.merge("RGBA", (r, g, b, a))

            elif effect == "tiles":
                frame = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                grid = 4
                tw = w // grid
                th = h // grid
                for gy in range(grid):
                    for gx in range(grid):
                        x0, y0 = gx * tw, gy * th
                        tile = base.crop((x0, y0, x0 + tw, y0 + th))
                        ang = math.sin(t * math.pi * 2 + gx + gy) * 9
                        rot = tile.rotate(ang, resample=Image.Resampling.BICUBIC)
                        frame.paste(rot, (x0, y0), rot)

            elif effect == "tv":
                frame = Image.new("RGBA", (w, h), (28, 28, 34, 255))
                screen = base.resize((int(w * 0.78), int(h * 0.62)), Image.Resampling.BICUBIC)
                sx, sy = (w - screen.width) // 2, int(h * 0.14)
                frame.paste(screen, (sx, sy), screen)
                draw = ImageDraw.Draw(frame)
                draw.rectangle((sx - 8, sy - 8, sx + screen.width + 8, sy + screen.height + 8), outline=(120, 120, 130, 255), width=5)
                for yy in range(sy, sy + screen.height, 5):
                    draw.line((sx, yy, sx + screen.width, yy), fill=(0, 0, 0, 25), width=1)
                draw.rectangle((w // 2 - 14, sy + screen.height + 10, w // 2 + 14, h - 8), fill=(80, 80, 90, 255))

            elif effect == "wall":
                frame = base.copy()
                draw = ImageDraw.Draw(frame)
                for yy in range(0, h, 22):
                    draw.line((0, yy, w, yy), fill=(210, 210, 210, 35), width=2)
                for xx in range(0, w, 44):
                    draw.line((xx, 0, xx, h), fill=(180, 180, 180, 25), width=2)
                shade = Image.new("RGBA", (w, h), (60, 50, 40, 35))
                frame = Image.alpha_composite(frame, shade)

            elif effect == "equations":
                frame = base.copy()
                draw = ImageDraw.Draw(frame)
                formulas = ["E=mc^2", "f(x)=x^2", "\u2211n", "\u222bx dx", "\u0394v/t", "\u03c0r\u00b2", "a^2+b^2=c^2"]
                for _ in range(12):
                    tx = random.randint(0, max(0, w - 50))
                    ty = (random.randint(0, h) + i * 9) % (h + 30) - 20
                    draw.text((tx, ty), random.choice(formulas), fill=(255, 255, 255, 150))
                fade = Image.new("RGBA", (w, h), (0, 20, 40, 35))
                frame = Image.alpha_composite(frame, fade)

            elif effect == "flush":
                angle = i * (360 / count)
                spun = base.rotate(angle * 1.6, resample=Image.Resampling.BICUBIC)
                frame = Image.new("RGBA", (w, h), (20, 30, 40, 255))
                mask = Image.new("L", (w, h), 0)
                dm = ImageDraw.Draw(mask)
                r = int(min(w, h) * 0.42)
                dm.ellipse((w // 2 - r, h // 2 - r, w // 2 + r, h // 2 + r), fill=255)
                frame.paste(spun, (0, 0), mask)
                ring = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                dr = ImageDraw.Draw(ring)
                dr.ellipse((w // 2 - r - 8, h // 2 - r - 8, w // 2 + r + 8, h // 2 + r + 8), outline=(220, 220, 220, 240), width=12)
                frame = Image.alpha_composite(frame, ring)

            elif effect == "gallery":
                frame = Image.new("RGBA", (w, h), (46, 38, 30, 255))
                art_w, art_h = int(w * 0.66), int(h * 0.66)
                art = base.resize((art_w, art_h), Image.Resampling.BICUBIC)
                x, y = (w - art_w) // 2, (h - art_h) // 2
                frame.paste(art, (x, y), art)
                draw = ImageDraw.Draw(frame)
                draw.rectangle((x - 14, y - 14, x + art_w + 14, y + art_h + 14), outline=(210, 180, 130, 255), width=8)
                glow_x = int((w + 80) * t) - 40
                draw.ellipse((glow_x - 28, 8, glow_x + 28, 36), fill=(255, 245, 210, 70))

            elif effect == "globe":
                frame = Image.new("RGBA", (w, h), (8, 16, 28, 255))
                sphere = base.resize((int(w * 0.76), int(h * 0.76)), Image.Resampling.BICUBIC)
                sphere = ImageChops.offset(sphere, int(i * 7) % max(1, sphere.width), 0)
                sx, sy = (w - sphere.width) // 2, (h - sphere.height) // 2
                mask = Image.new("L", (w, h), 0)
                dm = ImageDraw.Draw(mask)
                dm.ellipse((sx, sy, sx + sphere.width, sy + sphere.height), fill=255)
                frame.paste(sphere, (sx, sy), mask.crop((sx, sy, sx + sphere.width, sy + sphere.height)))
                draw = ImageDraw.Draw(frame)
                draw.ellipse((sx, sy, sx + sphere.width, sy + sphere.height), outline=(190, 220, 255, 210), width=3)
                for k in range(1, 5):
                    x = sx + int(sphere.width * k / 5)
                    draw.arc((sx, sy, sx + sphere.width, sy + sphere.height), 90, 270, fill=(160, 200, 255, 100), width=1)
                    draw.line((x, sy + 8, x, sy + sphere.height - 8), fill=(160, 200, 255, 80), width=1)

            elif effect == "ipcam":
                frame = base.copy()
                draw = ImageDraw.Draw(frame)
                draw.rectangle((0, 0, w, h), outline=(255, 255, 255, 120), width=2)
                draw.rectangle((8, 8, 68, 26), fill=(0, 0, 0, 170))
                rec_color = (255, 70, 70, 255) if i % 2 == 0 else (120, 40, 40, 255)
                draw.ellipse((12, 12, 20, 20), fill=rec_color)
                draw.text((24, 10), "REC", fill=(255, 255, 255, 230))
                draw.text((w - 86, 10), f"{(i*3)%24:02d}:13", fill=(255, 255, 255, 200))

            elif effect == "kanye":
                gray = ImageOps.grayscale(base).convert("RGBA")
                frame = gray.copy()
                draw = ImageDraw.Draw(frame)
                draw.rectangle((0, 0, w, h), outline=(255, 0, 0, 255), width=8)
                draw.rectangle((14, h - 62, w - 14, h - 18), fill=(0, 0, 0, 170))
                draw.text((20, h - 52), "PARENTAL ADVISORY", fill=(255, 255, 255, 230))

            elif effect == "lamp":
                frame = base.copy()
                overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                draw = ImageDraw.Draw(overlay)
                lx, ly = int(w * 0.2), int(h * 0.15)
                for rr in range(140, 20, -12):
                    alpha = max(8, int((rr / 140) * 70))
                    draw.ellipse((lx - rr, ly - rr, lx + rr, ly + rr), fill=(255, 230, 150, alpha))
                draw.rectangle((lx - 5, 0, lx + 5, ly + 30), fill=(80, 80, 80, 255))
                frame = Image.alpha_composite(frame, overlay)

            elif effect == "laundry":
                frame = Image.new("RGBA", (w, h), (205, 210, 218, 255))
                tub = base.rotate(i * (360 / count), resample=Image.Resampling.BICUBIC)
                win = int(min(w, h) * 0.62)
                tub = tub.resize((win, win), Image.Resampling.BICUBIC)
                x, y = (w - win) // 2, (h - win) // 2
                mask = Image.new("L", (win, win), 0)
                dm = ImageDraw.Draw(mask)
                dm.ellipse((0, 0, win, win), fill=255)
                frame.paste(tub, (x, y), mask)
                draw = ImageDraw.Draw(frame)
                draw.ellipse((x - 10, y - 10, x + win + 10, y + win + 10), outline=(120, 130, 145, 255), width=12)

            elif effect == "layers":
                frame = Image.new("RGBA", (w, h), (15, 15, 20, 255))
                for layer in range(4):
                    frac = 1 - layer * 0.12
                    lw, lh = int(w * frac), int(h * frac)
                    layer_img = base.resize((lw, lh), Image.Resampling.BICUBIC)
                    ox = int(math.sin(t * math.pi * 2 + layer) * (10 + layer * 3))
                    oy = int(math.cos(t * math.pi * 2 + layer) * (8 + layer * 2))
                    x = (w - lw) // 2 + ox
                    y = (h - lh) // 2 + oy
                    alpha = 220 - layer * 45
                    layer_img.putalpha(alpha)
                    frame.paste(layer_img, (x, y), layer_img)

            elif effect == "logoff":
                frame = base.copy()
                tint = Image.new("RGBA", (w, h), (28, 92, 180, 85))
                frame = Image.alpha_composite(frame, tint)
                draw = ImageDraw.Draw(frame)
                draw.rectangle((0, h - 62, w, h), fill=(0, 32, 96, 220))
                draw.text((12, h - 46), "Logging off...", fill=(255, 255, 255, 230))
                spinner = ["|", "/", "-", "\\\\"][i % 4]
                draw.text((w - 26, h - 46), spinner, fill=(255, 255, 255, 230))

            elif effect == "magnify":
                frame = base.copy()
                lens_r = int(min(w, h) * 0.18)
                cx = int((w - lens_r * 2) * t) + lens_r
                cy = int(h * 0.45 + math.sin(t * math.pi * 2) * h * 0.18)
                box = (max(0, cx - lens_r), max(0, cy - lens_r), min(w, cx + lens_r), min(h, cy + lens_r))
                crop = base.crop(box).resize((lens_r * 2, lens_r * 2), Image.Resampling.BICUBIC)
                mask = Image.new("L", (lens_r * 2, lens_r * 2), 0)
                dm = ImageDraw.Draw(mask)
                dm.ellipse((0, 0, lens_r * 2, lens_r * 2), fill=255)
                frame.paste(crop, (cx - lens_r, cy - lens_r), mask)
                draw = ImageDraw.Draw(frame)
                draw.ellipse((cx - lens_r, cy - lens_r, cx + lens_r, cy + lens_r), outline=(240, 240, 240, 255), width=4)
                draw.line((cx + lens_r - 4, cy + lens_r - 4, cx + lens_r + 38, cy + lens_r + 34), fill=(230, 230, 230, 255), width=5)

            elif effect == "paparazzi":
                frame = base.copy()
                overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                draw = ImageDraw.Draw(overlay)
                if i % 3 == 0:
                    draw.rectangle((0, 0, w, h), fill=(255, 255, 255, 130))
                for _ in range(6):
                    px = random.randint(0, w)
                    py = random.randint(0, h)
                    draw.ellipse((px - 10, py - 10, px + 10, py + 10), fill=(255, 255, 255, 80))
                frame = Image.alpha_composite(frame, overlay)

            elif effect == "phase_overlay":
                ghost = ImageChops.offset(base, int(8 * math.sin(t * math.pi * 2)), int(6 * math.cos(t * math.pi * 2)))
                frame = Image.blend(base, ghost, 0.5)

            elif effect == "phone":
                frame = Image.new("RGBA", (w, h), (24, 24, 28, 255))
                sw, sh = int(w * 0.68), int(h * 0.84)
                screen = base.resize((sw - 16, sh - 24), Image.Resampling.BICUBIC)
                sx, sy = (w - sw) // 2, (h - sh) // 2
                draw = ImageDraw.Draw(frame)
                draw.rounded_rectangle((sx, sy, sx + sw, sy + sh), radius=28, fill=(8, 8, 10, 255), outline=(120, 120, 130, 255), width=4)
                frame.paste(screen, (sx + 8, sy + 16), screen)
                draw.ellipse((sx + sw // 2 - 6, sy + 6, sx + sw // 2 + 6, sy + 18), fill=(40, 40, 45, 255))

            elif effect == "plank":
                frame = base.copy()
                draw = ImageDraw.Draw(frame)
                plank_h = 26
                for yy in range(0, h, plank_h):
                    col = (130 + (yy // plank_h) % 2 * 20, 95, 62, 55)
                    draw.rectangle((0, yy, w, yy + plank_h - 2), fill=col)
                    for _ in range(3):
                        x0 = random.randint(0, w)
                        draw.line((x0, yy + 3, x0 + random.randint(10, 40), yy + plank_h - 4), fill=(80, 55, 40, 70), width=1)

            elif effect == "plates":
                frame = base.copy()
                overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                draw = ImageDraw.Draw(overlay)
                cx, cy = w // 2, h // 2
                for ring in range(6):
                    rr = int((ring + 1) * min(w, h) * 0.08 + (i * 4) % 16)
                    draw.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), outline=(240, 240, 245, 140 - ring * 15), width=3)
                frame = Image.alpha_composite(frame, overlay)

            elif effect == "pyramid_overlay":
                frame = base.copy()
                overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                draw = ImageDraw.Draw(overlay)
                top = (w // 2, int(h * 0.12))
                left = (int(w * 0.22), int(h * 0.86))
                right = (int(w * 0.78), int(h * 0.86))
                pulse = 80 + int((math.sin(t * math.pi * 2) + 1) * 55)
                draw.polygon([top, left, right], outline=(255, 220, 120, 220), fill=(255, 210, 80, pulse))
                frame = Image.alpha_composite(frame, overlay)

            elif effect == "radiate":
                frame = base.copy()
                overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                draw = ImageDraw.Draw(overlay)
                cx, cy = w // 2, h // 2
                rays = 24
                for r0 in range(rays):
                    ang = (2 * math.pi * r0 / rays) + (t * math.pi * 2)
                    x2 = int(cx + math.cos(ang) * w)
                    y2 = int(cy + math.sin(ang) * h)
                    draw.line((cx, cy, x2, y2), fill=(255, 255, 180, 50), width=2)
                frame = Image.alpha_composite(frame, overlay)

            elif effect == "reflection":
                top_h = int(h * 0.58)
                top = base.crop((0, 0, w, top_h))
                refl = ImageOps.flip(top).convert("RGBA")
                frame = Image.new("RGBA", (w, h), (0, 0, 0, 255))
                frame.paste(top, (0, 0), top)
                refl = self._wave_distort(refl, t * math.pi * 2, amplitude=3, wavelength=18)
                fade = Image.new("L", (w, h - top_h), 0)
                df = ImageDraw.Draw(fade)
                for yy in range(h - top_h):
                    a = max(0, 170 - int((yy / max(1, h - top_h)) * 170))
                    df.line((0, yy, w, yy), fill=a)
                refl = refl.resize((w, h - top_h), Image.Resampling.BICUBIC)
                refl.putalpha(fade)
                frame.paste(refl, (0, top_h), refl)

            elif effect == "ripped":
                frame = base.copy()
                draw = ImageDraw.Draw(frame)
                mid = int(h * (0.48 + 0.08 * math.sin(t * math.pi * 2)))
                points = []
                x = 0
                while x <= w:
                    points.append((x, mid + random.randint(-14, 14)))
                    x += random.randint(14, 32)
                for p in points:
                    draw.line((p[0], p[1] - 1, p[0], p[1] + 1), fill=(255, 255, 255, 180), width=2)
                if len(points) >= 2:
                    draw.line(points, fill=(255, 255, 255, 220), width=3)

            elif effect == "shear_overlay":
                dx = int(math.sin(t * math.pi * 2) * 24)
                frame = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                for y in range(h):
                    row = base.crop((0, y, w, y + 1))
                    shift = int(dx * (y / max(1, h - 1)))
                    frame.paste(row, (shift, y))

            else:
                frame = base.copy()

            frames.append(frame)

        return frames

    async def _run_animation_effect(self, ctx, effect: str, url: str = None, url2: str = None):
        async with ctx.typing():
            raw1 = await self._fetch_image_bytes(ctx, url, attachment_index=0)
            if not raw1:
                return await self.bot.warn(ctx, "Could not fetch image.")

            base = self._open_rgba_image(raw1)
            if not base:
                return await self.bot.warn(ctx, "Invalid image.")

            second = None
            if effect == "heart":
                raw2 = await self._fetch_image_bytes(ctx, url2, attachment_index=1)
                if not raw2:
                    return await self.bot.warn(ctx, "Heart animation needs two images (2 URLs or 2 attachments).")
                second = self._open_rgba_image(raw2)
                if not second:
                    return await self.bot.warn(ctx, "Invalid second image.")

            frames = self._build_animation_frames(effect, base, second)
            out = self._gif_bytes(frames, duration=65)
            if not out:
                return await self.bot.warn(ctx, "Failed to render animation.")

            await ctx.send(file=discord.File(out, filename=f"{effect}.gif"))

    @commands.group(name="media", invoke_without_command=True)
    async def media(self, ctx):
        """Local image manipulation commands"""
        await ctx.send_help(ctx.command)

    @media.group(name="animate", invoke_without_command=True)
    async def animate(self, ctx):
        """Apply animated effects to an image"""
        await ctx.send_help(ctx.command)

    @animate.command(name="3d")
    async def animate_3d(self, ctx, url: str = None):
        """Create a 3D depth animation"""
        await self._run_animation_effect(ctx, "3d", url)

    @animate.command(name="boil")
    async def animate_boil(self, ctx, url: str = None):
        """Create a boiling/bubbling effect"""
        await self._run_animation_effect(ctx, "boil", url)

    @animate.command(name="bomb")
    async def animate_bomb(self, ctx, url: str = None):
        """Add an explosion animation"""
        await self._run_animation_effect(ctx, "bomb", url)

    @animate.command(name="earthquake")
    async def animate_earthquake(self, ctx, url: str = None):
        """Add a shaking earthquake effect"""
        await self._run_animation_effect(ctx, "earthquake", url)

    @animate.command(name="fan")
    async def animate_fan(self, ctx, url: str = None):
        """Create a fan blade spinning effect"""
        await self._run_animation_effect(ctx, "fan", url)

    @animate.command(name="fire")
    async def animate_fire(self, ctx, url: str = None):
        """Add animated fire effects"""
        await self._run_animation_effect(ctx, "fire", url)

    @animate.command(name="glitch")
    async def animate_glitch(self, ctx, url: str = None):
        """Add glitch/corruption effects"""
        await self._run_animation_effect(ctx, "glitch", url)

    @animate.command(name="heart")
    async def animate_heart(self, ctx, url1: str = None, url2: str = None):
        """Create a heart locket animation with two images"""
        await self._run_animation_effect(ctx, "heart", url1, url2)

    @animate.command(name="hearts")
    async def animate_hearts(self, ctx, url: str = None):
        """Add floating heart animations"""
        await self._run_animation_effect(ctx, "hearts", url)

    @animate.command(name="magik")
    async def animate_magik(self, ctx, url: str = None):
        """Apply a liquid distortion animation"""
        await self._run_animation_effect(ctx, "magik", url)

    @animate.command(name="patpat")
    async def animate_patpat(self, ctx, url: str = None):
        """Add a headpat animation"""
        await self._run_animation_effect(ctx, "patpat", url)

    @animate.command(name="rain")
    async def animate_rain(self, ctx, url: str = None):
        """Add falling rain effects"""
        await self._run_animation_effect(ctx, "rain", url)

    @animate.command(name="ripple")
    async def animate_ripple(self, ctx, url: str = None):
        """Create a ripple animation effect"""
        await self._run_animation_effect(ctx, "ripple", url)

    @animate.command(name="roll")
    async def animate_roll(self, ctx, url: str = None):
        """Make the image roll like a barrel"""
        await self._run_animation_effect(ctx, "roll", url)

    @animate.command(name="shine")
    async def animate_shine(self, ctx, url: str = None):
        """Add a shining animation effect"""
        await self._run_animation_effect(ctx, "shine", url)

    @animate.command(name="shock")
    async def animate_shock(self, ctx, url: str = None):
        """Add an electric shock effect"""
        await self._run_animation_effect(ctx, "shock", url)

    @animate.command(name="shoot")
    async def animate_shoot(self, ctx, url: str = None):
        """Add shooting star effects"""
        await self._run_animation_effect(ctx, "shoot", url)

    @animate.command(name="spin")
    async def animate_spin(self, ctx, url: str = None):
        """Make the image spin in circles"""
        await self._run_animation_effect(ctx, "spin", url)

    @animate.command(name="triggered")
    async def animate_triggered(self, ctx, url: str = None):
        """Add a triggered meme effect"""
        await self._run_animation_effect(ctx, "triggered", url)

    @animate.command(name="wasted")
    async def animate_wasted(self, ctx, url: str = None):
        """Add a GTA wasted screen effect"""
        await self._run_animation_effect(ctx, "wasted", url)

    @animate.command(name="wave")
    async def animate_wave(self, ctx, url: str = None):
        """Create a waving animation effect"""
        await self._run_animation_effect(ctx, "wave", url)

    @animate.command(name="wiggle")
    async def animate_wiggle(self, ctx, url: str = None):
        """Make the image wiggle back and forth"""
        await self._run_animation_effect(ctx, "wiggle", url)

    @media.group(name="distort", invoke_without_command=True)
    async def distort(self, ctx):
        """Apply distortion effects to an image"""
        await ctx.send_help(ctx.command)

    @distort.command(name="burn")
    async def distort_burn(self, ctx, url: str = None):
        """Apply a burning distortion effect"""
        await self._run_animation_effect(ctx, "burn", url)

    @distort.command(name="dizzy")
    async def distort_dizzy(self, ctx, url: str = None):
        """Create a dizzying spiral distortion effect"""
        await self._run_animation_effect(ctx, "dizzy", url)

    @distort.command(name="endless")
    async def distort_endless(self, ctx, url: str = None):
        """Create an endless looping distortion"""
        await self._run_animation_effect(ctx, "endless", url)

    @distort.command(name="infinity")
    async def distort_infinity(self, ctx, url: str = None):
        """Apply an infinity mirror effect"""
        await self._run_animation_effect(ctx, "infinity", url)

    @distort.command(name="melt")
    async def distort_melt(self, ctx, url: str = None):
        """Make the image appear to melt"""
        await self._run_animation_effect(ctx, "melt", url)

    @distort.command(name="phase")
    async def distort_phase(self, ctx, url: str = None):
        """Apply a phasing distortion effect"""
        await self._run_animation_effect(ctx, "phase", url)

    @distort.command(name="poly")
    async def distort_poly(self, ctx, url: str = None):
        """Create a polygonal distortion pattern"""
        await self._run_animation_effect(ctx, "poly", url)

    @distort.command(name="pyramid")
    async def distort_pyramid(self, ctx, url: str = None):
        """Apply a pyramid-like distortion effect"""
        await self._run_animation_effect(ctx, "pyramid", url)

    @distort.command(name="shear")
    async def distort_shear(self, ctx, url: str = None):
        """Apply a shearing distortion effect"""
        await self._run_animation_effect(ctx, "shear", url)

    @distort.command(name="shred")
    async def distort_shred(self, ctx, url: str = None):
        """Shred the image into strips"""
        await self._run_animation_effect(ctx, "shred", url)

    @distort.command(name="slice")
    async def distort_slice(self, ctx, url: str = None):
        """Slice the image into segments"""
        await self._run_animation_effect(ctx, "slice", url)

    @distort.command(name="stretch")
    async def distort_stretch(self, ctx, url: str = None):
        """Apply a stretching distortion effect"""
        await self._run_animation_effect(ctx, "stretch", url)

    @media.group(name="modify", invoke_without_command=True)
    async def modify(self, ctx):
        """Apply stylized image modification effects"""
        await ctx.send_help(ctx.command)

    @modify.command(name="ads")
    async def modify_ads(self, ctx, url: str = None):
        """modify image into an advertisement style"""
        await self._run_animation_effect(ctx, "ads", url)

    @modify.command(name="bayer")
    async def modify_bayer(self, ctx, url: str = None):
        """Apply a Bayer matrix dithering effect"""
        await self._run_animation_effect(ctx, "bayer", url)

    @modify.command(name="bevel")
    async def modify_bevel(self, ctx, url: str = None):
        """Add a beveled edge effect"""
        await self._run_animation_effect(ctx, "bevel", url)

    @modify.command(name="billboard")
    async def modify_billboard(self, ctx, url: str = None):
        """Display image on a billboard"""
        await self._run_animation_effect(ctx, "billboard", url)

    @modify.command(name="cube")
    async def modify_cube(self, ctx, url: str = None):
        """Wrap image around a 3D cube"""
        await self._run_animation_effect(ctx, "cube", url)

    @modify.command(name="emojify")
    async def modify_emojify(self, ctx, url: str = None):
        """Convert image into emoji pixels"""
        await self._run_animation_effect(ctx, "emojify", url)

    @modify.command(name="flag2")
    async def modify_flag2(self, ctx, url: str = None):
        """Create a waving flag effect"""
        await self._run_animation_effect(ctx, "flag2", url)

    @modify.command(name="gameboy")
    async def modify_gameboy(self, ctx, url: str = None):
        """Apply a Gameboy-style effect"""
        await self._run_animation_effect(ctx, "gameboy", url)

    @modify.command(name="half_invert")
    async def modify_half_invert(self, ctx, url: str = None):
        """Invert half of the image"""
        await self._run_animation_effect(ctx, "half_invert", url)

    @modify.command(name="letters")
    async def modify_letters(self, ctx, url: str = None):
        """Convert image into ASCII letters"""
        await self._run_animation_effect(ctx, "letters", url)

    @modify.command(name="lines")
    async def modify_lines(self, ctx, url: str = None):
        """Apply a lined pattern effect"""
        await self._run_animation_effect(ctx, "lines", url)

    @modify.command(name="lsd")
    async def modify_lsd(self, ctx, url: str = None):
        """Apply a psychedelic color effect"""
        await self._run_animation_effect(ctx, "lsd", url)

    @modify.command(name="matrix")
    async def modify_matrix(self, ctx, url: str = None):
        """Apply a Matrix-style effect"""
        await self._run_animation_effect(ctx, "matrix", url)

    @modify.command(name="minecraft")
    async def modify_minecraft(self, ctx, url: str = None):
        """Convert image into Minecraft blocks"""
        await self._run_animation_effect(ctx, "minecraft", url)

    @modify.command(name="neon")
    async def modify_neon(self, ctx, url: str = None):
        """Add neon glow effects"""
        await self._run_animation_effect(ctx, "neon", url)

    @modify.command(name="optics")
    async def modify_optics(self, ctx, url: str = None):
        """Apply an optical distortion effect"""
        await self._run_animation_effect(ctx, "optics", url)

    @modify.command(name="pattern")
    async def modify_pattern(self, ctx, url: str = None):
        """Create a repeating pattern from the image"""
        await self._run_animation_effect(ctx, "pattern", url)

    @modify.command(name="sensitive")
    async def modify_sensitive(self, ctx, url: str = None):
        """Add a sensitive content warning overlay"""
        await self._run_animation_effect(ctx, "sensitive", url)

    @modify.command(name="soap")
    async def modify_soap(self, ctx, url: str = None):
        """Add a soap bubble effect"""
        await self._run_animation_effect(ctx, "soap", url)

    @modify.command(name="stereo")
    async def modify_stereo(self, ctx, url: str = None):
        """Apply a stereoscopic 3D effect"""
        await self._run_animation_effect(ctx, "stereo", url)

    @modify.command(name="tiles")
    async def modify_tiles(self, ctx, url: str = None):
        """Split image into rotating tiles"""
        await self._run_animation_effect(ctx, "tiles", url)

    @modify.command(name="tv")
    async def modify_tv(self, ctx, url: str = None):
        """Display image on a TV screen"""
        await self._run_animation_effect(ctx, "tv", url)

    @modify.command(name="wall")
    async def modify_wall(self, ctx, url: str = None):
        """Project image onto a wall"""
        await self._run_animation_effect(ctx, "wall", url)

    @media.group(name="overlay", invoke_without_command=True)
    async def overlay(self, ctx):
        """Add stylized overlays to an image"""
        await ctx.send_help(ctx.command)

    @overlay.command(name="equations")
    async def overlay_equations(self, ctx, url: str = None):
        """Add mathematical equations overlay"""
        await self._run_animation_effect(ctx, "equations", url)

    @overlay.command(name="flush")
    async def overlay_flush(self, ctx, url: str = None):
        """Add toilet flush effect overlay"""
        await self._run_animation_effect(ctx, "flush", url)

    @overlay.command(name="gallery")
    async def overlay_gallery(self, ctx, url: str = None):
        """Display image in an art gallery setting"""
        await self._run_animation_effect(ctx, "gallery", url)

    @overlay.command(name="globe")
    async def overlay_globe(self, ctx, url: str = None):
        """Place image on a rotating globe"""
        await self._run_animation_effect(ctx, "globe", url)

    @overlay.command(name="ipcam")
    async def overlay_ipcam(self, ctx, url: str = None):
        """Add security camera overlay effect"""
        await self._run_animation_effect(ctx, "ipcam", url)

    @overlay.command(name="kanye")
    async def overlay_kanye(self, ctx, url: str = None):
        """Add Kanye West album cover style"""
        await self._run_animation_effect(ctx, "kanye", url)

    @overlay.command(name="lamp")
    async def overlay_lamp(self, ctx, url: str = None):
        """Add glowing lamp lighting effect"""
        await self._run_animation_effect(ctx, "lamp", url)

    @overlay.command(name="laundry")
    async def overlay_laundry(self, ctx, url: str = None):
        """Place image in washing machine animation"""
        await self._run_animation_effect(ctx, "laundry", url)

    @overlay.command(name="layers")
    async def overlay_layers(self, ctx, url: str = None):
        """Create layered depth effect"""
        await self._run_animation_effect(ctx, "layers", url)

    @overlay.command(name="logoff")
    async def overlay_logoff(self, ctx, url: str = None):
        """Add Windows logoff screen effect"""
        await self._run_animation_effect(ctx, "logoff", url)

    @overlay.command(name="magnify")
    async def overlay_magnify(self, ctx, url: str = None):
        """Add magnifying glass effect"""
        await self._run_animation_effect(ctx, "magnify", url)

    @overlay.command(name="paparazzi")
    async def overlay_paparazzi(self, ctx, url: str = None):
        """Add paparazzi camera effect"""
        await self._run_animation_effect(ctx, "paparazzi", url)

    @overlay.command(name="phase")
    async def overlay_phase(self, ctx, url: str = None):
        """Add phase effect"""
        await self._run_animation_effect(ctx, "phase_overlay", url)

    @overlay.command(name="phone")
    async def overlay_phone(self, ctx, url: str = None):
        """Add phone camera effect"""
        await self._run_animation_effect(ctx, "phone", url)

    @overlay.command(name="plank")
    async def overlay_plank(self, ctx, url: str = None):
        """Add plank effect"""
        await self._run_animation_effect(ctx, "plank", url)

    @overlay.command(name="plates")
    async def overlay_plates(self, ctx, url: str = None):
        """Add plates effect"""
        await self._run_animation_effect(ctx, "plates", url)

    @overlay.command(name="pyramid")
    async def overlay_pyramid(self, ctx, url: str = None):
        """Add pyramid effect"""
        await self._run_animation_effect(ctx, "pyramid_overlay", url)

    @overlay.command(name="radiate")
    async def overlay_radiate(self, ctx, url: str = None):
        """Add radiate effect"""
        await self._run_animation_effect(ctx, "radiate", url)

    @overlay.command(name="reflection")
    async def overlay_reflection(self, ctx, url: str = None):
        """Add reflection effect"""
        await self._run_animation_effect(ctx, "reflection", url)

    @overlay.command(name="ripped")
    async def overlay_ripped(self, ctx, url: str = None):
        """Add ripped effect"""
        await self._run_animation_effect(ctx, "ripped", url)

    @overlay.command(name="shear")
    async def overlay_shear(self, ctx, url: str = None):
        """Add shear effect"""
        await self._run_animation_effect(ctx, "shear_overlay", url)

    @media.command(name="grayscale")
    async def grayscale(self, ctx, url: str = None):
        """Apply a greyscale filter using Pillow"""
        if Image is None or ImageOps is None:
            return await ctx.send("Pillow (PIL) is not installed. Run `pip install pillow` and restart.")

        data = await self.get_target_bytes(ctx, url)
        if not data:
            return await self.bot.warn(ctx, "Could not fetch image.")

        with Image.open(data) as img:
            res = ImageOps.grayscale(img)
            out = BytesIO()
            res.save(out, format="PNG")
            out.seek(0)
            await ctx.send(file=discord.File(out, filename="grayscale.png"))

    @media.command(name="invert")
    async def invert(self, ctx, url: str = None):
        """Invert image colors using Pillow"""
        if Image is None or ImageOps is None:
            return await ctx.send("Pillow (PIL) is not installed. Run `pip install pillow` and restart.")

        data = await self.get_target_bytes(ctx, url)
        with Image.open(data).convert("RGB") as img:
            res = ImageOps.invert(img)
            out = BytesIO()
            res.save(out, format="PNG")
            out.seek(0)
            await ctx.send(file=discord.File(out, filename="invert.png"))

    @media.command(name="blur")
    async def blur(self, ctx, url: str = None, radius: int = 5):
        """Blur an image using Pillow"""
        if Image is None or ImageFilter is None:
            return await ctx.send("Pillow (PIL) is not installed. Run `pip install pillow` and restart.")

        data = await self.get_target_bytes(ctx, url)
        with Image.open(data) as img:
            res = img.filter(ImageFilter.GaussianBlur(radius))
            out = BytesIO()
            res.save(out, format="PNG")
            out.seek(0)
            await ctx.send(file=discord.File(out, filename="blur.png"))

    @media.command(name="magik")
    async def magik(self, ctx, url: str = None):
        """Apply liquid rescale (magik) using Wand (ImageMagick)"""
        if WandImage is None:
            return await ctx.send("Wand is not installed. Run `pip install wand` and restart.")
        
        data = await self.get_target_bytes(ctx, url)
        
        # We run Wand in an executor because it is CPU intensive and blocking
        def process():
            with WandImage(blob=data.getvalue()) as img:
                img.format = 'png'
                img.liquid_rescale(width=int(img.width * 0.5), height=int(img.height * 0.5))
                img.resize(width=img.width * 2, height=img.height * 2)
                return img.make_blob()

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, process)
        await ctx.send(file=discord.File(BytesIO(result), filename="magik.png"))

    @media.command(name="swirl")
    async def swirl(self, ctx, url: str = None, degree: int = 60):
        """Apply a swirl effect using Wand"""
        if WandImage is None:
            return await ctx.send("Wand is not installed. Run `pip install wand` and restart.")
        
        data = await self.get_target_bytes(ctx, url)
        
        def process():
            with WandImage(blob=data.getvalue()) as img:
                img.format = 'png'
                img.swirl(degree=degree)
                return img.make_blob()

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, process)
        await ctx.send(file=discord.File(BytesIO(result), filename="swirl.png"))

    @media.command(name="speechbubble", aliases=["sb"])
    async def speechbubble(self, ctx, url: str = None):
        """Adds a speech bubble transparency to the top of an image"""
        if Image is None:
            return await ctx.send("Pillow (PIL) is not installed. Run `pip install pillow` and restart.")

        data = await self.get_target_bytes(ctx, url)
        with Image.open(data).convert("RGBA") as img:
            # Create the bubble shape (this is a simplified logic)
            # In a real scenario, you'd overlay a 'bubble.png' template
            width, height = img.size
            bubble_height = int(height * 0.2)
            
            # Crop the image to make room or overlay
            # For brevity, let's just grayscale the top 20% as a placeholder
            # Real speechbubble would use a mask overlay.
            await ctx.send("Speechbubble logic requires a local 'mask.png' to look good. Would you like that file?")

    @commands.command(name="namehistory")
    async def namehistory(self, ctx, user: discord.User = None):
        """View a user's username history"""
        user = user or ctx.author
        
        from models.configs import UserConfig
        from utils.paginator import WockPaginator
        
        res = await UserConfig.find_one(UserConfig.user_id == user.id)
        
        if not res or not res.username_history:
            return await self.bot.neutral(ctx, f"**{user}** has no username history")
        
        history = sorted(res.username_history, key=lambda x: x.get("timestamp", ""), reverse=True)
        
        pages = []
        for entry in history:
            name = entry.get("name", "Unknown")
            timestamp = entry.get("timestamp", "Unknown")
            if timestamp != "Unknown":
                from datetime import datetime as dt
                try:
                    date_obj = dt.fromisoformat(timestamp)
                    timestamp = date_obj.strftime("%B %d, %Y at %I:%M %p")
                except:
                    pass
            
            embed = discord.Embed(
                color=0x242429,
                title=f"{user}'s Username History",
                description=f"**Username:** `{name}`\n**Changed:** {timestamp}"
            )
            embed.set_thumbnail(url=user.avatar.url if user.avatar else None)
            embed.set_footer(text=f"Total names: {len(history)}")
            pages.append(embed)
        
        if len(pages) == 1:
            return await ctx.send(embed=pages[0])
        
        paginator = WockPaginator(ctx, pages)
        await paginator.start()

    @commands.command(name="clearnames")
    async def clearnames(self, ctx, user: discord.User = None):
        """Clear username history (admin only for others)"""
        user = user or ctx.author
        
        if user != ctx.author and not ctx.author.guild_permissions.administrator:
            return await self.bot.warn(ctx, "You can only clear your own username history")
        
        from models.configs import UserConfig
        
        res = await UserConfig.find_one(UserConfig.user_id == user.id)
        
        if not res or not res.username_history:
            return await self.bot.neutral(ctx, f"**{user}** has no username history to clear")
        
        res.username_history = []
        await res.save()
        await self.bot.grant(ctx, f"Cleared username history for **{user}**")

    @commands.group(name="tags", aliases=["t", "tag"], invoke_without_command=True)
    async def tags(self, ctx, *, name: str = None):
        """Manage or view server tags"""
        if not name:
            return await ctx.send_help(ctx.command)

        config = await self.get_guild_data(ctx.guild.id)
        if not getattr(config, "tags", None):
            return await ctx.send_help(ctx.command)

        lookup = name.lower()
        tag = config.tags.get(lookup)
        if not tag:
            return await ctx.send_help(ctx.command)

        content = tag.get("content") if isinstance(tag, dict) else str(tag)
        await ctx.send(content)

    @tags.command(name="add")
    @commands.has_permissions(manage_messages=True)
    async def tags_add(self, ctx, name: str = None, *, content: str = None):
        """Add a tag to the server"""
        if not name or not content:
            return await ctx.send_help(ctx.command)

        config = await self.get_guild_data(ctx.guild.id)
        if not getattr(config, "tags", None):
            config.tags = {}

        tag_name = name.lower()
        if tag_name in config.tags:
            return await self.bot.warn(ctx, f"Tag **{tag_name}** already exists.")

        config.tags[tag_name] = {
            "content": content,
            "author_id": ctx.author.id
        }
        await config.save()
        await self.bot.grant(ctx, f"Added tag **{tag_name}**.")

    @tags.command(name="view")
    async def tags_view(self, ctx, *, name: str = None):
        """View a tag by its name"""
        if not name:
            return await ctx.send_help(ctx.command)

        config = await self.get_guild_data(ctx.guild.id)
        if not getattr(config, "tags", None):
            return await self.bot.warn(ctx, f"Tag **{name.lower()}** not found.")

        tag_name = name.lower()
        tag = config.tags.get(tag_name)
        if not tag:
            return await self.bot.warn(ctx, f"Tag **{tag_name}** not found.")

        content = tag.get("content") if isinstance(tag, dict) else str(tag)
        await ctx.send(content)

    @tags.command(name="list")
    async def tags_list(self, ctx):
        """List all tags in the server"""
        config = await self.get_guild_data(ctx.guild.id)
        if not getattr(config, "tags", None):
            return await self.bot.warn(ctx, "No tags created yet.")

        sorted_tags = sorted(config.tags.keys())
        if not sorted_tags:
            return await self.bot.warn(ctx, "No tags created yet.")

        pages = []
        chunk_size = 10
        for i in range(0, len(sorted_tags), chunk_size):
            chunk = sorted_tags[i:i + chunk_size]
            description = "\n".join([
                f"`{i + index + 1}.` **{tag_name}**"
                for index, tag_name in enumerate(chunk)
            ])

            embed = discord.Embed(
                color=self.bot.config.get("color", 0x242429),
                title=f"Tags in {ctx.guild.name}",
                description=description
            )
            pages.append(embed)

        if len(pages) == 1:
            return await ctx.send(embed=pages[0])

        await self.bot.paginator(ctx, pages)

    @tags.command(name="remove")
    @commands.has_permissions(manage_messages=True)
    async def tags_remove(self, ctx, *, name: str = None):
        """Remove a tag by its name"""
        if not name:
            return await ctx.send_help(ctx.command)

        config = await self.get_guild_data(ctx.guild.id)
        if not getattr(config, "tags", None):
            return await self.bot.warn(ctx, f"Tag **{name.lower()}** does not exist.")

        tag_name = name.lower()
        if tag_name not in config.tags:
            return await self.bot.warn(ctx, f"Tag **{tag_name}** does not exist.")

        config.tags.pop(tag_name, None)
        await config.save()
        await self.bot.grant(ctx, f"Removed tag **{tag_name}**.")

    @tags.command(name="reset")
    @commands.has_permissions(manage_messages=True)
    async def tags_reset(self, ctx):
        """Remove all tags in the server"""
        config = await self.get_guild_data(ctx.guild.id)
        config.tags = {}
        await config.save()
        await self.bot.grant(ctx, "Cleared all tags for this server.")

    def _parse_time(self, time_str: str) -> timedelta:
        """Parse time string like '5m', '2h', '1d' to timedelta"""
        try:
            value = int(time_str[:-1])
            unit = time_str[-1].lower()
            
            if unit == 'm':
                return timedelta(minutes=value)
            elif unit == 'h':
                return timedelta(hours=value)
            elif unit == 'd':
                return timedelta(days=value)
            elif unit == 's':
                return timedelta(seconds=value)
            else:
                return None
        except:
            return None

    @commands.group(name="schedule", invoke_without_command=True)
    async def schedule(self, ctx):
        """Manage scheduled messages"""
        await ctx.send_help(ctx.command)

    @schedule.command(name="add")
    async def schedule_add(self, ctx, *, args: str = None):
        """Schedule a message for later
        
        Usage: ,schedule add [time] <channel> <content>
        Time format: 5m, 2h, 1d (minutes, hours, days) - defaults to 1h
        """
        if not args:
            return await ctx.send_help(ctx.command)
        
        parts = args.split(None, 2)
        
        if len(parts) < 2:
            return await self.bot.deny(ctx, "Usage: ,schedule add [time] <channel> <content>")
        
        # Try to parse as: [time] <channel> <content>
        idx = 0
        time_str = "1h"
        
        # Check if first arg is a time
        if any(parts[0].endswith(suffix) for suffix in ['m', 'h', 'd', 's']):
            time_str = parts[0]
            idx = 1
        
        if len(parts) <= idx:
            return await self.bot.deny(ctx, "Usage: ,schedule add [time] <channel> <content>")
        
        # Next part should be channel
        channel_input = parts[idx]
        idx += 1
        
        # Parse channel
        try:
            if channel_input.startswith('<#') and channel_input.endswith('>'):
                channel_id = int(channel_input[2:-1])
                channel = ctx.guild.get_channel(channel_id)
            else:
                # Try to find by name
                channel = discord.utils.get(ctx.guild.text_channels, name=channel_input)
                if not channel:
                    channel_id = int(channel_input)
                    channel = ctx.guild.get_channel(channel_id)
        except:
            return await self.bot.deny(ctx, "Invalid channel.")
        
        if not channel:
            return await self.bot.deny(ctx, "Channel not found.")
        
        # Remaining is content
        if idx >= len(parts):
            return await self.bot.deny(ctx, "Usage: ,schedule add [time] <channel> <content>")
        
        # Reconstruct content from remaining parts
        remaining = args.split(None, idx)
        content = remaining[-1] if remaining else ""
        
        if not content:
            return await self.bot.deny(ctx, "Message content cannot be empty.")
        
        delta = self._parse_time(time_str)
        if not delta:
            return await self.bot.deny(ctx, "Invalid time format. Use: 5m, 2h, 1d")
        
        scheduled_time = datetime.utcnow() + delta
        message_id = secrets.token_hex(8)
        
        scheduled = ScheduledMessage(
            guild_id=ctx.guild.id,
            channel_id=channel.id,
            author_id=ctx.author.id,
            content=content,
            scheduled_time=scheduled_time,
            message_id=message_id
        )
        await scheduled.save()
        
        await self.bot.grant(ctx, f"Message scheduled for <t:{int(scheduled_time.timestamp())}:R> (ID: `{message_id}`)")
        self._schedule_message_task(ctx.bot, scheduled)

    def _schedule_message_task(self, bot, scheduled: ScheduledMessage):
        """Create an async task to send the scheduled message"""
        async def send_scheduled():
            delay = (scheduled.scheduled_time - datetime.utcnow()).total_seconds()
            if delay > 0:
                await asyncio.sleep(delay)
            
            try:
                channel = bot.get_channel(scheduled.channel_id)
                if channel:
                    await channel.send(scheduled.content)
                await scheduled.delete()
            except Exception as e:
                logger("error", f"Failed to send scheduled message: {e}")

        asyncio.create_task(send_scheduled())

    @schedule.command(name="list")
    async def schedule_list(self, ctx):
        """List pending scheduled messages in this server"""
        messages = await ScheduledMessage.find(ScheduledMessage.guild_id == ctx.guild.id).to_list(None)
        
        if not messages:
            return await ctx.send("No scheduled messages in this server.")
        
        embed = discord.Embed(title="Scheduled Messages", color=0x242429)
        for msg in messages:
            channel = ctx.guild.get_channel(msg.channel_id)
            channel_name = channel.name if channel else "unknown"
            time_str = f"<t:{int(msg.scheduled_time.timestamp())}:R>"
            
            content_preview = msg.content[:100] + "..." if len(msg.content) > 100 else msg.content
            embed.add_field(
                name=f"`{msg.message_id}`",
                value=f"**Channel:** #{channel_name}\n**Time:** {time_str}\n**Content:** {content_preview}",
                inline=False
            )
        
        await ctx.send(embed=embed)

    @schedule.command(name="remove")
    async def schedule_remove(self, ctx, message_id: str):
        """Cancel a pending scheduled message"""
        msg = await ScheduledMessage.find_one(ScheduledMessage.message_id == message_id)
        
        if not msg:
            return await self.bot.deny(ctx, f"Scheduled message `{message_id}` not found.")
        
        if msg.guild_id != ctx.guild.id and msg.author_id != ctx.author.id:
            return await self.bot.deny(ctx, "You can only remove your own scheduled messages.")
        
        await msg.delete()
        await self.bot.grant(ctx, f"Removed scheduled message `{message_id}`")

    @commands.group(name="reminder", invoke_without_command=True)
    async def reminder(self, ctx):
        """Manage your personal reminders"""
        await ctx.send_help(ctx.command)

    @reminder.command(name="add")
    async def reminder_add(self, ctx, *, time_or_content: str = None):
        """Create a new reminder
        
        Usage: ,reminder add [time] <context>
        Time format: 5m, 2h, 1d (minutes, hours, days) - defaults to 1h
        """
        if not time_or_content:
            return await ctx.send_help(ctx.command)
        
        # Parse time and content from the input
        parts = time_or_content.split(None, 1)
        
        if len(parts) == 1:
            # Only one argument provided, treat as content with default time
            time_str = "1h"
            content = parts[0]
        else:
            # Two or more arguments
            first_arg = parts[0]
            rest = parts[1]
            
            # Check if first arg looks like a time
            if any(first_arg.endswith(suffix) for suffix in ['m', 'h', 'd', 's']):
                time_str = first_arg
                content = rest
            else:
                # First arg is content, use default time
                time_str = "1h"
                content = time_or_content
        
        delta = self._parse_time(time_str)
        if not delta:
            return await self.bot.deny(ctx, "Invalid time format. Use: 5m, 2h, 1d")
        
        reminder_time = datetime.utcnow() + delta
        reminder_id = secrets.token_hex(8)
        
        reminder = Reminder(
            user_id=ctx.author.id,
            content=content,
            reminder_time=reminder_time,
            reminder_id=reminder_id
        )
        await reminder.save()
        
        await self.bot.grant(ctx, f"Reminder set for <t:{int(reminder_time.timestamp())}:R> (ID: `{reminder_id}`)")
        self._schedule_reminder_task(ctx.bot, reminder, ctx.author.id)

    def _schedule_reminder_task(self, bot, reminder: Reminder, user_id: int):
        """Create an async task to send the reminder"""
        async def send_reminder():
            delay = (reminder.reminder_time - datetime.utcnow()).total_seconds()
            if delay > 0:
                await asyncio.sleep(delay)
            
            try:
                user = bot.get_user(user_id)
                if user:
                    embed = discord.Embed(title="⏰ Reminder", color=0x242429, description=reminder.content)
                    embed.set_footer(text=f"ID: {reminder.reminder_id}")
                    await user.send(embed=embed)
                await reminder.delete()
            except Exception as e:
                logger("error", f"Failed to send reminder: {e}")
                try:
                    await reminder.delete()
                except:
                    pass

        asyncio.create_task(send_reminder())

    @reminder.command(name="list")
    async def reminder_list(self, ctx):
        """List your active reminders"""
        reminders = await Reminder.find(Reminder.user_id == ctx.author.id).to_list(None)
        
        if not reminders:
            return await ctx.send("You have no active reminders.")
        
        embed = discord.Embed(title="Your Reminders", color=0x242429)
        for reminder in reminders:
            time_str = f"<t:{int(reminder.reminder_time.timestamp())}:R>"
            
            content_preview = reminder.content[:100] + "..." if len(reminder.content) > 100 else reminder.content
            embed.add_field(
                name=f"`{reminder.reminder_id}`",
                value=f"**Time:** {time_str}\n**Context:** {content_preview}",
                inline=False
            )
        
        await ctx.send(embed=embed)

    @reminder.command(name="remove")
    async def reminder_remove(self, ctx, reminder_id: str):
        """Cancel a reminder"""
        reminder = await Reminder.find_one(Reminder.reminder_id == reminder_id)
        
        if not reminder:
            return await self.bot.deny(ctx, f"Reminder `{reminder_id}` not found.")
        
        if reminder.user_id != ctx.author.id:
            return await self.bot.deny(ctx, "You can only remove your own reminders.")
        
        await reminder.delete()
        await self.bot.grant(ctx, f"Removed reminder `{reminder_id}`")

    @commands.command(name="createembed", aliases=["ce"])
    async def createembed(self, ctx, *, code: str = None):
        """Create and send a message with an embed using parser syntax.
        
        Syntax:
        {title: Your Title}
        {description: Your Description}
        {color: #FF0000}
        {field_name1: Field Name}
        {field_value1: Field Value}
        {field_inline1: true}
        {image: URL}
        {thumbnail: URL}
        {author: Author Name}
        {footer: Footer Text}
        
        Example:
        ;ce {title: Hello} {description: This is a test} {color: #00FF00}
        """
        if not code:
            return await ctx.send_help(ctx.command)
        
        try:
            parser = EmbedParser(ctx)
            embed = parser.parse(code)
            await ctx.send(embed=embed)
        except ValueError as e:
            await self.bot.deny(ctx, f"Invalid embed syntax: {str(e)}")
        except Exception as e:
            logger("error", f"Error creating embed for {ctx.author}: {e}")
            await self.bot.deny(ctx, f"Error creating embed: {str(e)}")

    @commands.group(invoke_without_command=True)
    async def activity(self, ctx, target: str = None):
        """View activity stats for users or channels"""
        if ctx.invoked_subcommand is None:
            if not target:
                return await ctx.send_help(ctx.command)
            
            # Get events cog for activity data
            events_cog = self.bot.get_cog('Events')
            if not events_cog:
                return await self.bot.deny(ctx, "Activity tracking not available.")
            
            # Try to parse as user mention or channel
            user = None
            channel = None
            
            try:
                user = await commands.UserConverter().convert(ctx, target)
            except:
                pass
            
            try:
                channel = await commands.TextChannelConverter().convert(ctx, target)
            except:
                pass
            
            if user:
                await self.show_user_activity(ctx, user, events_cog)
            elif channel:
                await self.show_channel_activity(ctx, channel, events_cog)
            else:
                await self.bot.warn(ctx, "Could not find that user or channel.")

    async def show_user_activity(self, ctx, user: discord.User, events_cog):
        """Display activity stats for a user"""
        user_activity = events_cog.activity_data[ctx.guild.id].get(user.id, {})
        messages = user_activity.get('messages', 0)
        voice_time = user_activity.get('voice_time', 0)
        last_seen = user_activity.get('last_seen')
        
        hours = voice_time // 3600
        minutes = (voice_time % 3600) // 60
        seconds = voice_time % 60
        
        embed = discord.Embed(
            title=f"{user.name}'s Activity",
            description=f"Activity stats for **{ctx.guild.name}**",
            color=0x242429
        )
        embed.add_field(name="Messages", value=f"{messages:,}", inline=True)
        embed.add_field(name="Voice Time", value=f"{hours}h {minutes}m {seconds}s", inline=True)
        embed.add_field(name="Last Seen", value=f"<t:{int(last_seen.timestamp())}:R>" if last_seen else "Never", inline=True)
        embed.set_thumbnail(url=user.display_avatar.url)
        
        await ctx.send(embed=embed)

    async def show_channel_activity(self, ctx, channel: discord.TextChannel, events_cog):
        """Display activity stats for a channel"""
        channel_activity = events_cog.activity_data[ctx.guild.id]
        
        # Calculate stats for this channel
        total_messages = sum(activity.get('messages', 0) for activity in channel_activity.values())
        total_users = len([a for a in channel_activity.values() if a.get('messages', 0) > 0])
        
        embed = discord.Embed(
            title=f"{channel.mention} Activity",
            description=f"Activity stats for **{channel.name}**",
            color=0x242429
        )
        embed.add_field(name="Total Messages", value=f"{total_messages:,}", inline=True)
        embed.add_field(name="Active Users", value=f"{total_users}", inline=True)
        
        await ctx.send(embed=embed)

    @activity.group(name="tracker", invoke_without_command=True)
    async def activity_tracker(self, ctx):
        """Manage activity tracker channels"""
        await ctx.send_help(ctx.command)

    @activity_tracker.command(name="add")
    async def tracker_add(self, ctx, channel: discord.TextChannel = None):
        """Set up activity tracking in a channel"""
        channel = channel or ctx.channel
        
        events_cog = self.bot.get_cog('Events')
        if not events_cog:
            return await self.bot.deny(ctx, "Activity tracking not available.")
        
        embed = discord.Embed(
            title="Activity Tracker",
            description=f"Tracking activity for **{ctx.guild.name}**",
            color=0x242429
        )
        embed.add_field(name="Messages", value="0", inline=True)
        embed.add_field(name="Active Users", value="0", inline=True)
        embed.add_field(name="Last Updated", value=f"<t:{int(datetime.utcnow().timestamp())}:t>", inline=True)
        embed.set_footer(text="Updates every 5 minutes")
        
        message = await channel.send(embed=embed)
        
        # Store tracker info in events cog
        if not hasattr(events_cog, 'tracker_channels'):
            events_cog.tracker_channels = {}
        
        events_cog.tracker_channels[message.id] = {
            'guild_id': ctx.guild.id,
            'channel_id': channel.id,
            'message_id': message.id,
            'type': 'guild'
        }
        
        await self.bot.grant(ctx, f"Activity tracker set up in {channel.mention}")

    @activity_tracker.command(name="remove")
    async def tracker_remove(self, ctx, message_id: int = None):
        """Remove an activity tracker"""
        if not message_id:
            return await ctx.send_help(ctx.command)
        
        events_cog = self.bot.get_cog('Events')
        if not events_cog or not hasattr(events_cog, 'tracker_channels'):
            return await self.bot.warn(ctx, "Tracker not found.")
        
        if message_id in events_cog.tracker_channels:
            del events_cog.tracker_channels[message_id]
            await self.bot.grant(ctx, "Activity tracker removed.")
        else:
            await self.bot.warn(ctx, "Tracker not found.")

    async def get_giveaway_from_message_link(self, ctx, message_link: str) -> Optional[Giveaway]:
        """Parse message link and get giveaway"""
        try:
            parts = message_link.split('/')
            if len(parts) < 3:
                await self.bot.warn(ctx, "Invalid message link format.")
                return None
            
            message_id = int(parts[-1])
            giveaway = await Giveaway.find_one(Giveaway.message_id == message_id)
            
            if not giveaway:
                await self.bot.warn(ctx, "Giveaway not found.")
                return None
            
            return giveaway
        except (ValueError, IndexError):
            await self.bot.warn(ctx, "Invalid message link.")
            return None

    def create_giveaway_embed(self, giveaway: Giveaway) -> discord.Embed:
        """Create a giveaway embed"""
        embed = discord.Embed(
            title="🎉 GIVEAWAY",
            description=giveaway.description or "No description provided.",
            color=giveaway.color,
            timestamp=giveaway.end_time
        )
        
        embed.add_field(name="Prize", value=giveaway.prize, inline=False)
        embed.add_field(name="Winners", value=str(giveaway.winners_count), inline=True)
        embed.add_field(name="Entries", value=str(len(giveaway.entries)), inline=True)
        
        if giveaway.min_level:
            embed.add_field(name="Minimum Level", value=str(giveaway.min_level), inline=True)
        if giveaway.max_level:
            embed.add_field(name="Maximum Level", value=str(giveaway.max_level), inline=True)
        
        if giveaway.min_account_age_days:
            embed.add_field(name="Minimum Account Age", value=f"{giveaway.min_account_age_days} days", inline=True)
        if giveaway.min_server_stay_days:
            embed.add_field(name="Minimum Server Stay", value=f"{giveaway.min_server_stay_days} days", inline=True)
        
        if giveaway.required_roles:
            roles_text = ", ".join([f"<@&{role_id}>" for role_id in giveaway.required_roles])
            embed.add_field(name="Required Roles", value=roles_text, inline=False)
        
        embed.add_field(name="Ends", value=f"<t:{int(giveaway.end_time.timestamp())}:R>", inline=False)
        
        if giveaway.image_url:
            embed.set_image(url=giveaway.image_url)
        if giveaway.thumbnail_url:
            embed.set_thumbnail(url=giveaway.thumbnail_url)
        
        return embed

    async def end_giveaway_internal(self, giveaway: Giveaway, early: bool = False):
        """Internal method to end a giveaway and pick winners"""
        if not giveaway.is_active:
            return
        
        giveaway.is_active = False
        giveaway.ended_at = datetime.utcnow()
        
        # Pick winners
        if giveaway.entries:
            winners = random.sample(giveaway.entries, min(giveaway.winners_count, len(giveaway.entries)))
            giveaway.winners = winners
        
        await giveaway.save()
        
        # Update embed
        try:
            guild = self.bot.get_guild(giveaway.guild_id)
            channel = guild.get_channel(giveaway.channel_id) if guild else None
            
            if channel:
                msg = await channel.fetch_message(giveaway.message_id)
                embed = self.create_giveaway_embed(giveaway)
                
                if giveaway.winners:
                    winners_text = ", ".join([f"<@{uid}>" for uid in giveaway.winners])
                    embed.add_field(name="🏆 Winners", value=winners_text, inline=False)
                else:
                    embed.add_field(name="🏆 Winners", value="No valid entries", inline=False)
                
                await msg.edit(embed=embed, view=None)
                
                # Announce winners in channel
                if giveaway.winners:
                    winners_text = ", ".join([f"<@{uid}>" for uid in giveaway.winners])
                    announce_embed = discord.Embed(
                        title="🏆 Giveaway Winners",
                        description=f"Congratulations to the winner(s) of **{giveaway.prize}**!",
                        color=giveaway.color
                    )
                    announce_embed.add_field(name="Winners", value=winners_text, inline=False)
                    await channel.send(embed=announce_embed)
                else:
                    announce_embed = discord.Embed(
                        title="❌ Giveaway Ended",
                        description=f"The giveaway for **{giveaway.prize}** ended with no valid entries.",
                        color=0xff0000
                    )
                    await channel.send(embed=announce_embed)
                
                # Award roles if set
                if giveaway.winners and giveaway.award_roles:
                    for winner_id in giveaway.winners:
                        member = guild.get_member(winner_id)
                        if member:
                            for role_id in giveaway.award_roles:
                                role = guild.get_role(role_id)
                                if role:
                                    try:
                                        await member.add_roles(role)
                                    except:
                                        pass
                
                # DM winners
                if giveaway.winners:
                    for winner_id in giveaway.winners:
                        user = self.bot.get_user(winner_id)
                        if user:
                            try:
                                embed = discord.Embed(
                                    title="🎉 You Won!",
                                    description=f"You won **{giveaway.prize}** in {guild.name}!",
                                    color=giveaway.color
                                )
                                await user.send(embed=embed)
                            except:
                                pass
        
        except Exception as e:
            logger("error", f"Error updating giveaway message: {e}")

    @commands.group(name="giveaways", aliases=["gw", "giveaway", "gws"], invoke_without_command=True)
    @commands.has_permissions(manage_channels=True)
    async def giveaways(self, ctx):
        """Start a giveaway quickly and easily"""
        await ctx.send_help(ctx.command)

    @giveaways.command(name="start")
    @commands.has_permissions(manage_channels=True)
    async def giveaway_start(self, ctx, channel: discord.TextChannel, duration: str, winners: int, *, prize: str):
        """Start a giveaway with your provided duration, winners and prize description
        
        Duration format: 1h, 30m, 1d, etc.
        """
        
        duration_seconds = 0
        duration_str = duration.lower()
        
        time_units = {
            's': 1,
            'm': 60,
            'h': 3600,
            'd': 86400,
            'w': 604800
        }
        
        matches = re.findall(r'(\d+)([smhdw])', duration_str)
        if not matches:
            return await self.bot.warn(ctx, "Invalid duration format. Use format like: 1h, 30m, 1d, etc.")
        
        for amount, unit in matches:
            duration_seconds += int(amount) * time_units.get(unit, 0)
        
        if duration_seconds == 0:
            return await self.bot.warn(ctx, "Invalid duration.")
        
        if winners < 1:
            return await self.bot.warn(ctx, "You must have at least 1 winner.")
        
        try:
            end_time = datetime.utcnow() + timedelta(seconds=duration_seconds)
            
            giveaway = Giveaway(
                guild_id=ctx.guild.id,
                channel_id=channel.id,
                message_id=0,
                host_ids=[ctx.author.id],
                prize=prize,
                winners_count=winners,
                end_time=end_time,
                created_at=datetime.utcnow()
            )
            await giveaway.insert()
            
            embed = self.create_giveaway_embed(giveaway)
            msg = await channel.send(embed=embed, view=GiveawayView(str(giveaway.id)))
            
            giveaway.message_id = msg.id
            await giveaway.save()
            
            await self.bot.grant(ctx, f"Giveaway started in {channel.mention}!")
        
        except Exception as e:
            logger("error", f"Error starting giveaway: {e}")
            await self.bot.deny(ctx, "Failed to start giveaway.")

    @giveaways.command(name="end")
    @commands.has_permissions(manage_channels=True)
    async def giveaway_end(self, ctx, message_link: str = None):
        """End an active giveaway early"""
        if not message_link:
            return await ctx.send_help(ctx.command)
        
        try:
            giveaway = await self.get_giveaway_from_message_link(ctx, message_link)
            if not giveaway:
                return
            
            if ctx.author.id not in giveaway.host_ids and not ctx.author.guild_permissions.administrator:
                return await self.bot.warn(ctx, "You can't manage this giveaway.")
            
            await self.end_giveaway_internal(giveaway, early=True)
            await self.bot.grant(ctx, "Giveaway ended!")
        except Exception as e:
            logger("error", f"Error ending giveaway: {e}")
            await self.bot.warn(ctx, "Failed to end giveaway.")

    @giveaways.command(name="reroll")
    @commands.has_permissions(manage_channels=True)
    async def giveaway_reroll(self, ctx, message_link: str = None, winners: int = None):
        """Reroll a winner for the specified giveaway"""
        if not message_link or winners is None:
            return await ctx.send_help(ctx.command)
        
        try:
            giveaway = await self.get_giveaway_from_message_link(ctx, message_link)
            if not giveaway:
                return
            
            if ctx.author.id not in giveaway.host_ids and not ctx.author.guild_permissions.administrator:
                return await self.bot.warn(ctx, "You can't manage this giveaway.")
            
            if not giveaway.entries:
                return await self.bot.warn(ctx, "No entries to reroll from.")
            
            if winners < 1 or winners > len(giveaway.entries):
                return await self.bot.warn(ctx, f"You can reroll between 1 and {len(giveaway.entries)} winners.")
            
            new_winners = random.sample(giveaway.entries, winners)
            giveaway.winners = new_winners
            await giveaway.save()
            
            try:
                guild = self.bot.get_guild(giveaway.guild_id)
                channel = guild.get_channel(giveaway.channel_id) if guild else None
                
                if channel:
                    msg = await channel.fetch_message(giveaway.message_id)
                    embed = self.create_giveaway_embed(giveaway)
                    winners_text = ", ".join([f"<@{uid}>" for uid in giveaway.winners])
                    embed.add_field(name="🏆 Winners", value=winners_text, inline=False)
                    await msg.edit(embed=embed)
            except:
                pass
            
            await self.bot.grant(ctx, "Giveaway rerolled!")
        except Exception as e:
            logger("error", f"Error rerolling giveaway: {e}")
            await self.bot.warn(ctx, "Failed to reroll giveaway.")

    @giveaways.command(name="cancel")
    @commands.has_permissions(manage_channels=True)
    async def giveaway_cancel(self, ctx, message_link: str = None):
        """Delete a giveaway without picking any winners"""
        if not message_link:
            return await ctx.send_help(ctx.command)
        
        try:
            giveaway = await self.get_giveaway_from_message_link(ctx, message_link)
            if not giveaway:
                return
            
            if ctx.author.id not in giveaway.host_ids and not ctx.author.guild_permissions.administrator:
                return await self.bot.warn(ctx, "You can't manage this giveaway.")
            
            await giveaway.delete()
            
            try:
                guild = self.bot.get_guild(giveaway.guild_id)
                channel = guild.get_channel(giveaway.channel_id) if guild else None
                
                if channel:
                    msg = await channel.fetch_message(giveaway.message_id)
                    await msg.delete()
            except:
                pass
            
            await self.bot.grant(ctx, "Giveaway cancelled!")
        except Exception as e:
            logger("error", f"Error cancelling giveaway: {e}")
            await self.bot.warn(ctx, "Failed to cancel giveaway.")

    @giveaways.command(name="list")
    async def giveaway_list(self, ctx):
        """List every active giveaway in the server"""
        try:
            giveaways = await Giveaway.find(
                Giveaway.guild_id == ctx.guild.id,
                Giveaway.is_active == True
            ).to_list()
            
            if not giveaways:
                return await self.bot.neutral(ctx, "No active giveaways in this server.")
            
            embed = discord.Embed(
                title="📋 Active Giveaways",
                color=self.bot.config["color"],
                timestamp=datetime.utcnow()
            )
            
            for i, giveaway in enumerate(giveaways[:10], 1):
                time_left = giveaway.end_time - datetime.utcnow()
                hours = int(time_left.total_seconds() // 3600)
                minutes = int((time_left.total_seconds() % 3600) // 60)
                
                value = f"**Prize:** {giveaway.prize}\n"
                value += f"**Winners:** {giveaway.winners_count}\n"
                value += f"**Entries:** {len(giveaway.entries)}\n"
                value += f"**Ends:** {hours}h {minutes}m"
                
                embed.add_field(name=f"Giveaway #{i}", value=value, inline=False)
            
            if len(giveaways) > 10:
                embed.set_footer(text=f"Showing 10 of {len(giveaways)} giveaways")
            
            await ctx.send(embed=embed)
        except Exception as e:
            logger("error", f"Error listing giveaways: {e}")
            await self.bot.warn(ctx, "Failed to list giveaways.")

    @giveaways.group(name="edit", invoke_without_command=True)
    @commands.has_permissions(manage_channels=True)
    async def giveaway_edit(self, ctx):
        """Edit options and limits for a specific giveaway"""
        await ctx.send_help(ctx.command)

    @giveaway_edit.command(name="prize")
    async def edit_prize(self, ctx, message_link: str = None, *, prize: str = None):
        """Change prize for a giveaway"""
        if not message_link or not prize:
            return await ctx.send_help(ctx.command)
        
        try:
            giveaway = await self.get_giveaway_from_message_link(ctx, message_link)
            if not giveaway:
                return
            
            if ctx.author.id not in giveaway.host_ids and not ctx.author.guild_permissions.administrator:
                return await self.bot.warn(ctx, "You can't manage this giveaway.")
            
            giveaway.prize = prize
            await giveaway.save()
            
            try:
                guild = self.bot.get_guild(giveaway.guild_id)
                channel = guild.get_channel(giveaway.channel_id) if guild else None
                
                if channel:
                    msg = await channel.fetch_message(giveaway.message_id)
                    embed = self.create_giveaway_embed(giveaway)
                    await msg.edit(embed=embed)
            except:
                pass
            
            await self.bot.grant(ctx, "Prize updated!")
        except Exception as e:
            logger("error", f"Error updating prize: {e}")
            await self.bot.warn(ctx, "Failed to update prize.")

    @giveaway_edit.command(name="minlevel")
    async def edit_minlevel(self, ctx, message_link: str = None, level: int = None):
        """Set the minimum level requirement for giveaway entry"""
        if not message_link or level is None:
            return await ctx.send_help(ctx.command)
        
        try:
            giveaway = await self.get_giveaway_from_message_link(ctx, message_link)
            if not giveaway:
                return
            
            if ctx.author.id not in giveaway.host_ids and not ctx.author.guild_permissions.administrator:
                return await self.bot.warn(ctx, "You can't manage this giveaway.")
            
            if level < 0:
                return await self.bot.warn(ctx, "Level must be 0 or higher.")
            
            giveaway.min_level = level if level > 0 else None
            await giveaway.save()
            
            try:
                guild = self.bot.get_guild(giveaway.guild_id)
                channel = guild.get_channel(giveaway.channel_id) if guild else None
                
                if channel:
                    msg = await channel.fetch_message(giveaway.message_id)
                    embed = self.create_giveaway_embed(giveaway)
                    await msg.edit(embed=embed)
            except:
                pass
            
            await self.bot.grant(ctx, "Minimum level updated!")
        except Exception as e:
            logger("error", f"Error updating minlevel: {e}")
            await self.bot.warn(ctx, "Failed to update minimum level.")

    @giveaway_edit.command(name="maxlevel")
    async def edit_maxlevel(self, ctx, message_link: str = None, level: int = None):
        """Set the maximum level requirement for giveaway entry"""
        if not message_link or level is None:
            return await ctx.send_help(ctx.command)
        
        try:
            giveaway = await self.get_giveaway_from_message_link(ctx, message_link)
            if not giveaway:
                return
            
            if ctx.author.id not in giveaway.host_ids and not ctx.author.guild_permissions.administrator:
                return await self.bot.warn(ctx, "You can't manage this giveaway.")
            
            if level < 0:
                return await self.bot.warn(ctx, "Level must be 0 or higher.")
            
            giveaway.max_level = level if level > 0 else None
            await giveaway.save()
            
            try:
                guild = self.bot.get_guild(giveaway.guild_id)
                channel = guild.get_channel(giveaway.channel_id) if guild else None
                
                if channel:
                    msg = await channel.fetch_message(giveaway.message_id)
                    embed = self.create_giveaway_embed(giveaway)
                    await msg.edit(embed=embed)
            except:
                pass
            
            await self.bot.grant(ctx, "Maximum level updated!")
        except Exception as e:
            logger("error", f"Error updating maxlevel: {e}")
            await self.bot.warn(ctx, "Failed to update maximum level.")

    @giveaway_edit.command(name="winners")
    async def edit_winners(self, ctx, message_link: str = None, count: int = None):
        """Change the amount of winners for a giveaway"""
        if not message_link or count is None:
            return await ctx.send_help(ctx.command)
        
        try:
            giveaway = await self.get_giveaway_from_message_link(ctx, message_link)
            if not giveaway:
                return
            
            if ctx.author.id not in giveaway.host_ids and not ctx.author.guild_permissions.administrator:
                return await self.bot.warn(ctx, "You can't manage this giveaway.")
            
            if count < 1:
                return await self.bot.warn(ctx, "You must have at least 1 winner.")
            
            giveaway.winners_count = count
            await giveaway.save()
            
            try:
                guild = self.bot.get_guild(giveaway.guild_id)
                channel = guild.get_channel(giveaway.channel_id) if guild else None
                
                if channel:
                    msg = await channel.fetch_message(giveaway.message_id)
                    embed = self.create_giveaway_embed(giveaway)
                    await msg.edit(embed=embed)
            except:
                pass
            
            await self.bot.grant(ctx, "Winner count updated!")
        except Exception as e:
            logger("error", f"Error updating winners: {e}")
            await self.bot.warn(ctx, "Failed to update winner count.")

    @giveaway_edit.command(name="description")
    async def edit_description(self, ctx, message_link: str = None, *, text: str = None):
        """Change description for a giveaway"""
        if not message_link or not text:
            return await ctx.send_help(ctx.command)
        
        try:
            giveaway = await self.get_giveaway_from_message_link(ctx, message_link)
            if not giveaway:
                return
            
            if ctx.author.id not in giveaway.host_ids and not ctx.author.guild_permissions.administrator:
                return await self.bot.warn(ctx, "You can't manage this giveaway.")
            
            giveaway.description = text
            await giveaway.save()
            
            try:
                guild = self.bot.get_guild(giveaway.guild_id)
                channel = guild.get_channel(giveaway.channel_id) if guild else None
                
                if channel:
                    msg = await channel.fetch_message(giveaway.message_id)
                    embed = self.create_giveaway_embed(giveaway)
                    await msg.edit(embed=embed)
            except:
                pass
            
            await self.bot.grant(ctx, "Description updated!")
        except Exception as e:
            logger("error", f"Error updating description: {e}")
            await self.bot.warn(ctx, "Failed to update description.")

    @giveaway_edit.command(name="image")
    async def edit_image(self, ctx, message_link: str = None, url_or_attachment: str = None):
        """Change image for a giveaway embed"""
        if not message_link:
            return await ctx.send_help(ctx.command)
        
        try:
            giveaway = await self.get_giveaway_from_message_link(ctx, message_link)
            if not giveaway:
                return
            
            if ctx.author.id not in giveaway.host_ids and not ctx.author.guild_permissions.administrator:
                return await self.bot.warn(ctx, "You can't manage this giveaway.")
            
            image_url = url_or_attachment
            if ctx.message.attachments:
                image_url = ctx.message.attachments[0].url
            
            if not image_url:
                return await self.bot.warn(ctx, "Provide an image URL or attachment.")
            
            giveaway.image_url = image_url
            await giveaway.save()
            
            try:
                guild = self.bot.get_guild(giveaway.guild_id)
                channel = guild.get_channel(giveaway.channel_id) if guild else None
                
                if channel:
                    msg = await channel.fetch_message(giveaway.message_id)
                    embed = self.create_giveaway_embed(giveaway)
                    await msg.edit(embed=embed)
            except:
                pass
            
            await self.bot.grant(ctx, "Image updated!")
        except Exception as e:
            logger("error", f"Error updating image: {e}")
            await self.bot.warn(ctx, "Failed to update image.")

    @giveaway_edit.command(name="thumbnail")
    async def edit_thumbnail(self, ctx, message_link: str = None, url_or_attachment: str = None):
        """Change thumbnail for a giveaway embed"""
        if not message_link:
            return await ctx.send_help(ctx.command)
        
        try:
            giveaway = await self.get_giveaway_from_message_link(ctx, message_link)
            if not giveaway:
                return
            
            if ctx.author.id not in giveaway.host_ids and not ctx.author.guild_permissions.administrator:
                return await self.bot.warn(ctx, "You can't manage this giveaway.")
            
            thumbnail_url = url_or_attachment
            if ctx.message.attachments:
                thumbnail_url = ctx.message.attachments[0].url
            
            if not thumbnail_url:
                return await self.bot.warn(ctx, "Provide a thumbnail URL or attachment.")
            
            giveaway.thumbnail_url = thumbnail_url
            await giveaway.save()
            
            try:
                guild = self.bot.get_guild(giveaway.guild_id)
                channel = guild.get_channel(giveaway.channel_id) if guild else None
                
                if channel:
                    msg = await channel.fetch_message(giveaway.message_id)
                    embed = self.create_giveaway_embed(giveaway)
                    await msg.edit(embed=embed)
            except:
                pass
            
            await self.bot.grant(ctx, "Thumbnail updated!")
        except Exception as e:
            logger("error", f"Error updating thumbnail: {e}")
            await self.bot.warn(ctx, "Failed to update thumbnail.")

    @giveaway_edit.command(name="color")
    async def edit_color(self, ctx, message_link: str = None, color: str = None):
        """Change color for a giveaway embed"""
        if not message_link or not color:
            return await ctx.send_help(ctx.command)
        
        try:
            giveaway = await self.get_giveaway_from_message_link(ctx, message_link)
            if not giveaway:
                return
            
            if ctx.author.id not in giveaway.host_ids and not ctx.author.guild_permissions.administrator:
                return await self.bot.warn(ctx, "You can't manage this giveaway.")
            
            try:
                if color.startswith('#'):
                    color = color[1:]
                
                color_int = int(color, 16)
                giveaway.color = color_int
                await giveaway.save()
                
                try:
                    guild = self.bot.get_guild(giveaway.guild_id)
                    channel = guild.get_channel(giveaway.channel_id) if guild else None
                    
                    if channel:
                        msg = await channel.fetch_message(giveaway.message_id)
                        embed = self.create_giveaway_embed(giveaway)
                        await msg.edit(embed=embed)
                except:
                    pass
                
                await self.bot.grant(ctx, "Color updated!")
            
            except ValueError:
                await self.bot.warn(ctx, "Invalid color format. Use HEX (#RRGGBB)")
        except Exception as e:
            logger("error", f"Error updating color: {e}")
            await self.bot.warn(ctx, "Failed to update color.")

    @giveaway_edit.command(name="duration")
    async def edit_duration(self, ctx, message_link: str = None, date: str = None):
        """Change the end date for a giveaway"""
        if not message_link or not date:
            return await ctx.send_help(ctx.command)
        
        try:
            giveaway = await self.get_giveaway_from_message_link(ctx, message_link)
            if not giveaway:
                return
            
            if ctx.author.id not in giveaway.host_ids and not ctx.author.guild_permissions.administrator:
                return await self.bot.warn(ctx, "You can't manage this giveaway.")
            
            duration_seconds = 0
            duration_str = date.lower()
            
            time_units = {
                's': 1,
                'm': 60,
                'h': 3600,
                'd': 86400,
                'w': 604800
            }
            
            matches = re.findall(r'(\d+)([smhdw])', duration_str)
            if not matches:
                return await self.bot.warn(ctx, "Invalid duration format. Use format like: 1h, 30m, 1d, etc.")
            
            for amount, unit in matches:
                duration_seconds += int(amount) * time_units.get(unit, 0)
            
            if duration_seconds == 0:
                return await self.bot.warn(ctx, "Invalid duration.")
            
            giveaway.end_time = datetime.utcnow() + timedelta(seconds=duration_seconds)
            await giveaway.save()
            
            try:
                guild = self.bot.get_guild(giveaway.guild_id)
                channel = guild.get_channel(giveaway.channel_id) if guild else None
                
                if channel:
                    msg = await channel.fetch_message(giveaway.message_id)
                    embed = self.create_giveaway_embed(giveaway)
                    await msg.edit(embed=embed)
            except:
                pass
            
            await self.bot.grant(ctx, "Duration updated!")
        except Exception as e:
            logger("error", f"Error updating duration: {e}")
            await self.bot.warn(ctx, "Failed to update duration.")

    @giveaway_edit.command(name="requiredroles")
    async def edit_requiredroles(self, ctx, message_link: str = None, *, roles: str = None):
        """Set required roles for giveaway entry"""
        if not message_link:
            return await ctx.send_help(ctx.command)
        
        try:
            giveaway = await self.get_giveaway_from_message_link(ctx, message_link)
            if not giveaway:
                return
            
            if ctx.author.id not in giveaway.host_ids and not ctx.author.guild_permissions.administrator:
                return await self.bot.warn(ctx, "You can't manage this giveaway.")
            
            role_ids = []
            for role_mention in ctx.message.role_mentions:
                role_ids.append(role_mention.id)
            
            if not role_ids:
                giveaway.required_roles = []
            else:
                giveaway.required_roles = role_ids
            
            await giveaway.save()
            
            try:
                guild = self.bot.get_guild(giveaway.guild_id)
                channel = guild.get_channel(giveaway.channel_id) if guild else None
                
                if channel:
                    msg = await channel.fetch_message(giveaway.message_id)
                    embed = self.create_giveaway_embed(giveaway)
                    await msg.edit(embed=embed)
            except:
                pass
            
            await self.bot.grant(ctx, "Required roles updated!")
        except Exception as e:
            logger("error", f"Error updating requiredroles: {e}")
            await self.bot.warn(ctx, "Failed to update required roles.")

    @giveaway_edit.command(name="roles")
    async def edit_roles(self, ctx, message_link: str = None, *, roles: str = None):
        """Award winners specific roles for a giveaway"""
        if not message_link:
            return await ctx.send_help(ctx.command)
        
        try:
            giveaway = await self.get_giveaway_from_message_link(ctx, message_link)
            if not giveaway:
                return
            
            if ctx.author.id not in giveaway.host_ids and not ctx.author.guild_permissions.administrator:
                return await self.bot.warn(ctx, "You can't manage this giveaway.")
            
            role_ids = []
            for role_mention in ctx.message.role_mentions:
                role_ids.append(role_mention.id)
            
            if not role_ids:
                giveaway.award_roles = []
            else:
                giveaway.award_roles = role_ids
            
            await giveaway.save()
            await self.bot.grant(ctx, "Award roles updated!")
        except Exception as e:
            logger("error", f"Error updating roles: {e}")
            await self.bot.warn(ctx, "Failed to update award roles.")

    @giveaway_edit.command(name="age")
    async def edit_age(self, ctx, message_link: str = None, days: int = None):
        """Set minimum account age for new entries"""
        if not message_link or days is None:
            return await ctx.send_help(ctx.command)
        
        try:
            giveaway = await self.get_giveaway_from_message_link(ctx, message_link)
            if not giveaway:
                return
            
            if ctx.author.id not in giveaway.host_ids and not ctx.author.guild_permissions.administrator:
                return await self.bot.warn(ctx, "You can't manage this giveaway.")
            
            if days < 0:
                return await self.bot.warn(ctx, "Days must be 0 or higher.")
            
            giveaway.min_account_age_days = days if days > 0 else None
            await giveaway.save()
            
            try:
                guild = self.bot.get_guild(giveaway.guild_id)
                channel = guild.get_channel(giveaway.channel_id) if guild else None
                
                if channel:
                    msg = await channel.fetch_message(giveaway.message_id)
                    embed = self.create_giveaway_embed(giveaway)
                    await msg.edit(embed=embed)
            except:
                pass
            
            await self.bot.grant(ctx, "Minimum account age updated!")
        except Exception as e:
            logger("error", f"Error updating age: {e}")
            await self.bot.warn(ctx, "Failed to update account age.")

    @giveaway_edit.command(name="stay")
    async def edit_stay(self, ctx, message_link: str = None, days: int = None):
        """Set minimum server stay for new entries"""
        if not message_link or days is None:
            return await ctx.send_help(ctx.command)
        
        try:
            giveaway = await self.get_giveaway_from_message_link(ctx, message_link)
            if not giveaway:
                return
            
            if ctx.author.id not in giveaway.host_ids and not ctx.author.guild_permissions.administrator:
                return await self.bot.warn(ctx, "You can't manage this giveaway.")
            
            if days < 0:
                return await self.bot.warn(ctx, "Days must be 0 or higher.")
            
            giveaway.min_server_stay_days = days if days > 0 else None
            await giveaway.save()
            
            try:
                guild = self.bot.get_guild(giveaway.guild_id)
                channel = guild.get_channel(giveaway.channel_id) if guild else None
                
                if channel:
                    msg = await channel.fetch_message(giveaway.message_id)
                    embed = self.create_giveaway_embed(giveaway)
                    await msg.edit(embed=embed)
            except:
                pass
            
            await self.bot.grant(ctx, "Minimum server stay updated!")
        except Exception as e:
            logger("error", f"Error updating stay: {e}")
            await self.bot.warn(ctx, "Failed to update server stay.")

    @giveaway_edit.command(name="host")
    async def edit_host(self, ctx, message_link: str = None, *, members: str = None):
        """Set new hosts for a giveaway"""
        if not message_link:
            return await ctx.send_help(ctx.command)
        
        try:
            giveaway = await self.get_giveaway_from_message_link(ctx, message_link)
            if not giveaway:
                return
            
            if ctx.author.id not in giveaway.host_ids and not ctx.author.guild_permissions.administrator:
                return await self.bot.warn(ctx, "You can't manage this giveaway.")
            
            host_ids = []
            for member_mention in ctx.message.mentions:
                host_ids.append(member_mention.id)
            
            if not host_ids:
                giveaway.host_ids = [ctx.author.id]
            else:
                giveaway.host_ids = host_ids
            
            await giveaway.save()
            await self.bot.grant(ctx, "Hosts updated!")
        except Exception as e:
            logger("error", f"Error updating hosts: {e}")
            await self.bot.warn(ctx, "Failed to update hosts.")

    async def get_guild_data(self, guild_id):
        """Get or create guild data for booster roles"""
        from models.configs import GuildConfig
        config = await GuildConfig.find_one(GuildConfig.guild_id == guild_id)
        if not config:
            config = GuildConfig(guild_id=guild_id)
            await config.save()
        return config

    @commands.group(name="boosterrole", aliases=['br'], invoke_without_command=True)
    async def boosterrole(self, ctx):
        """Main command for Booster Role management."""
        await ctx.send_help(ctx.command)

    @boosterrole.command(name="enable")
    @commands.has_permissions(manage_roles=True)
    async def br_enable(self, ctx):
        """Enable the booster role system."""
        config = await self.get_guild_data(ctx.guild.id)
        config.booster_role_enabled = True
        await config.save()
        await self.bot.grant(ctx, "Booster roles have been **enabled**")

    @boosterrole.command(name="base")
    @commands.has_permissions(manage_roles=True)
    async def br_base(self, ctx, role: discord.Role):
        """Set a base role for boosters to be placed under."""
        config = await self.get_guild_data(ctx.guild.id)
        config.booster_role_base_id = role.id
        await config.save()
        await self.bot.grant(ctx, f"Base role set to **{role.name}**")

    @boosterrole.command(name="create")
    async def br_create(self, ctx, *, role_name: str):
        """Create a custom booster role."""
        if not ctx.author.premium_since and not ctx.author.guild_permissions.manage_guild:
            return await self.bot.warn(ctx, "You must be a **server booster** to use this command")

        config = await self.get_guild_data(ctx.guild.id)

        if str(ctx.author.id) in config.booster_user_roles:
            return await self.bot.warn(ctx, "You already have a booster role")

        position = 1
        if config.booster_role_base_id:
            base_role = ctx.guild.get_role(config.booster_role_base_id)
            if base_role:
                position = max(1, base_role.position - 1)

        try:
            new_role = await ctx.guild.create_role(
                name=role_name,
                reason=f"Booster role for {ctx.author}",
            )
            await new_role.edit(position=position)
            await ctx.author.add_roles(new_role)

            config.booster_user_roles[str(ctx.author.id)] = new_role.id
            await config.save()
            await self.bot.grant(ctx, f"Created role: **{role_name}**")
        except discord.Forbidden:
            await self.bot.warn(ctx, "Failed to create role. Ensure my role is high enough")

    @boosterrole.command(name="color")
    async def br_color(self, ctx, hex_color: str):
        """Change the color of your booster role."""
        config = await self.get_guild_data(ctx.guild.id)
        role_id = config.booster_user_roles.get(str(ctx.author.id))

        if not role_id:
            return await self.bot.warn(ctx, "You don't have a booster role")

        role = ctx.guild.get_role(role_id)
        if not role:
            return await self.bot.warn(ctx, "Your role was not found")

        try:
            # Convert hex string to discord.Color
            color = discord.Color(int(hex_color.lstrip('#'), 16))
            await role.edit(color=color)
            await self.bot.grant(ctx, f"Color updated to `{hex_color}`")
        except ValueError:
            await self.bot.warn(ctx, "Invalid hex color provided")

    @boosterrole.command(name="icon")
    async def br_icon(self, ctx, icon_url: str = None):
        """Set an icon for your booster role (Requires Level 2)."""
        if ctx.guild.premium_tier < 2:
            return await self.bot.warn(ctx, "Server needs Level 2 Boost for role icons")

        config = await self.get_guild_data(ctx.guild.id)
        role_id = config.booster_user_roles.get(str(ctx.author.id))
        
        if not role_id:
            return await self.bot.warn(ctx, "You don't have a booster role")

        role = ctx.guild.get_role(role_id)
        
        icon_data = None
        if ctx.message.attachments:
            icon_data = await ctx.message.attachments[0].read()
        elif icon_url:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(icon_url) as resp:
                        icon_data = await resp.read()
            except:
                return await self.bot.warn(ctx, "Failed to fetch image from URL")
        else:
            return await self.bot.warn(ctx, "Please provide an image URL or attachment")

        try:
            await role.edit(display_icon=icon_data)
            await self.bot.grant(ctx, "Icon updated")
        except Exception as e:
            await self.bot.warn(ctx, f"Invalid icon or error setting it: {e}")

    @boosterrole.command(name="remove")
    async def br_remove(self, ctx):
        """Remove your custom booster role."""
        config = await self.get_guild_data(ctx.guild.id)
        role_id = config.booster_user_roles.get(str(ctx.author.id))

        if not role_id:
            return await self.bot.warn(ctx, "You don't have a booster role")

        role = ctx.guild.get_role(role_id)
        if role:
            try:
                await role.delete(reason="Booster role removed by user")
            except:
                pass

        del config.booster_user_roles[str(ctx.author.id)]
        await config.save()
        await self.bot.grant(ctx, "Booster role removed")

    @boosterrole.command(name="list")
    async def br_list(self, ctx):
        """List all current booster roles."""
        config = await self.get_guild_data(ctx.guild.id)
        user_roles = config.booster_user_roles

        if not user_roles:
            return await self.bot.neutral(ctx, "There are no booster roles in this server")

        entries = [f"<@{u}>: <@&{r}>" for u, r in user_roles.items()]
        
        pages = []
        for i in range(0, len(entries), 10):
            chunk = entries[i:i + 10]
            embed = discord.Embed(
                title="Booster Roles",
                description="\n".join(chunk),
                color=0x242429
            )
            embed.set_footer(text=f"Page {i//10 + 1} of {(len(entries)-1)//10 + 1}")
            pages.append(embed)

        if len(pages) == 1:
            await ctx.send(embed=pages[0])
        else:
            await self.bot.paginator(ctx, pages)

    @commands.command(name="gemini", aliases=["ai", "ask"])
    async def gemini(self, ctx, *, query: str = None):
        """Ask the Gemini AI a question"""
        if not query:
            return await self.bot.warn(ctx, "Please provide a question or prompt for the AI.")

        # Configure Gemini API
        api_key = os.getenv('GEMINI_KEY') or 'AIzaSyCAtPRuCX4rSryo8wzgEjaAb5BWctqbMvk'
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.5-flash")

        await ctx.typing()

        try:
            response = model.generate_content(query)
            text = response.text

            if len(text) <= 2000:
                embed = discord.Embed(
                    title="Gemini AI",
                    description=text,
                    color=0x4285F4
                )
                embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)
                return await ctx.send(embed=embed)

            # Pagination for long responses
            pages = []
            chunk_size = 2000
            
            for i in range(0, len(text), chunk_size):
                page_num = (i // chunk_size) + 1
                embed = discord.Embed(
                    title="Gemini AI (Continued)",
                    description=text[i:i + chunk_size],
                    color=0x4285F4
                )
                embed.set_footer(text=f"Page {page_num}")
                pages.append(embed)

            await self.bot.paginator(ctx, pages)

        except Exception as err:
            logger("error", f"Gemini Command Error: {err}")
            
            if "SAFETY" in str(err).upper():
                return await self.bot.warn(ctx, "The AI declined to answer that prompt due to safety filters.")
            
            await self.bot.warn(ctx, "There was an error communicating with Gemini. Make sure your API key is valid.")

    @commands.command(name="numlookup", aliases=["phone", "lookup"])
    async def numlookup(self, ctx, *, number: str = None):
        """Lookup information for a phone number"""
        if not number:
            return await self.bot.warn(ctx, "Please provide a phone number (e.g., `+14152007986`).")
        
        if phonenumbers is None:
            return await self.bot.warn(ctx, "Phone number library not installed. Install with `pip install phonenumbers`")

        try:
            phone_number = phonenumbers.parse(number if number.startswith('+') else f'+{number}')
            
            if not phonenumbers.is_valid_number(phone_number):
                return await self.bot.warn(ctx, "Please provide a **valid** phone number with a country code (e.g., `+1...`).")

            embed = discord.Embed(
                title=f"📞 Phone Lookup: {phonenumbers.format_number(phone_number, phonenumbers.PhoneNumberFormat.INTERNATIONAL)}",
                color=0x242429
            )
            embed.add_field(
                name="Country",
                value=f"🇷🇺 {phonenumbers.region_code_for_number(phone_number) or 'Unknown'}",
                inline=True
            )
            embed.add_field(
                name="Country Code",
                value=f"`+{phone_number.country_code}`",
                inline=True
            )
            embed.add_field(
                name="Type",
                value=f"`{phonenumbers.number_type(phone_number).name.lower()}`",
                inline=True
            )
            embed.add_field(
                name="National Format",
                value=f"`{phonenumbers.format_number(phone_number, phonenumbers.PhoneNumberFormat.NATIONAL)}`",
                inline=True
            )
            embed.add_field(
                name="E.164 Format",
                value=f"`{phonenumbers.format_number(phone_number, phonenumbers.PhoneNumberFormat.E164)}`",
                inline=True
            )
            embed.timestamp = datetime.utcnow()
            
            await ctx.send(embed=embed)

        except phonenumbers.NumberParseException:
            await self.bot.warn(ctx, "An error occurred. Make sure to include the country code (e.g., `+1` for USA).")

    @commands.command(name="emaillookup", aliases=["email", "rep", "breach"])
    async def emaillookup(self, ctx, *, email: str = None):
        """Check email reputation and security breaches via Abstract"""
        if not email:
            return await self.bot.warn(ctx, "Please provide an email address.")

        await ctx.typing()

        try:
            api_key = 'a3358299bd4143c599baa7d048e2dfb3'
            url = f'https://emailreputation.abstractapi.com/v1/?api_key={api_key}&email={email}'
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    data = await response.json()

            if 'error' in data:
                return await self.bot.warn(ctx, f"Error: {data.get('error', {}).get('message', 'Unknown error')}")

            risk = data.get('email_risk', {}).get('address_risk_status', 'unknown')
            
            if risk == 'low':
                embed_color = 0x23a55a
            elif risk == 'medium':
                embed_color = 0xfaa61a
            else:
                embed_color = 0xf23f43

            embed = discord.Embed(
                title=f"Email Reputation: {data.get('email_address', 'Unknown')}",
                color=embed_color
            )

            deliverability = data.get('email_deliverability', {})
            embed.add_field(
                name="📧 Deliverability",
                value=f"Status: `{deliverability.get('status', 'N/A')}`\nSMTP: `{'Valid' if deliverability.get('is_smtp_valid') else 'Invalid'}`",
                inline=True
            )

            quality = data.get('email_quality', {})
            embed.add_field(
                name="🛡️ Risk Level",
                value=f"Level: `{risk.upper()}`\nScore: `{int((quality.get('score', 0) or 0) * 100)}%`",
                inline=True
            )

            domain = data.get('email_domain', {})
            embed.add_field(
                name="🌐 Domain",
                value=f"Age: `{domain.get('domain_age', 'N/A')} days`\nLive: `{'Yes' if domain.get('is_live_site') else 'No'}`",
                inline=True
            )

            embed.set_footer(text=f"Requested by {ctx.author.name}", icon_url=ctx.author.display_avatar.url)
            embed.timestamp = datetime.utcnow()

            breaches = data.get('email_breaches', {})
            if breaches.get('total_breaches', 0) > 0:
                breach_list = '\n'.join([
                    f"• **{b.get('domain')}** ({b.get('breach_date')})"
                    for b in breaches.get('breached_domains', [])[:5]
                ])
                embed.add_field(
                    name=f"🔓 Data Breaches ({breaches.get('total_breaches')})",
                    value=breach_list,
                    inline=False
                )
            else:
                embed.add_field(
                    name="🔓 Data Breaches",
                    value="✅ No known breaches found.",
                    inline=False
                )

            await ctx.send(embed=embed)

        except Exception as error:
            logger("error", f"Email Lookup Error: {error}")
            await self.bot.warn(ctx, "An error occurred while fetching the reputation data.")

    @commands.command(name="iplookup", aliases=["ip", "geo"])
    async def iplookup(self, ctx, *, ip_address: str = None):
        """Lookup information about an IP address"""
        if not ip_address:
            return await self.bot.warn(ctx, "Please provide an IP address.")

        await ctx.typing()

        try:
            url = f'http://ip-api.com/json/{ip_address}?fields=status,message,country,countryCode,regionName,city,zip,lat,lon,timezone,isp,org,as,query'
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    data = await response.json()

            if data.get('status') == 'fail':
                return await self.bot.warn(ctx, f"Failed to lookup IP: **{data.get('message')}**")

            embed = discord.Embed(
                title=f"IP Information: {data.get('query')}",
                color=0x2f3136
            )

            embed.add_field(
                name="Location",
                value=f"{data.get('city')}, {data.get('regionName')}, {data.get('country')} ({data.get('zip', 'N/A')})",
                inline=False
            )
            embed.add_field(name="ISP", value=data.get('isp', 'Unknown'), inline=True)
            embed.add_field(name="Organization", value=data.get('org', 'Unknown'), inline=True)
            embed.add_field(name="ASN", value=data.get('as', 'Unknown'), inline=True)
            embed.add_field(name="Timezone", value=data.get('timezone', 'Unknown'), inline=True)
            embed.add_field(
                name="Coordinates",
                value=f"`{data.get('lat')}, {data.get('lon')}`",
                inline=True
            )

            embed.set_footer(text=f"Requested by {ctx.author.name}", icon_url=ctx.author.display_avatar.url)
            embed.timestamp = datetime.utcnow()

            await ctx.send(embed=embed)

        except Exception as error:
            logger("error", f"IP Lookup Error: {error}")
            await self.bot.warn(ctx, "An error occurred while retrieving information for that IP address.")

    @commands.command(name="npm", aliases=["package", "pkg"])
    async def npm(self, ctx, *, package_name: str = None):
        """Search for a package on the NPM registry"""
        if not package_name:
            return await self.bot.warn(ctx, "Please provide a package name.")

        await ctx.typing()

        try:
            url = f'https://registry.npmjs.com/{package_name}'
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 404:
                        return await self.bot.warn(ctx, f"The package `{package_name}` does not exist.")
                    data = await response.json()

            if data.get('error'):
                return await self.bot.warn(ctx, f"Could not find a package named `{package_name}`.")

            latest_version = data.get('dist-tags', {}).get('latest')
            details = data.get('versions', {}).get(latest_version, {})
            maintainers = ', '.join([m.get('name', 'Unknown') for m in data.get('maintainers', [])]) or 'None'

            embed = discord.Embed(
                title=f"NPM: {data.get('name')}",
                url=f"https://www.npmjs.com/package/{data.get('name')}",
                description=data.get('description', 'No description provided.'),
                color=0xCB3837
            )

            embed.set_thumbnail(url='https://static-00.iconduck.com/assets.00/npm-icon-2048x2048-89u8u3id.png')
            embed.add_field(name="Latest Version", value=f"`{latest_version}`", inline=True)
            embed.add_field(name="Author", value=f"`{data.get('author', {}).get('name', 'Anonymous')}`", inline=True)
            embed.add_field(name="License", value=f"`{data.get('license', 'N/A')}`", inline=True)
            embed.add_field(name="Maintainers", value=f"`{maintainers}`", inline=False)
            embed.add_field(
                name="Dependencies",
                value=f"`{len(details.get('dependencies', {}))}`",
                inline=True
            )
            embed.add_field(
                name="Install",
                value=f"```bash\nnpm install {data.get('name')}\n```",
                inline=False
            )
            embed.timestamp = datetime.utcnow()

            view = discord.ui.View()
            view.add_item(
                discord.ui.Button(
                    label="View on NPM",
                    url=f"https://www.npmjs.com/package/{data.get('name')}"
                )
            )

            await ctx.send(embed=embed, view=view)

        except Exception as error:
            logger("error", f"NPM Search Error: {error}")
            await self.bot.warn(ctx, "An error occurred while fetching package data.")

    @commands.command(name="pypi", aliases=["python", "pip"])
    async def pypi(self, ctx, *, library_name: str = None):
        """Search for a package on the Python Package Index (PyPI)"""
        if not library_name:
            return await self.bot.warn(ctx, "Please provide a library name.")

        await ctx.typing()

        try:
            url = f'https://pypi.org/pypi/{library_name}/json'
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 404:
                        return await self.bot.warn(ctx, f"The library `{library_name}` was not found on PyPI.")
                    data = await response.json()

            info = data.get('info', {})
            summary = info.get('summary', 'No summary provided.')

            embed = discord.Embed(
                title=f"PyPI: {info.get('name')}",
                url=info.get('package_url'),
                description=summary,
                color=0x3776AB
            )

            embed.set_thumbnail(url='https://upload.wikimedia.org/wikipedia/commons/thumb/c/c3/Python-logo-notext.svg/1869px-Python-logo-notext.svg.png')
            embed.add_field(name="Latest Version", value=f"`{info.get('version')}`", inline=True)
            embed.add_field(name="Author", value=f"`{info.get('author', 'Anonymous')}`", inline=True)
            embed.add_field(name="License", value=f"`{info.get('license', 'N/A')}`", inline=True)
            embed.add_field(
                name="Python Version",
                value=f"`{info.get('requires_python', 'Any')}`",
                inline=False
            )
            
            home_page = info.get('home_page', '')
            if home_page and home_page != 'UNKNOWN':
                embed.add_field(name="Home Page", value=home_page, inline=False)
            else:
                embed.add_field(name="Home Page", value="None", inline=False)

            embed.add_field(
                name="Install",
                value=f"```bash\npip install {info.get('name')}\n```",
                inline=False
            )
            embed.timestamp = datetime.utcnow()

            view = discord.ui.View()
            view.add_item(
                discord.ui.Button(
                    label="View on PyPI",
                    url=info.get('package_url')
                )
            )

            await ctx.send(embed=embed, view=view)

        except Exception as error:
            logger("error", f"PyPI Search Error: {error}")
            await self.bot.warn(ctx, "An error occurred while fetching library data.")

    @commands.command(name="wikipedia", aliases=["wiki"])
    async def wikipedia(self, ctx, *, query: str = None):
        """Search Wikipedia for a specific query"""
        if not query:
            return await self.bot.warn(ctx, "Please provide a search query.")

        await ctx.typing()

        try:
            url = f'https://en.wikipedia.org/api/rest_v1/page/summary/{query}'
            headers = {'User-Agent': 'wock-bot/1.0 (Discord Bot; https://github.com/wock-bot)'}
            
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(url) as response:
                    if response.status == 404:
                        return await self.bot.warn(ctx, "No results found for that query.")
                    data = await response.json(content_type=None)

            if not data or data.get('title') == 'Not found.':
                return await self.bot.warn(ctx, "No results found for that query.")

            embed = discord.Embed(
                title=data.get('title'),
                url=data.get('content_urls', {}).get('desktop', {}).get('page'),
                description=data.get('extract', data.get('description', 'No summary available.')),
                color=0xf6f6f6
            )

            embed.set_author(
                name=ctx.author.name,
                icon_url=ctx.author.display_avatar.url
            )

            thumbnail = data.get('thumbnail')
            if thumbnail:
                embed.set_thumbnail(url=thumbnail.get('source'))
            else:
                embed.set_thumbnail(url='https://i.imgur.com/6S7X9S0.png')

            links = []
            content_urls = data.get('content_urls', {}).get('desktop', {})
            if content_urls.get('page'):
                links.append(f"[Page]({content_urls.get('page')})")
            if content_urls.get('revisions'):
                links.append(f"[Revisions]({content_urls.get('revisions')})")
            if content_urls.get('edit'):
                links.append(f"[Edit]({content_urls.get('edit')})")
            if content_urls.get('talk'):
                links.append(f"[Talk]({content_urls.get('talk')})")

            if links:
                embed.add_field(name="Quick Links", value=" | ".join(links), inline=False)

            embed.timestamp = datetime.utcnow()

            await ctx.send(embed=embed)

        except Exception as error:
            logger("error", f"Wikipedia Search Error: {error}")
            await self.bot.warn(ctx, "An error occurred while searching Wikipedia.")


async def setup(bot):
    await bot.add_cog(Utility(bot))
