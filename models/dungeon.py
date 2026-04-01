from beanie import Document
from pydantic import Field
from typing import Dict

class DungeonData(Document):
    user_id: int = Field(unique=True)
    hp: int = 100
    max_hp: int = 100
    level: int = 1
    xp: int = 0
    gear: Dict[str, str] = Field(default_factory=lambda: {"weapon": "Rusty Sword", "armor": "Leather Tunic"})

    class Settings:
        name = "dungeon_data"
