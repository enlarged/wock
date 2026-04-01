import discord
from discord.ext import commands
import aiohttp
import asyncio
import random
from datetime import datetime
from models.entertainment import EntertainmentInteraction, MuhaProfile
from models.garden import GardenData
from models.dungeon import DungeonData
from models.configs import UserConfig

MUHA_FLAVORS = [
    "mint", "mango", "berry", "grape", "watermelon", "raspberry", "strawberry", "banana", "apple", "peach",
    "blueberry", "lemon", "orange", "pineapple", "melon", "kiwi", "pomegranate", "coconut", "cherry", "vanilla",
    "cotton candy", "bubblegum", "caramel", "coffee", "chocolate", "peppermint", "menthol", "tobacco", "honey",
    "cinnamon", "cream", "cake", "cookie", "donut", "ice cream", "yogurt", "soda", "energy drink", "alcohol",
    "candy", "gummy", "sour", "spicy", "salty", "sweet"
]

CROPS = {
    "sunflower": {"price": 50,   "sell": 120,  "time": 3600,   "emoji": "🌻", "type": "Flower"},
    "tulip":     {"price": 75,   "sell": 180,  "time": 7200,   "emoji": "🌷", "type": "Flower"},
    "rose":      {"price": 100,  "sell": 250,  "time": 10800,  "emoji": "🌹", "type": "Flower"},
    "hibiscus":  {"price": 150,  "sell": 400,  "time": 14400,  "emoji": "🌺", "type": "Flower"},
    "cherry":    {"price": 300,  "sell": 850,  "time": 28800,  "emoji": "🌸", "type": "Flower"},
    "carrot":    {"price": 30,   "sell": 70,   "time": 1800,   "emoji": "🥕", "type": "Veggie"},
    "corn":      {"price": 60,   "sell": 140,  "time": 3600,   "emoji": "🌽", "type": "Veggie"},
    "broccoli":  {"price": 90,   "sell": 220,  "time": 5400,   "emoji": "🥦", "type": "Veggie"},
    "eggplant":  {"price": 120,  "sell": 310,  "time": 7200,   "emoji": "🍆", "type": "Veggie"},
    "cactus":    {"price": 500,  "sell": 1500, "time": 43200,  "emoji": "🌵", "type": "Exotic"},
    "mushroom":  {"price": 800,  "sell": 2500, "time": 86400,  "emoji": "🍄", "type": "Exotic"},
    "herb":      {"price": 1000, "sell": 3500, "time": 172800, "emoji": "🌿", "type": "Exotic"},
}

def _load_dictionary() -> set:
    paths = ["/root/wock/utils/words.txt", "/usr/share/dict/words"]
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                words = {w.strip().lower() for w in f if len(w.strip()) >= 3}
            if words:
                print(f"[BLACKTEA] Dictionary loaded ({len(words)} words) from {path}")
                return words
        except Exception:
            continue
    print("[BLACKTEA] No dictionary found.")
    return set()

BLACKTEA_DICTIONARY = _load_dictionary()

MONSTERS = [
    {"name": "Slime",    "hp": 30,  "atk": 5,  "xp": 20,   "loot": 50},
    {"name": "Goblin",   "hp": 50,  "atk": 12, "xp": 50,   "loot": 150},
    {"name": "Skeleton", "hp": 70,  "atk": 18, "xp": 100,  "loot": 300},
    {"name": "Orc",      "hp": 120, "atk": 25, "xp": 250,  "loot": 800},
    {"name": "Dragon",   "hp": 500, "atk": 60, "xp": 2000, "loot": 10000},
]


