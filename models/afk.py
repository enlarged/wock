from beanie import Document
from datetime import datetime
from typing import Optional

class AFK(Document):
    """AFK status tracking"""
    guild_id: int
    user_id: int
    message: str
    timestamp: datetime
    
    class Settings:
        name = "afk_users"
