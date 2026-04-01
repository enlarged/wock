import discord
import aiohttp
import logging
from discord.ext import commands
from models.lastfm import LastfmData
from utils.paginator import WockPaginator
from utils.parser import EmbedParser

log = logging.getLogger(__name__)

class Lastfm(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.api_key = self.bot.config.get('lastfm') 
        self.base_url = "https://ws.audioscrobbler.com/2.0/"
        self.lastfm_cache = {}
        
        self.genius_client_id = "9LV3_Xt0j8_Mms7olhaGz61JTblBt0dInvSzdsg513ooBw-rvZvjQQuhLhHyr8gs"
        self.genius_client_secret = "wxNA33hAEqyzhMt8YISblKe8jJGvNlhShHtdNa5Dvr4ZbgWFk4eVyx2GhS-F09vuTpY2aqyFzmyRu8h68Asr1g"
        self.genius_access_token = "AigS_b1pa88L2kXeSdxtVutfVWVxw2Qvrzb6eF_j3Z1LgJgV00P4At6q566KdTv2"

    async def fetch(self, method, params):
        params.update({"method": method, "api_key": self.api_key, "format": "json"})
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.base_url, params=params) as resp:
                    return await resp.json() if resp.status == 200 else {"error": True}
        except Exception:
            log.exception("Last.fm fetch error")
            return {"error": True}

    @commands.group(invoke_without_command=True, aliases=['lf'])
    async def lastfm(self, ctx):
        """Interact with your music via Last.fm"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @lastfm.command(name="set")
    async def lf_set(self, ctx, username: str = None):
        """Set your Last.fm username"""
        if not username:
            embed = discord.Embed(
                title="How to Set Your Last.fm Username",
                description="Follow these steps to connect your Last.fm account to the bot:",
                color=0x242429
            )
            embed.add_field(
                name="Step 1: Create a Last.fm Account",
                value="If you don't have a Last.fm account yet, go to https://www.last.fm/join and sign up. It's free!",
                inline=False
            )
            embed.add_field(
                name="Step 2: Set Your Username via Bot",
                value=f"Use the command: `{ctx.clean_prefix}lastfm set <your_username>`\n\nExample: `{ctx.clean_prefix}lastfm set john_doe`",
                inline=False
            )
            embed.add_field(
                name="Step 3: Scrobble Tracks",
                value="Start listening to music on Spotify, Apple Music, or any scrobbling service connected to Last.fm.",
                inline=False
            )
            embed.add_field(
                name="What's a Scrobble?",
                value="A scrobble is a record of every song you listen to. Last.fm tracks these automatically!",
                inline=False
            )
            embed.set_footer(text="Once set, use commands like ,fm, ,recent, ,toptracks and more!")
            return await ctx.send(embed=embed)
        
        try:
            data = await self.fetch("user.getinfo", {"user": username})
            if "error" in data or "user" not in data:
                return await self.bot.warn(ctx, "Invalid Last.fm username.")

            await LastfmData.find_one(LastfmData.user_id == ctx.author.id).upsert(
                {"$set": {"username": username}},
                on_insert=LastfmData(user_id=ctx.author.id, username=username)
            )
            await self.bot.grant(ctx, f"Your Last.fm username has been set to **{username}**")
        except Exception:
            log.exception("Database error")
            await self.bot.deny(ctx, "A database error occurred.")

    @lastfm.command(name="remove", aliases=["unset"])
    async def lf_remove(self, ctx):
        """Remove your Last.fm username"""
        entry = await LastfmData.find_one(LastfmData.user_id == ctx.author.id)
        if not entry:
            return await self.bot.warn(ctx, "You don't have a Last.fm username set.")
        await entry.delete()
        await self.bot.grant(ctx, "Successfully removed your Last.fm data.")

    @commands.command(name="nowplaying", aliases=["np", "fm"])
    async def standalone_np(self, ctx, user: discord.Member = None):
        """View what you are currently listening to"""
        try:
            user = user or ctx.author
            entry = await LastfmData.find_one(LastfmData.user_id == user.id)
            if not entry:
                return await self.bot.warn(ctx, f"{'You' if user == ctx.author else user.name} haven't set a Last.fm username.")

            data = await self.fetch("user.getrecenttracks", {"user": entry.username, "limit": 1})
            if "error" in data:
                return await self.bot.deny(ctx, "Failed to fetch Last.fm data. Check the username.")
            
            tracks = data.get("recenttracks", {}).get("track", [])
            if not tracks: 
                return await self.bot.warn(ctx, "No recent tracks found.")

            track = tracks[0]
            artist = track.get('artist', {})
            album = track.get('album', {})
            
            if entry.custom_embed_template:
                try:
                    variables = {
                        "{track_name}": track.get('name', 'Unknown'),
                        "{track_url}": track.get('url', ''),
                        "{artist_name}": artist.get('#text', 'Unknown') if isinstance(artist, dict) else str(artist),
                        "{artist_url}": artist.get('url', '') if isinstance(artist, dict) else '',
                        "{album_name}": album.get('#text', 'Unknown') if isinstance(album, dict) else str(album),
                        "{album_url}": album.get('url', '') if isinstance(album, dict) else '',
                        "{play_count}": track.get('playcount', '0'),
                        "{album_image}": track.get('image', [{'#text': ''}])[-1].get('#text', ''),
                        "{username}": entry.username,
                    }
                    
                    parsed_template = entry.custom_embed_template
                    for var, value in variables.items():
                        parsed_template = parsed_template.replace(var, str(value))
                    
                    parser = EmbedParser(ctx)
                    embed = parser.parse(parsed_template)
                    return await ctx.send(embed=embed)
                except Exception as e:
                    log.error(f"Failed to parse custom embed for {ctx.author}: {e}")
                    await self.bot.warn(ctx, f"Custom embed has invalid syntax. Error: {str(e)}")
            
            artist_name = artist.get('#text', 'Unknown') if isinstance(artist, dict) else str(artist)
            embed = discord.Embed(color=0x242429)
            embed.set_author(name=f"Last.fm: {entry.username}", icon_url=user.display_avatar.url)
            embed.description = f"**[{track['name']}]({track['url']})**\nby **{artist_name}**"
            if track['image'][-1]['#text']: 
                embed.set_thumbnail(url=track['image'][-1]['#text'])
            await ctx.send(embed=embed)
        except Exception as e:
            log.exception(f"Error in nowplaying command: {e}")
            return await self.bot.deny(ctx, f"An error occurred: {str(e)}")

    @lastfm.command(name="recent", aliases=["rc"])
    async def lf_recent(self, ctx):
        """View your 5 most recent tracks"""
        entry = await LastfmData.find_one(LastfmData.user_id == ctx.author.id)
        if not entry: return await self.bot.warn(ctx, "Set your username first.")

        data = await self.fetch("user.getrecenttracks", {"user": entry.username, "limit": 5})
        items = data.get("recenttracks", {}).get("track", [])
        if not items: return await self.bot.warn(ctx, "No recent tracks found.")

        description = "\n".join([f"**{t['name']}** by **{t['artist']['#text']}**" for t in items])
        embed = discord.Embed(title=f"{entry.username}'s Recent Tracks", description=description, color=0x242429)
        await ctx.send(embed=embed)
        
    @lastfm.command(name="profile", aliases=["pf"])
    async def lf_profile(self, ctx):
        """View your Last.fm profile"""
        entry = await LastfmData.find_one(LastfmData.user_id == ctx.author.id)
        if not entry: return await self.bot.warn(ctx, "Set your username first.")
        data = await self.fetch("user.getinfo", {"user": entry.username})
        if "error" in data or "user" not in data:
            return await self.bot.warn(ctx, "Failed to fetch Last.fm data. Check your username.")
        user_data = data["user"]
        embed = discord.Embed(title=f"{user_data['name']}'s Last.fm Profile", description=user_data.get('bio', {}).get('summary', 'No bio available.'), color=0x242429)
        embed.set_thumbnail(url=user_data.get('image', [])[-1].get('#text', ''))
        embed.add_field(name="Playcount", value=f"{int(user_data.get('playcount', '0')):,}", inline=True)
        embed.add_field(name="Listeners", value=f"{int(user_data.get('listeners', '0')):,}", inline=True)
        embed.add_field(name="Registered", value=user_data.get('registered', {}).get('#text', 'Unknown'), inline=True)
        await ctx.send(embed=embed)

    
    @lastfm.command(name="toptracks", aliases=["tt"])
    async def lf_tt(self, ctx):
        """View your top 50 tracks"""
        entry = await LastfmData.find_one(LastfmData.user_id == ctx.author.id)
        if not entry: return await self.bot.warn(ctx, "Set your username first.")

        data = await self.fetch("user.gettoptracks", {"user": entry.username, "limit": 50})
        items = data.get("toptracks", {}).get("track", [])
        if not items: return await self.bot.warn(ctx, "No tracks found.")

        pages = []
        for i in range(0, len(items), 10):
            chunk = items[i:i + 10]
            description = "\n".join([f"`{i+j+1}.` **{t['name']}** - {t['artist']['name']} ({int(t['playcount']):,} plays)" for j, t in enumerate(chunk)])
            embed = discord.Embed(title=f"{entry.username}'s Top Tracks", description=description, color=0x242429)
            pages.append(embed)
        
        await WockPaginator(ctx, pages).start()

    @lastfm.command(name="topartists", aliases=["ta"])
    async def lf_ta(self, ctx):
        """View your top 50 artists"""
        entry = await LastfmData.find_one(LastfmData.user_id == ctx.author.id)
        if not entry: return await self.bot.warn(ctx, "Set your username first.")

        data = await self.fetch("user.gettopartists", {"user": entry.username, "limit": 50})
        items = data.get("topartists", {}).get("artist", [])
        
        pages = []
        for i in range(0, len(items), 10):
            chunk = items[i:i + 10]
            description = "\n".join([f"`{i+j+1}.` **{a['name']}** ({int(a['playcount']):,} plays)" for j, a in enumerate(chunk)])
            embed = discord.Embed(title=f"{entry.username}'s Top Artists", description=description, color=0x242429)
            pages.append(embed)
        
        await WockPaginator(ctx, pages).start()

    @lastfm.command(name="topalbums", aliases=["talb"])
    async def lf_talb(self, ctx):
        """View your top 50 albums"""
        entry = await LastfmData.find_one(LastfmData.user_id == ctx.author.id)
        if not entry: return await self.bot.warn(ctx, "Set your username first.")

        data = await self.fetch("user.gettopalbums", {"user": entry.username, "limit": 50})
        items = data.get("topalbums", {}).get("album", [])
        
        pages = []
        for i in range(0, len(items), 10):
            chunk = items[i:i + 10]
            description = "\n".join([f"`{i+j+1}.` **{a['name']}** - {a['artist']['name']} ({int(a['playcount']):,} plays)" for j, a in enumerate(chunk)])
            embed = discord.Embed(title=f"{entry.username}'s Top Albums", description=description, color=0x242429)
            pages.append(embed)
        
        await WockPaginator(ctx, pages).start()

    @lastfm.group(name="customembed", aliases=["ce"], invoke_without_command=True)
    async def lf_customembed(self, ctx):
        """Set a custom embed for now playing track
        
        Available variables:
        {track_name} - Song title
        {track_url} - Song URL
        {artist_name} - Artist name
        {artist_url} - Artist URL
        {album_name} - Album name
        {album_url} - Album URL
        {play_count} - Total play count
        {album_image} - Album art URL
        {username} - Last.fm username
        """
        await ctx.send_help(ctx.command)

    @lf_customembed.command(name="set")
    async def lf_ce_set(self, ctx, *, template: str = None):
        """Set a custom embed template for your now playing display
        
        Example: {title: {track_name}} {desc: by **{artist_name}**} {thumbnail: {album_image}}
        """
        if not template:
            return await ctx.send_help(ctx.command)
        
        try:
            entry = await LastfmData.find_one(LastfmData.user_id == ctx.author.id)
            if not entry:
                return await self.bot.warn(ctx, "Set your username first using `,lastfm set <username>`.")
            
            entry.custom_embed_template = template
            await entry.save()
            await self.bot.grant(ctx, "Custom embed template saved. Use `,fm` to see it in action!")
        except Exception as e:
            log.exception(f"Error saving custom embed template: {e}")
            return await self.bot.deny(ctx, f"Failed to save template: {str(e)}")

    @lf_customembed.command(name="reset")
    async def lf_ce_reset(self, ctx):
        """Reset your custom embed template to default"""
        try:
            entry = await LastfmData.find_one(LastfmData.user_id == ctx.author.id)
            if not entry:
                return await self.bot.warn(ctx, "You don't have a Last.fm username set.")
            
            if not entry.custom_embed_template:
                return await self.bot.neutral(ctx, "You don't have a custom embed template set.")
            
            entry.custom_embed_template = None
            await entry.save()
            await self.bot.grant(ctx, "Custom embed template reset to default.")
        except Exception as e:
            log.exception(f"Error resetting custom embed template: {e}")
            return await self.bot.deny(ctx, f"Failed to reset template: {str(e)}")

    @lf_customembed.command(name="preview")
    async def lf_ce_preview(self, ctx, *, template: str = None):
        """Preview a custom embed template with your current track"""
        if not template:
            return await ctx.send_help(ctx.command)
        
        try:
            entry = await LastfmData.find_one(LastfmData.user_id == ctx.author.id)
            if not entry:
                return await self.bot.warn(ctx, "Set your username first using `,lastfm set <username>`.")
            
            data = await self.fetch("user.getrecenttracks", {"user": entry.username, "limit": 1})
            if "error" in data:
                return await self.bot.deny(ctx, "Failed to fetch Last.fm data. Check your username.")
            
            tracks = data.get("recenttracks", {}).get("track", [])
            if not tracks:
                return await self.bot.warn(ctx, "No recent tracks found.")
            
            track = tracks[0]
            artist = track.get('artist', {})
            album = track.get('album', {})
            
            variables = {
                "{track_name}": track.get('name', 'Unknown'),
                "{track_url}": track.get('url', ''),
                "{artist_name}": artist.get('#text', 'Unknown') if isinstance(artist, dict) else str(artist),
                "{artist_url}": artist.get('url', '') if isinstance(artist, dict) else '',
                "{album_name}": album.get('#text', 'Unknown') if isinstance(album, dict) else str(album),
                "{album_url}": album.get('url', '') if isinstance(album, dict) else '',
                "{play_count}": track.get('playcount', '0'),
                "{album_image}": track.get('image', [{'#text': ''}])[-1].get('#text', ''),
                "{username}": entry.username,
            }
            
            parsed_template = template
            for var, value in variables.items():
                parsed_template = parsed_template.replace(var, str(value))
            
            parser = EmbedParser(ctx)
            embed = parser.parse(parsed_template)
            await ctx.send(embed=embed)
        except ValueError as e:
            return await self.bot.deny(ctx, f"Invalid embed syntax: {str(e)}")
        except Exception as e:
            log.exception(f"Error previewing embed: {e}")
            return await self.bot.deny(ctx, f"Failed to preview embed: {str(e)}")

    @lastfm.command(name="whoknows", aliases=["wk"])
    async def lf_whoknows(self, ctx, *, artist: str = None):
        """Who listens to the current artist in this server"""
        try:
            entry = await LastfmData.find_one(LastfmData.user_id == ctx.author.id)
            if not entry:
                return await self.bot.warn(ctx, "Set your Last.fm username first.")
            
            if not artist:
                data = await self.fetch("user.getrecenttracks", {"user": entry.username, "limit": 1})
                if "error" in data:
                    return await self.bot.deny(ctx, "Failed to fetch Last.fm data.")
                tracks = data.get("recenttracks", {}).get("track", [])
                if not tracks:
                    return await self.bot.warn(ctx, "No recent tracks found.")
                track = tracks[0]
                artist = track.get('artist', {})
                artist = artist.get('#text', 'Unknown') if isinstance(artist, dict) else str(artist)
            
            listeners = []
            for member in ctx.guild.members:
                if member.bot:
                    continue
                user_entry = await LastfmData.find_one(LastfmData.user_id == member.id)
                if not user_entry:
                    continue
                
                user_data = await self.fetch("user.gettopartists", {"user": user_entry.username, "period": "7day"})
                if "error" in user_data:
                    continue
                
                artists = user_data.get("topartists", {}).get("artist", [])
                for art in artists:
                    art_name = art.get('name', '').lower()
                    if art_name == artist.lower():
                        playcount = art.get('playcount', '0')
                        listeners.append((member, int(playcount)))
                        break
            
            if not listeners:
                return await self.bot.warn(ctx, f"No one in this server listens to **{artist}**")
            
            listeners.sort(key=lambda x: x[1], reverse=True)
            
            description = ""
            for idx, (member, count) in enumerate(listeners, 1):
                description += f"{idx}. **{member}** - {count:,} scrobbles\n"
            
            embed = discord.Embed(title=f"Who Knows: {artist}", description=description, color=0x242429)
            await ctx.send(embed=embed)
        except Exception as e:
            log.exception(f"Error in whoknows: {e}")
            await self.bot.deny(ctx, f"An error occurred: {str(e)}")

    @lastfm.command(name="globalwhoknows", aliases=["gwk"])
    async def lf_globalwhoknows(self, ctx, *, artist: str = None):
        """Who listens to artist (Global)"""
        if not artist:
            return await ctx.send_help(ctx.command)
        
        try:
            await ctx.defer()
            
            all_entries = await LastfmData.find_many().to_list(None)
            if not all_entries:
                return await ctx.send(embed=discord.Embed(description="No Last.fm users found in database.", color=0xED4245))
            
            listeners = []
            
            for entry in all_entries:
                cache_key = f"{entry.user_id}:alltime"
                if cache_key not in self.lastfm_cache:
                    user_data = await self.fetch("user.gettopartists", {"user": entry.username, "period": "alltime", "limit": 50})
                    if "error" not in user_data:
                        self.lastfm_cache[cache_key] = user_data
                
                user_data = self.lastfm_cache.get(cache_key, {})
                if "error" in user_data or "topartists" not in user_data:
                    continue
                
                artists = user_data.get("topartists", {}).get("artist", [])
                for art in artists:
                    art_name = art.get('name', '').lower()
                    if art_name == artist.lower():
                        playcount = art.get('playcount', '0')
                        member = ctx.bot.get_user(entry.user_id) or f"User#{entry.user_id}"
                        listeners.append((member, int(playcount), entry.username))
                        break
            
            if not listeners:
                return await ctx.send(embed=discord.Embed(description=f"No one listens to **{artist}**", color=0xED4245))
            
            listeners.sort(key=lambda x: x[1], reverse=True)
            
            description = ""
            for idx, (member, count, username) in enumerate(listeners[:10], 1):
                description += f"{idx}. **{member}** ({username}) - {count:,} scrobbles\n"
            
            embed = discord.Embed(title=f"Global Who Knows: {artist}", description=description, color=0x242429)
            await ctx.send(embed=embed)
        except Exception as e:
            log.exception(f"Error in globalwhoknows: {e}")
            await self.bot.deny(ctx, f"An error occurred: {str(e)}")

    @lastfm.command(name="crowns", aliases=["cr"])
    async def lf_crowns(self, ctx, user: discord.Member = None):
        """Show your earned crowns"""
        try:
            user = user or ctx.author
            entry = await LastfmData.find_one(LastfmData.user_id == user.id)
            if not entry:
                return await self.bot.warn(ctx, f"{'You' if user == ctx.author else user.name} haven't set a Last.fm username.")
            
            data = await self.fetch("user.gettopartists", {"user": entry.username, "period": "7day", "limit": 10})
            if "error" in data:
                return await self.bot.deny(ctx, "Failed to fetch Last.fm data.")
            
            artists = data.get("topartists", {}).get("artist", [])
            if not artists:
                return await self.bot.warn(ctx, "No artists found.")
            
            crowns = []
            for art in artists:
                artist_name = art.get('name', 'Unknown')
                playcount = int(art.get('playcount', '0'))
                
                is_crown = True
                for member in ctx.guild.members:
                    if member.bot or member == user:
                        continue
                    member_entry = await LastfmData.find_one(LastfmData.user_id == member.id)
                    if not member_entry:
                        continue
                    
                    member_data = await self.fetch("user.gettopartists", {"user": member_entry.username, "period": "7day"})
                    if "error" in member_data:
                        continue
                    
                    member_artists = member_data.get("topartists", {}).get("artist", [])
                    for m_art in member_artists:
                        if m_art.get('name', '').lower() == artist_name.lower():
                            if int(m_art.get('playcount', '0')) > playcount:
                                is_crown = False
                                break
                    if not is_crown:
                        break
                
                if is_crown:
                    crowns.append((artist_name, playcount))
            
            if not crowns:
                return await ctx.send(embed=discord.Embed(description=f"**{user}** has no crowns yet. 👑", color=0x242429))
            
            description = ""
            for idx, (artist, count) in enumerate(crowns, 1):
                description += f"{idx}. **{artist}** - {count:,} scrobbles 👑\n"
            
            embed = discord.Embed(title=f"{user.name}'s Crowns", description=description, color=0x242429)
            embed.set_thumbnail(url=user.display_avatar.url)
            await ctx.send(embed=embed)
        except Exception as e:
            log.exception(f"Error in crowns: {e}")
            await self.bot.deny(ctx, f"An error occurred: {str(e)}")

    @lastfm.command(name="connect", aliases=["login"])
    async def lf_connect(self, ctx, username: str = None):
        """Link your Last.fm account to the bot"""
        if not username:
            embed = discord.Embed(
                title="How to Set Your Last.fm Username",
                description="Follow these steps to connect your Last.fm account to the bot:",
                color=0x242429
            )
            embed.add_field(
                name="Step 1: Create a Last.fm Account",
                value="If you don't have a Last.fm account yet, go to https://www.last.fm/join and sign up. It's free!",
                inline=False
            )
            embed.add_field(
                name="Step 2: Set Your Username via Bot",
                value=f"Use the command: `{ctx.clean_prefix}lastfm set <your_username>`\n\nExample: `{ctx.clean_prefix}lastfm set john_doe`",
                inline=False
            )
            embed.add_field(
                name="Step 3: Scrobble Tracks",
                value="Start listening to music on Spotify, Apple Music, or any scrobbling service connected to Last.fm.",
                inline=False
            )
            embed.add_field(
                name="What's a Scrobble?",
                value="A scrobble is a record of every song you listen to. Last.fm tracks these automatically!",
                inline=False
            )
            embed.set_footer(text="Once set, use commands like ,fm, ,recent, ,toptracks and more!")
            return await ctx.send(embed=embed)
        
        try:
            data = await self.fetch("user.getinfo", {"user": username})
            if "error" in data or "user" not in data:
                return await self.bot.warn(ctx, "Invalid Last.fm username.")
            await LastfmData.find_one(LastfmData.user_id == ctx.author.id).upsert(
                {"$set": {"username": username}},
                on_insert=LastfmData(user_id=ctx.author.id, username=username)
            )
            await self.bot.grant(ctx, f"Successfully connected to Last.fm account **{username}**")
        except Exception as e:
            log.exception("Database error")
            await self.bot.deny(ctx, "A database error occurred.")

    @lastfm.command(name="sync", aliases=["update", "refresh"])
    async def lf_sync(self, ctx):
        """Refresh your local Last.fm library"""
        try:
            entry = await LastfmData.find_one(LastfmData.user_id == ctx.author.id)
            if not entry:
                return await self.bot.warn(ctx, "Set your Last.fm username first.")
            
            cache_key = f"{ctx.author.id}:*"
            for key in list(self.lastfm_cache.keys()):
                if key.startswith(f"{ctx.author.id}:"):
                    del self.lastfm_cache[key]
            
            data = await self.fetch("user.getinfo", {"user": entry.username})
            if "error" in data:
                return await self.bot.deny(ctx, "Failed to sync with Last.fm.")
            
            await self.bot.grant(ctx, "Your Last.fm library has been refreshed.")
        except Exception as e:
            log.exception(f"Error syncing Last.fm: {e}")
            await self.bot.deny(ctx, f"Failed to sync: {str(e)}")

    @lastfm.command(name="compare", aliases=["vs", "match"])
    async def lf_compare(self, ctx, user: discord.Member = None):
        """Compare your music taste with another member"""
        if not user:
            return await ctx.send_help(ctx.command)
        
        try:
            entry1 = await LastfmData.find_one(LastfmData.user_id == ctx.author.id)
            entry2 = await LastfmData.find_one(LastfmData.user_id == user.id)
            
            if not entry1:
                return await self.bot.warn(ctx, "You haven't set a Last.fm username.")
            if not entry2:
                return await self.bot.warn(ctx, f"**{user}** hasn't set a Last.fm username.")
            
            data1 = await self.fetch("user.gettopartists", {"user": entry1.username, "limit": 50})
            data2 = await self.fetch("user.gettopartists", {"user": entry2.username, "limit": 50})
            
            if "error" in data1 or "error" in data2:
                return await self.bot.deny(ctx, "Failed to fetch Last.fm data.")
            
            artists1 = {a['name'].lower(): int(a['playcount']) for a in data1.get("topartists", {}).get("artist", [])}
            artists2 = {a['name'].lower(): int(a['playcount']) for a in data2.get("topartists", {}).get("artist", [])}
            
            common = set(artists1.keys()) & set(artists2.keys())
            
            if not common:
                return await ctx.send(embed=discord.Embed(description=f"**{ctx.author}** and **{user}** have no common artists.", color=0x242429))
            
            common_sorted = sorted(common, key=lambda x: artists1[x] + artists2[x], reverse=True)[:10]
            
            description = ""
            for idx, artist in enumerate(common_sorted, 1):
                description += f"{idx}. **{artist.title()}** - You: {artists1[artist]:,} | {user.name}: {artists2[artist]:,}\n"
            
            embed = discord.Embed(title=f"Music Taste Comparison", description=description, color=0x242429)
            embed.set_footer(text=f"{ctx.author.name} vs {user.name}")
            await ctx.send(embed=embed)
        except Exception as e:
            log.exception(f"Error comparing users: {e}")
            await self.bot.deny(ctx, f"Failed to compare: {str(e)}")

    @lastfm.command(name="wkalbum", aliases=["whoknowsalbum", "wka"])
    async def lf_wkalbum(self, ctx, *, album: str = None):
        """View the top listeners for an album"""
        try:
            entry = await LastfmData.find_one(LastfmData.user_id == ctx.author.id)
            if not entry:
                return await self.bot.warn(ctx, "Set your Last.fm username first.")
            
            if not album:
                data = await self.fetch("user.getrecenttracks", {"user": entry.username, "limit": 1})
                if "error" in data:
                    return await self.bot.deny(ctx, "Failed to fetch Last.fm data.")
                tracks = data.get("recenttracks", {}).get("track", [])
                if not tracks:
                    return await self.bot.warn(ctx, "No recent tracks found.")
                album = tracks[0].get('album', {})
                album = album.get('#text', 'Unknown') if isinstance(album, dict) else str(album)
            
            listeners = []
            for member in ctx.guild.members:
                if member.bot:
                    continue
                user_entry = await LastfmData.find_one(LastfmData.user_id == member.id)
                if not user_entry:
                    continue
                
                user_data = await self.fetch("user.gettopalbums", {"user": user_entry.username, "period": "7day"})
                if "error" in user_data:
                    continue
                
                albums = user_data.get("topalbums", {}).get("album", [])
                for alb in albums:
                    alb_name = alb.get('name', '').lower()
                    if alb_name == album.lower():
                        playcount = alb.get('playcount', '0')
                        listeners.append((member, int(playcount)))
                        break
            
            if not listeners:
                return await self.bot.warn(ctx, f"No one in this server listens to **{album}**")
            
            listeners.sort(key=lambda x: x[1], reverse=True)
            description = "\n".join([f"{idx}. **{member}** - {count:,} scrobbles" for idx, (member, count) in enumerate(listeners, 1)])
            embed = discord.Embed(title=f"Who Knows: {album}", description=description, color=0x242429)
            await ctx.send(embed=embed)
        except Exception as e:
            log.exception(f"Error in wkalbum: {e}")
            await self.bot.deny(ctx, f"An error occurred: {str(e)}")

    @lastfm.command(name="wktrack", aliases=["whoknowstrack", "wkt"])
    async def lf_wktrack(self, ctx, *, track: str = None):
        """View the top listeners for a track"""
        try:
            entry = await LastfmData.find_one(LastfmData.user_id == ctx.author.id)
            if not entry:
                return await self.bot.warn(ctx, "Set your Last.fm username first.")
            
            if not track:
                data = await self.fetch("user.getrecenttracks", {"user": entry.username, "limit": 1})
                if "error" in data:
                    return await self.bot.deny(ctx, "Failed to fetch Last.fm data.")
                tracks = data.get("recenttracks", {}).get("track", [])
                if not tracks:
                    return await self.bot.warn(ctx, "No recent tracks found.")
                track = tracks[0].get('name', 'Unknown')
            
            listeners = []
            for member in ctx.guild.members:
                if member.bot:
                    continue
                user_entry = await LastfmData.find_one(LastfmData.user_id == member.id)
                if not user_entry:
                    continue
                
                user_data = await self.fetch("user.gettoptracks", {"user": user_entry.username, "period": "7day"})
                if "error" in user_data:
                    continue
                
                tacks = user_data.get("toptracks", {}).get("track", [])
                for tck in tacks:
                    tck_name = tck.get('name', '').lower()
                    if tck_name == track.lower():
                        playcount = tck.get('playcount', '0')
                        listeners.append((member, int(playcount)))
                        break
            
            if not listeners:
                return await self.bot.warn(ctx, f"No one in this server listens to **{track}**")
            
            listeners.sort(key=lambda x: x[1], reverse=True)
            description = "\n".join([f"{idx}. **{member}** - {count:,} scrobbles" for idx, (member, count) in enumerate(listeners, 1)])
            embed = discord.Embed(title=f"Who Knows: {track}", description=description, color=0x242429)
            await ctx.send(embed=embed)
        except Exception as e:
            log.exception(f"Error in wktrack: {e}")
            await self.bot.deny(ctx, f"An error occurred: {str(e)}")

    @lastfm.command(name="globalwkalbum", aliases=["globalwka", "gwka"])
    async def lf_globalwkalbum(self, ctx, *, album: str = None):
        """View the top listeners for an album globally"""
        try:
            if not album:
                entry = await LastfmData.find_one(LastfmData.user_id == ctx.author.id)
                if not entry:
                    return await self.bot.warn(ctx, "Set your Last.fm username first or specify an album.")
                data = await self.fetch("user.getrecenttracks", {"user": entry.username, "limit": 1})
                if "error" in data:
                    return await self.bot.deny(ctx, "Failed to fetch Last.fm data.")
                tracks = data.get("recenttracks", {}).get("track", [])
                if not tracks:
                    return await self.bot.warn(ctx, "No recent tracks found.")
                album = tracks[0].get('album', {})
                album = album.get('#text', 'Unknown') if isinstance(album, dict) else str(album)
            
            await ctx.defer()
            
            all_entries = await LastfmData.find_many().to_list(None)
            if not all_entries:
                return await ctx.send(embed=discord.Embed(description="No Last.fm users found in database.", color=0xED4245))
            
            listeners = []
            for entry in all_entries:
                user_data = await self.fetch("user.gettopalbums", {"user": entry.username, "limit": 50})
                if "error" in user_data:
                    continue
                
                albums = user_data.get("topalbums", {}).get("album", [])
                for alb in albums:
                    if alb.get('name', '').lower() == album.lower():
                        playcount = alb.get('playcount', '0')
                        member = ctx.bot.get_user(entry.user_id) or f"User#{entry.user_id}"
                        listeners.append((member, int(playcount), entry.username))
                        break
            
            if not listeners:
                return await ctx.send(embed=discord.Embed(description=f"No one listens to **{album}**", color=0xED4245))
            
            listeners.sort(key=lambda x: x[1], reverse=True)
            description = "\n".join([f"{idx}. **{member}** ({username}) - {count:,} scrobbles" for idx, (member, count, username) in enumerate(listeners[:10], 1)])
            embed = discord.Embed(title=f"Global Who Knows: {album}", description=description, color=0x242429)
            await ctx.send(embed=embed)
        except Exception as e:
            log.exception(f"Error in globalwkalbum: {e}")
            await self.bot.deny(ctx, f"An error occurred: {str(e)}")

    @lastfm.command(name="globalwktrack", aliases=["globalwkt", "gwkt"])
    async def lf_globalwktrack(self, ctx, *, track: str = None):
        """View the top listeners for a track globally"""
        try:
            if not track:
                entry = await LastfmData.find_one(LastfmData.user_id == ctx.author.id)
                if not entry:
                    return await self.bot.warn(ctx, "Set your Last.fm username first or specify a track.")
                data = await self.fetch("user.getrecenttracks", {"user": entry.username, "limit": 1})
                if "error" in data:
                    return await self.bot.deny(ctx, "Failed to fetch Last.fm data.")
                tracks = data.get("recenttracks", {}).get("track", [])
                if not tracks:
                    return await self.bot.warn(ctx, "No recent tracks found.")
                track = tracks[0].get('name', 'Unknown')
            
            await ctx.defer()
            
            all_entries = await LastfmData.find_many().to_list(None)
            if not all_entries:
                return await ctx.send(embed=discord.Embed(description="No Last.fm users found in database.", color=0xED4245))
            
            listeners = []
            for entry in all_entries:
                user_data = await self.fetch("user.gettoptracks", {"user": entry.username, "limit": 50})
                if "error" in user_data:
                    continue
                
                tacks = user_data.get("toptracks", {}).get("track", [])
                for tck in tacks:
                    if tck.get('name', '').lower() == track.lower():
                        playcount = tck.get('playcount', '0')
                        member = ctx.bot.get_user(entry.user_id) or f"User#{entry.user_id}"
                        listeners.append((member, int(playcount), entry.username))
                        break
            
            if not listeners:
                return await ctx.send(embed=discord.Embed(description=f"No one listens to **{track}**", color=0xED4245))
            
            listeners.sort(key=lambda x: x[1], reverse=True)
            description = "\n".join([f"{idx}. **{member}** ({username}) - {count:,} scrobbles" for idx, (member, count, username) in enumerate(listeners[:10], 1)])
            embed = discord.Embed(title=f"Global Who Knows: {track}", description=description, color=0x242429)
            await ctx.send(embed=embed)
        except Exception as e:
            log.exception(f"Error in globalwktrack: {e}")
            await self.bot.deny(ctx, f"An error occurred: {str(e)}")

    @lastfm.group(name="plays", invoke_without_command=True)
    async def lf_plays(self, ctx):
        """View your play counts"""
        await ctx.send_help(ctx.command)

    @lf_plays.command(name="album", aliases=["al", "a"])
    async def lf_plays_album(self, ctx, *, album: str = None):
        """View how many plays you have for an album"""
        try:
            entry = await LastfmData.find_one(LastfmData.user_id == ctx.author.id)
            if not entry:
                return await self.bot.warn(ctx, "Set your Last.fm username first.")
            
            if not album:
                data = await self.fetch("user.getrecenttracks", {"user": entry.username, "limit": 1})
                if "error" in data:
                    return await self.bot.deny(ctx, "Failed to fetch Last.fm data.")
                tracks = data.get("recenttracks", {}).get("track", [])
                if not tracks:
                    return await self.bot.warn(ctx, "No recent tracks found.")
                album = tracks[0].get('album', {})
                album = album.get('#text', 'Unknown') if isinstance(album, dict) else str(album)
            
            data = await self.fetch("user.gettopalbums", {"user": entry.username, "limit": 100})
            if "error" in data:
                return await self.bot.deny(ctx, "Failed to fetch Last.fm data.")
            
            albums = data.get("topalbums", {}).get("album", [])
            for alb in albums:
                if alb.get('name', '').lower() == album.lower():
                    playcount = alb.get('playcount', '0')
                    embed = discord.Embed(title=f"Album Plays", description=f"**{alb.get('name')}** by **{alb.get('artist', {}).get('name', 'Unknown')}**\n\n{playcount:,} plays", color=0x242429)
                    if alb.get('image', [{}])[-1].get('#text'):
                        embed.set_thumbnail(url=alb.get('image', [{}])[-1].get('#text'))
                    return await ctx.send(embed=embed)
            
            await self.bot.warn(ctx, f"Album **{album}** not found in your top albums.")
        except Exception as e:
            log.exception(f"Error in plays_album: {e}")
            await self.bot.deny(ctx, f"An error occurred: {str(e)}")

    @lf_plays.command(name="track", aliases=["tr", "t"])
    async def lf_plays_track(self, ctx, *, track: str = None):
        """View how many plays you have for a track"""
        try:
            entry = await LastfmData.find_one(LastfmData.user_id == ctx.author.id)
            if not entry:
                return await self.bot.warn(ctx, "Set your Last.fm username first.")
            
            if not track:
                data = await self.fetch("user.getrecenttracks", {"user": entry.username, "limit": 1})
                if "error" in data:
                    return await self.bot.deny(ctx, "Failed to fetch Last.fm data.")
                tracks = data.get("recenttracks", {}).get("track", [])
                if not tracks:
                    return await self.bot.warn(ctx, "No recent tracks found.")
                track = tracks[0].get('name', 'Unknown')
            
            data = await self.fetch("user.gettoptracks", {"user": entry.username, "limit": 100})
            if "error" in data:
                return await self.bot.deny(ctx, "Failed to fetch Last.fm data.")
            
            tacks = data.get("toptracks", {}).get("track", [])
            for tck in tacks:
                if tck.get('name', '').lower() == track.lower():
                    playcount = tck.get('playcount', '0')
                    embed = discord.Embed(title=f"Track Plays", description=f"**{tck.get('name')}** by **{tck.get('artist', {}).get('name', 'Unknown')}**\n\n{playcount:,} plays", color=0x242429)
                    return await ctx.send(embed=embed)
            
            await self.bot.warn(ctx, f"Track **{track}** not found in your top tracks.")
        except Exception as e:
            log.exception(f"Error in plays_track: {e}")
            await self.bot.deny(ctx, f"An error occurred: {str(e)}")

    @lastfm.command(name="playsalbum", aliases=["playsal"])
    async def lf_playsalbum(self, ctx, *, album: str = None):
        """View how many plays you have for an album"""
        try:
            return await self.lf_plays_album(ctx, album=album)
        except Exception as e:
            log.exception(f"Error in playsalbum: {e}")
            await self.bot.deny(ctx, f"An error occurred: {str(e)}")

    @lastfm.command(name="playstrack", aliases=["playst"])
    async def lf_playstrack(self, ctx, *, track: str = None):
        """View how many plays you have for a track"""
        try:
            return await self.lf_plays_track(ctx, track=track)
        except Exception as e:
            log.exception(f"Error in playstrack: {e}")
            await self.bot.deny(ctx, f"An error occurred: {str(e)}")

    @lastfm.command(name="collage", aliases=["chart", "grid"])
    async def lf_collage(self, ctx):
        """Generate a collage of your top albums on Last.fm"""
        entry = await LastfmData.find_one(LastfmData.user_id == ctx.author.id)
        if not entry:
            return await self.bot.warn(ctx, "Set your Last.fm username first.")
        
        await ctx.defer()
        
        try:
            data = await self.fetch("user.gettopalbums", {"user": entry.username, "limit": 9})
            if "error" in data:
                return await self.bot.deny(ctx, "Failed to fetch Last.fm data.")
            
            albums = data.get("topalbums", {}).get("album", [])[:9]
            if not albums:
                return await self.bot.warn(ctx, "No albums found.")
            
            description = "**Top 9 Albums:**\n"
            for idx, album in enumerate(albums, 1):
                description += f"{idx}. **{album.get('name')}** by {album.get('artist', {}).get('name', 'Unknown')} ({album.get('playcount', '0')} plays)\n"
            
            embed = discord.Embed(title=f"{entry.username}'s Album Collage", description=description, color=0x242429)
            embed.set_footer(text="Album artwork would display here in a full implementation")
            await ctx.send(embed=embed)
        except Exception as e:
            log.exception(f"Error generating collage: {e}")
            await self.bot.deny(ctx, f"Failed to generate collage: {str(e)}")

    @lastfm.command(name="lyrics", aliases=["lyric", "lyr"])
    async def lf_lyrics(self, ctx):
        """View the lyrics for your Last.fm track"""
        entry = await LastfmData.find_one(LastfmData.user_id == ctx.author.id)
        if not entry:
            return await self.bot.warn(ctx, "Set your Last.fm username first.")
        
        await ctx.defer()
        
        try:
            data = await self.fetch("user.getrecenttracks", {"user": entry.username, "limit": 1})
            if "error" in data:
                return await self.bot.deny(ctx, "Failed to fetch Last.fm data.")
            
            tracks = data.get("recenttracks", {}).get("track", [])
            if not tracks:
                return await self.bot.warn(ctx, "No recent tracks found.")
            
            track = tracks[0]
            track_name = track.get('name', 'Unknown')
            artist = track.get('artist', {})
            artist_name = artist.get('#text', 'Unknown') if isinstance(artist, dict) else str(artist)
            
            genius_url = "https://api.genius.com/search"
            headers = {"Authorization": f"Bearer {self.genius_access_token}", "User-Agent": "Mozilla/5.0"}
            params = {"q": f"{track_name} {artist_name}"}
            
            async with aiohttp.ClientSession() as session:
                async with session.get(genius_url, headers=headers, params=params) as resp:
                    if resp.status != 200:
                        return await self.bot.deny(ctx, f"Genius API error: {resp.status}")
                    
                    content_type = resp.headers.get('content-type', '')
                    if 'application/json' not in content_type:
                        return await self.bot.deny(ctx, "Genius API returned non-JSON response. The API token may be invalid or expired.")
                    
                    try:
                        genius_data = await resp.json()
                    except Exception as e:
                        log.error(f"Failed to parse Genius response: {e}")
                        return await self.bot.deny(ctx, "Failed to parse Genius API response.")
            
            hits = genius_data.get("response", {}).get("hits", [])
            if not hits:
                return await self.bot.warn(ctx, f"No lyrics found for **{track_name}** by **{artist_name}** on Genius.")
            
            song_data = hits[0].get("result", {})
            song_url = song_data.get("url", "")
            song_title = song_data.get("title", "Unknown")
            song_artist = song_data.get("primary_artist", {}).get("name", "Unknown")
            
            if not song_url:
                return await self.bot.warn(ctx, "Could not find song URL on Genius.")
            
            async with aiohttp.ClientSession() as session:
                async with session.get(song_url) as resp:
                    if resp.status != 200:
                        return await self.bot.deny(ctx, "Failed to fetch lyrics.")
                    
                    page_content = await resp.text()
            
            import re
            
            lyrics_match = re.search(r'<div data-lyrics-container="true"[^>]*>(.*?)(?=<div data-lyrics-container|$)', page_content, re.DOTALL)
            
            if not lyrics_match:
                lyrics_match = re.search(r'<div class="Lyrics__Container[^"]*"[^>]*>(.*?)(?=<div class="Lyrics__Container|$)', page_content, re.DOTALL)
            
            if not lyrics_match:
                lyrics_match = re.search(r'<h2[^>]*>Lyrics<\/h2>(.*?)(?=<h2|$)', page_content, re.DOTALL)
            
            if not lyrics_match:
                script_match = re.search(r'"lyrics":\s*"([^"]*(?:\\.[^"]*)*)"', page_content)
                if script_match:
                    lyrics_text = script_match.group(1).replace('\\n', '\n')
                else:
                    return await self.bot.warn(ctx, "Could not parse lyrics from Genius. The page structure may have changed.")
            else:
                lyrics_html = lyrics_match.group(1)
                lyrics_html = re.sub(r'<script[^>]*>.*?</script>', '', lyrics_html, flags=re.DOTALL)
                lyrics_html = re.sub(r'<style[^>]*>.*?</style>', '', lyrics_html, flags=re.DOTALL)
                lyrics_html = re.sub(r'<br\s*/?>', '\n', lyrics_html)
                lyrics_text = re.sub(r'<[^>]+>', '', lyrics_html)
                lyrics_text = re.sub(r'\n\s*\n', '\n', lyrics_text)
                lyrics_text = lyrics_text.strip()
            
            if not lyrics_text or len(lyrics_text) < 10:
                return await self.bot.warn(ctx, "Could not extract meaningful lyrics from the page.")
            
            max_chars = 4000
            if len(lyrics_text) > max_chars:
                lyrics_text = lyrics_text[:max_chars] + "\n\n[Lyrics truncated - view full lyrics on Genius]"
            
            embed = discord.Embed(
                title=f"Lyrics: {song_title}",
                description=f"by **{song_artist}**",
                color=0x242429
            )
            embed.add_field(name="Lyrics", value=lyrics_text if lyrics_text else "Lyrics not available", inline=False)
            embed.add_field(name="Source", value=f"[View on Genius]({song_url})", inline=False)
            
            await ctx.send(embed=embed)
        except Exception as e:
            log.exception(f"Error fetching lyrics: {e}")
            await self.bot.deny(ctx, f"Failed to fetch lyrics: {str(e)}")

    @lastfm.command(name="milestone", aliases=["ms"])
    async def lf_milestone(self, ctx):
        """View what your scrobble for a milestone was"""
        try:
            entry = await LastfmData.find_one(LastfmData.user_id == ctx.author.id)
            if not entry:
                return await self.bot.warn(ctx, "Set your Last.fm username first.")
            
            data = await self.fetch("user.getinfo", {"user": entry.username})
            if "error" in data:
                return await self.bot.deny(ctx, "Failed to fetch Last.fm data.")
            
            user_data = data.get("user", {})
            playcount = int(user_data.get('playcount', '0'))
            
            if playcount >= 100000:
                milestone = (playcount // 10000) * 10000
            elif playcount >= 10000:
                milestone = (playcount // 1000) * 1000
            else:
                milestone = (playcount // 100) * 100
            
            embed = discord.Embed(title="Scrobble Milestone", description=f"Your next milestone is at **{milestone + (10000 if milestone >= 100000 else 1000 if milestone >= 10000 else 100):,}** scrobbles.\n\nCurrent: **{playcount:,}** scrobbles", color=0x242429)
            await ctx.send(embed=embed)
        except Exception as e:
            log.exception(f"Error fetching milestone: {e}")
            await self.bot.deny(ctx, f"Failed to fetch milestone: {str(e)}")

    @lastfm.command(name="pace")
    async def lf_pace(self, ctx):
        """View an estimated date for your next milestone"""
        try:
            entry = await LastfmData.find_one(LastfmData.user_id == ctx.author.id)
            if not entry:
                return await self.bot.warn(ctx, "Set your Last.fm username first.")
            
            data = await self.fetch("user.getinfo", {"user": entry.username})
            if "error" in data:
                return await self.bot.deny(ctx, "Failed to fetch Last.fm data.")
            
            user_data = data.get("user", {})
            playcount = int(user_data.get('playcount', '0'))
            
            registered = user_data.get('registered', {})
            if isinstance(registered, dict):
                try:
                    from datetime import datetime as dt
                    reg_timestamp = int(registered.get('#text', '0'))
                    reg_date = dt.fromtimestamp(reg_timestamp)
                    days_active = (dt.utcnow() - reg_date).days
                    daily_scrobbles = playcount / max(days_active, 1)
                except:
                    daily_scrobbles = 5
            else:
                daily_scrobbles = 5
            
            if playcount >= 100000:
                next_milestone = (playcount // 10000 + 1) * 10000
            elif playcount >= 10000:
                next_milestone = (playcount // 1000 + 1) * 1000
            else:
                next_milestone = (playcount // 100 + 1) * 100
            
            scrobbles_needed = next_milestone - playcount
            days_until = scrobbles_needed / max(daily_scrobbles, 1)
            
            from datetime import timedelta as td
            estimated_date = dt.utcnow() + td(days=days_until)
            
            embed = discord.Embed(
                title="Scrobble Pace",
                description=f"At **{daily_scrobbles:.1f}** scrobbles/day\n\nYou'll reach **{next_milestone:,}** scrobbles on approximately **{estimated_date.strftime('%B %d, %Y')}**",
                color=0x242429
            )
            await ctx.send(embed=embed)
        except Exception as e:
            log.exception(f"Error calculating pace: {e}")
            await self.bot.deny(ctx, f"Failed to calculate pace: {str(e)}")

    @lastfm.command(name="pixelate", aliases=["pixel", "jumble"])
    async def lf_pixelate(self, ctx):
        """Try to guess the pixelated album cover"""
        try:
            entry = await LastfmData.find_one(LastfmData.user_id == ctx.author.id)
            if not entry:
                return await self.bot.warn(ctx, "Set your Last.fm username first.")
            
            await ctx.defer()
            
            from PIL import Image
            import random
            import io
            
            data = await self.fetch("user.gettopalbums", {"user": entry.username, "limit": 50})
            if "error" in data:
                return await self.bot.deny(ctx, "Failed to fetch Last.fm data.")
            
            albums = data.get("topalbums", {}).get("album", [])
            if not albums:
                return await self.bot.warn(ctx, "No albums found.")
            
            album = random.choice(albums)
            image_url = album.get('image', [{}])[-1].get('#text', '')
            
            if not image_url:
                return await self.bot.warn(ctx, "Selected album has no cover art.")
            
            async with aiohttp.ClientSession() as session:
                async with session.get(image_url) as resp:
                    if resp.status != 200:
                        return await self.bot.deny(ctx, "Failed to download album cover.")
                    image_data = await resp.read()
            
            img = Image.open(io.BytesIO(image_data))
            img = img.convert('RGB')
            
            pixel_size = 10
            small = img.resize((img.width // pixel_size, img.height // pixel_size), Image.Resampling.LANCZOS)
            pixelated = small.resize((img.width, img.height), Image.Resampling.NEAREST)
            
            buffer = io.BytesIO()
            pixelated.save(buffer, format='PNG')
            buffer.seek(0)
            
            embed = discord.Embed(
                title="Album Guesser",
                description=f"Can you guess the album?\n\nHint: It's from your top {len(albums)} albums!",
                color=0x242429
            )
            embed.set_image(url="attachment://pixelated.png")
            embed.set_footer(text=f"Answer: {album.get('name')} by {album.get('artist', {}).get('name', 'Unknown')}")
            
            await ctx.send(embed=embed, file=discord.File(buffer, filename="pixelated.png"))
        except Exception as e:
            log.exception(f"Error in pixelate: {e}")
            await self.bot.deny(ctx, f"Failed to pixelate album: {str(e)}")

    @lastfm.group(name="mode", invoke_without_command=True)
    async def lf_mode(self, ctx):
        """Manage your embed style settings"""
        await ctx.send_help(ctx.command)

    @lf_mode.command(name="remove", aliases=["delete", "reset"])
    async def lf_mode_remove(self, ctx):
        """Remove the custom embed style for the now playing command"""
        try:
            entry = await LastfmData.find_one(LastfmData.user_id == ctx.author.id)
            if not entry:
                return await self.bot.warn(ctx, "You don't have a Last.fm username set.")
            
            if not entry.custom_embed_template:
                return await self.bot.neutral(ctx, "You don't have a custom embed style set.")
            
            entry.custom_embed_template = None
            await entry.save()
            await self.bot.grant(ctx, "Custom embed style removed.")
        except Exception as e:
            log.exception(f"Error removing mode: {e}")
            await self.bot.deny(ctx, f"Failed to remove mode: {str(e)}")

    @lf_mode.command(name="view", aliases=["show", "display"])
    async def lf_mode_view(self, ctx):
        """View your current embed style for the now playing command"""
        try:
            entry = await LastfmData.find_one(LastfmData.user_id == ctx.author.id)
            if not entry:
                return await self.bot.warn(ctx, "You don't have a Last.fm username set.")
            
            if not entry.custom_embed_template:
                return await self.bot.neutral(ctx, "You're using the default embed style.")
            
            embed = discord.Embed(
                title="Your Embed Style",
                description=f"```\n{entry.custom_embed_template}\n```",
                color=0x242429
            )
            await ctx.send(embed=embed)
        except Exception as e:
            log.exception(f"Error viewing mode: {e}")
            await self.bot.deny(ctx, f"Failed to view mode: {str(e)}")

    @lastfm.group(name="reactions", invoke_without_command=True)
    async def lf_reactions(self, ctx):
        """Manage voting reactions"""
        await ctx.send_help(ctx.command)

    @lf_reactions.command(name="remove", aliases=["none", "delete"])
    async def lf_reactions_remove(self, ctx):
        """Remove the voting reactions for the now playing command"""
        try:
            await self.bot.neutral(ctx, "Reactions feature would be implemented in a full version.")
        except Exception as e:
            log.exception(f"Error in reactions_remove: {e}")
            await self.bot.deny(ctx, f"An error occurred: {str(e)}")

    @lf_reactions.command(name="reset", aliases=["clear", "default"])
    async def lf_reactions_reset(self, ctx):
        """Reset the custom voting reactions for the now playing command"""
        try:
            await self.bot.neutral(ctx, "Reactions feature would be implemented in a full version.")
        except Exception as e:
            log.exception(f"Error in reactions_reset: {e}")
            await self.bot.deny(ctx, f"An error occurred: {str(e)}")

    @lastfm.group(name="command", invoke_without_command=True)
    async def lf_command(self, ctx):
        """Manage custom now playing commands"""
        await ctx.send_help(ctx.command)

    @lf_command.command(name="remove", aliases=["delete", "reset"])
    async def lf_command_remove(self, ctx):
        """Remove the custom command for the now playing command"""
        try:
            await self.bot.neutral(ctx, "Custom commands feature would be implemented in a full version.")
        except Exception as e:
            log.exception(f"Error in command_remove: {e}")
            await self.bot.deny(ctx, f"An error occurred: {str(e)}")

async def setup(bot):
    await bot.add_cog(Lastfm(bot))