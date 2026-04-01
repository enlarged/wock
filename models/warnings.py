from beanie import Document
from datetime import datetime

class Warning(Document):
    """User warning tracking"""
    guild_id: int
    user_id: int
    moderator_id: int
    reason: str
    timestamp: datetime
    case_number: int  # Links to ModCase for full context
    
    class Settings:
        name = "warnings"