class Entertainment(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.nekos_api = "https://nekos.life/api/v2"
        self.blacktea_games: set = set()
        self.dungeon_cooldowns: dict = {}

    async def cog_command_error(self, ctx, error):
        if isinstance(error, commands.MemberNotFound):
            return await self.bot.warn(ctx, "I couldn't find that member.")
        if isinstance(error, commands.MissingRequiredArgument):
            return await ctx.send_help(ctx.command)

    async def get_nekos_image(self, action: str) -> str:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.nekos_api}/img/{action}") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("url")
        except Exception:
            pass
        return None

    async def track_interaction(self, user1_id: int, user2_id: int, action: str) -> int:
        try:
            user_a = min(user1_id, user2_id)
            user_b = max(user1_id, user2_id)
            existing = await EntertainmentInteraction.find_one({
                "user1_id": user_a,
                "user2_id": user_b,
                "interaction_type": action
            })
            if existing:
                await existing.update({"$inc": {"count": 1}})
                return existing.count + 1
            else:
                new_interaction = EntertainmentInteraction(
                    user1_id=user_a,
                    user2_id=user_b,
                    interaction_type=action,
                    count=1
                )
                await new_interaction.insert()
                return 1
        except Exception as e:
            print(f"Entertainment tracking error: {e}")
            return 1

    async def entertainment_action(self, ctx, action: str, member: discord.Member = None):
        if member and member.id == ctx.author.id:
            return await self.bot.deny(ctx, f"You can't {action} yourself!")
        async with ctx.typing():
            image_url = await self.get_nekos_image(action)
            if not image_url:
                return await self.bot.deny(ctx, f"Failed to fetch {action} image from API.")
            embed = discord.Embed(color=0x242429)
            embed.set_image(url=image_url)
            if member:
                count = await self.track_interaction(ctx.author.id, member.id, action)
                embed.description = f"{ctx.author.mention} {action}ed {member.mention}!"
                embed.set_footer(text=f"that's {count} {action}{'s' if count != 1 else ''} shared between you two this far!")
            else:
                embed.description = f"{ctx.author.mention} {action}ed!"
            await ctx.send(embed=embed)

    async def get_or_create_muha_profile(self, user_id: int) -> MuhaProfile:
        profile = await MuhaProfile.find_one(MuhaProfile.user_id == user_id)
        if profile:
            return profile
        profile = MuhaProfile(user_id=user_id)
        await profile.insert()
        return profile

    @commands.group(name="muha", aliases=["za"], invoke_without_command=True)
    async def muha(self, ctx):
        """Hit the muha"""
        profile = await self.get_or_create_muha_profile(ctx.author.id)
        profile.hits += 1
        await profile.save()
        embed = discord.Embed(color=0x736d66)
        embed.description = (
            f"<:chicken:1471957914049577220> {ctx.author.mention} has hit their "
            f"**{profile.flavor}** flavored muha **{profile.hits}** times"
        )
        await ctx.send(embed=embed)

    @muha.command(name="flavors")
    async def muha_flavors(self, ctx):
        """List all available muha flavors"""
        embed = discord.Embed(
            title="Available Muha Flavors",
            color=self.bot.config.get("color", 0x242429),
            description=", ".join(f"`{flavor}`" for flavor in MUHA_FLAVORS)
        )
        await ctx.send(embed=embed)

    @muha.command(name="flavor")
    async def muha_flavor(self, ctx, *, new_flavor: str = None):
        """Set your muha flavor"""
        if not new_flavor:
            return await ctx.send_help(ctx.command)
        new_flavor = new_flavor.lower()
        if new_flavor not in MUHA_FLAVORS:
            return await self.bot.warn(ctx, "Invalid flavor. Use `muha flavors` to see the list.")
        profile = await self.get_or_create_muha_profile(ctx.author.id)
        profile.flavor = new_flavor
        await profile.save()
        await self.bot.grant(ctx, f"Your muha flavor has been set to **{new_flavor}**")

    @muha.command(name="leaderboard", aliases=["lb"])
    async def muha_leaderboard(self, ctx):
        """View the muha leaderboard"""
        top_muhars = await MuhaProfile.find(MuhaProfile.hits > 0).sort([("hits", -1)]).limit(10).to_list()
        leaderboard = "\n".join(
            f"**{index}.** <@{entry.user_id}> — `{entry.hits}` hits"
            for index, entry in enumerate(top_muhars, start=1)
        )
        embed = discord.Embed(
            title="Feind Leaderboard",
            color=self.bot.config.get("color", 0x242429),
            description=leaderboard or "No hits recorded yet."
        )
        await ctx.send(embed=embed)

    @muha.command(name="count")
    async def muha_count(self, ctx, user: discord.User = None):
        """View a user's muha hit count"""
        target = user or ctx.author
        profile = await MuhaProfile.find_one(MuhaProfile.user_id == target.id)
        if not profile or profile.hits <= 0:
            if target.id == ctx.author.id:
                return await self.bot.warn(ctx, "You haven't hit a muha yet.")
            return await self.bot.warn(ctx, "That user hasn't hit a muha yet.")
        embed = discord.Embed(color=0x736d66)
        embed.description = (
            f"{target.mention} has hit their **{profile.flavor}** flavored muha "
            f"**{profile.hits}** times"
        )
        await ctx.send(embed=embed)

    @commands.command(name="blacktea", aliases=["bt"])
    @commands.guild_only()
    async def blacktea(self, ctx):
        """A fast-paced elimination word game"""
        if not BLACKTEA_DICTIONARY:
            return await self.bot.warn(ctx, "Dictionary failed to load.")
        if ctx.channel.id in self.blacktea_games:
            return await self.bot.warn(ctx, "A game is already running in this channel.")

        self.blacktea_games.add(ctx.channel.id)

        try:
            players = {}

            join_embed = discord.Embed(
                title="☕ Black Tea - Join the Game!",
                description=(
                    "React with ☕ to join.\n"
                    "You start with **3 lives**.\n"
                    "Solve word puzzles to stay alive.\n\n"
                    "**20 seconds to join!**"
                ),
                color=0x000001
            )
            join_msg = await ctx.send(embed=join_embed)
            await join_msg.add_reaction("☕")

            def join_check(reaction, user):
                return (
                    reaction.message.id == join_msg.id
                    and str(reaction.emoji) == "☕"
                    and not user.bot
                )

            deadline = asyncio.get_event_loop().time() + 20
            while True:
                remaining_time = deadline - asyncio.get_event_loop().time()
                if remaining_time <= 0:
                    break
                try:
                    reaction, user = await self.bot.wait_for("reaction_add", check=join_check, timeout=remaining_time)
                    if user.id not in players:
                        players[user.id] = {"user": user, "lives": 3, "eliminated": False}
                except asyncio.TimeoutError:
                    break

            if len(players) < 2:
                return await self.bot.warn(ctx, "Not enough players joined. Need at least 2.")

            await self.bot.grant(ctx, f"Game starting with **{len(players)} players**!")

            words_list = list(BLACKTEA_DICTIONARY)
            round_num = 1
            game_active = True

            while game_active:
                alive = [p for p in players.values() if not p["eliminated"]]
                if len(alive) <= 1:
                    break

                await ctx.send(f"🔥 **Round {round_num}**")

                for player_data in list(alive):
                    if player_data["eliminated"]:
                        continue

                    random_word = random.choice(words_list)
                    while len(random_word) < 3:
                        random_word = random.choice(words_list)

                    start = random.randint(0, len(random_word) - 3)
                    letters = random_word[start:start + 3]

                    await ctx.send(
                        f"👤 {player_data['user'].mention} | ❤️ {player_data['lives']}\n"
                        f"Letters: **{letters.upper()}** (8s)"
                    )

                    solved = False

                    def msg_check(m):
                        return m.author.id == player_data["user"].id and m.channel.id == ctx.channel.id

                    turn_deadline = asyncio.get_event_loop().time() + 8
                    while True:
                        turn_remaining = turn_deadline - asyncio.get_event_loop().time()
                        if turn_remaining <= 0:
                            break
                        try:
                            msg = await self.bot.wait_for("message", check=msg_check, timeout=turn_remaining)
                            inp = msg.content.lower().strip()
                            if letters in inp and inp in BLACKTEA_DICTIONARY:
                                solved = True
                                await msg.add_reaction("✅")
                                break
                            else:
                                await msg.add_reaction("❌")
                        except asyncio.TimeoutError:
                            break

                    if not solved:
                        player_data["lives"] -= 1
                        await ctx.send(
                            f"❌ {player_data['user'].mention} failed! "
                            f"Lives left: ❤️ {player_data['lives']}"
                        )
                        if player_data["lives"] <= 0:
                            player_data["eliminated"] = True
                            await ctx.send(f"💀 **{player_data['user'].display_name} has been eliminated!**")

                    await asyncio.sleep(0.7)

                    if len([p for p in players.values() if not p["eliminated"]]) <= 1:
                        game_active = False
                        break

                round_num += 1

            remaining = [p for p in players.values() if not p["eliminated"]]
            if len(remaining) == 1:
                winner = remaining[0]
                await ctx.send(f"🏆 **{winner['user'].display_name} WINS BLACK TEA!**")
            else:
                await self.bot.warn(ctx, "Everyone has been eliminated! No winners this time.")

            await ctx.send("🏁 **Game Over.**")

        finally:
            self.blacktea_games.discard(ctx.channel.id)

    @commands.command()
    async def hug(self, ctx, member: discord.Member = None):
        """Hug someone"""
        await self.entertainment_action(ctx, "hug", member)

    @commands.command()
    async def kiss(self, ctx, member: discord.Member = None):
        """Kiss someone"""
        await self.entertainment_action(ctx, "kiss", member)

    @commands.command()
    async def cuddle(self, ctx, member: discord.Member = None):
        """Cuddle someone"""
        await self.entertainment_action(ctx, "cuddle", member)

    @commands.command()
    async def pat(self, ctx, member: discord.Member = None):
        """Pat someone"""
        await self.entertainment_action(ctx, "pat", member)

    @commands.command()
    async def tickle(self, ctx, member: discord.Member = None):
        """Tickle someone"""
        await self.entertainment_action(ctx, "tickle", member)

    @commands.command()
    async def punch(self, ctx, member: discord.Member = None):
        """Punch someone"""
        await self.entertainment_action(ctx, "punch", member)

    @commands.command()
    async def slap(self, ctx, member: discord.Member = None):
        """Slap someone"""
        await self.entertainment_action(ctx, "slap", member)

    @commands.command()
    async def bite(self, ctx, member: discord.Member = None):
        """Bite someone playfully"""
        await self.entertainment_action(ctx, "bite", member)

    @commands.command()
    async def lick(self, ctx, member: discord.Member = None):
        """Lick someone playfully"""
        await self.entertainment_action(ctx, "lick", member)

    @commands.command()
    async def nuzzle(self, ctx, member: discord.Member = None):
        """Nuzzle someone"""
        await self.entertainment_action(ctx, "nuzzle", member)

    @commands.command()
    async def poke(self, ctx, member: discord.Member = None):
        """Poke someone playfully"""
        await self.entertainment_action(ctx, "poke", member)

    @commands.command()
    async def handhold(self, ctx, member: discord.Member = None):
        """Hold hands with someone"""
        await self.entertainment_action(ctx, "handhold", member)

    @commands.command()
    async def brofist(self, ctx, member: discord.Member = None):
        """Give someone a friendly fist bump"""
        await self.entertainment_action(ctx, "brofist", member)

    @commands.command()
    async def clap(self, ctx, member: discord.Member = None):
        """Applaud someone"""
        await self.entertainment_action(ctx, "clap", member)

    @commands.command()
    async def highfive(self, ctx, member: discord.Member = None):
        """High five someone"""
        await self.entertainment_action(ctx, "highfive", member)

    @commands.command()
    async def stare(self, ctx, member: discord.Member = None):
        """Stare at someone"""
        await self.entertainment_action(ctx, "stare", member)

    @commands.group(name="garden", invoke_without_command=True)
    @commands.guild_only()
    async def garden(self, ctx):
        """An advanced farming and floral simulation"""
        await ctx.send_help(ctx.command)

    @garden.command(name="shop")
    async def garden_shop(self, ctx):
        """View the seed catalog"""
        flowers = "".join(f"{v['emoji']} **{k}**: ${v['price']} *(Sells: ${v['sell']})*\n" for k, v in CROPS.items() if v["type"] == "Flower")
        veggies = "".join(f"{v['emoji']} **{k}**: ${v['price']} *(Sells: ${v['sell']})*\n" for k, v in CROPS.items() if v["type"] == "Veggie")
        exotics = "".join(f"{v['emoji']} **{k}**: ${v['price']} *(Sells: ${v['sell']})*\n" for k, v in CROPS.items() if v["type"] == "Exotic")

        embed = discord.Embed(
            title="👨\u200d🌾 The Great Seed Catalog",
            description="Browse our seeds. Buy low, sell high!",
            color=0xADFF2F
        )
        embed.add_field(name="🌸 Flowers", value=flowers, inline=False)
        embed.add_field(name="🥦 Vegetables", value=veggies, inline=False)
        embed.add_field(name="💎 Exotics", value=exotics, inline=False)
        await ctx.send(embed=embed)

    @garden.command(name="buy")
    async def garden_buy(self, ctx, crop: str = None, amount: int = 1):
        """Buy seeds"""
        if not crop:
            return await ctx.send_help(ctx.command)
        crop = crop.lower()
        if crop not in CROPS:
            return await self.bot.warn(ctx, "We don't sell those seeds here.")

        amount = max(1, amount)
        total_cost = CROPS[crop]["price"] * amount

        economy = await UserConfig.find_one(UserConfig.user_id == ctx.author.id)
        if not economy:
            economy = UserConfig(user_id=ctx.author.id)
            await economy.insert()

        if economy.balance < total_cost:
            return await self.bot.warn(ctx, f"You need **${total_cost}** but only have **${economy.balance}**.")

        economy.balance -= total_cost
        await economy.save()

        garden_data = await GardenData.find_one(GardenData.user_id == ctx.author.id)
        if not garden_data:
            garden_data = GardenData(user_id=ctx.author.id)
            await garden_data.insert()

        garden_data.seeds[crop] = garden_data.seeds.get(crop, 0) + amount
        await garden_data.save()

        await self.bot.grant(ctx, f"You bought **{amount}x {crop}** seeds for **${total_cost}**.")

    @garden.command(name="plant")
    async def garden_plant(self, ctx, crop: str = None):
        """Plant a seed"""
        if not crop:
            return await ctx.send_help(ctx.command)
        crop = crop.lower()
        if crop not in CROPS:
            return await self.bot.warn(ctx, "That's not a valid plant type.")

        garden_data = await GardenData.find_one(GardenData.user_id == ctx.author.id)
        if not garden_data:
            garden_data = GardenData(user_id=ctx.author.id)
            await garden_data.insert()

        if not garden_data.seeds.get(crop, 0) > 0:
            return await self.bot.warn(ctx, f"You don't have any **{crop}** seeds.")
        if len(garden_data.plots) >= 10:
            return await self.bot.warn(ctx, "Your farm is full! (Max 10 plots)")

        garden_data.seeds[crop] -= 1
        garden_data.plots.append({"plant_type": crop, "planted_at": datetime.utcnow().isoformat()})
        await garden_data.save()

        hours = CROPS[crop]["time"] / 3600
        time_str = f"{int(hours)}h" if hours >= 1 else f"{int(CROPS[crop]['time'] / 60)}m"
        await self.bot.grant(ctx, f"Planted {CROPS[crop]['emoji']} **{crop}**. Ready in {time_str}.")

    @garden.command(name="harvest")
    async def garden_harvest(self, ctx):
        """Collect all grown items"""
        garden_data = await GardenData.find_one(GardenData.user_id == ctx.author.id)
        if not garden_data or not garden_data.plots:
            return await self.bot.warn(ctx, "Nothing is currently growing.")

        now = datetime.utcnow()
        total_profit = 0
        items = []
        remaining_plots = []

        for plot in garden_data.plots:
            info = CROPS[plot["plant_type"]]
            planted_at = datetime.fromisoformat(plot["planted_at"])
            elapsed = (now - planted_at).total_seconds()
            if elapsed >= info["time"]:
                total_profit += info["sell"]
                items.append(f"{info['emoji']} {plot['plant_type']}")
            else:
                remaining_plots.append(plot)

        if not items:
            return await self.bot.warn(ctx, "None of your plants are ready yet.")

        garden_data.plots = remaining_plots
        await garden_data.save()

        economy = await UserConfig.find_one(UserConfig.user_id == ctx.author.id)
        if not economy:
            economy = UserConfig(user_id=ctx.author.id)
            await economy.insert()
        economy.balance += total_profit
        await economy.save()

        await self.bot.grant(ctx, f"Harvested: {', '.join(items)}\nTotal Profit: **${total_profit}**!")

    @garden.command(name="status")
    async def garden_status(self, ctx):
        """Check your farm status"""
        garden_data = await GardenData.find_one(GardenData.user_id == ctx.author.id)
        economy = await UserConfig.find_one(UserConfig.user_id == ctx.author.id)

        balance = economy.balance if economy else 0
        now = datetime.utcnow()

        if garden_data and garden_data.plots:
            plot_lines = []
            for i, plot in enumerate(garden_data.plots):
                info = CROPS[plot["plant_type"]]
                planted_at = datetime.fromisoformat(plot["planted_at"])
                time_left = info["time"] - (now - planted_at).total_seconds()
                if time_left <= 0:
                    status = "✅ **READY**"
                else:
                    mins_left = int(time_left / 60)
                    status = f"⏳ {mins_left}m left"
                plot_lines.append(f"**{i + 1}.** {info['emoji']} {plot['plant_type']} — {status}")
            plot_status = "\n".join(plot_lines)
        else:
            plot_status = "_Soil is currently empty._"

        seeds = garden_data.seeds if garden_data else {}
        inv = " | ".join(
            f"{CROPS[name]['emoji']} {count}x {name}"
            for name, count in seeds.items() if count > 0
        ) or "No seeds."

        embed = discord.Embed(
            title=f"{ctx.author.display_name}'s Estate",
            color=0x32CD32
        )
        embed.set_thumbnail(url=ctx.author.display_avatar.url)
        embed.add_field(name="🚜 Garden Plots (10 Max)", value=plot_status, inline=False)
        embed.add_field(name="🎒 Seed Storage", value=inv, inline=False)
        embed.add_field(name="💰 Wallet", value=f"${balance}", inline=False)
        await ctx.send(embed=embed)

    @commands.group(invoke_without_command=True)
    async def dungeon(self, ctx):
        await ctx.send_help(ctx.command)

    @dungeon.command(name="explore")
    async def dungeon_explore(self, ctx):
        now = datetime.utcnow().timestamp()
        last = self.dungeon_cooldowns.get(ctx.author.id, 0)
        remaining = 60 - (now - last)
        if remaining > 0:
            return await self.bot.warn(ctx, f"You're still recovering. Try again in **{remaining:.0f}s**.")

        data = await DungeonData.find_one(DungeonData.user_id == ctx.author.id)
        if not data:
            data = DungeonData(user_id=ctx.author.id)
            await data.save()

        if data.hp <= 0:
            return await self.bot.warn(ctx, "You're dead! Use `dungeon heal` to recover.")

        monster = random.choice(MONSTERS)
        user_atk = random.randint(1, 10 + data.level * 2) + 5
        won = user_atk >= monster["hp"] / 4

        self.dungeon_cooldowns[ctx.author.id] = now

        embed = discord.Embed(color=discord.Color.red() if not won else discord.Color.green())
        embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)

        if won:
            data.xp += monster["xp"]
            leveled_up = False
            while data.xp >= data.level * 500:
                data.xp -= data.level * 500
                data.level += 1
                data.max_hp += 20
                data.hp = data.max_hp
                leveled_up = True

            config = await UserConfig.find_one(UserConfig.user_id == ctx.author.id)
            if not config:
                config = UserConfig(user_id=ctx.author.id)
            config.balance += monster["loot"]
            await config.save()
            await data.save()

            embed.title = f"⚔️ Victory against {monster['name']}!"
            desc = (
                f"You dealt **{user_atk} damage** and won!\n"
                f"+**{monster['xp']} XP** | +**${monster['loot']}**\n"
                f"HP: **{data.hp}/{data.max_hp}** | XP: **{data.xp}/{data.level * 500}**"
            )
            if leveled_up:
                desc += f"\n\n🎉 **Level Up! You are now level {data.level}!**"
            embed.description = desc
        else:
            damage = random.randint(monster["atk"] // 2, monster["atk"])
            data.hp = max(0, data.hp - damage)
            await data.save()

            embed.title = f"💀 Defeated by {monster['name']}!"
            embed.description = (
                f"You dealt **{user_atk} damage** but it wasn't enough.\n"
                f"You took **{damage} damage** | HP: **{data.hp}/{data.max_hp}**"
            )

        await ctx.send(embed=embed)

    @dungeon.command(name="heal")
    async def dungeon_heal(self, ctx):
        data = await DungeonData.find_one(DungeonData.user_id == ctx.author.id)
        if not data:
            data = DungeonData(user_id=ctx.author.id)
            await data.save()

        if data.hp >= data.max_hp:
            return await self.bot.warn(ctx, "You're already at full HP.")

        cost = 200
        config = await UserConfig.find_one(UserConfig.user_id == ctx.author.id)
        if not config or config.balance < cost:
            return await self.bot.warn(ctx, f"You need **${cost}** to heal.")

        config.balance -= cost
        data.hp = data.max_hp
        await config.save()
        await data.save()

        embed = discord.Embed(
            description=f"💊 Healed to full HP! (**{data.max_hp}/{data.max_hp}**) | -**${cost}**",
            color=discord.Color.green()
        )
        embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)

    @dungeon.command(name="profile")
    async def dungeon_profile(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        data = await DungeonData.find_one(DungeonData.user_id == member.id)
        if not data:
            if member == ctx.author:
                data = DungeonData(user_id=member.id)
                await data.save()
            else:
                return await self.bot.warn(ctx, f"**{member.display_name}** hasn't started their dungeon journey yet.")

        xp_needed = data.level * 500
        bar_len = 12
        filled = int(bar_len * data.xp / xp_needed) if xp_needed else bar_len
        xp_bar = "█" * filled + "░" * (bar_len - filled)

        hp_bar_len = 12
        hp_filled = int(hp_bar_len * data.hp / data.max_hp) if data.max_hp else hp_bar_len
        hp_bar = "█" * hp_filled + "░" * (hp_bar_len - hp_filled)

        embed = discord.Embed(title=f"⚔️ {member.display_name}'s Dungeon Profile", color=discord.Color.dark_gold())
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="❤️ HP", value=f"`{hp_bar}` {data.hp}/{data.max_hp}", inline=False)
        embed.add_field(name="✨ XP", value=f"`{xp_bar}` {data.xp}/{xp_needed}", inline=False)
        embed.add_field(name="🏆 Level", value=str(data.level), inline=True)
        embed.add_field(name="⚔️ Weapon", value=data.gear.get("weapon", "None"), inline=True)
        embed.add_field(name="🛡️ Armor", value=data.gear.get("armor", "None"), inline=True)
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Entertainment(bot))
