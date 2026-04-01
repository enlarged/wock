from beanie import Document
from typing import Optional, List
from datetime import datetime


class Giveaway(Document):
    """Giveaway tracking"""
    guild_id: int
    channel_id: int
    message_id: int
    host_ids: List[int]  # Users who can edit/manage the giveaway
    
    # Giveaway settings
    prize: str
    winners_count: int
    end_time: datetime
    
    # Requirements
    min_level: Optional[int] = None
    max_level: Optional[int] = None
    min_account_age_days: Optional[int] = None
    min_server_stay_days: Optional[int] = None
    required_roles: List[int] = []  # Roles required to enter
    award_roles: List[int] = []  # Roles to give winners
    
    # Embed customization
    description: Optional[str] = None
    image_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    color: int = 0x242429
    
    # Entries and state
    entries: List[int] = []  # User IDs who entered
    winners: List[int] = []  # User IDs who won
    is_active: bool = True
    created_at: datetime = None
    ended_at: Optional[datetime] = None
    
    class Settings:
        name = "giveaways"
