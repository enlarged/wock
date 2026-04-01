from beanie import Document
from typing import Optional, List
from datetime import datetime


class StarboardConfig(Document):
    """Starboard configuration for a guild"""
    guild_id: int
    starboard_channel_id: Optional[int] = None
    emoji: str = "⭐"
    threshold: int = 5
    color: int = 0xFFD700  # Gold color
    allow_self_star: bool = False
    allow_jump_url: bool = True
    allow_timestamp: bool = True
    allow_attachments: bool = True
    locked: bool = False
    ignored_channels: List[int] = []
    ignored_members: List[int] = []
    ignored_roles: List[int] = []
    
    class Settings:
        name = "starboard_configs"


class StarboardPost(Document):
    """Track starboard posts"""
    guild_id: int
    original_message_id: int
    original_channel_id: int
    original_author_id: int
    starboard_message_id: Optional[int] = None
    starboard_channel_id: int
    reaction_count: int = 0
    content: str
    author_name: str
    author_avatar: Optional[str] = None
    attachments: List[str] = []
    created_at: datetime = None
    
    class Settings:
        name = "starboard_posts"
