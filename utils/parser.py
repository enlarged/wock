import re
import discord
from models.configs import UserConfig

class EmbedParser:
    """Parser for creating embeds from a simple {key: value} string format."""

    FIELD_NAME = re.compile(r"^field_name(\d+)$", re.IGNORECASE)
    FIELD_VALUE = re.compile(r"^field_value(\d+)$", re.IGNORECASE)
    FIELD_INLINE = re.compile(r"^field_inline(\d+)$", re.IGNORECASE)

    def __init__(self, ctx):
        self.ctx = ctx

    async def parse_content(self, content: str, variables: dict = None) -> str:
        """Parse regular message content and replace variables."""
        if not content or not isinstance(content, str):
            return ""
        
        result = content
        
        user_config = await UserConfig.find_one(UserConfig.user_id == self.ctx.author.id)
        user_level = user_config.level if user_config else 1
        
        default_vars = {
            "user": self.ctx.author.name,
            "user.mention": self.ctx.author.mention,
            "user.id": str(self.ctx.author.id),
            "guild": self.ctx.guild.name if self.ctx.guild else "DM",
            "guild.id": str(self.ctx.guild.id) if self.ctx.guild else "0",
            "channel": self.ctx.channel.name if hasattr(self.ctx.channel, 'name') else str(self.ctx.channel),
            "channel.id": str(self.ctx.channel.id),
            "level": str(user_level),
        }
        
        if variables:
            default_vars.update(variables)
        
        for var_name, var_value in default_vars.items():
            result = result.replace(f"{{{var_name}}}", str(var_value))
        
        return result

    def _parse_color(self, value: str):
        value = value.strip().lower()
        if value.startswith("#"):
            value = value[1:]
        if value.startswith("0x"):
            value = value[2:]
        if not value:
            return None
        try:
            int_color = int(value, 16) if any(c in value for c in "abcdef") else int(value, 16 if value.endswith("x") else 10)
        except ValueError:
            try:
                int_color = int(value, 10)
            except ValueError:
                raise ValueError(f"Invalid color value: {value}")
        return discord.Color(int_color)

    def parse(self, code: str):
        if not isinstance(code, str) or not code.strip():
            raise ValueError("Embed code must be a non-empty string")

        embed = discord.Embed()
        raw_fields = {}

        for match in re.finditer(r"\{([^{}]+?)\}", code):
            content = match.group(1).strip()
            if ":" not in content:
                continue
            key, val = content.split(":", 1)
            key = key.strip().lower()
            val = val.strip()

            if not key:
                continue

            if key in ("desc", "description"):
                embed.description = val
            elif key == "title":
                embed.title = val
            elif key == "url":
                embed.url = val
            elif key == "color":
                try:
                    embed.color = self._parse_color(val)
                except Exception as e:
                    raise ValueError(f"Invalid color parser: {e}")
            elif key == "image":
                embed.set_image(url=val)
            elif key in ("thumb", "thumbnail"):
                embed.set_thumbnail(url=val)
            elif key == "author":
                raw_fields.setdefault("author", {})["name"] = val
            elif key == "a_icon":
                raw_fields.setdefault("author", {})["icon_url"] = val
            elif key == "author_url":
                raw_fields.setdefault("author", {})["url"] = val
            elif key == "footer":
                raw_fields.setdefault("footer", {})["text"] = val
            elif key == "f_icon":
                raw_fields.setdefault("footer", {})["icon_url"] = val
            else:
                m_name = self.FIELD_NAME.match(key)
                m_value = self.FIELD_VALUE.match(key)
                m_inline = self.FIELD_INLINE.match(key)
                if m_name:
                    idx = int(m_name.group(1))
                    raw_fields.setdefault("fields", {}).setdefault(idx, {})["name"] = val
                elif m_value:
                    idx = int(m_value.group(1))
                    raw_fields.setdefault("fields", {}).setdefault(idx, {})["value"] = val
                elif m_inline:
                    idx = int(m_inline.group(1))
                    raw_fields.setdefault("fields", {}).setdefault(idx, {})["inline"] = val.lower() in ("true", "1", "yes", "y")
                else:
                    raw_fields.setdefault("unknown", {})[key] = val

        if "author" in raw_fields:
            author = raw_fields["author"]
            embed.set_author(
                name=author.get("name", discord.Embed.Empty),
                icon_url=author.get("icon_url", discord.Embed.Empty),
                url=author.get("url", discord.Embed.Empty),
            )

        if "footer" in raw_fields:
            footer = raw_fields["footer"]
            embed.set_footer(
                text=footer.get("text", discord.Embed.Empty),
                icon_url=footer.get("icon_url", discord.Embed.Empty),
            )

        for idx in sorted(raw_fields.get("fields", {})):
            field_data = raw_fields["fields"][idx]
            if "name" in field_data and "value" in field_data:
                embed.add_field(
                    name=field_data["name"],
                    value=field_data["value"],
                    inline=field_data.get("inline", False),
                )

        return embed
