from beanie import Document
from pydantic import Field

class EntertainmentInteraction(Document):
    """Track entertainment interaction counts between users"""
    user1_id: int
    user2_id: int
    interaction_type: str  # hug, kiss, cuddle, etc
    count: int = 1
    
    class Settings:
        name = "roleplay_interactions"


class MuhaProfile(Document):
    """Track muha hit count and selected flavor per user"""
    user_id: int = Field(unique=True)
    flavor: str = "mango"
    hits: int = 0

    class Settings:
        name = "muha_profiles"
