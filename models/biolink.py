from datetime import datetime
from typing import List, Optional

from beanie import Document
from pydantic import BaseModel, Field


class BioConnection(BaseModel):
    platform: str
    username: str


class BioProfile(Document):
    user_id: int = Field(unique=True)
    username: str = Field(unique=True)
    display_name: Optional[str] = None
    description: str = "welcome to my profile"
    avatar_url: Optional[str] = None
    background_url: Optional[str] = None
    username_color: str = "#ffffff"
    spotify_track_id: Optional[str] = None
    views: int = 0
    connections: List[BioConnection] = Field(default_factory=list)
    group_id: Optional[str] = None
    group_role: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "bio_profiles"


class BioGroup(Document):
    name: str
    owner_id: int
    admin_ids: List[int] = Field(default_factory=list)
    members: List[int] = Field(default_factory=list)
    emoji: str = "👥"
    icon_url: Optional[str] = None
    color: int = 0x242429
    banner_url: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "bio_groups"
