from beanie import Document
from typing import Optional
from datetime import datetime

class ModCase(Document):
    """Moderation case tracking"""
    guild_id: int
    user_id: int  # The user who was moderated
    moderator_id: int  # The user who took the action
    action: str  # "ban", "kick", "timeout", "purge", etc.
    reason: str
    timestamp: datetime
    case_number: int
    duration: Optional[str] = None  # For timeouts
    
    class Settings:
        name = "modcases"
