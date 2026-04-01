from beanie import Document
from typing import Optional

class LastfmData(Document):
    user_id: int
    username: str
    custom_embed_template: Optional[str] = None

    class Settings:
        name = "lastfm"