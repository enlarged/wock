import discord
from discord.ext import commands

class Paginator(discord.ui.View):
    """Custom paginator for help embeds with custom buttons"""
    def __init__(self, bot, embeds: list, ctx: commands.Context, invoker: int, timeout: float = 180.0):
        super().__init__(timeout=timeout)
        self.bot = bot
        self.embeds = embeds
        self.ctx = ctx
        self.invoker = invoker
        self.current_page = 0
        self.message = None
        self._buttons = {}

    def add_button(self, button_type: str, emoji: str):
        """Add a button to the paginator"""
        self._buttons[button_type] = emoji

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker:
            await interaction.response.send_message("This paginator is not for you.", ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        if self.message:
            for item in self.children:
                item.disabled = True
            try:
                await self.message.edit(view=self)
            except Exception:
                pass

    async def update_page(self, interaction: discord.Interaction):
        await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self)

    @discord.ui.button(style=discord.ButtonStyle.gray)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = (self.current_page - 1) % len(self.embeds)
        await self.update_page(interaction)

    @discord.ui.button(style=discord.ButtonStyle.gray)
    async def goto_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Go to page feature not implemented", ephemeral=True)

    @discord.ui.button(style=discord.ButtonStyle.gray)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = (self.current_page + 1) % len(self.embeds)
        await self.update_page(interaction)

    @discord.ui.button(style=discord.ButtonStyle.red)
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        if self.message:
            await self.message.delete()

    async def start(self):
        if len(self.embeds) == 0:
            return
        
        # Set custom emojis for buttons
        for i, button in enumerate(self.children):
            button_type = ['prev', 'goto', 'next', 'delete'][i] if i < 4 else None
            if button_type and button_type in self._buttons:
                button.emoji = self._buttons[button_type]
        
        if len(self.embeds) == 1:
            self.message = await self.ctx.send(embed=self.embeds[0])
        else:
            self.message = await self.ctx.send(embed=self.embeds[0], view=self)

class WockPaginator(discord.ui.View):
    def __init__(self, ctx: commands.Context, pages: list[discord.Embed], timeout: float = 60.0):
        super().__init__(timeout=timeout)
        self.ctx = ctx
        self.pages = pages
        self.current_page = 0
        self.message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.ctx.author:
            await interaction.response.send_message("This menu isn't for you.", ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        if self.message:
            for item in self.children:
                item.disabled = True
            await self.message.edit(view=self)

    async def update_message(self, interaction: discord.Interaction):
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

    @discord.ui.button(label="←", style=discord.ButtonStyle.gray)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 0:
            self.current_page -= 1
        else:
            self.current_page = len(self.pages) - 1
        await self.update_message(interaction)

    @discord.ui.button(label="→", style=discord.ButtonStyle.gray)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page < len(self.pages) - 1:
            self.current_page += 1
        else:
            self.current_page = 0
        await self.update_message(interaction)

    async def start(self):
        if len(self.pages) == 1:
            self.message = await self.ctx.send(embed=self.pages[0])
        else:
            self.message = await self.ctx.send(embed=self.pages[0], view=self)