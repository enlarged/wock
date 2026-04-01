import discord
import aiohttp
import asyncio
import os
import re
from datetime import datetime
from typing import Union
from beanie import PydanticObjectId
from discord.ext import commands, tasks
from utils.logger import logger
from utils.paginator import WockPaginator
from utils.catbox import upload_to_catbox
from models.configs import UserConfig, GuildConfig
from models.gang import Gang
from models.biolink import BioProfile, BioGroup, BioConnection
from models.upload import Upload
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from googletrans import Translator


class Miscellaneous(commands.Cog, name="Miscellaneous"):
    def __init__(self, bot):
        self.bot = bot
        self.cache = {}
        self.wock_headers = {"x-api-key": self.bot.config.get("wock_api")}
        self.rapid_headers = {
            'x-rapidapi-key': '141e070f61mshebb9f8f7dac8e8fp179694jsnb651b7323525',
            'x-rapidapi-host': 'twitter241.p.rapidapi.com'
        }
        self.weather_api_key = '985f10d327f3695fa10aab134e0b6391'
        self.twitch_client_id = 'iijx9qzjllzo68zzxi07plct52ld2m'
        self.twitch_client_secret = '1fowmpozxd4n6sdhruuhln2knv2rlg'
        self.twitch_access_token = None
        self.twitch_live_cache = {}
        self.api_key = 'vnPxojTZgUQwtYjMx07ay12aBpceLGALWUy5TggW4GHRTbIGyW'
        self.check_twitch_live.start()
        
        self.spotify_client_id = '90595927739c497889bb248dd3aa422d'
        self.spotify_client_secret = '5f3629f827b74218af14627dac0daed7'
        auth_manager = SpotifyClientCredentials(client_id=self.spotify_client_id, client_secret=self.spotify_client_secret)
        self.sp = spotipy.Spotify(auth_manager=auth_manager)
        self.translator = Translator()
        
        self.tumblr_api_key = 'vnPxojTZgUQwtYjMx07ay12aBpceLGALWUy5TggW4GHRTbIGyW'
        self.fnbr_key = '816ece3e-a07d-4856-a7de-101b00279ddf'
        self.host_cooldowns = {}
    
    def cog_unload(self):
        self.check_twitch_live.cancel()

    def get_channel_or_thread(self, guild, channel_id):
        """Get a channel or thread by ID from a guild"""
        channel = guild.get_channel(channel_id)
        if channel:
            return channel
        
        for thread in guild.threads:
            if thread.id == channel_id:
                return thread
        
        return None

    def clean_html(self, raw_html):
        if not raw_html: return "No description provided."
        return re.sub(r'<.*?>', '', raw_html)[:240]

    @commands.command(name="roblox", aliases=["rbx", "rblx"])
    async def roblox(self, ctx, *, username: str = None):
        """View a Roblox player's profile"""
        if not username:
            return await ctx.send_help(ctx.command)
        async with aiohttp.ClientSession(headers=self.wock_headers) as session:
            async with session.get("https://wock.best/api/roblox/profile", params={"username": username}) as resp:
                data = await resp.json()
                if not data.get("success"): return await self.bot.warn(ctx, f"User **{username}** not found.")
                
                user, stats = data["user"], data["stats"]
                status_map = {0: 'Offline 🔴', 1: 'Online 🟢', 2: 'In Game 🔵', 3: 'In Studio 🟠'}
                
                embed = discord.Embed(color=0x2ecc71 if user['status'] > 0 else self.bot.config["color"],
                    title=f"{user['displayName']} (@{user['name']})",
                    url=f"https://roblox.com/users/{user['id']}/profile",
                    description=user.get('description') or "*No bio provided.*")

                async with session.get(f"https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds={user['id']}&size=420x420&format=Png") as r:
                    av_data = await r.json()
                    if av_data.get('data'): embed.set_thumbnail(url=av_data['data'][0]['imageUrl'])

                embed.add_field(name="Friends", value=f"{stats['friends']:,}", inline=True)
                embed.add_field(name="Followers", value=f"{stats['followers']:,}", inline=True)
                embed.add_field(name="Following", value=f"{stats['following']:,}", inline=True)
                embed.set_footer(text=f"Status: {status_map.get(user['status'], 'Offline')} • ID: {user['id']}")
                await ctx.send(embed=embed)

    @commands.command(aliases=["tt"])
    async def tiktok(self, ctx, username: str = None):
        """View a TikTok profile"""
        if not username:
            return await ctx.send_help(ctx.command)
        target = username.lower()
        if target in self.cache: return await ctx.send(embed=self.cache[target])

        async with aiohttp.ClientSession() as session:
            async with session.post(f"https://www.tikwm.com/api/user/info?unique_id={target}") as r:
                data = await r.json()
                if not data.get("data"): return await self.bot.warn(ctx, f"User **{username}** not found.")
                
                u, s = data["data"]["user"], data["data"]["stats"]
                embed = discord.Embed(color=0x242429, title=f"{u['nickname']} (@{u['uniqueId']})",
                    url=f"https://tiktok.com/@{u['uniqueId']}", description=u.get("signature"))
                embed.set_thumbnail(url=u["avatarLarger"])
                embed.add_field(name="Likes", value=f"{s['heartCount']:,}", inline=True)
                embed.add_field(name="Followers", value=f"{s['followerCount']:,}", inline=True)
                embed.add_field(name="Following", value=f"{s['followingCount']:,}", inline=True)
                
                self.cache[target] = embed
                await ctx.send(embed=embed)

    @commands.group(name="spotify", aliases=["sp"], invoke_without_command=True)
    async def spotify(self, ctx, *, query: str = None):
        """Search Spotify for a track"""
        if not query:
            return await ctx.send_help(ctx.command)
        
        results = self.sp.search(q=query, limit=1, type='track')
        tracks = results['tracks']['items']
        
        if not tracks:
            return await self.bot.warn(ctx, f"No tracks found for **{query}**")
        
        await ctx.send(tracks[0]['external_urls']['spotify'])

    @spotify.command(name="album")
    async def spotify_album(self, ctx, *, query: str = None):
        """Search for an album on Spotify"""
        if not query:
            return await ctx.send_help(ctx.command)
            
        results = self.sp.search(q=query, limit=1, type='album')
        albums = results['albums']['items']
        
        if not albums:
            return await self.bot.warn(ctx, f"No albums found for **{query}**")
            
        await ctx.send(albums[0]['external_urls']['spotify'])

    @spotify.command(name="artist")
    async def spotify_artist(self, ctx, *, query: str = None):
        """Search for an artist on Spotify"""
        if not query:
            return await ctx.send_help(ctx.command)
            
        results = self.sp.search(q=query, limit=1, type='artist')
        artists = results['artists']['items']
        
        if not artists:
            return await self.bot.warn(ctx, f"No artists found for **{query}**")
            
        await ctx.send(artists[0]['external_urls']['spotify'])

    @spotify.command(name="playlist")
    async def spotify_playlist(self, ctx, *, query: str = None):
        """Search for a playlist on Spotify"""
        if not query:
            return await ctx.send_help(ctx.command)
            
        results = self.sp.search(q=query, limit=1, type='playlist')
        playlists = results['playlists']['items']
        
        if not playlists:
            return await self.bot.warn(ctx, f"No playlists found for **{query}**")
            
        await ctx.send(playlists[0]['external_urls']['spotify'])

    @commands.group(name="youtube", aliases=["yt"], invoke_without_command=True)
    async def youtube(self, ctx, *, query: str = None):
        """Search YouTube for a video"""
        if not query:
            return await ctx.send_help(ctx.command)
        
        try:
            async with aiohttp.ClientSession() as session:
                params = {"search_query": query}
                async with session.get("https://www.youtube.com/results", params=params) as resp:
                    if resp.status != 200:
                        return await self.bot.deny(ctx, "Failed to search YouTube")
                    
                    html = await resp.text()
                    import re
                    video_id_pattern = r'\"videoId\":\"([a-zA-Z0-9_-]{11})'
                    matches = re.findall(video_id_pattern, html)
                    
                    if not matches:
                        return await self.bot.warn(ctx, f"No videos found for **{query}**")
                    
                    video_url = f"https://youtube.com/watch?v={matches[0]}"
                    await ctx.send(video_url)
        except Exception as e:
            await self.bot.deny(ctx, f"Error searching YouTube: {str(e)}")

    @commands.command(
        name="wolfram",
        aliases=["w", "wolframalpha"],
        description="Get math and scientific answers from Wolfram Alpha",
        help="2+2"
    )
    async def wolfram(self, ctx, *, query: str = None):
        """Get math and scientific answers from Wolfram Alpha"""
        if not query:
            return await ctx.send_help(ctx.command)

        app_id = "UQTHY6-T6GYE36LHR"
        url = "https://api.wolframalpha.com/v1/result"
        params = {"i": query, "appid": app_id}

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, params=params) as response:
                    if response.status == 200:
                        result = await response.text()

                        if not result or not result.strip():
                            return await self.bot.warn(ctx, f"No results found for **{query}**.")

                        await ctx.send(f"```\n{result}\n```")
                    elif response.status == 501:
                        await self.bot.warn(ctx, "Wolfram Alpha has no short answer for that query.")
                    else:
                        await self.bot.deny(ctx, f"Wolfram Alpha returned an error (status: `{response.status}`).")
            except Exception as e:
                logger("error", f"Error in wolfram command: {e}")
                await self.bot.deny(ctx, "An error occurred while communicating with Wolfram Alpha.")

    @commands.command(name="ocr", aliases=["read"])
    async def ocr(self, ctx, image_url: str = None):
        """Detect text in an image"""
        if ctx.message.attachments:
            image_url = ctx.message.attachments[0].url
        elif image_url and (image_url.startswith("http://") or image_url.startswith("https://")):
            pass
        else:
            return await self.bot.warn(ctx, "Please **attach an image** or provide a valid **image URL**.")

        form = aiohttp.FormData()
        form.add_field("url", image_url)
        form.add_field("language", "eng")
        form.add_field("scale", "true")
        form.add_field("OCREngine", "2")

        try:
            headers = {"apikey": "K84848884588957"}
            async with aiohttp.ClientSession() as session:
                async with session.post("https://api.ocr.space/parse/image", data=form, headers=headers) as response:
                    if response.status != 200:
                        return await self.bot.warn(ctx, "An error occurred while communicating with the OCR API.")

                    data = await response.json(content_type=None)

            text = data.get("ParsedResults", [{}])[0].get("ParsedText", "") if isinstance(data, dict) else ""

            if text and text.strip():
                sanitized_text = text[:1900] + "..." if len(text) > 1900 else text
                return await self.bot.neutral(ctx, f"**Extracted Text:**\n```{sanitized_text}```")

            return await self.bot.warn(ctx, "No readable text was detected in that image.")
        except Exception as e:
            logger("error", f"OCR Error: {e}")
            return await self.bot.warn(ctx, "An error occurred while communicating with the OCR API.")

    @commands.command(name="define", aliases=["def", "definition"])
    async def define(self, ctx, *, query: str = None):
        """Get definitions from the dictionary"""
        if not query:
            return await ctx.send_help(ctx.command)

        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://api.dictionaryapi.dev/api/v2/entries/en/{query}") as resp:
                data = await resp.json()

        if isinstance(data, dict) and data.get("title") == "No Definitions Found":
            return await self.bot.deny(ctx, f"{ctx.author.mention}: No definitions found for **{query}**.")

        if not data:
            return await self.bot.deny(ctx, f"{ctx.author.mention}: No definitions found for **{query}**.")

        word_data = data[0]
        embed = discord.Embed(color=self.bot.config.get("color"), title=f"{word_data['word'].title()}")
        embed.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar.url)

        definitions = []
        for meaning in word_data.get("meanings", [])[:3]:
            part_of_speech = meaning.get("partOfSpeech", "Unknown")
            for i, defn in enumerate(meaning.get("definitions", [])[:2], 1):
                definition_text = defn.get("definition", "N/A")
                example = f"\n*Example: {defn['example']}*" if defn.get("example") else ""
                definitions.append(f"**{i}. [{part_of_speech}]** {definition_text}{example}")

        if definitions:
            embed.add_field(name="Definitions", value="\n".join(definitions)[:1024], inline=False)

        embed.set_footer(text=f"Requested by {ctx.author}")
        await ctx.send(embed=embed)

    @commands.command(name="urbandictionary", aliases=["ud", "urban"])
    async def urbandictionary(self, ctx, *, query: str = None):
        """Get definitions from Urban Dictionary"""
        if not query:
            return await ctx.send_help(ctx.command)

        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(f"https://api.urbandictionary.com/v0/define?term={query}", headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status != 200:
                        return await self.bot.deny(ctx, f"{ctx.author.mention}: Could not fetch definition for **{query}**.")
                    data = await resp.json()
            except asyncio.TimeoutError:
                return await self.bot.deny(ctx, f"{ctx.author.mention}: Request timed out.")
            except Exception as e:
                return await self.bot.deny(ctx, f"{ctx.author.mention}: Error fetching definition.")

        if not data.get("list"):
            return await self.bot.deny(ctx, f"{ctx.author.mention}: No definitions found for **{query}**.")

        embed = discord.Embed(color=self.bot.config.get("color"), title=f"{query.title()}")
        embed.set_author(name=ctx.author.name, icon_url=ctx.author.display_avatar.url)

        urban_def = data["list"][0]
        embed.add_field(name="Definition", value=urban_def.get("definition", "N/A")[:1024], inline=False)
        if urban_def.get("example"):
            embed.add_field(name="Example", value=f"*{urban_def.get('example', 'N/A')}*"[:1024], inline=False)

        embed.set_footer(text=f"Requested by {ctx.author}")
        await ctx.send(embed=embed)

    @commands.command(aliases=["ig", "insta"])
    async def instagram(self, ctx, username: str = None):
        """View an Instagram profile"""
        if not username:
            return await ctx.send_help(ctx.command)
        target = username.replace('@', '').lower()
        if target in self.cache: return await ctx.send(embed=self.cache[target])

        headers = {**self.rapid_headers, 'x-rapidapi-host': 'instagram120.p.rapidapi.com'}
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.post('https://instagram120.p.rapidapi.com/api/instagram/profile', json={'username': target}) as r:
                data = await r.json()
                res = data.get("result")
                if not res: return await self.bot.warn(ctx, f"User **@{target}** not found.")

                embed = discord.Embed(color=0xE1306C, title=f"{res.get('full_name') or res['username']} (@{res['username']})",
                    url=f"https://instagram.com/{res['username']}", description=res.get("biography"))
                embed.set_thumbnail(url=res.get("profile_pic_url_hd"))
                embed.add_field(name="Posts", value=f"{res.get('edge_owner_to_timeline_media', {}).get('count', 0):,}", inline=True)
                embed.add_field(name="Followers", value=f"{res.get('edge_followed_by', {}).get('count', 0):,}", inline=True)
                embed.add_field(name="Following", value=f"{res.get('edge_follow', {}).get('count', 0):,}", inline=True)
                
                self.cache[target] = embed
                await ctx.send(embed=embed)


    @commands.command(aliases=["x", "twt"])
    async def twitter(self, ctx, username: str = None):
        """View a Twitter/X profile"""
        if not username:
            return await ctx.send_help(ctx.command)
        target = username.replace('@', '').lower()
        if target in self.cache: return await ctx.send(embed=self.cache[target])

        async with aiohttp.ClientSession(headers=self.rapid_headers) as session:
            async with session.get(f"https://twitter241.p.rapidapi.com/user?username={target}") as r:
                data = await r.json()
                res = data.get("result", {}).get("data", {}).get("user", {}).get("result")
                if not res or res.get("__typename") == "UserUnavailable":
                    return await self.bot.warn(ctx, f"User **@{target}** not found.")

                l, c = res.get("legacy", {}), res.get("core", {})
                avatar = (l.get("profile_image_url_https") or "").replace("_normal", "")
                
                embed = discord.Embed(color=0x000001, title=f"{c.get('name') or l.get('name')} {'✅' if res.get('is_blue_verified') else ''}",
                    url=f"https://x.com/{target}", description=l.get("description"))
                embed.set_thumbnail(url=avatar)
                if l.get("profile_banner_url"): embed.set_image(url=l["profile_banner_url"])
                embed.add_field(name="Followers", value=f"{l.get('followers_count', 0):,}", inline=True)
                embed.add_field(name="Following", value=f"{l.get('friends_count', 0):,}", inline=True)
                embed.add_field(name="Tweets", value=f"{l.get('statuses_count', 0):,}", inline=True)
                
                self.cache[target] = embed
                await ctx.send(embed=embed)

    async def get_twitch_token(self):
        """Retrieve a fresh Twitch OAuth token"""
        if not self.twitch_client_id or not self.twitch_client_secret:
            print("❌ Twitch credentials not set in environment variables")
            return None
        
        try:
            url = f"https://id.twitch.tv/oauth2/token?client_id={self.twitch_client_id}&client_secret={self.twitch_client_secret}&grant_type=client_credentials"
            async with aiohttp.ClientSession() as session:
                async with session.post(url) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        print(f"❌ Twitch token error {resp.status}: {text}")
                        return None
                    data = await resp.json()
                    token = data.get('access_token')
                    if token:
                        self.twitch_access_token = token
                        return token
                    return None
        except Exception as e:
            print(f"❌ Error getting Twitch token: {e}")
            return None

    async def get_twitch_user_info(self, username: str):
        """Get Twitch user info and stream status"""
        try:
            if not self.twitch_access_token:
                if not await self.get_twitch_token():
                    return None

            if not self.twitch_client_id:
                return None

            headers = {
                'Client-ID': self.twitch_client_id,
                'Authorization': f'Bearer {self.twitch_access_token}'
            }
            
            async with aiohttp.ClientSession() as session:
                url = f"https://api.twitch.tv/helix/users?login={username}"
                
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 401:
                        self.twitch_access_token = None
                        if not await self.get_twitch_token():
                            return None
                        headers['Authorization'] = f'Bearer {self.twitch_access_token}'
                        async with session.get(url, headers=headers) as retry_resp:
                            if retry_resp.status != 200:
                                return None
                            user_data = await retry_resp.json()
                    elif resp.status == 200:
                        user_data = await resp.json()
                    else:
                        return None
                    
                    if not user_data.get('data'):
                        return None
                    
                    user = user_data['data'][0]
                    user_id = user['id']

                async with session.get(f"https://api.twitch.tv/helix/streams?user_id={user_id}", headers=headers) as resp:
                    if resp.status == 200:
                        stream_data = await resp.json()
                        stream = stream_data['data'][0] if stream_data.get('data') else None
                    else:
                        stream = None

                return {"user": user, "stream": stream}
        except Exception as e:
            print(f"❌ Error getting Twitch user info: {e}")
            return None

    @commands.group(name='twitch', aliases=['tw'], invoke_without_command=True)
    async def twitch(self, ctx, user: str = None):
        """Twitch profile lookup"""
        if not user:
            return await ctx.send_help(self.twitch)

        user = user.replace('@', '')
        
        if user.lower() in self.cache:
            return await ctx.send(embed=self.cache[user.lower()])

        async with ctx.typing():
            data = await self.get_twitch_user_info(user)
            if not data:
                return await self.bot.warn(ctx, f"Could not find Twitch user **{user}**. Check console for errors.")

            u, stream = data['user'], data['stream']
            
            try:
                created_at = datetime.fromisoformat(u['created_at'].replace('Z', '+00:00'))
            except:
                created_at = None
            
            embed = discord.Embed(
                title=f"{u['display_name']} (@{u['login']})",
                url=f"https://twitch.tv/{u['login']}",
                description=u.get('description') or "*No bio provided.*",
                color=0x9146FF,
                timestamp=datetime.now()
            )
            embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
            
            if u.get('profile_image_url'):
                embed.set_thumbnail(url=u['profile_image_url'])
            
            if created_at:
                embed.add_field(name='Created', value=created_at.strftime('%B %d, %Y'), inline=True)
            
            if stream:
                embed.add_field(name='Status', value=f"🔴 **LIVE** - {stream.get('title', 'N/A')}", inline=False)
                embed.add_field(name='Game', value=stream.get('game_name', 'N/A'), inline=True)
                embed.add_field(name='Viewers', value=f"{stream.get('viewer_count', 0):,}", inline=True)
            else:
                embed.add_field(name='Status', value='⚫ Offline', inline=True)

            self.cache[user.lower()] = embed
            await ctx.send(embed=embed)

    @twitch.group(name='feed', invoke_without_command=False)
    async def twitch_feed(self, ctx):
        """Manage Twitch feeds for this server"""
        if ctx.invoked_subcommand is None:
            return await ctx.send_help(self.twitch_feed)

    @twitch_feed.command(name='add')
    @commands.has_permissions(manage_guild=True)
    async def twitch_feed_add(self, ctx, channel: Union[discord.TextChannel, discord.Thread], twitch_user: str):
        """Add a Twitch feed to a channel"""
        from models.configs import GuildConfig
        
        twitch_user = twitch_user.lower().replace('@', '')
        
        async with ctx.typing():
            data = await self.get_twitch_user_info(twitch_user)
            if not data:
                return await self.bot.warn(ctx, f"Could not find Twitch user **{twitch_user}**.")
            
            try:
                config = await GuildConfig.find_one({"guild_id": ctx.guild.id})
                if not config:
                    config = GuildConfig(guild_id=ctx.guild.id)
                
                config.twitch_feeds[twitch_user] = channel.id
                await config.save()
                
                await self.bot.grant(ctx, f"Added **Twitch feed** for **{twitch_user}** → {channel.mention}")
            except Exception as e:
                print(f"❌ Error adding Twitch feed: {e}")
                import traceback
                traceback.print_exc()
                await self.bot.warn(ctx, f"Error adding feed: {str(e)}")

    @twitch_feed.command(name='remove')
    @commands.has_permissions(manage_guild=True)
    async def twitch_feed_remove(self, ctx, twitch_user: str):
        """Remove a Twitch feed"""
        from models.configs import GuildConfig
        
        twitch_user = twitch_user.lower().replace('@', '')
        config = await GuildConfig.find_one({"guild_id": ctx.guild.id})
        
        if not config or twitch_user not in config.twitch_feeds:
            return await self.bot.warn(ctx, f"No feed found for **{twitch_user}**.")
        
        del config.twitch_feeds[twitch_user]
        await config.save()
        
        await self.bot.grant(ctx, f"Removed **Twitch feed** for **{twitch_user}**.")

    @twitch_feed.command(name='list')
    async def twitch_feed_list(self, ctx):
        """List all Twitch feeds in this server"""
        from models.configs import GuildConfig
        
        config = await GuildConfig.find_one({"guild_id": ctx.guild.id})
        
        if not config or not config.twitch_feeds:
            return await ctx.send(embed=discord.Embed(
                title="Twitch Feeds",
                description="No feeds configured.",
                color=self.bot.config.get("color")
            ))
        
        embed = discord.Embed(
            title="Twitch Feeds",
            color=0x9146FF
        )
        
        for streamer, channel_id in config.twitch_feeds.items():
            channel = self.get_channel_or_thread(ctx.guild, channel_id)
            embed.add_field(
                name=streamer,
                value=f"→ {channel.mention if channel else f'Unknown Channel (ID: {channel_id})'}",
                inline=False
            )
        
        await ctx.send(embed=embed)

    @tasks.loop(minutes=2)
    async def check_twitch_live(self):
        """Background task to check Twitch streams and send notifications"""
        try:
            configs = await GuildConfig.find({"twitch_feeds": {"$exists": True, "$ne": {}}}).to_list(length=None)
            
            if not configs:
                return
            
            streamers_to_check = set()
            guild_feeds = {}
            
            for config in configs:
                for streamer, channel_id in config.twitch_feeds.items():
                    streamers_to_check.add(streamer)
                    if streamer not in guild_feeds:
                        guild_feeds[streamer] = []
                    guild_feeds[streamer].append((config.guild_id, channel_id))
            
            if not streamers_to_check:
                return
            
            if not self.twitch_access_token:
                await self.get_twitch_token()
            
            if not self.twitch_access_token:
                print("❌ Could not get Twitch token for live check")
                return
            
            headers = {
                'Client-ID': self.twitch_client_id,
                'Authorization': f'Bearer {self.twitch_access_token}'
            }
            
            url = "https://api.twitch.tv/helix/streams"
            params = [('user_login', name) for name in list(streamers_to_check)]
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, params=params) as resp:
                    if resp.status != 200:
                        print(f"❌ Twitch API error: {resp.status}")
                        return
                    
                    data = await resp.json()
                    live_streams = data.get('data', [])
                    
                    for stream in live_streams:
                        streamer_name = stream['user_login'].lower()
                        stream_id = stream['id']
                        
                        if streamer_name in self.twitch_live_cache and self.twitch_live_cache[streamer_name] == stream_id:
                            continue
                        
                        self.twitch_live_cache[streamer_name] = stream_id
                        
                        embed = discord.Embed(
                            title=f"🔴 {stream['user_name']} is LIVE!",
                            description=f"**{stream['title']}**",
                            url=f"https://twitch.tv/{streamer_name}",
                            color=0x9146FF,
                            timestamp=datetime.now()
                        )
                        embed.add_field(name="Game", value=stream.get('game_name', 'N/A'), inline=True)
                        embed.add_field(name="Viewers", value=f"{stream.get('viewer_count', 0):,}", inline=True)
                        
                        thumb_url = stream.get('thumbnail_url', '')
                        if thumb_url:
                            thumb_url = thumb_url.replace('{width}', '1280').replace('{height}', '720')
                            embed.set_image(url=f"{thumb_url}?t={int(datetime.now().timestamp())}")
                        
                        for guild_id, channel_id in guild_feeds.get(streamer_name, []):
                            try:
                                guild = self.bot.get_guild(guild_id)
                                if not guild:
                                    continue
                                channel = self.get_channel_or_thread(guild, channel_id)
                                if channel:
                                    await channel.send(embed=embed)
                                    print(f"✅ Sent live notification for {streamer_name} to guild {guild_id}")
                            except Exception as e:
                                print(f"❌ Error sending Twitch notification: {e}")
        
        except Exception as e:
            print(f"❌ Error in check_twitch_live: {e}")
            import traceback
            traceback.print_exc()
    
    @check_twitch_live.before_loop
    async def before_check_twitch_live(self):
        """Wait until bot is ready before starting the loop"""
        await self.bot.wait_until_ready()

    async def fetch_sc(self, search_type, query):
        params = {
            'q': query,
            'limit': 1
        }
        headers = {
            'Authorization': 'OAuth 2-292593-994587358-Af8VbLnc6zIplJ'
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://api-v2.soundcloud.com/search/{search_type}", params=params, headers=headers) as resp:
                if resp.status == 200:
                    return await resp.json()
                return None

    @commands.group(name="soundcloud", aliases=["sc"], invoke_without_command=True)
    async def soundcloud(self, ctx, *, query: str = None):
        """Search SoundCloud for a track"""
        if not query:
            return await ctx.send_help(ctx.command)
        
        data = await self.fetch_sc('tracks', query)
        if not data or not data.get('collection'):
            return await self.bot.warn(ctx, f"No tracks found for **{query}**")
        
        await ctx.send(data['collection'][0]['permalink_url'])

    @soundcloud.command(name="user", aliases=["artist"])
    async def soundcloud_user(self, ctx, *, query: str = None):
        """Search for a user or artist on SoundCloud"""
        if not query:
            return await ctx.send_help(ctx.command)
            
        data = await self.fetch_sc('users', query)
        if not data or not data.get('collection'):
            return await self.bot.warn(ctx, f"No users found for **{query}**")
            
        await ctx.send(data['collection'][0]['permalink_url'])

    @soundcloud.command(name="playlist")
    async def soundcloud_playlist(self, ctx, *, query: str = None):
        """Search for a playlist on SoundCloud"""
        if not query:
            return await ctx.send_help(ctx.command)
            
        data = await self.fetch_sc('playlists', query)
        if not data or not data.get('collection'):
            return await self.bot.warn(ctx, f"No playlists found for **{query}**")
            
        await ctx.send(data['collection'][0]['permalink_url'])

    @soundcloud.group(name="feeds", invoke_without_command=True)
    async def soundcloud_feeds(self, ctx):
        """Manage SoundCloud artist feed notifications"""
        await ctx.send_help(ctx.command)

    @soundcloud_feeds.command(name="add")
    @commands.has_permissions(administrator=True)
    async def soundcloud_feeds_add(self, ctx, channel: Union[discord.TextChannel, discord.Thread] = None, *, artist: str = None):
        """Add a SoundCloud artist to feed notifications"""
        if not channel or not artist:
            return await ctx.send_help(ctx.command)
        
        from models.configs import GuildConfig
        
        data = await self.fetch_sc('users', artist)
        if not data or not data.get('collection'):
            return await self.bot.warn(ctx, f"Artist **{artist}** not found on SoundCloud")
        
        user_data = data['collection'][0]
        artist_id = str(user_data['id'])
        artist_name = user_data['username']
        
        res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not res:
            res = GuildConfig(guild_id=ctx.guild.id)
        
        if not hasattr(res, 'soundcloud_feeds'):
            res.soundcloud_feeds = {}
        if not hasattr(res, 'soundcloud_last_tracks'):
            res.soundcloud_last_tracks = {}
        
        res.soundcloud_feeds[artist_id] = channel.id
        await res.save()
        
        await self.bot.grant(ctx, f"Added **{artist_name}** to SoundCloud feed notifications in {channel.mention}")

    @soundcloud_feeds.command(name="remove")
    @commands.has_permissions(administrator=True)
    async def soundcloud_feeds_remove(self, ctx, *, artist: str = None):
        """Remove a SoundCloud artist from feed notifications"""
        if not artist:
            return await ctx.send_help(ctx.command)
        
        from models.configs import GuildConfig
        
        data = await self.fetch_sc('users', artist)
        if not data or not data.get('collection'):
            return await self.bot.warn(ctx, f"Artist **{artist}** not found on SoundCloud")
        
        artist_id = str(data['collection'][0]['id'])
        
        res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not res or artist_id not in res.soundcloud_feeds:
            return await self.bot.warn(ctx, f"Artist **{artist}** is not in your feed list")
        
        del res.soundcloud_feeds[artist_id]
        if artist_id in res.soundcloud_last_tracks:
            del res.soundcloud_last_tracks[artist_id]
        await res.save()
        
        await self.bot.grant(ctx, f"Removed **{artist}** from SoundCloud feeds")

    @soundcloud_feeds.command(name="list")
    async def soundcloud_feeds_list(self, ctx):
        """View all active SoundCloud artist feeds"""
        from models.configs import GuildConfig
        
        res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        
        if not res or not res.soundcloud_feeds:
            return await self.bot.neutral(ctx, "No SoundCloud feeds are active in this server")
        
        feeds = []
        for artist_id, channel_id in res.soundcloud_feeds.items():
            channel = self.get_channel_or_thread(ctx.guild, channel_id)
            channel_mention = channel.mention if channel else f"<#{channel_id}>"
            feeds.append(f"**{artist_id}** → {channel_mention}")
        
        embed = discord.Embed(color=0x35465c, title="Active SoundCloud Feeds", description="\n".join(feeds))
        await ctx.send(embed=embed)

    @commands.group(name="tumblr", aliases=["tmb"], invoke_without_command=True)
    async def tumblr(self, ctx, *, user: str = None):
        """Search for a Tumblr user profile"""
        if not user:
            return await ctx.send_help(ctx.command)
        
        target = user.lower().strip().replace('.tumblr.com', '')
        url = f"https://api.tumblr.com/v2/blog/{target}.tumblr.com/info"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params={'api_key': self.tumblr_api_key}) as resp:
                data = await resp.json()
                if resp.status != 200:
                    return await self.bot.warn(ctx, f"User **{target}** not found.")

                b = data['response']['blog']
                embed = discord.Embed(color=0x35465c, title=f"User: {b['name']}", url=b['url'])
                embed.set_thumbnail(url=f"https://api.tumblr.com/v2/blog/{target}.tumblr.com/avatar/128")
                embed.description = self.clean_html(b.get('description', ''))
                
                embed.add_field(name="Username", value=b['name'], inline=True)
                embed.add_field(name="Posts", value=f"{b.get('posts', 0):,}", inline=True)
                
                await ctx.send(embed=embed)

    @tumblr.command(name="blog")
    async def tumblr_blog(self, ctx, *, blog: str = None):
        """View detailed info and recent posts from a blog"""
        if not blog:
            return await ctx.send_help(ctx.command)
            
        target = blog.lower().strip().replace('.tumblr.com', '')
        url = f"https://api.tumblr.com/v2/blog/{target}.tumblr.com/posts"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params={'api_key': self.tumblr_api_key, 'limit': 5}) as resp:
                data = await resp.json()
                if resp.status != 200:
                    return await self.bot.warn(ctx, f"Blog **{target}** not found.")

                res = data['response']
                b = res['blog']
                posts = res.get('posts', [])

                embed = discord.Embed(color=0x35465c, title=f"Blog: {b.get('title', b['name'])}", url=b['url'])
                embed.set_thumbnail(url=f"https://api.tumblr.com/v2/blog/{target}.tumblr.com/avatar/128")
                embed.description = self.clean_html(b.get('description', ''))

                if posts:
                    links = [f"[{i}]({p['post_url']}) {p.get('summary', 'View')[:30]}..." for i, p in enumerate(posts, 1)]
                    embed.add_field(name='Recent Posts', value="\n".join(links), inline=False)

                await ctx.send(embed=embed)

    @tumblr.group(name="feeds", invoke_without_command=True)
    async def tumblr_feeds(self, ctx):
        """Manage Tumblr blog feed notifications"""
        await ctx.send_help(ctx.command)

    @tumblr_feeds.command(name="add")
    @commands.has_permissions(administrator=True)
    async def tumblr_feeds_add(self, ctx, channel: Union[discord.TextChannel, discord.Thread] = None, *, user: str = None):
        """Add a Tumblr blog to feed notifications"""
        if not channel or not user:
            return await ctx.send_help(ctx.command)
        
        from models.configs import GuildConfig
        
        target = user.lower().strip().replace('.tumblr.com', '')
        
        url = f"https://api.tumblr.com/v2/blog/{target}.tumblr.com/info"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params={'api_key': self.api_key}) as resp:
                if resp.status != 200:
                    return await self.bot.warn(ctx, f"Blog **{target}** not found on Tumblr")
        
        res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not res:
            res = GuildConfig(guild_id=ctx.guild.id)
        
        if not hasattr(res, 'tumblr_feeds'):
            res.tumblr_feeds = {}
        if not hasattr(res, 'tumblr_last_posts'):
            res.tumblr_last_posts = {}
        
        res.tumblr_feeds[target] = channel.id
        await res.save()
        
        await self.bot.grant(ctx, f"Added **{target}** blog feed to {channel.mention}")

    @tumblr_feeds.command(name="remove")
    @commands.has_permissions(administrator=True)
    async def tumblr_feeds_remove(self, ctx, *, user: str = None):
        """Remove a Tumblr blog from feed notifications"""
        if not user:
            return await ctx.send_help(ctx.command)
        
        from models.configs import GuildConfig
        
        target = user.lower().strip().replace('.tumblr.com', '')
        
        res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        if not res or target not in res.tumblr_feeds:
            return await self.bot.warn(ctx, f"Blog **{target}** is not in your feed list")
        
        del res.tumblr_feeds[target]
        if target in res.tumblr_last_posts:
            del res.tumblr_last_posts[target]
        await res.save()
        
        await self.bot.grant(ctx, f"Removed **{target}** from blog feeds")

    @tumblr_feeds.command(name="list")
    async def tumblr_feeds_list(self, ctx):
        """View all active Tumblr blog feeds"""
        from models.configs import GuildConfig
        
        res = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
        
        if not res or not res.tumblr_feeds:
            return await self.bot.neutral(ctx, "No Tumblr feeds are active in this server")
        
        feeds = []
        for blog, channel_id in res.tumblr_feeds.items():
            channel = self.get_channel_or_thread(ctx.guild, channel_id)
            channel_mention = channel.mention if channel else f"<#{channel_id}>"
            feeds.append(f"**{blog}** → {channel_mention}")
        
        embed = discord.Embed(color=0x35465c, title="Active Tumblr Feeds", description="\n".join(feeds))
        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_ready(self):
        """Start the feed checkers when bot is ready"""
        if not hasattr(self, 'tumblr_feed_task_started'):
            self.tumblr_feed_task_started = True
            self.bot.loop.create_task(self.check_tumblr_feeds())
        
        if not hasattr(self, 'soundcloud_feed_task_started'):
            self.soundcloud_feed_task_started = True
            self.bot.loop.create_task(self.check_soundcloud_feeds())

    async def check_tumblr_feeds(self):
        """Periodically check for new Tumblr posts"""
        from models.configs import GuildConfig
        import asyncio
        
        while True:
            try:
                await asyncio.sleep(300)
                
                all_configs = await GuildConfig.find({"tumblr_feeds": {"$exists": True, "$ne": {}}}).to_list(None)
                
                for config in all_configs:
                    guild = self.bot.get_guild(config.guild_id)
                    if not guild:
                        continue
                    
                    for blog, channel_id in config.tumblr_feeds.items():
                        try:
                            channel = self.get_channel_or_thread(guild, channel_id)
                            if not channel:
                                continue
                            
                            url = f"https://api.tumblr.com/v2/blog/{blog}.tumblr.com/posts"
                            async with aiohttp.ClientSession() as session:
                                async with session.get(url, params={'api_key': self.api_key, 'limit': 1}) as resp:
                                    if resp.status != 200:
                                        continue
                                    
                                    data = await resp.json()
                                    posts = data.get('response', {}).get('posts', [])
                                    
                                    if not posts:
                                        continue
                                    
                                    latest_post = posts[0]
                                    post_id = str(latest_post['id'])
                                    last_post_id = config.tumblr_last_posts.get(blog)
                                    
                                    if last_post_id != post_id:
                                        post_type = latest_post.get('type', 'text').upper()
                                        tags = latest_post.get('tags', [])
                                        note_count = latest_post.get('note_count', 0)
                                        date = latest_post.get('date', '')
                                        
                                        embed = discord.Embed(
                                            color=0x35465c,
                                            title=latest_post.get('summary', 'New Post')[:256],
                                            url=latest_post['post_url'],
                                            description=self.clean_html(latest_post.get('body', ''))[:240]
                                        )
                                        embed.set_author(name=f"New post from {blog}")
                                        embed.add_field(name="Type", value=post_type, inline=True)
                                        
                                        if tags:
                                            tags_str = ', '.join(f"#{tag}" for tag in tags[:5])
                                            if len(tags) > 5:
                                                tags_str += f" +{len(tags) - 5} more"
                                            embed.add_field(name="Tags", value=tags_str, inline=True)
                                        
                                        embed.add_field(name="Notes", value=str(note_count), inline=True)
                                        
                                        if date:
                                            try:
                                                if isinstance(date, str):
                                                    from datetime import datetime as dt
                                                    date_obj = dt.strptime(date.replace(' GMT', ''), '%Y-%m-%d %H:%M:%S')
                                                    timestamp = int(date_obj.timestamp())
                                                else:
                                                    timestamp = int(date)
                                                embed.add_field(name="Published", value=f"<t:{timestamp}:R>", inline=False)
                                            except:
                                                embed.add_field(name="Published", value=str(date)[:100], inline=False)
                                        
                                        if latest_post.get('reblog'):
                                            reblog_info = latest_post['reblog'].get('comment', 'Reblogged')
                                            embed.add_field(name="Reblog", value=self.clean_html(reblog_info)[:100], inline=False)
                                        
                                        photos = latest_post.get('photos', [])
                                        if photos:
                                            first_photo = photos[0].get('original_size', {})
                                            if 'url' in first_photo:
                                                embed.set_image(url=first_photo['url'])
                                        
                                        try:
                                            await channel.send(embed=embed)
                                        except:
                                            pass
                                        
                                        config.tumblr_last_posts[blog] = post_id
                                        await config.save()
                        except Exception as e:
                            print(f"Error checking Tumblr feed for {blog}: {e}")
            except Exception as e:
                print(f"Error in check_tumblr_feeds: {e}")
                await asyncio.sleep(60)

    async def check_soundcloud_feeds(self):
        """Periodically check for new SoundCloud tracks"""
        from models.configs import GuildConfig
        import asyncio
        
        while True:
            try:
                await asyncio.sleep(300)
                
                all_configs = await GuildConfig.find({"soundcloud_feeds": {"$exists": True, "$ne": {}}}).to_list(None)
                
                for config in all_configs:
                    guild = self.bot.get_guild(config.guild_id)
                    if not guild:
                        continue
                    
                    for artist_id, channel_id in config.soundcloud_feeds.items():
                        try:
                            channel = self.get_channel_or_thread(guild, channel_id)
                            if not channel:
                                continue
                            
                            url = f"https://soundcloud.com/api-v2/users/{artist_id}/tracks"
                            async with aiohttp.ClientSession() as session:
                                async with session.get(url, params={'limit': 1, 'linked_partitioning': 1}) as resp:
                                    if resp.status != 200:
                                        continue
                                    
                                    data = await resp.json()
                                    collection = data.get('collection', [])
                                    
                                    if not collection:
                                        continue
                                    
                                    latest_track = collection[0]
                                    track_id = str(latest_track['id'])
                                    last_track_id = config.soundcloud_last_tracks.get(artist_id)
                                    
                                    if last_track_id != track_id:
                                        embed = discord.Embed(
                                            color=0xff6600,
                                            title=latest_track.get('title', 'New Track'),
                                            url=latest_track.get('permalink_url', '')
                                        )
                                        embed.set_author(name=f"New track from {latest_track['user']['username']}")
                                        
                                        if latest_track.get('description'):
                                            embed.description = self.clean_html(latest_track['description'][:240])
                                        
                                        try:
                                            await channel.send(embed=embed)
                                        except:
                                            pass
                                        
                                        config.soundcloud_last_tracks[artist_id] = track_id
                                        await config.save()
                        except Exception as e:
                            print(f"Error checking SoundCloud feed for {artist_id}: {e}")
            except Exception as e:
                print(f"Error in check_soundcloud_feeds: {e}")
                await asyncio.sleep(60)

    def _gang_all_member_ids(self, gang: Gang) -> set[int]:
        return {gang.owner_id, *gang.admin_ids, *gang.member_ids}

    async def _find_gang_by_user(self, guild_id: int, user_id: int):
        gangs = await Gang.find(Gang.guild_id == guild_id).to_list()
        for gang in gangs:
            if user_id in self._gang_all_member_ids(gang):
                return gang
        return None

    async def _find_gang_by_name(self, guild_id: int, name: str):
        gangs = await Gang.find(Gang.guild_id == guild_id).to_list()
        target = name.lower().strip()
        for gang in gangs:
            if gang.name.lower() == target:
                return gang
        return None

    async def _send_gang_info(self, ctx, gang: Gang):
        owner = ctx.guild.get_member(gang.owner_id)
        owner_text = owner.mention if owner else f"<@{gang.owner_id}>"

        admin_mentions = [
            (ctx.guild.get_member(uid).mention if ctx.guild.get_member(uid) else f"<@{uid}>")
            for uid in gang.admin_ids
        ]
        member_mentions = [
            (ctx.guild.get_member(uid).mention if ctx.guild.get_member(uid) else f"<@{uid}>")
            for uid in gang.member_ids
        ]

        total_members = 1 + len(gang.admin_ids) + len(gang.member_ids)
        embed = discord.Embed(
            color=gang.color,
            title=f"{gang.emoji} {gang.name}",
            description=f"Owner: {owner_text}"
        )
        embed.add_field(name="Members", value=str(total_members), inline=True)
        embed.add_field(name="Admins", value=str(len(gang.admin_ids)), inline=True)
        embed.add_field(name="Created", value=f"<t:{int(gang.created_at.timestamp())}:R>", inline=True)

        if admin_mentions:
            embed.add_field(name="Admin List", value=", ".join(admin_mentions)[:1024], inline=False)
        if member_mentions:
            embed.add_field(name="Member List", value=", ".join(member_mentions)[:1024], inline=False)
        if gang.banner_url:
            embed.set_image(url=gang.banner_url)

        await ctx.send(embed=embed)

    @commands.group(name="gang", invoke_without_command=True)
    async def gang(self, ctx, member: discord.Member = None):
        """Display gang stats"""
        target = member or ctx.author
        gang = await self._find_gang_by_user(ctx.guild.id, target.id)
        if not gang:
            return await self.bot.warn(ctx, f"**{target}** is not in a gang.")
        await self._send_gang_info(ctx, gang)

    @gang.command(name="create")
    async def gang_create(self, ctx, *, name: str = None):
        """Create a new gang"""
        if not name:
            return await ctx.send_help(ctx.command)

        name = name.strip()
        if len(name) < 2 or len(name) > 32:
            return await self.bot.warn(ctx, "Gang name must be between 2 and 32 characters.")

        current = await self._find_gang_by_user(ctx.guild.id, ctx.author.id)
        if current:
            return await self.bot.warn(ctx, "You are already in a gang.")

        existing = await self._find_gang_by_name(ctx.guild.id, name)
        if existing:
            return await self.bot.warn(ctx, f"Gang **{name}** already exists.")

        gang = Gang(guild_id=ctx.guild.id, name=name, owner_id=ctx.author.id)
        await gang.insert()
        await self.bot.grant(ctx, f"Created gang **{name}**.")

    @gang.command(name="disband")
    async def gang_disband(self, ctx):
        """Permanently delete your gang"""
        gang = await self._find_gang_by_user(ctx.guild.id, ctx.author.id)
        if not gang:
            return await self.bot.warn(ctx, "You are not in a gang.")
        if gang.owner_id != ctx.author.id:
            return await self.bot.warn(ctx, "Only the gang owner can disband the gang.")

        name = gang.name
        await gang.delete()
        await self.bot.grant(ctx, f"Disbanded gang **{name}**.")

    @gang.command(name="info")
    async def gang_info(self, ctx, member: discord.Member = None):
        """Display gang stats"""
        target = member or ctx.author
        gang = await self._find_gang_by_user(ctx.guild.id, target.id)
        if not gang:
            return await self.bot.warn(ctx, f"**{target}** is not in a gang.")
        await self._send_gang_info(ctx, gang)

    @gang.command(name="invite")
    async def gang_invite(self, ctx, member: discord.Member = None):
        """Invite a user"""
        if not member:
            return await ctx.send_help(ctx.command)
        if member.bot:
            return await self.bot.warn(ctx, "You cannot invite bots.")
        if member == ctx.author:
            return await self.bot.warn(ctx, "You cannot invite yourself.")

        gang = await self._find_gang_by_user(ctx.guild.id, ctx.author.id)
        if not gang:
            return await self.bot.warn(ctx, "You are not in a gang.")
        if ctx.author.id != gang.owner_id and ctx.author.id not in gang.admin_ids:
            return await self.bot.warn(ctx, "Only gang admins can invite members.")

        target_gang = await self._find_gang_by_user(ctx.guild.id, member.id)
        if target_gang:
            return await self.bot.warn(ctx, f"{member.mention} is already in a gang.")

        gang.member_ids.append(member.id)
        gang.member_ids = list(dict.fromkeys(gang.member_ids))
        await gang.save()
        await self.bot.grant(ctx, f"Invited {member.mention} to **{gang.name}**.")

    @gang.command(name="kick")
    async def gang_kick(self, ctx, member: discord.Member = None):
        """Remove a member"""
        if not member:
            return await ctx.send_help(ctx.command)

        gang = await self._find_gang_by_user(ctx.guild.id, ctx.author.id)
        if not gang:
            return await self.bot.warn(ctx, "You are not in a gang.")
        if ctx.author.id != gang.owner_id and ctx.author.id not in gang.admin_ids:
            return await self.bot.warn(ctx, "Only gang admins can kick members.")

        if member.id == gang.owner_id:
            return await self.bot.warn(ctx, "You cannot kick the gang owner.")

        if member.id in gang.admin_ids and ctx.author.id != gang.owner_id:
            return await self.bot.warn(ctx, "Only the owner can kick an admin.")

        if member.id not in gang.admin_ids and member.id not in gang.member_ids:
            return await self.bot.warn(ctx, f"{member.mention} is not in your gang.")

        if member.id in gang.admin_ids:
            gang.admin_ids.remove(member.id)
        if member.id in gang.member_ids:
            gang.member_ids.remove(member.id)

        await gang.save()
        await self.bot.grant(ctx, f"Removed {member.mention} from **{gang.name}**.")

    @gang.command(name="leave")
    async def gang_leave(self, ctx):
        """Leave your gang"""
        gang = await self._find_gang_by_user(ctx.guild.id, ctx.author.id)
        if not gang:
            return await self.bot.warn(ctx, "You are not in a gang.")
        if gang.owner_id == ctx.author.id:
            return await self.bot.warn(ctx, "Owners cannot leave. Transfer ownership or disband the gang.")

        if ctx.author.id in gang.admin_ids:
            gang.admin_ids.remove(ctx.author.id)
        if ctx.author.id in gang.member_ids:
            gang.member_ids.remove(ctx.author.id)

        await gang.save()
        await self.bot.grant(ctx, f"You left **{gang.name}**.")

    @gang.command(name="promote")
    async def gang_promote(self, ctx, member: discord.Member = None):
        """Promote to admin"""
        if not member:
            return await ctx.send_help(ctx.command)

        gang = await self._find_gang_by_user(ctx.guild.id, ctx.author.id)
        if not gang:
            return await self.bot.warn(ctx, "You are not in a gang.")
        if ctx.author.id != gang.owner_id:
            return await self.bot.warn(ctx, "Only the gang owner can promote members.")
        if member.id == gang.owner_id:
            return await self.bot.warn(ctx, "That user is already the owner.")
        if member.id in gang.admin_ids:
            return await self.bot.warn(ctx, f"{member.mention} is already an admin.")
        if member.id not in gang.member_ids:
            return await self.bot.warn(ctx, f"{member.mention} is not a gang member.")

        gang.member_ids.remove(member.id)
        gang.admin_ids.append(member.id)
        await gang.save()
        await self.bot.grant(ctx, f"Promoted {member.mention} to admin.")

    @gang.command(name="setbanner")
    async def gang_setbanner(self, ctx, url: str = None):
        """Set gang banner"""
        gang = await self._find_gang_by_user(ctx.guild.id, ctx.author.id)
        if not gang:
            return await self.bot.warn(ctx, "You are not in a gang.")
        if ctx.author.id != gang.owner_id and ctx.author.id not in gang.admin_ids:
            return await self.bot.warn(ctx, "Only gang admins can edit gang settings.")

        url = url or (ctx.message.attachments[0].url if ctx.message.attachments else None)
        if not url:
            return await ctx.send_help(ctx.command)
        if not (url.startswith("http://") or url.startswith("https://")):
            return await self.bot.warn(ctx, "Provide a valid image URL.")

        gang.banner_url = url
        await gang.save()
        await self.bot.grant(ctx, "Gang banner updated.")

    @gang.command(name="setcolor")
    async def gang_setcolor(self, ctx, color: str = None):
        """Set gang embed color"""
        if not color:
            return await ctx.send_help(ctx.command)

        gang = await self._find_gang_by_user(ctx.guild.id, ctx.author.id)
        if not gang:
            return await self.bot.warn(ctx, "You are not in a gang.")
        if ctx.author.id != gang.owner_id and ctx.author.id not in gang.admin_ids:
            return await self.bot.warn(ctx, "Only gang admins can edit gang settings.")

        try:
            color = color.lstrip("#")
            color_int = int(color, 16)
        except ValueError:
            return await self.bot.warn(ctx, "Invalid color format. Use HEX (#RRGGBB).")

        gang.color = color_int
        await gang.save()
        await self.bot.grant(ctx, f"Gang color set to `#{color.upper()}`.")

    @gang.command(name="setemoji")
    async def gang_setemoji(self, ctx, *, emoji: str = None):
        """Set gang emoji"""
        if not emoji:
            return await ctx.send_help(ctx.command)

        gang = await self._find_gang_by_user(ctx.guild.id, ctx.author.id)
        if not gang:
            return await self.bot.warn(ctx, "You are not in a gang.")
        if ctx.author.id != gang.owner_id and ctx.author.id not in gang.admin_ids:
            return await self.bot.warn(ctx, "Only gang admins can edit gang settings.")

        gang.emoji = emoji.strip()[:32]
        await gang.save()
        await self.bot.grant(ctx, "Gang emoji updated.")

    @gang.command(name="transfer")
    async def gang_transfer(self, ctx, member: discord.Member = None):
        """Transfer ownership"""
        if not member:
            return await ctx.send_help(ctx.command)

        gang = await self._find_gang_by_user(ctx.guild.id, ctx.author.id)
        if not gang:
            return await self.bot.warn(ctx, "You are not in a gang.")
        if ctx.author.id != gang.owner_id:
            return await self.bot.warn(ctx, "Only the owner can transfer ownership.")
        if member.bot:
            return await self.bot.warn(ctx, "You cannot transfer ownership to a bot.")
        if member.id == gang.owner_id:
            return await self.bot.warn(ctx, "That user is already the owner.")

        if member.id not in gang.admin_ids and member.id not in gang.member_ids:
            return await self.bot.warn(ctx, "Target user must be in your gang.")

        old_owner = gang.owner_id
        if member.id in gang.admin_ids:
            gang.admin_ids.remove(member.id)
        if member.id in gang.member_ids:
            gang.member_ids.remove(member.id)

        if old_owner not in gang.admin_ids:
            gang.admin_ids.append(old_owner)

        gang.owner_id = member.id
        await gang.save()
        await self.bot.grant(ctx, f"Transferred ownership of **{gang.name}** to {member.mention}.")

    @gang.command(name="leaderboard")
    async def gang_leaderboard(self, ctx):
        """View top gangs"""
        gangs = await Gang.find(Gang.guild_id == ctx.guild.id).to_list()
        if not gangs:
            return await self.bot.neutral(ctx, "No gangs in this server yet.")

        gangs.sort(key=lambda g: (1 + len(g.admin_ids) + len(g.member_ids)), reverse=True)
        lines = []
        for idx, gang in enumerate(gangs[:10], 1):
            size = 1 + len(gang.admin_ids) + len(gang.member_ids)
            lines.append(f"`{idx}.` {gang.emoji} **{gang.name}** — **{size}** members")

        embed = discord.Embed(
            color=0x242429,
            title=f"🏆 Gang Leaderboard — {ctx.guild.name}",
            description="\n".join(lines)
        )
        await ctx.send(embed=embed)

    @commands.group(name="fortnite", aliases=["fort", "fn"], invoke_without_command=True)
    async def fortnite(self, ctx):
        """View Fortnite related information"""
        await ctx.send_help(ctx.command)

    @fortnite.command(name="lookup", aliases=["search", "find"])
    async def fortnite_lookup(self, ctx, *, cosmetic: str = None):
        """Lookup Fortnite cosmetics"""
        if not cosmetic:
            return await ctx.send_help(ctx.command)

        url = f"https://fnbr.co/api/images?search={cosmetic}"
        headers = {'x-api-key': self.fnbr_key}

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                data = await resp.json()

                if resp.status != 200 or not data.get('data'):
                    return await ctx.send("No results found.")

                item = data['data'][0]
                name = item.get('name')
                desc = item.get('description', 'No description available.')
                rarity = item.get('rarity', 'Common')
                item_type = item.get('readableType', 'Unknown')
                icon = item.get('images', {}).get('icon')

                embed = discord.Embed(color=0x242429, title=name, description=desc)
                embed.add_field(name="Type", value=f"> {item_type}", inline=True)
                embed.add_field(name="Rarity", value=f"> {rarity}", inline=True)
                if icon:
                    embed.set_thumbnail(url=icon)

                await ctx.send(embed=embed)

    @fortnite.command(name="map")
    async def fortnite_map(self, ctx):
        """View the current Fortnite map"""
        url = "https://fortnite-api.com/v1/map"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return await ctx.send("Failed to fetch map.")
                
                data = await resp.json()
                map_url = data['data']['images']['pois']

                embed = discord.Embed(color=0x242429, title="Fortnite Map")
                embed.description = "Here is the current Fortnite map."
                embed.set_image(url=map_url)
                await ctx.send(embed=embed)

    @fortnite.command(name="news")
    async def fortnite_news(self, ctx):
        """View the latest Fortnite news"""
        url = "https://fortnite-api.com/v2/news"

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return await ctx.send("Failed to fetch news.")
                
                data = await resp.json()
                motds = data['data']['br']['motds']

                embed = discord.Embed(color=0x242429, title="Fortnite News")
                embed.description = "Here are the latest Fortnite news updates."

                for news in motds[:5]:
                    embed.add_field(name=news['title'], value=news.get('body', 'No content'), inline=False)

                await ctx.send(embed=embed)

    @fortnite.command(name="shop", aliases=["store"])
    async def fortnite_shop(self, ctx):
        """View the current Fortnite item shop"""
        now = datetime.now()
        date_str = f"{now.day}-{now.month}-{now.year}"
        shop_url = f"https://bot.fnbr.co/shop-image/fnbr-shop-{date_str}.png"

        embed = discord.Embed(color=0x242429, title="Fortnite Item Shop")
        embed.set_image(url=shop_url)
        embed.timestamp = discord.utils.utcnow()
        
        await ctx.send(embed=embed)

    async def _make_default_bio_username(self, user: discord.abc.User) -> str:
        base = re.sub(r"[^a-z0-9_]", "", user.name.lower())[:20] or f"user{user.id}"
        candidate = base
        suffix = 1
        while True:
            existing = await BioProfile.find_one(BioProfile.username == candidate)
            if not existing or existing.user_id == user.id:
                return candidate
            suffix += 1
            candidate = f"{base[: max(1, 20 - len(str(suffix)))]}{suffix}"

    async def _get_or_create_bio_profile(self, user: discord.abc.User) -> BioProfile:
        profile = await BioProfile.find_one(BioProfile.user_id == user.id)
        if profile:
            return profile

        username = await self._make_default_bio_username(user)
        profile = BioProfile(
            user_id=user.id,
            username=username,
            display_name=user.display_name,
            avatar_url=str(user.display_avatar.url),
            background_url=str(user.display_avatar.url),
            description="welcome to my profile",
        )
        await profile.insert()
        return profile

    def _biogroup_all_member_ids(self, group: BioGroup) -> set[int]:
        return {group.owner_id, *(group.admin_ids or []), *(group.members or [])}

    async def _find_biogroup_by_user(self, user_id: int):
        groups = await BioGroup.find_all().to_list()
        for group in groups:
            if user_id in self._biogroup_all_member_ids(group):
                return group
        return None

    async def _find_biogroup_by_name(self, name: str):
        groups = await BioGroup.find_all().to_list()
        target = (name or "").lower().strip()
        for group in groups:
            if (group.name or "").lower() == target:
                return group
        return None

    @commands.group(name="biolink", aliases=["bl"], invoke_without_command=True)
    async def biolink(self, ctx):
        """Manage your personal bio link profile (wock.best/@user)"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @biolink.command(name="username")
    async def biolink_username(self, ctx, username: str = None):
        """Claim or change your @handle"""
        if not username:
            return await self.bot.warn(ctx, "Provide a username.")

        clean = re.sub(r"[^a-z0-9_]", "", username.lower())
        if len(clean) < 3:
            return await self.bot.warn(ctx, "Username must be 3+ characters (alphanumeric + underscore).")

        exists = await BioProfile.find_one(BioProfile.username == clean)
        if exists and exists.user_id != ctx.author.id:
            return await self.bot.warn(ctx, "That username is already claimed.")

        profile = await self._get_or_create_bio_profile(ctx.author)
        profile.username = clean
        profile.display_name = ctx.author.display_name
        if not profile.avatar_url:
            profile.avatar_url = str(ctx.author.display_avatar.url)
        if not profile.background_url:
            profile.background_url = str(ctx.author.display_avatar.url)
        profile.updated_at = datetime.utcnow()
        await profile.save()

        return await self.bot.grant(ctx, f"Your bio is live: **https://wock.best/@{clean}**")

    @biolink.command(name="bio")
    async def biolink_bio(self, ctx, *, text: str = None):
        """Set your profile description"""
        if not text:
            return await self.bot.warn(ctx, "Provide a description.")
        if len(text) > 200:
            return await self.bot.warn(ctx, "Bio must be under 200 characters.")

        profile = await self._get_or_create_bio_profile(ctx.author)
        profile.description = text
        profile.display_name = ctx.author.display_name
        profile.updated_at = datetime.utcnow()
        await profile.save()
        return await self.bot.grant(ctx, "Updated bio description.")

    @biolink.command(name="background")
    async def biolink_background(self, ctx, url: str = None):
        """Set your profile background image"""
        image_url = None
        if ctx.message.attachments:
            image_url = ctx.message.attachments[0].url
        elif url and url.startswith(("http://", "https://")):
            image_url = url

        if not image_url:
            return await self.bot.warn(ctx, "Please attach an image or provide a direct image URL.")

        profile = await self._get_or_create_bio_profile(ctx.author)
        profile.background_url = image_url
        profile.updated_at = datetime.utcnow()
        await profile.save()
        return await self.bot.grant(ctx, "Updated your profile background image.")

    @biolink.command(name="avatar")
    async def biolink_avatar(self, ctx, url: str = None):
        """Set your profile avatar image"""
        image_url = None
        if ctx.message.attachments:
            image_url = ctx.message.attachments[0].url
        elif url and url.startswith(("http://", "https://")):
            image_url = url

        if not image_url:
            return await self.bot.warn(ctx, "Please attach an image or provide a direct image URL.")

        profile = await self._get_or_create_bio_profile(ctx.author)
        profile.avatar_url = image_url
        profile.updated_at = datetime.utcnow()
        await profile.save()
        return await self.bot.grant(ctx, "Updated your profile avatar.")

    @biolink.command(name="connection")
    async def biolink_connection(self, ctx, action: str = None, platform: str = None, *, handle: str = None):
        """Add or remove social links: add/remove <platform> <user>"""
        if not action:
            return await self.bot.warn(ctx, "Use `add <platform> <user>` or `remove <platform>`.")

        action = action.lower()
        platform = (platform or "").lower().strip()

        profile = await self._get_or_create_bio_profile(ctx.author)
        profile.connections = list(profile.connections or [])

        if action == "add" and platform and handle:
            profile.connections = [c for c in profile.connections if str(c.platform).lower() != platform]
            profile.connections.append(BioConnection(platform=platform, username=handle.strip()))
            profile.updated_at = datetime.utcnow()
            await profile.save()
            return await self.bot.grant(ctx, f"Added **{platform}**.")

        if action == "remove" and platform:
            before = len(profile.connections)
            profile.connections = [c for c in profile.connections if str(c.platform).lower() != platform]
            if len(profile.connections) == before:
                return await self.bot.warn(ctx, f"No **{platform}** connection was found.")
            profile.updated_at = datetime.utcnow()
            await profile.save()
            return await self.bot.grant(ctx, f"Removed **{platform}**.")

        return await self.bot.warn(ctx, "Use `add <platform> <user>` or `remove <platform>`.")

    @biolink.group(name="group", invoke_without_command=True)
    async def biolink_group(self, ctx):
        """Create/manage a biolink group"""
        group = await self._find_biogroup_by_user(ctx.author.id)
        if not group:
            return await self.bot.warn(ctx, "You are not in a biolink group.")

        owner = self.bot.get_user(group.owner_id)
        owner_text = owner.mention if owner else f"<@{group.owner_id}>"
        total = 1 + len(group.admin_ids or []) + len(group.members or [])
        group_slug = re.sub(r"[^a-z0-9]+", "-", (group.name or "group").lower()).strip("-") or "group"

        embed = discord.Embed(
            color=getattr(group, "color", 0x242429),
            title=f"{group.name}",
            description=f"Owner: {owner_text}\nGroup page: https://wock.best/groups/{group_slug}"
        )
        embed.add_field(name="Members", value=str(total), inline=True)
        embed.add_field(name="Admins", value=str(len(group.admin_ids or [])), inline=True)
        embed.add_field(name="Created", value=f"<t:{int(group.created_at.timestamp())}:R>", inline=True)
        if getattr(group, "icon_url", None):
            embed.set_thumbnail(url=group.icon_url)
        if getattr(group, "banner_url", None):
            embed.set_image(url=group.banner_url)
        await ctx.send(embed=embed)

    @biolink_group.command(name="create")
    async def biolink_group_create(self, ctx, *, name: str = None):
        """Create a biolink group"""
        if not name:
            return await self.bot.warn(ctx, "Provide a group name.")

        clean_name = name.strip()
        if len(clean_name) < 2 or len(clean_name) > 40:
            return await self.bot.warn(ctx, "Group name must be between 2 and 40 characters.")

        current = await self._find_biogroup_by_user(ctx.author.id)
        if current:
            return await self.bot.warn(ctx, "You are already in a biolink group.")

        existing = await self._find_biogroup_by_name(clean_name)
        if existing:
            return await self.bot.warn(ctx, "That group name is already taken.")

        group = BioGroup(name=clean_name, owner_id=ctx.author.id)
        await group.insert()

        profile = await self._get_or_create_bio_profile(ctx.author)
        profile.group_id = str(group.id)
        profile.group_role = "Owner"
        profile.updated_at = datetime.utcnow()
        await profile.save()

        return await self.bot.grant(ctx, f"Created group **{clean_name}**.")

    @biolink_group.command(name="info")
    async def biolink_group_info(self, ctx, *, name: str = None):
        """Show group info"""
        group = await (self._find_biogroup_by_name(name) if name else self._find_biogroup_by_user(ctx.author.id))
        if not group:
            return await self.bot.warn(ctx, "Group not found.")

        owner = self.bot.get_user(group.owner_id)
        owner_text = owner.mention if owner else f"<@{group.owner_id}>"

        admins = [f"<@{uid}>" for uid in (group.admin_ids or [])]
        members = [f"<@{uid}>" for uid in (group.members or [])]
        total = 1 + len(group.admin_ids or []) + len(group.members or [])
        group_slug = re.sub(r"[^a-z0-9]+", "-", (group.name or "group").lower()).strip("-") or "group"

        embed = discord.Embed(
            color=getattr(group, "color", 0x242429),
            title=f"{group.name}",
            description=f"Owner: {owner_text}\nGroup page: https://wock.best/groups/{group_slug}"
        )
        embed.add_field(name="Members", value=str(total), inline=True)
        embed.add_field(name="Admins", value=str(len(group.admin_ids or [])), inline=True)
        embed.add_field(name="Created", value=f"<t:{int(group.created_at.timestamp())}:R>", inline=True)
        if getattr(group, "icon_url", None):
            embed.set_thumbnail(url=group.icon_url)
        if admins:
            embed.add_field(name="Admin List", value=", ".join(admins)[:1024], inline=False)
        if members:
            embed.add_field(name="Member List", value=", ".join(members)[:1024], inline=False)
        if getattr(group, "banner_url", None):
            embed.set_image(url=group.banner_url)
        await ctx.send(embed=embed)

    @biolink_group.command(name="disband")
    async def biolink_group_disband(self, ctx):
        """Disband your biolink group"""
        group = await self._find_biogroup_by_user(ctx.author.id)
        if not group:
            return await self.bot.warn(ctx, "You are not in a biolink group.")
        if group.owner_id != ctx.author.id:
            return await self.bot.warn(ctx, "Only the group owner can disband the group.")

        member_ids = self._biogroup_all_member_ids(group)
        profiles = await BioProfile.find_all().to_list()
        for profile in profiles:
            if profile.user_id in member_ids:
                profile.group_id = None
                profile.group_role = None
                profile.updated_at = datetime.utcnow()
                await profile.save()

        name = group.name
        await group.delete()
        return await self.bot.grant(ctx, f"Disbanded group **{name}**.")

    @biolink_group.command(name="invite")
    async def biolink_group_invite(self, ctx, member: discord.Member = None):
        """Invite a user to your biolink group"""
        if not member:
            return await ctx.send_help(ctx.command)
        if member.bot:
            return await self.bot.warn(ctx, "You cannot invite bots.")
        if member.id == ctx.author.id:
            return await self.bot.warn(ctx, "You cannot invite yourself.")

        group = await self._find_biogroup_by_user(ctx.author.id)
        if not group:
            return await self.bot.warn(ctx, "You are not in a biolink group.")
        if ctx.author.id != group.owner_id and ctx.author.id not in (group.admin_ids or []):
            return await self.bot.warn(ctx, "Only group admins can invite members.")

        target_group = await self._find_biogroup_by_user(member.id)
        if target_group:
            return await self.bot.warn(ctx, f"{member.mention} is already in a biolink group.")

        if member.id not in (group.members or []):
            group.members.append(member.id)
        group.members = list(dict.fromkeys(group.members))
        await group.save()

        profile = await self._get_or_create_bio_profile(member)
        profile.group_id = str(group.id)
        profile.group_role = "Member"
        profile.updated_at = datetime.utcnow()
        await profile.save()

        return await self.bot.grant(ctx, f"Invited {member.mention} to **{group.name}**.")

    @biolink_group.command(name="kick")
    async def biolink_group_kick(self, ctx, member: discord.Member = None):
        """Kick a member from your biolink group"""
        if not member:
            return await ctx.send_help(ctx.command)

        group = await self._find_biogroup_by_user(ctx.author.id)
        if not group:
            return await self.bot.warn(ctx, "You are not in a biolink group.")
        if ctx.author.id != group.owner_id and ctx.author.id not in (group.admin_ids or []):
            return await self.bot.warn(ctx, "Only group admins can kick members.")
        if member.id == group.owner_id:
            return await self.bot.warn(ctx, "You cannot kick the group owner.")
        if member.id in (group.admin_ids or []) and ctx.author.id != group.owner_id:
            return await self.bot.warn(ctx, "Only the owner can kick an admin.")

        is_member = member.id in (group.members or [])
        is_admin = member.id in (group.admin_ids or [])
        if not is_member and not is_admin:
            return await self.bot.warn(ctx, f"{member.mention} is not in your group.")

        if is_admin:
            group.admin_ids.remove(member.id)
        if is_member:
            group.members.remove(member.id)
        await group.save()

        profile = await BioProfile.find_one(BioProfile.user_id == member.id)
        if profile:
            profile.group_id = None
            profile.group_role = None
            profile.updated_at = datetime.utcnow()
            await profile.save()

        return await self.bot.grant(ctx, f"Removed {member.mention} from **{group.name}**.")

    @biolink_group.command(name="leave")
    async def biolink_group_leave(self, ctx):
        """Leave your biolink group"""
        group = await self._find_biogroup_by_user(ctx.author.id)
        if not group:
            return await self.bot.warn(ctx, "You are not in a biolink group.")
        if group.owner_id == ctx.author.id:
            return await self.bot.warn(ctx, "Owners cannot leave. Transfer ownership or disband the group.")

        if ctx.author.id in (group.admin_ids or []):
            group.admin_ids.remove(ctx.author.id)
        if ctx.author.id in (group.members or []):
            group.members.remove(ctx.author.id)
        await group.save()

        profile = await BioProfile.find_one(BioProfile.user_id == ctx.author.id)
        if profile:
            profile.group_id = None
            profile.group_role = None
            profile.updated_at = datetime.utcnow()
            await profile.save()

        return await self.bot.grant(ctx, f"You left **{group.name}**.")

    @biolink_group.command(name="promote")
    async def biolink_group_promote(self, ctx, member: discord.Member = None):
        """Promote a member to admin"""
        if not member:
            return await ctx.send_help(ctx.command)

        group = await self._find_biogroup_by_user(ctx.author.id)
        if not group:
            return await self.bot.warn(ctx, "You are not in a biolink group.")
        if ctx.author.id != group.owner_id:
            return await self.bot.warn(ctx, "Only the group owner can promote members.")
        if member.id == group.owner_id:
            return await self.bot.warn(ctx, "That user is already the owner.")
        if member.id in (group.admin_ids or []):
            return await self.bot.warn(ctx, f"{member.mention} is already an admin.")
        if member.id not in (group.members or []):
            return await self.bot.warn(ctx, f"{member.mention} is not a group member.")

        group.members.remove(member.id)
        group.admin_ids.append(member.id)
        group.admin_ids = list(dict.fromkeys(group.admin_ids))
        await group.save()

        profile = await self._get_or_create_bio_profile(member)
        profile.group_id = str(group.id)
        profile.group_role = "Admin"
        profile.updated_at = datetime.utcnow()
        await profile.save()

        return await self.bot.grant(ctx, f"Promoted {member.mention} to admin.")

    @biolink_group.command(name="transfer")
    async def biolink_group_transfer(self, ctx, member: discord.Member = None):
        """Transfer group ownership"""
        if not member:
            return await ctx.send_help(ctx.command)
        if member.bot:
            return await self.bot.warn(ctx, "You cannot transfer ownership to a bot.")

        group = await self._find_biogroup_by_user(ctx.author.id)
        if not group:
            return await self.bot.warn(ctx, "You are not in a biolink group.")
        if ctx.author.id != group.owner_id:
            return await self.bot.warn(ctx, "Only the owner can transfer ownership.")
        if member.id == group.owner_id:
            return await self.bot.warn(ctx, "That user is already the owner.")
        if member.id not in self._biogroup_all_member_ids(group):
            return await self.bot.warn(ctx, "Target user must be in your group.")

        old_owner_id = group.owner_id
        if member.id in (group.admin_ids or []):
            group.admin_ids.remove(member.id)
        if member.id in (group.members or []):
            group.members.remove(member.id)

        if old_owner_id not in (group.admin_ids or []):
            group.admin_ids.append(old_owner_id)

        group.owner_id = member.id
        group.admin_ids = list(dict.fromkeys(group.admin_ids))
        await group.save()

        new_owner_profile = await self._get_or_create_bio_profile(member)
        new_owner_profile.group_id = str(group.id)
        new_owner_profile.group_role = "Owner"
        new_owner_profile.updated_at = datetime.utcnow()
        await new_owner_profile.save()

        old_owner_profile = await self._get_or_create_bio_profile(ctx.author)
        old_owner_profile.group_id = str(group.id)
        old_owner_profile.group_role = "Admin"
        old_owner_profile.updated_at = datetime.utcnow()
        await old_owner_profile.save()

        return await self.bot.grant(ctx, f"Transferred ownership of **{group.name}** to {member.mention}.")

    @biolink_group.command(name="setbanner")
    async def biolink_group_setbanner(self, ctx, url: str = None):
        """Set biolink group banner"""
        group = await self._find_biogroup_by_user(ctx.author.id)
        if not group:
            return await self.bot.warn(ctx, "You are not in a biolink group.")
        if ctx.author.id != group.owner_id and ctx.author.id not in (group.admin_ids or []):
            return await self.bot.warn(ctx, "Only group admins can edit group settings.")

        url = url or (ctx.message.attachments[0].url if ctx.message.attachments else None)
        if not url:
            return await ctx.send_help(ctx.command)
        if not url.startswith(("http://", "https://")):
            return await self.bot.warn(ctx, "Provide a valid image URL.")

        group.banner_url = url
        await group.save()
        return await self.bot.grant(ctx, "Group banner updated.")

    @biolink_group.command(name="setcolor")
    async def biolink_group_setcolor(self, ctx, color: str = None):
        """Set biolink group color"""
        if not color:
            return await ctx.send_help(ctx.command)

        group = await self._find_biogroup_by_user(ctx.author.id)
        if not group:
            return await self.bot.warn(ctx, "You are not in a biolink group.")
        if ctx.author.id != group.owner_id and ctx.author.id not in (group.admin_ids or []):
            return await self.bot.warn(ctx, "Only group admins can edit group settings.")

        try:
            color_int = int(color.lstrip("#"), 16)
        except ValueError:
            return await self.bot.warn(ctx, "Invalid color format. Use HEX (#RRGGBB).")

        group.color = color_int
        await group.save()
        return await self.bot.grant(ctx, f"Group color set to `#{color.lstrip('#').upper()}`.")

    @biolink_group.command(name="icon", aliases=["seticon", "setemoji"])
    async def biolink_group_icon(self, ctx, url: str = None):
        """Set biolink group icon"""

        group = await self._find_biogroup_by_user(ctx.author.id)
        if not group:
            return await self.bot.warn(ctx, "You are not in a biolink group.")
        if ctx.author.id != group.owner_id and ctx.author.id not in (group.admin_ids or []):
            return await self.bot.warn(ctx, "Only group admins can edit group settings.")

        icon = url or (ctx.message.attachments[0].url if ctx.message.attachments else None)
        if not icon:
            return await self.bot.warn(ctx, "Provide an image URL or attach an image.")
        if not icon.startswith(("http://", "https://")):
            return await self.bot.warn(ctx, "Provide a valid image URL.")

        group.icon_url = icon
        await group.save()
        return await self.bot.grant(ctx, "Group icon updated.")

    @biolink_group.command(name="leaderboard")
    async def biolink_group_leaderboard(self, ctx):
        """View top biolink groups by size"""
        groups = await BioGroup.find_all().to_list()
        if not groups:
            return await self.bot.neutral(ctx, "No biolink groups yet.")

        groups.sort(key=lambda g: (1 + len(g.admin_ids or []) + len(g.members or [])), reverse=True)
        lines = []
        for idx, group in enumerate(groups[:10], 1):
            size = 1 + len(group.admin_ids or []) + len(group.members or [])
            lines.append(f"`{idx}.` **{group.name}** — **{size}** members")

        embed = discord.Embed(
            color=0x242429,
            title="🏆 Biolink Group Leaderboard",
            description="\n".join(lines),
        )
        await ctx.send(embed=embed)

    @biolink.command(name="color")
    async def biolink_color(self, ctx, hex_code: str = None):
        """Set username hex color"""
        if not hex_code or not re.fullmatch(r"#(?:[A-Fa-f0-9]{6}|[A-Fa-f0-9]{3})", hex_code):
            return await self.bot.warn(ctx, "Invalid hex code (e.g., #ff0000).")

        profile = await self._get_or_create_bio_profile(ctx.author)
        profile.username_color = hex_code
        profile.updated_at = datetime.utcnow()
        await profile.save()
        return await self.bot.grant(ctx, f"Username color set to **{hex_code}**.")

    @biolink.command(name="reset")
    async def biolink_reset(self, ctx, confirmation: str = None):
        """Wipe your entire profile"""
        if (confirmation or "").lower() != "confirm":
            return await self.bot.warn(ctx, "This is destructive. Run again with `confirm` to proceed.")

        profile = await BioProfile.find_one(BioProfile.user_id == ctx.author.id)
        if not profile:
            return await self.bot.warn(ctx, "You don't have a profile to reset.")

        group = await self._find_biogroup_by_user(ctx.author.id)
        if group:
            if group.owner_id == ctx.author.id:
                if group.admin_ids or group.members:
                    return await self.bot.warn(ctx, "You own a group. Transfer ownership or disband it before resetting.")
                await group.delete()
            else:
                if ctx.author.id in (group.admin_ids or []):
                    group.admin_ids.remove(ctx.author.id)
                if ctx.author.id in (group.members or []):
                    group.members.remove(ctx.author.id)
                await group.save()

        await profile.delete()
        return await self.bot.grant(ctx, "Profile wiped.")

    @commands.group(name="host", aliases=["upload", "img"], invoke_without_command=True)
    async def host(self, ctx):
        """Host images on Wock"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @host.command(name="upload")
    async def host_upload(self, ctx, custom_name: str = None, flag: str = None):
        """Host an image: <attachment> [name] [--private]"""
        user_id = ctx.author.id
        now = discord.utils.utcnow().timestamp()
        cooldown_until = self.host_cooldowns.get(user_id, 0)
        if now < cooldown_until:
            return await self.bot.warn(ctx, f"Please wait **{cooldown_until - now:.1f}s** before uploading again.")

        attachment = ctx.message.attachments[0] if ctx.message.attachments else None
        if not attachment:
            return await self.bot.warn(ctx, "You must attach an image to host it.")

        valid_types = {"image/png", "image/jpeg", "image/gif", "image/webp"}
        content_type = (attachment.content_type or "").lower()
        ext = (attachment.filename.rsplit(".", 1)[-1].lower() if "." in attachment.filename else "")
        if content_type not in valid_types and ext not in {"png", "jpg", "jpeg", "gif", "webp"}:
            return await self.bot.warn(ctx, "Invalid file type. PNG, JPG, GIF, or WEBP only.")

        async with ctx.typing():
            temp_session = None
            http = getattr(self.bot, "session", None)
            if http is None or getattr(http, "closed", False):
                temp_session = aiohttp.ClientSession()
                http = temp_session

            try:
                detected_nsfw = False
                try:
                    async with http.get(
                        "https://api.sightengine.com/1.0/check.json",
                        params={
                            "url": attachment.url,
                            "models": "nudity-2.0",
                            "api_user": "1716479231",
                            "api_secret": "vepAFsfeeAtQphQYFcwp8aZ3K6fjhd6y",
                        },
                        timeout=aiohttp.ClientTimeout(total=12),
                    ) as resp:
                        sight_data = await resp.json(content_type=None)
                        n = (sight_data or {}).get("nudity", {})
                        detected_nsfw = bool(
                            (n.get("sexual_activity", 0) > 0.1)
                            or (n.get("sexual_display", 0) > 0.1)
                            or (n.get("erotica", 0) > 0.1)
                            or (n.get("bikini", 0) > 0.3)
                            or (n.get("lingerie", 0) > 0.3)
                        )
                except Exception:
                    detected_nsfw = False

                is_private_flag = ("--private" in (ctx.message.content or "").lower()) or ((flag or "").lower() == "--private")
                final_nsfw = detected_nsfw
                final_private = is_private_flag or detected_nsfw

                file_ext = ext or "png"
                if custom_name and custom_name.startswith("--"):
                    custom_name = None

                if custom_name:
                    requested_name = re.sub(r"[^a-z0-9_\-]", "_", custom_name.lower())
                    clean_file_name = f"{requested_name}.{file_ext}"
                    existing = await Upload.find_one(Upload.file_name == clean_file_name)
                    if existing:
                        return await self.bot.warn(ctx, f"The name `{requested_name}` is already taken.")
                else:
                    base = re.sub(r"\s+", "_", attachment.filename.lower())
                    clean_file_name = f"{int(discord.utils.utcnow().timestamp())}_{base}"

                async with http.get(attachment.url, timeout=aiohttp.ClientTimeout(total=25)) as file_resp:
                    if file_resp.status != 200:
                        return await self.bot.warn(ctx, "Could not download the attachment.")
                    payload = await file_resp.read()

                raw_link = await upload_to_catbox(http, payload, attachment.filename)
                if not raw_link:
                    return await self.bot.warn(ctx, "There was an error processing or hosting your image.")

                doc = Upload(
                    user_id=user_id,
                    uploader_name=ctx.author.name,
                    url=raw_link,
                    file_name=clean_file_name,
                    is_nsfw=final_nsfw,
                    is_private=final_private,
                )
                await doc.insert()

                self.host_cooldowns[user_id] = discord.utils.utcnow().timestamp() + 30

                embed = discord.Embed(
                    title="Successfully Hosted (Hidden)" if final_nsfw else "Successfully Hosted",
                    color=0xff4444 if final_nsfw else self.bot.config.get("color", 0x242429),
                )
                embed.set_thumbnail(url=raw_link)
                embed.add_field(name="Wock Link", value=f"[{clean_file_name}](https://wock.best/gallery/{clean_file_name})", inline=False)
                embed.add_field(name="ID", value=f"`{doc.id}`", inline=True)
                return await ctx.send(embed=embed)
            except Exception as error:
                logger("error", f"[Host Error]: {error}")
                return await self.bot.warn(ctx, "There was an error processing or hosting your image.")
            finally:
                if temp_session and not temp_session.closed:
                    await temp_session.close()

    @host.command(name="rename")
    async def host_rename(self, ctx, image_id: str = None, new_name: str = None):
        """Rename an existing host by ID: <id> <new_name>"""
        if not image_id or not re.fullmatch(r"[0-9a-fA-F]{24}", image_id):
            return await self.bot.warn(ctx, "Provide a valid Image ID.")
        if not new_name:
            return await self.bot.warn(ctx, "Provide a new name for the file.")

        safe_name = re.sub(r"[^a-z0-9_\-]", "_", new_name.lower())
        try:
            upload_id = PydanticObjectId(image_id)
        except Exception:
            return await self.bot.warn(ctx, "Provide a valid Image ID.")

        img = await Upload.find_one(Upload.id == upload_id, Upload.user_id == ctx.author.id)
        if not img:
            return await self.bot.warn(ctx, "Asset not found or unauthorized.")

        ext = img.file_name.rsplit(".", 1)[-1] if "." in img.file_name else "png"
        final_name = f"{safe_name}.{ext}"
        existing = await Upload.find_one(Upload.file_name == final_name)
        if existing:
            return await self.bot.warn(ctx, f"The name `{safe_name}` is already taken.")

        img.file_name = final_name
        await img.save()
        return await self.bot.grant(ctx, f"Renamed asset to **[{final_name}](https://wock.best/gallery/{final_name})**.")

    @host.command(name="list")
    async def host_list(self, ctx):
        """View your last uploads"""
        uploads = await Upload.find(Upload.user_id == ctx.author.id).sort(-Upload.created_at).to_list()
        if not uploads:
            return await self.bot.warn(ctx, "No images found in your Wock registry.")

        pages = []
        per_page = 10
        total_pages = (len(uploads) + per_page - 1) // per_page
        for i in range(0, len(uploads), per_page):
            chunk = uploads[i:i + per_page]
            lines = []
            for idx, img in enumerate(chunk, start=i + 1):
                icon = "🔞" if img.is_nsfw else ("🔐" if img.is_private else "🖼️")
                display_name = img.file_name if len(img.file_name) <= 22 else f"{img.file_name[:19]}..."
                lines.append(f"{idx}. {icon} **[{display_name}](https://wock.best/gallery/{img.file_name})** (`{img.id}`)")

            embed = discord.Embed(
                title=f"Your Wock Assets ({len(uploads)})",
                description="\n".join(lines),
                color=self.bot.config.get("color", 0x242429),
            )
            embed.set_footer(text=f"Page {(i // per_page) + 1} of {total_pages}")
            pages.append(embed)

        if len(pages) == 1:
            return await ctx.send(embed=pages[0])
        await WockPaginator(ctx, pages).start()

    @host.command(name="delete")
    async def host_delete(self, ctx, image_id: str = None):
        """Delete an upload by ID"""
        if not image_id or not re.fullmatch(r"[0-9a-fA-F]{24}", image_id):
            return await self.bot.warn(ctx, "Invalid ID format.")

        try:
            upload_id = PydanticObjectId(image_id)
        except Exception:
            return await self.bot.warn(ctx, "Invalid ID format.")

        img = await Upload.find_one(Upload.id == upload_id, Upload.user_id == ctx.author.id)
        if not img:
            return await self.bot.warn(ctx, "Asset not found or unauthorized.")

        await img.delete()
        return await self.bot.grant(ctx, f"Successfully purged asset `{image_id}`.")

    @host.command(name="clearall")
    async def host_clearall(self, ctx):
        """Developer only: clear host registry"""
        if ctx.author.id != 1065363954597113896:
            return await self.bot.warn(ctx, "Only the developer can purge the entire registry.")

        all_uploads = await Upload.find_all().to_list()
        count = len(all_uploads)
        for item in all_uploads:
            await item.delete()
        return await self.bot.grant(ctx, f"Registry wiped. Purged **{count}** assets.")

    @commands.command(name="weather", aliases=['wthr'])
    async def weather(self, ctx, *, city: str = None):
        """View weather for a city"""
        if not city:
            return await ctx.send_help(ctx.command)

        async with aiohttp.ClientSession() as session:
            url = "https://api.openweathermap.org/data/2.5/weather"
            params = {
                "q": city,
                "appid": self.weather_api_key,
                "units": "metric"
            }
            
            async with session.get(url, params=params) as resp:
                if resp.status == 404:
                    return await ctx.send(f"City **{city}** not found.")
                if resp.status != 200:
                    return await ctx.send("An error occurred while fetching weather data.")
                
                data = await resp.json()

        temp_c = data['main']['temp']
        temp_f = (temp_c * 9/5) + 32
        description = data['weather'][0]['description'].title()

        embed = discord.Embed(
            title=f"{description} in {data['name']}, {data['sys']['country']}",
            color=0x242429,
            timestamp=ctx.message.created_at
        )

        embed.add_field(name='Temperature', value=f"{temp_c:.1f}°C / {temp_f:.1f}°F", inline=True)
        embed.add_field(name='Wind', value=f"{data['wind']['speed']} m/s", inline=True)
        embed.add_field(name='Humidity', value=f"{data['main']['humidity']}%", inline=True)
        embed.add_field(name='Sun Rise', value=f"<t:{data['sys']['sunrise']}:t>", inline=True)
        embed.add_field(name='Sun Set', value=f"<t:{data['sys']['sunset']}:t>", inline=True)
        embed.add_field(name='Visibility', value=f"{data['visibility'] / 1000:.1f} km", inline=True)

        await ctx.send(embed=embed)

    @commands.command(name="timezone", aliases=['tz'])
    async def timezone(self, ctx, *, place: str = None):
        """View the timezone of a certain place"""
        if not place:
            return await ctx.send_help(ctx.command)

        async with aiohttp.ClientSession() as session:
            url = "https://api.openweathermap.org/data/2.5/weather"
            params = {
                "q": place,
                "appid": self.weather_api_key,
                "units": "metric"
            }
            
            async with session.get(url, params=params) as resp:
                if resp.status == 404:
                    return await ctx.send(f"Place **{place}** not found.")
                if resp.status != 200:
                    return await ctx.send("An error occurred while fetching timezone data.")
                
                data = await resp.json()

        timezone_offset = data['timezone']
        country = data['sys']['country']
        city_name = data['name']
        
        current_time = int(datetime.now().timestamp()) + timezone_offset
        
        embed = discord.Embed(
            description=f"It is currently <t:{current_time}:t> in **{city_name}, {country}**",
            color=0x242429
        )
        
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(Miscellaneous(bot))