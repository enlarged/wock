from beanie import Document
from pydantic import Field
from datetime import datetime
from typing import Dict, List

class GardenPlot(dict):
    pass

class GardenData(Document):
    user_id: int = Field(unique=True)
    seeds: Dict[str, int] = Field(default_factory=dict)
    plots: List[Dict] = Field(default_factory=list)

    class Settings:
        name = "garden_data"
