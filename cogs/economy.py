import discord
import random
import re
import asyncio
from discord.ext import commands
from models.configs import UserConfig

class Economy(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.c_neutral = 0x242429 
        self.c_win = 0x5df271  
        self.c_loss = 0xf25d5d 

    async def get_user(self, user_id: int):
        res = await UserConfig.find_one(UserConfig.user_id == user_id)
        if not res:
            res = UserConfig(user_id=user_id, balance=0)
            await res.insert()
        return res

    def parse_amount(self, balance: int, amount_str: str):
        if not amount_str: return None
        amount_str = str(amount_str).lower().strip()
        if amount_str == "all": return balance
        if amount_str == "half": return balance // 2
        if "%" in amount_str:
            try:
                percentage = int(re.sub(r"\D", "", amount_str))
                return int(balance * (percentage / 100))
            except: return None
        try:
            return int(amount_str)
        except: return None

    def wock_embed(self, description, color):
        """Ultra-Minimalist: No author, no title, just clean text."""
        return discord.Embed(description=description, color=color)

    @commands.command(name="balance", aliases=["bal", "money", "+2"])
    async def balance(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        user = await self.get_user(member.id)
        desc = f"{member.mention}: Your current balance is **${user.balance:,}**."
        await ctx.send(embed=self.wock_embed(desc, self.c_neutral))

    @commands.command(name="dice", aliases=["gamble", "bet"])
    async def dice(self, ctx, amount_str: str = None):
        if not amount_str: 
            return await ctx.send_help(ctx.command)
            
        user = await self.get_user(ctx.author.id)
        amount = self.parse_amount(user.balance, amount_str)

        if amount is None or amount <= 0: 
            return await self.bot.warn(ctx, f"{ctx.author.mention}: You must provide a valid amount to bet.")
        if user.balance < amount: 
            return await self.bot.warn(ctx, f"{ctx.author.mention}: You do not have enough funds to bet **${amount:,}**.")

        u_roll, b_roll = random.randint(1, 6), random.randint(1, 6)
        
        if u_roll > b_roll:
            user.balance += amount
            desc = f"{ctx.author.mention}: You rolled a **{u_roll}** and won **${amount:,}**."
            color = self.c_win
        elif u_roll < b_roll:
            user.balance -= amount
            desc = f"{ctx.author.mention}: You rolled a **{u_roll}** and lost **${amount:,}**."
            color = self.c_loss
        else:
            desc = f"{ctx.author.mention}: You both rolled a **{u_roll}**. It's a draw."
            color = self.c_neutral

        await user.save()
        await ctx.send(embed=self.wock_embed(desc, color))

    @commands.command(name="coinflip", aliases=["cf"])
    async def coinflip(self, ctx, choice: str = None, amount_str: str = None):
        if not choice or not amount_str: 
            return await ctx.send_help(ctx.command)
            
        choice = choice.lower()
        if choice not in ["heads", "tails", "h", "t"]:
            return await self.bot.warn(ctx, f"{ctx.author.mention}: You must choose either **heads** or **tails**.")
        
        user_choice = "heads" if choice in ["heads", "h"] else "tails"
        
        user = await self.get_user(ctx.author.id)
        amount = self.parse_amount(user.balance, amount_str)

        if amount is None or amount <= 0: 
            return await self.bot.warn(ctx, f"{ctx.author.mention}: You must provide a valid amount to bet.")
        if user.balance < amount: 
            return await self.bot.warn(ctx, f"{ctx.author.mention}: You do not have enough funds to bet **${amount:,}**.")

        outcome = random.choice(["heads", "tails"])

        if user_choice == outcome:
            user.balance += amount
            desc = f"{ctx.author.mention}: It was **{outcome}**. You won **${amount:,}**."
            color = self.c_win
        else:
            user.balance -= amount
            desc = f"{ctx.author.mention}: It was **{outcome}**. You lost **${amount:,}**."
            color = self.c_loss

        await user.save()
        await ctx.send(embed=self.wock_embed(desc, color))

    @commands.command(name="blackjack", aliases=["bj"])
    async def blackjack(self, ctx, amount_str: str = None):
        if not amount_str: 
            return await ctx.send_help(ctx.command)
            
        user = await self.get_user(ctx.author.id)
        amount = self.parse_amount(user.balance, amount_str)

        if amount is None or amount <= 0: 
            return await self.bot.warn(ctx, f"{ctx.author.mention}: You must provide a valid amount to bet.")
        if user.balance < amount: 
            return await self.bot.warn(ctx, f"{ctx.author.mention}: You do not have enough funds to bet **${amount:,}**.")

        player_hand = [random.randint(1, 11), random.randint(1, 11)]
        dealer_hand = [random.randint(1, 11), random.randint(1, 11)]

        def bj_desc(status):
            return (f"{ctx.author.mention}: **Blackjack** ({status})\n"
                    f"Your hand: **{sum(player_hand)}**\n"
                    f"Dealer hand: **{dealer_hand[0]}**")

        msg = await ctx.send(embed=self.wock_embed(bj_desc("Playing"), self.c_neutral))
        
        for e in ['✅', '🛑']: await msg.add_reaction(e)

        try:
            def check(r, u): return u == ctx.author and str(r.emoji) in ['✅', '🛑'] and r.message.id == msg.id
            reaction, _ = await self.bot.wait_for('reaction_add', timeout=30.0, check=check)
            
            if str(reaction.emoji) == '✅':
                player_hand.append(random.randint(1, 11))
        except asyncio.TimeoutError: pass

        p_total, d_total = sum(player_hand), sum(dealer_hand)
        if p_total > 21:
            user.balance -= amount
            result, color = f"Bust! Lost **${amount:,}**", self.c_loss
        elif d_total > 21 or p_total > d_total:
            user.balance += amount
            result, color = f"Won **${amount:,}**", self.c_win
        else:
            user.balance -= amount
            result, color = f"Lost **${amount:,}**", self.c_loss

        await user.save()
        final_desc = f"{ctx.author.mention}: {result}. Hand: **{p_total}** vs **{d_total}**."
        await msg.edit(embed=self.wock_embed(final_desc, color))
        await msg.clear_reactions()

    @commands.command(name="setup")
    async def setup_account(self, ctx):
        """Initialize your economy account"""
        user = await self.get_user(ctx.author.id)
        if not hasattr(user, 'bank'):
            user.bank = 0
        if not hasattr(user, 'security_features'):
            user.security_features = {}
        await user.save()
        desc = f"Account initialized! Balance: **${user.balance:,}** | Bank: **${user.bank:,}**"
        await ctx.send(embed=self.wock_embed(desc, self.c_neutral))

    @commands.command(name="bank")
    async def check_bank(self, ctx, member: discord.Member = None):
        """Check bank balance"""
        member = member or ctx.author
        user = await self.get_user(member.id)
        bank = getattr(user, 'bank', 0) or 0
        desc = f"{member.mention}: Bank balance is **${bank:,}**."
        await ctx.send(embed=self.wock_embed(desc, self.c_neutral))

    @commands.command(name="deposit")
    async def deposit(self, ctx, amount_str: str = None):
        """Deposit money into your bank"""
        if not amount_str:
            return await self.bot.warn(ctx, "Specify an amount to deposit")
        
        user = await self.get_user(ctx.author.id)
        amount = self.parse_amount(user.balance, amount_str)
        
        if not amount or amount <= 0:
            return await self.bot.warn(ctx, "Invalid amount")
        if amount > user.balance:
            return await self.bot.warn(ctx, "Insufficient balance")
        
        user.balance -= amount
        user.bank = getattr(user, 'bank', 0) or 0
        user.bank += amount
        await user.save()
        desc = f"Deposited **${amount:,}** to your bank. Balance: **${user.balance:,}** | Bank: **${user.bank:,}**"
        await ctx.send(embed=self.wock_embed(desc, self.c_win))

    @commands.command(name="withdrawal")
    async def withdraw(self, ctx, amount_str: str = None):
        """Withdraw money from your bank"""
        if not amount_str:
            return await self.bot.warn(ctx, "Specify an amount to withdraw")
        
        user = await self.get_user(ctx.author.id)
        bank = getattr(user, 'bank', 0) or 0
        amount = self.parse_amount(bank, amount_str)
        
        if not amount or amount <= 0:
            return await self.bot.warn(ctx, "Invalid amount")
        if amount > bank:
            return await self.bot.warn(ctx, "Insufficient bank balance")
        
        user.bank -= amount
        user.balance += amount
        await user.save()
        desc = f"Withdrew **${amount:,}** from your bank. Balance: **${user.balance:,}** | Bank: **${user.bank:,}**"
        await ctx.send(embed=self.wock_embed(desc, self.c_win))

    @commands.command(name="give")
    async def give(self, ctx, member: discord.Member = None, amount_str: str = None):
        """Give money to another user"""
        if not member or not amount_str:
            return await ctx.send_help(ctx.command)
        
        if member == ctx.author:
            return await self.bot.warn(ctx, "You can't give money to yourself")
        if member.bot:
            return await self.bot.warn(ctx, "You can't give money to bots")
        
        sender = await self.get_user(ctx.author.id)
        amount = self.parse_amount(sender.balance, amount_str)
        
        if not amount or amount <= 0:
            return await self.bot.warn(ctx, "Invalid amount")
        if amount > sender.balance:
            return await self.bot.warn(ctx, "Insufficient balance")
        
        receiver = await self.get_user(member.id)
        sender.balance -= amount
        receiver.balance += amount
        
        await sender.save()
        await receiver.save()
        desc = f"Gave **${amount:,}** to {member.mention}. Your balance: **${sender.balance:,}**"
        await ctx.send(embed=self.wock_embed(desc, self.c_win))

    @commands.command(name="rob")
    async def rob(self, ctx, member: discord.Member = None):
        """Rob another user's balance (risky!)"""
        if not member:
            return await self.bot.warn(ctx, "Specify a user to rob")
        if member == ctx.author:
            return await self.bot.warn(ctx, "You can't rob yourself")
        if member.bot:
            return await self.bot.warn(ctx, "You can't rob bots")
        
        robber = await self.get_user(ctx.author.id)
        victim = await self.get_user(member.id)
        
        victim_security = getattr(victim, 'security_features', {}) or {}
        has_padlock = victim_security.get('padlock', False)
        
        success_chance = 0.5 if not has_padlock else 0.25
        success = random.random() < success_chance
        
        if success and victim.balance > 0:
            steal_amount = random.randint(1, victim.balance // 2) if victim.balance > 1 else victim.balance
            robber.balance += steal_amount
            victim.balance -= steal_amount
            
            await robber.save()
            await victim.save()
            desc = f"Successfully robbed **${steal_amount:,}** from {member.mention}! Your balance: **${robber.balance:,}**"
            await ctx.send(embed=self.wock_embed(desc, self.c_win))
        else:
            robber.balance = max(0, robber.balance - 100)
            await robber.save()
            padlock_msg = " They had a padlock!" if has_padlock else ""
            desc = f"Failed to rob {member.mention} and lost **$100**!{padlock_msg} Balance: **${robber.balance:,}**"
            await ctx.send(embed=self.wock_embed(desc, self.c_loss))

    @commands.command(name="shop")
    async def shop(self, ctx, action: str = None, item: str = None):
        """Shop for security features"""
        shop_items = {
            "padlock": 1667,
            "safe": 5000,
            "alarm": 3333,
            "bodyguard": 8333
        }
        
        if not action or action.lower() == "list":
            items_list = "\n".join([f"**{name.capitalize()}** - **${price:,}**" for name, price in shop_items.items()])
            embed = discord.Embed(color=0x242429, title="Security Shop", description=items_list)
            return await ctx.send(embed=embed)
        
        if action.lower() == "buy":
            if not item:
                return await ctx.send_help(ctx.command)
            
            item = item.lower()
            if item not in shop_items:
                return await self.bot.warn(ctx, f"Item **{item}** not found in shop")
            
            price = shop_items[item]
            user = await self.get_user(ctx.author.id)
            
            if user.balance < price:
                return await self.bot.warn(ctx, f"Insufficient balance. Need **${price:,}**, have **${user.balance:,}**")
            
            security = getattr(user, 'security_features', {}) or {}
            if security.get(item, False):
                return await self.bot.warn(ctx, f"You already own a **{item}**")
            
            user.balance -= price
            security[item] = True
            user.security_features = security
            await user.save()
            desc = f"Purchased **{item.capitalize()}** for **${price:,}**! Balance: **${user.balance:,}**"
            await ctx.send(embed=self.wock_embed(desc, self.c_win))
        else:
            await ctx.send_help(ctx.command)

    @commands.command(name="tip", aliases=["transfer", "send", "+1"])
    async def tip(self, ctx, member: discord.Member = None, amount_str: str = None):
        """Tip another user some money"""
        if not member or not amount_str:
            return await ctx.send_help(ctx.command)
        
        if member == ctx.author:
            return await self.bot.warn(ctx, "You can't tip yourself")
        if member.bot:
            return await self.bot.warn(ctx, "You can't tip bots")
        
        sender = await self.get_user(ctx.author.id)
        amount = self.parse_amount(sender.balance, amount_str)
        
        if not amount or amount <= 0:
            return await self.bot.warn(ctx, "Invalid amount")
        if amount > sender.balance:
            return await self.bot.warn(ctx, "Insufficient balance")
        
        receiver = await self.get_user(member.id)
        sender.balance -= amount
        receiver.balance += amount
        
        await sender.save()
        await receiver.save()
        desc = f"Tipped **${amount:,}** to {member.mention}. Your balance: **${sender.balance:,}**"
        await ctx.send(embed=self.wock_embed(desc, self.c_win))

    @commands.command(name="work", aliases=["job"])
    @commands.cooldown(1, 3600, commands.BucketType.user)
    async def work(self, ctx):
        """Work for some money"""
        user = await self.get_user(ctx.author.id)
        earnings = random.randint(100, 500)
        user.balance += earnings
        await user.save()
        desc = f"{ctx.author.mention}: You worked and earned **${earnings:,}**. Balance: **${user.balance:,}**"
        await ctx.send(embed=self.wock_embed(desc, self.c_win))

    @commands.command(name="leaderboard", aliases=["lb", "top"])
    async def leaderboard(self, ctx):
        """Check the leaderboard of the richest users"""
        try:
            users = await UserConfig.find_many().sort([("balance", -1)]).limit(10).to_list(10)
            if not users:
                return await self.bot.warn(ctx, "No users on leaderboard yet")
            
            leaderboard_text = ""
            for idx, user in enumerate(users, 1):
                member = ctx.guild.get_member(user.user_id) or f"User #{user.user_id}"
                if idx == 1:
                    leaderboard_text += f"👑 **{member}** - **${user.balance:,}**\n"
                else:
                    leaderboard_text += f"{idx}. **{member}** - **${user.balance:,}**\n"
            
            embed = discord.Embed(color=0x242429, title="💰 Top 10 Richest Users", description=leaderboard_text)
            await ctx.send(embed=embed)
        except Exception as e:
            await self.bot.warn(ctx, f"Error fetching leaderboard: {str(e)}")

    @commands.command(name="rain")
    async def rain(self, ctx, amount_str: str = None):
        """Let people catch some scraps"""
        if not amount_str:
            return await ctx.send_help(ctx.command)
        
        user = await self.get_user(ctx.author.id)
        amount = self.parse_amount(user.balance, amount_str)
        
        if not amount or amount <= 0:
            return await self.bot.warn(ctx, "Invalid amount")
        if amount > user.balance:
            return await self.bot.warn(ctx, "Insufficient balance")
        
        user.balance -= amount
        await user.save()
        
        embed = discord.Embed(color=0x242429, description=f"💰 {ctx.author.mention} is raining **${amount:,}**!\n\nReact with 🎉 to catch some!")
        msg = await ctx.send(embed=embed)
        await msg.add_reaction("🎉")
        
        caught_users = set()
        async def collect_rain():
            def check(r, u): return str(r.emoji) == "🎉" and u != ctx.author and u.id not in caught_users
            
            for _ in range(5):
                try:
                    reaction, user_reacted = await self.bot.wait_for('reaction_add', timeout=30.0, check=check)
                    caught_users.add(user_reacted.id)
                    receiver = await self.get_user(user_reacted.id)
                    catch_amount = random.randint(1, amount // 5)
                    receiver.balance += catch_amount
                    await receiver.save()
                except asyncio.TimeoutError:
                    break
        
        await collect_rain()
        desc = f"Rain ended! **{len(caught_users)}** users caught some money."
        await msg.edit(embed=self.wock_embed(desc, self.c_neutral))

    @commands.command(name="slots")
    async def slots(self, ctx, amount_str: str = None):
        """Spin the slots for a chance to win big"""
        if not amount_str:
            return await ctx.send_help(ctx.command)
        
        user = await self.get_user(ctx.author.id)
        amount = self.parse_amount(user.balance, amount_str)
        
        if not amount or amount <= 0:
            return await self.bot.warn(ctx, "Invalid amount")
        if user.balance < amount:
            return await self.bot.warn(ctx, "Insufficient balance")
        
        symbols = ["🍎", "🍊", "🍋", "🍌", "🍉"]
        reels = [random.choice(symbols) for _ in range(3)]
        
        if reels[0] == reels[1] == reels[2]:
            user.balance += amount * 5
            result = f"Three of a kind! Won **${amount * 5:,}**"
            color = self.c_win
        elif reels[0] == reels[1] or reels[1] == reels[2]:
            user.balance += amount * 2
            result = f"Two of a kind! Won **${amount * 2:,}**"
            color = self.c_win
        else:
            user.balance -= amount
            result = f"No match. Lost **${amount:,}**"
            color = self.c_loss
        
        await user.save()
        desc = f"{' '.join(reels)}\n\n{ctx.author.mention}: {result}\nBalance: **${user.balance:,}**"
        await ctx.send(embed=self.wock_embed(desc, color))

    @commands.command(name="roulette")
    async def roulette(self, ctx, color: str = None, amount_str: str = None):
        """Bet on a color in roulette. Red: 1-10, 19-28 (2x) Black: 11-18, 29-36 (2x) Green: 0 (35x)"""
        if not color or not amount_str:
            return await ctx.send_help(ctx.command)
        
        user = await self.get_user(ctx.author.id)
        amount = self.parse_amount(user.balance, amount_str)
        
        if not amount or amount <= 0:
            return await self.bot.warn(ctx, "Invalid amount")
        if user.balance < amount:
            return await self.bot.warn(ctx, "Insufficient balance")
        
        color = color.lower()
        if color not in ["red", "black", "green"]:
            return await self.bot.warn(ctx, "Choose red, black, or green")
        
        spin = random.randint(0, 36)
        
        if spin == 0:
            actual_color = "green"
            multiplier = 35
        elif spin in [1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36]:
            actual_color = "red"
            multiplier = 2
        else:
            actual_color = "black"
            multiplier = 2
        
        if color == actual_color:
            user.balance += amount * multiplier
            result = f"🎯 You won! ({actual_color}) Won **${amount * multiplier:,}**"
            color_code = self.c_win
        else:
            user.balance -= amount
            result = f"❌ You lost. ({actual_color}) Lost **${amount:,}**"
            color_code = self.c_loss
        
        await user.save()
        desc = f"{ctx.author.mention}: {result}\nBalance: **${user.balance:,}**"
        await ctx.send(embed=self.wock_embed(desc, color_code))

    @commands.command(name="rockpaperscissors", aliases=["rps"])
    async def rps(self, ctx, choice: str = None, amount_str: str = None):
        """Play rock, paper, scissors against the bot"""
        if not choice or not amount_str:
            return await ctx.send_help(ctx.command)
        
        user = await self.get_user(ctx.author.id)
        amount = self.parse_amount(user.balance, amount_str)
        
        if not amount or amount <= 0:
            return await self.bot.warn(ctx, "Invalid amount")
        if user.balance < amount:
            return await self.bot.warn(ctx, "Insufficient balance")
        
        choice = choice.lower()
        if choice not in ["rock", "paper", "scissors"]:
            return await self.bot.warn(ctx, "Choose rock, paper, or scissors")
        
        bot_choice = random.choice(["rock", "paper", "scissors"])
        
        if choice == bot_choice:
            result = f"It's a draw! Both chose {choice}"
            color = self.c_neutral
        elif (choice == "rock" and bot_choice == "scissors") or \
             (choice == "paper" and bot_choice == "rock") or \
             (choice == "scissors" and bot_choice == "paper"):
            user.balance += amount
            result = f"You won! {choice} beats {bot_choice}. Won **${amount:,}**"
            color = self.c_win
        else:
            user.balance -= amount
            result = f"You lost! {bot_choice} beats {choice}. Lost **${amount:,}**"
            color = self.c_loss
        
        await user.save()
        desc = f"{ctx.author.mention}: {result}\nBalance: **${user.balance:,}**"
        await ctx.send(embed=self.wock_embed(desc, color))

    @commands.command(name="scratchcard")
    async def scratchcard(self, ctx, amount_str: str = None):
        """Scratch a card to reveal your prize"""
        if not amount_str:
            return await ctx.send_help(ctx.command)
        
        user = await self.get_user(ctx.author.id)
        amount = self.parse_amount(user.balance, amount_str)
        
        if not amount or amount <= 0:
            return await self.bot.warn(ctx, "Invalid amount")
        if user.balance < amount:
            return await self.bot.warn(ctx, "Insufficient balance")
        
        outcome = random.random()
        if outcome < 0.5:
            user.balance -= amount
            result = f"Lost **${amount:,}**"
            color = self.c_loss
        elif outcome < 0.8:
            user.balance += amount
            result = f"Won **${amount:,}**"
            color = self.c_win
        else:
            user.balance += amount * 3
            result = f"Jackpot! Won **${amount * 3:,}**"
            color = self.c_win
        
        await user.save()
        desc = f"{ctx.author.mention}: 🎫 {result}\nBalance: **${user.balance:,}**"
        await ctx.send(embed=self.wock_embed(desc, color))

    @commands.command(name="wheel", aliases=["spin"])
    async def wheel(self, ctx, amount_str: str = None):
        """Spin the wheel of fortune"""
        if not amount_str:
            return await ctx.send_help(ctx.command)
        
        user = await self.get_user(ctx.author.id)
        amount = self.parse_amount(user.balance, amount_str)
        
        if not amount or amount <= 0:
            return await self.bot.warn(ctx, "Invalid amount")
        if user.balance < amount:
            return await self.bot.warn(ctx, "Insufficient balance")
        
        spin = random.randint(1, 100)
        if spin <= 50:
            user.balance -= amount
            result = f"Lost **${amount:,}**"
            color = self.c_loss
        elif spin <= 85:
            user.balance += amount
            result = f"Won **${amount:,}**"
            color = self.c_win
        else:
            user.balance += amount * 5
            result = f"Big win! Won **${amount * 5:,}**"
            color = self.c_win
        
        await user.save()
        desc = f"{ctx.author.mention}: 🎡 {result}\nBalance: **${user.balance:,}**"
        await ctx.send(embed=self.wock_embed(desc, color))

    @commands.command(name="jackpot")
    async def jackpot(self, ctx, amount_str: str = None):
        """Try to win the jackpot with a very small chance and big reward"""
        if not amount_str:
            return await ctx.send_help(ctx.command)
        
        user = await self.get_user(ctx.author.id)
        amount = self.parse_amount(user.balance, amount_str)
        
        if not amount or amount <= 0:
            return await self.bot.warn(ctx, "Invalid amount")
        if user.balance < amount:
            return await self.bot.warn(ctx, "Insufficient balance")
        
        if random.random() < 0.01:
            user.balance += amount * 100
            result = f"🎰 JACKPOT! Won **${amount * 100:,}**"
            color = self.c_win
        else:
            user.balance -= amount
            result = f"No jackpot. Lost **${amount:,}**"
            color = self.c_loss
        
        await user.save()
        desc = f"{ctx.author.mention}: {result}\nBalance: **${user.balance:,}**"
        await ctx.send(embed=self.wock_embed(desc, color))

    @commands.command(name="crash")
    async def crash(self, ctx, amount_str: str = None):
        """Race against a crashing multiplier. Cash out before it crashes!"""
        if not amount_str:
            return await ctx.send_help(ctx.command)
        
        user = await self.get_user(ctx.author.id)
        amount = self.parse_amount(user.balance, amount_str)
        
        if not amount or amount <= 0:
            return await self.bot.warn(ctx, "Invalid amount")
        if user.balance < amount:
            return await self.bot.warn(ctx, "Insufficient balance")
        
        crash_point = random.uniform(1.1, 5.0)
        msg = await ctx.send(embed=self.wock_embed(f"{ctx.author.mention}: Multiplier: 1.00x | React ✅ to cash out!", self.c_neutral))
        await msg.add_reaction("✅")
        
        try:
            def check(r, u): return u == ctx.author and str(r.emoji) == "✅"
            reaction, _ = await self.bot.wait_for('reaction_add', timeout=10.0, check=check)
            
            user.balance += int(amount * 2)
            result = f"Cashed out at 2.00x! Won **${int(amount * 2):,}**"
            color = self.c_win
        except asyncio.TimeoutError:
            user.balance -= amount
            result = f"Crashed at {crash_point:.2f}x! Lost **${amount:,}**"
            color = self.c_loss
        
        await user.save()
        desc = f"{ctx.author.mention}: {result}\nBalance: **${user.balance:,}**"
        await msg.edit(embed=self.wock_embed(desc, color))
        await msg.clear_reactions()

    @commands.command(name="mines")
    async def mines(self, ctx, amount_str: str = None):
        """Click tiles to avoid mines. The more you click safely, the higher your multiplier"""
        if not amount_str:
            return await ctx.send_help(ctx.command)
        
        user = await self.get_user(ctx.author.id)
        amount = self.parse_amount(user.balance, amount_str)
        
        if not amount or amount <= 0:
            return await self.bot.warn(ctx, "Invalid amount")
        if user.balance < amount:
            return await self.bot.warn(ctx, "Insufficient balance")
        
        mines = set(random.sample(range(1, 26), 5))
        safe_clicks = 0
        
        embed = discord.Embed(color=0x242429, description=f"{ctx.author.mention}: Click safe tiles!\nSafe clicks: **0**")
        msg = await ctx.send(embed=embed)
        
        for i in range(1, 4):
            try:
                def check(r, u): return u == ctx.author and r.message.id == msg.id
                reaction, _ = await self.bot.wait_for('reaction_add', timeout=10.0, check=check)
                
                if safe_clicks + 1 in mines:
                    user.balance -= amount
                    result = f"💣 Hit a mine! Lost **${amount:,}**"
                    color = self.c_loss
                    break
                else:
                    safe_clicks += 1
                    embed.description = f"{ctx.author.mention}: Click safe tiles!\nSafe clicks: **{safe_clicks}** (Multiplier: {1 + safe_clicks * 0.5:.1f}x)"
                    await msg.edit(embed=embed)
            except asyncio.TimeoutError:
                break
        else:
            user.balance += int(amount * (1 + safe_clicks * 0.5))
            result = f"Cashed out! Won **${int(amount * (1 + safe_clicks * 0.5)):,}**"
            color = self.c_win
        
        await user.save()
        desc = f"{ctx.author.mention}: {result}\nBalance: **${user.balance:,}**"
        await msg.edit(embed=self.wock_embed(desc, color))

    @commands.command(name="higher", aliases=["highlow"])
    async def higher(self, ctx, choice: str = None, amount_str: str = None):
        """Pick if the next number will be higher or lower"""
        if not choice or not amount_str:
            return await ctx.send_help(ctx.command)
        
        user = await self.get_user(ctx.author.id)
        amount = self.parse_amount(user.balance, amount_str)
        
        if not amount or amount <= 0:
            return await self.bot.warn(ctx, "Invalid amount")
        if user.balance < amount:
            return await self.bot.warn(ctx, "Insufficient balance")
        
        choice = choice.lower()
        if choice not in ["higher", "lower", "h", "l"]:
            return await self.bot.warn(ctx, "Choose 'higher' or 'lower'")
        
        first_num = random.randint(1, 100)
        second_num = random.randint(1, 100)
        choice_type = "higher" if choice in ["higher", "h"] else "lower"
        
        if (choice_type == "higher" and second_num > first_num) or (choice_type == "lower" and second_num < first_num):
            user.balance += amount
            result = f"Correct! {first_num} → {second_num}. Won **${amount:,}**"
            color = self.c_win
        else:
            user.balance -= amount
            result = f"Wrong! {first_num} → {second_num}. Lost **${amount:,}**"
            color = self.c_loss
        
        await user.save()
        desc = f"{ctx.author.mention}: {result}\nBalance: **${user.balance:,}**"
        await ctx.send(embed=self.wock_embed(desc, color))

    @commands.command(name="plinko")
    async def plinko(self, ctx, amount_str: str = None):
        """Drop a ball through pegs and see where it lands"""
        if not amount_str:
            return await ctx.send_help(ctx.command)
        
        user = await self.get_user(ctx.author.id)
        amount = self.parse_amount(user.balance, amount_str)
        
        if not amount or amount <= 0:
            return await self.bot.warn(ctx, "Invalid amount")
        if user.balance < amount:
            return await self.bot.warn(ctx, "Insufficient balance")
        
        landing = random.randint(1, 10)
        multipliers = [0.5, 0.75, 1, 1.25, 1.5, 2, 1.5, 1.25, 1, 0.75]
        multiplier = multipliers[landing - 1]
        
        winnings = int(amount * multiplier)
        if multiplier >= 1:
            user.balance += winnings
            result = f"Won **${winnings:,}** ({multiplier:.2f}x)"
            color = self.c_win
        else:
            user.balance -= int(amount * (1 - multiplier))
            result = f"Lost **${int(amount * (1 - multiplier)):,}** ({multiplier:.2f}x)"
            color = self.c_loss
        
        await user.save()
        desc = f"{ctx.author.mention}: 🎯 {result}\nBalance: **${user.balance:,}**"
        await ctx.send(embed=self.wock_embed(desc, color))

    @commands.command(name="keno")
    async def keno(self, ctx, amount_str: str = None):
        """Pick numbers and match them with the drawn numbers"""
        if not amount_str:
            return await ctx.send_help(ctx.command)
        
        user = await self.get_user(ctx.author.id)
        amount = self.parse_amount(user.balance, amount_str)
        
        if not amount or amount <= 0:
            return await self.bot.warn(ctx, "Invalid amount")
        if user.balance < amount:
            return await self.bot.warn(ctx, "Insufficient balance")
        
        drawn = set(random.sample(range(1, 81), 20))
        matches = random.randint(0, 10)
        
        multipliers = {0: 0, 3: 1, 4: 2, 5: 5, 6: 10, 7: 50}
        multiplier = multipliers.get(matches, matches * 10)
        
        if multiplier > 0:
            user.balance += int(amount * multiplier)
            result = f"Matched {matches} numbers! Won **${int(amount * multiplier):,}** ({multiplier}x)"
            color = self.c_win
        else:
            user.balance -= amount
            result = f"Matched {matches} numbers. Lost **${amount:,}**"
            color = self.c_loss
        
        await user.save()
        desc = f"{ctx.author.mention}: {result}\nBalance: **${user.balance:,}**"
        await ctx.send(embed=self.wock_embed(desc, color))

    @commands.command(name="limbo")
    async def limbo(self, ctx, amount_str: str = None):
        """Land on or above your target to win"""
        if not amount_str:
            return await ctx.send_help(ctx.command)
        
        user = await self.get_user(ctx.author.id)
        amount = self.parse_amount(user.balance, amount_str)
        
        if not amount or amount <= 0:
            return await self.bot.warn(ctx, "Invalid amount")
        if user.balance < amount:
            return await self.bot.warn(ctx, "Insufficient balance")
        
        target = random.uniform(0.01, 100)
        result_num = random.uniform(0.01, 100)
        
        if result_num >= target:
            user.balance += int(amount * (target / 10))
            result = f"You landed on {result_num:.2f}! Won **${int(amount * (target / 10)):,}**"
            color = self.c_win
        else:
            user.balance -= amount
            result = f"You landed on {result_num:.2f}. Lost **${amount:,}**"
            color = self.c_loss
        
        await user.save()
        desc = f"{ctx.author.mention}: {result}\nBalance: **${user.balance:,}**"
        await ctx.send(embed=self.wock_embed(desc, color))

    @commands.command(name="dragontiger")
    async def dragontiger(self, ctx, choice: str = None, amount_str: str = None):
        """Dragon vs Tiger - Pick the side with the highest card"""
        if not choice or not amount_str:
            return await ctx.send_help(ctx.command)
        
        user = await self.get_user(ctx.author.id)
        amount = self.parse_amount(user.balance, amount_str)
        
        if not amount or amount <= 0:
            return await self.bot.warn(ctx, "Invalid amount")
        if user.balance < amount:
            return await self.bot.warn(ctx, "Insufficient balance")
        
        choice = choice.lower()
        if choice not in ["dragon", "tiger", "d", "t"]:
            return await self.bot.warn(ctx, "Choose 'dragon' or 'tiger'")
        
        dragon_card = random.randint(1, 13)
        tiger_card = random.randint(1, 13)
        choice_type = "dragon" if choice in ["dragon", "d"] else "tiger"
        
        if (choice_type == "dragon" and dragon_card > tiger_card) or (choice_type == "tiger" and tiger_card > dragon_card):
            user.balance += amount
            result = f"Your side won! Dragon: {dragon_card}, Tiger: {tiger_card}. Won **${amount:,}**"
            color = self.c_win
        else:
            user.balance -= amount
            result = f"You lost. Dragon: {dragon_card}, Tiger: {tiger_card}. Lost **${amount:,}**"
            color = self.c_loss
        
        await user.save()
        desc = f"{ctx.author.mention}: {result}\nBalance: **${user.balance:,}**"
        await ctx.send(embed=self.wock_embed(desc, color))

    @commands.command(name="shellgame", aliases=["cup"])
    async def shellgame(self, ctx, choice: str = None, amount_str: str = None):
        """Find the ball under the correct cup"""
        if not choice or not amount_str:
            return await ctx.send_help(ctx.command)
        
        user = await self.get_user(ctx.author.id)
        amount = self.parse_amount(user.balance, amount_str)
        
        if not amount or amount <= 0:
            return await self.bot.warn(ctx, "Invalid amount")
        if user.balance < amount:
            return await self.bot.warn(ctx, "Insufficient balance")
        
        try:
            choice_num = int(choice)
            if choice_num not in [1, 2, 3]:
                return await self.bot.warn(ctx, "Choose cup 1, 2, or 3")
        except:
            return await self.bot.warn(ctx, "Choose cup 1, 2, or 3")
        
        ball_location = random.randint(1, 3)
        
        if choice_num == ball_location:
            user.balance += amount * 2
            result = f"Found the ball! Won **${amount * 2:,}**"
            color = self.c_win
        else:
            user.balance -= amount
            result = f"Ball was under cup {ball_location}. Lost **${amount:,}**"
            color = self.c_loss
        
        await user.save()
        desc = f"{ctx.author.mention}: 🥤 {result}\nBalance: **${user.balance:,}**"
        await ctx.send(embed=self.wock_embed(desc, color))

    @commands.command(name="rakeback", aliases=["rake", "rb"])
    @commands.cooldown(1, 86400, commands.BucketType.user)
    async def rakeback(self, ctx):
        """Claim your rakeback for a percentage of your bets"""
        user = await self.get_user(ctx.author.id)
        rakeback_amount = random.randint(50, 300)
        user.balance += rakeback_amount
        await user.save()
        desc = f"{ctx.author.mention}: You claimed your daily rakeback of **${rakeback_amount:,}**!"
        await ctx.send(embed=self.wock_embed(desc, self.c_win))

    @commands.command(name="daily", aliases=["chest", "claim"])
    @commands.cooldown(1, 86400, commands.BucketType.user)
    async def daily(self, ctx):
        """Claim your daily chest for free money"""
        user = await self.get_user(ctx.author.id)
        reward = random.randint(400, 800)
        user.balance += reward
        await user.save()
        desc = f"{ctx.author.mention}: You've claimed your daily chest of **${reward:,}**. 📦"
        await ctx.send(embed=self.wock_embed(desc, self.c_win))

async def setup(bot):
    await bot.add_cog(Economy(bot))