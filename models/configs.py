from beanie import Document
from typing import Optional, Dict
from pydantic import Field
from datetime import datetime
import secrets

class GuildConfig(Document):
    guild_id: int = Field(unique=True)
    prefix: str = ";"

    modlog_channel_id: Optional[int] = None
    imute_role_id: Optional[int] = None
    rmute_role_id: Optional[int] = None
    mute_role_id: Optional[int] = None
    jail_role_id: Optional[int] = None
    jail_category_id: Optional[int] = None
    jail_channel_id: Optional[int] = None
    jail_remove_roles: bool = False
    autonick: Optional[str] = None
    staff_roles: list = Field(default_factory=list)

    dj_role_id: Optional[int] = None

    aliases: Dict[str, str] = Field(default_factory=dict)
    tags: Dict[str, dict] = Field(default_factory=dict)

    sticky_messages: Dict[str, dict] = Field(default_factory=dict)

    autoresponders: Dict[str, dict] = Field(default_factory=dict)
    invoke_messages: Dict[str, dict] = Field(default_factory=dict)

    ignored_members: list = Field(default_factory=list)
    ignored_channels: list = Field(default_factory=list)

    voicemaster_enabled: bool = False
    voicemaster_channel_id: Optional[int] = None
    voicemaster_interface_channel_id: Optional[int] = None
    voicemaster_interface_message_id: Optional[int] = None
    voicemaster_user_channels: Dict[str, int] = Field(default_factory=dict)

    twitch_feeds: Dict[str, int] = Field(default_factory=dict)

    tumblr_feeds: Dict[str, int] = Field(default_factory=dict)
    tumblr_last_posts: Dict[str, str] = Field(default_factory=dict)

    soundcloud_feeds: Dict[str, int] = Field(default_factory=dict)
    soundcloud_last_tracks: Dict[str, str] = Field(default_factory=dict)

    filter_invites: bool = False
    filter_invites_action: str = "kick"
    filter_spam: bool = False
    filter_spam_action: str = "kick"
    filter_words: bool = False
    filter_words_action: str = "kick"
    filtered_words: list = Field(default_factory=list)

    customization_presets: Dict[str, dict] = Field(default_factory=dict)

    reaction_roles: Dict[str, dict] = Field(default_factory=dict)

    disabled_commands: Dict[str, dict] = Field(default_factory=dict)
    disabled_modules: Dict[str, dict] = Field(default_factory=dict)
    fake_permissions: Dict[str, list] = Field(default_factory=dict)
    command_restrictions: Dict[str, list] = Field(default_factory=dict)

    leveling_enabled: bool = False
    level_roles: Dict[int, int] = Field(default_factory=dict)
    level_channel_id: Optional[int] = None
    level_message: Optional[str] = None

    ticket_category_id: Optional[int] = None
    ticket_transcript_channel_id: Optional[int] = None
    ticket_counter: int = 0
    ticket_roles: list = Field(default_factory=list)

    autoroles: list = Field(default_factory=list)

    button_roles: Dict[str, list] = Field(default_factory=dict)

    reaction_triggers: Dict[str, dict] = Field(default_factory=dict)
    previous_react_triggers: Dict[str, dict] = Field(default_factory=dict)
    auto_reactions: Dict[str, list] = Field(default_factory=dict)

    noselfreact_enabled: bool = False
    noselfreact_staff_bypass: bool = False
    noselfreact_exempt_members: list = Field(default_factory=list)
    noselfreact_exempt_channels: list = Field(default_factory=list)
    noselfreact_exempt_roles: list = Field(default_factory=list)
    noselfreact_monitored_emojis: list = Field(default_factory=list)
    noselfreact_punishment: str = "kick"

    booster_role_enabled: bool = False
    booster_role_base_id: Optional[int] = None
    booster_user_roles: Dict[str, int] = Field(default_factory=dict)

    gallery_channels: list = Field(default_factory=list)

    scheduled_nukes: Dict[str, dict] = Field(default_factory=dict)

    antinuke_enabled: bool = False
    antinuke_action: str = "ban"
    antinuke_threshold: int = 3
    antinuke_whitelist: list = Field(default_factory=list)
    antinuke_trusted: list = Field(default_factory=list)
    antinuke_modules: Dict[str, bool] = Field(default_factory=lambda: {
        "ban": False, "kick": False, "prune": False, "channelcreate": False,
        "channel": False, "rolecreate": False, "role": False, "botadd": False,
        "emoji": False, "integration": False, "webhooks": False, "guild": False,
        "vanity": False
    })

    class Settings:
        name = "guild_configs"

class UserConfig(Document):
    user_id: int = Field(unique=True)
    prefix: Optional[str] = None
    api_authorized: bool = False
    api_key: Optional[str] = None
    
    balance: int = 0
    bank: int = 0
    security_features: Dict[str, bool] = Field(default_factory=dict)
    
    lastfm_username: Optional[str] = None
    
    username_history: list = Field(default_factory=list)
    
    xp: int = 0
    level: int = 1

    class Settings:
        name = "user_configs"

class ScheduledMessage(Document):
    """Document for scheduled messages"""
    guild_id: int
    channel_id: int
    author_id: int
    content: str
    scheduled_time: datetime
    message_id: str = Field(default_factory=lambda: secrets.token_hex(8))
    
    class Settings:
        name = "scheduled_messages"

class Reminder(Document):
    """Document for user reminders"""
    user_id: int
    content: str
    reminder_time: datetime
    reminder_id: str = Field(default_factory=lambda: secrets.token_hex(8))
    
    class Settings:
        name = "reminders"