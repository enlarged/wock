from beanie import Document
from pydantic import Field
from datetime import datetime
from typing import Optional


class Gang(Document):
    guild_id: int
    name: str
    owner_id: int
    admin_ids: list[int] = Field(default_factory=list)
    member_ids: list[int] = Field(default_factory=list)
    emoji: str = "🛡️"
    color: int = 0x242429
    banner_url: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "gangs"
