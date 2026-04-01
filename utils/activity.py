import discord
from discord.ext import commands, tasks
from datetime import datetime

class Activity(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.update_tracker_embeds.start()

    async def cog_unload(self):
        self.update_tracker_embeds.cancel()

    @tasks.loop(minutes=5)
    async def update_tracker_embeds(self):
        """Update all tracker embeds with current activity"""
        events_cog = self.bot.get_cog('Events')
        if not events_cog or not hasattr(events_cog, 'tracker_channels'):
            return
        
        for message_id, tracker_info in list(events_cog.tracker_channels.items()):
            try:
                guild_id = tracker_info['guild_id']
                channel_id = tracker_info['channel_id']
                
                guild = self.bot.get_guild(guild_id)
                if not guild:
                    continue
                
                channel = guild.get_channel(channel_id)
                if not channel:
                    continue
                
                try:
                    message = await channel.fetch_message(message_id)
                except discord.NotFound:
                    del events_cog.tracker_channels[message_id]
                    continue
                
                # Calculate stats
                guild_activity = events_cog.activity_data[guild_id]
                total_messages = sum(activity.get('messages', 0) for activity in guild_activity.values())
                active_users = len([a for a in guild_activity.values() if a.get('messages', 0) > 0])
                
                # Top 5 chatters (by messages)
                top_chatters = sorted(
                    guild_activity.items(),
                    key=lambda x: x[1].get('messages', 0),
                    reverse=True
                )[:5]
                
                chatters_text = "\n".join([
                    f"{idx}. <@{user_id}> - {activity.get('messages', 0):,} messages"
                    for idx, (user_id, activity) in enumerate(top_chatters, 1)
                ]) if top_chatters else "No activity yet"
                
                # Top 5 voice activity
                top_voice = sorted(
                    guild_activity.items(),
                    key=lambda x: x[1].get('voice_time', 0),
                    reverse=True
                )[:5]
                
                voice_text = "\n".join([
                    self._format_voice_time(user_id, activity.get('voice_time', 0))
                    for idx, (user_id, activity) in enumerate(top_voice, 1)
                ]) if top_voice else "No activity yet"
                
                embed = discord.Embed(
                    title="📊 Activity Leaderboard",
                    description=f"Tracking activity for **{guild.name}**",
                    color=0x242429
                )
                embed.add_field(name="📈 Overview", value=f"**Messages:** {total_messages:,}\n**Active Users:** {active_users}", inline=True)
                embed.add_field(name="🕐 Last Updated", value=f"<t:{int(datetime.utcnow().timestamp())}:t>", inline=True)
                embed.add_field(name="💬 Top 5 Chatters", value=chatters_text, inline=False)
                embed.add_field(name="🎙️ Top 5 Voice Activity", value=voice_text, inline=False)
                embed.set_footer(text="Updates every 5 minutes")
                
                await message.edit(embed=embed)
            except Exception as e:
                print(f"Error updating tracker: {e}")

    def _format_voice_time(self, user_id: int, seconds: int) -> str:
        """Format voice time in a readable way"""
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60
        
        if hours > 0:
            time_str = f"{hours}h {minutes}m"
        elif minutes > 0:
            time_str = f"{minutes}m {secs}s"
        else:
            time_str = f"{secs}s"
        
        return f"🎤 <@{user_id}> - {time_str}"

    @update_tracker_embeds.before_loop
    async def before_update_tracker(self):
        """Wait until bot is ready"""
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(Activity(bot))
