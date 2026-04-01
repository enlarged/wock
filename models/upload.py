from datetime import datetime
from typing import Optional

from beanie import Document
from pydantic import Field


class Upload(Document):
    user_id: int
    uploader_name: str
    url: str
    file_name: str = Field(unique=True)
    is_nsfw: bool = False
    is_private: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "uploads"
