import discord
import os
import asyncio
import shutil
import re
import html
import json
import time
import hmac
import base64
import secrets
import hashlib
import resource
from datetime import datetime
import motor.motor_asyncio
import aiohttp
from aiohttp import web
from urllib.parse import urlencode, quote
from discord.ext import commands
from dotenv import load_dotenv
from beanie import init_beanie

from utils.logger import logger
from models.lastfm import LastfmData
from models.configs import GuildConfig, UserConfig
from models.moderation import ModCase
from models.warnings import Warning
from models.afk import AFK
from models.entertainment import EntertainmentInteraction, MuhaProfile
from models.giveaway import Giveaway
from models.starboard import StarboardConfig, StarboardPost 
from models.gang import Gang
from models.garden import GardenData
from models.dungeon import DungeonData
from models.biolink import BioProfile, BioGroup
from models.upload import Upload

load_dotenv()
TOKEN = os.getenv('WOCK_TOKEN')
OWNER_ID = os.getenv('OWNER_ID')
MONGO_URI = os.getenv('MONGO_URI', '')
DEFAULT_PREFIX = os.getenv('WOCK_PREFIX', ';')
AUTO_START_TUNNEL = os.getenv('WOCK_AUTOSTART_TUNNEL', 'true').lower() in {'1', 'true', 'yes', 'on'}
WEB_HOST = os.getenv('WOCK_WEB_HOST', '127.0.0.1')
WEB_PORT = int(os.getenv('WOCK_WEB_PORT', '3000'))
DISCORD_CLIENT_ID = ''
DISCORD_CLIENT_SECRET = ''
DISCORD_REDIRECT_URI = 'https://wock.best/api/auth/callback'
WEB_SESSION_SECRET = os.getenv('WOCK_WEB_SESSION_SECRET') or TOKEN or 'wock-web-session-secret'

_ORIGINAL_HAS_PERMISSIONS = commands.has_permissions
_ORIGINAL_HAS_GUILD_PERMISSIONS = commands.has_guild_permissions

async def get_prefix(bot, message):
    prefixes = [f"<@!{bot.user.id}> ", f"<@{bot.user.id}> "]
    
    if not hasattr(bot, 'db_client'):
        return prefixes + [DEFAULT_PREFIX]

    try:
        user_cfg = await UserConfig.find_one(UserConfig.user_id == message.author.id)
        if user_cfg and user_cfg.prefix:
            prefixes.append(user_cfg.prefix)
            return prefixes

        if message.guild:
            guild_cfg = await GuildConfig.find_one(GuildConfig.guild_id == message.guild.id)
            if guild_cfg and guild_cfg.prefix:
                prefixes.append(guild_cfg.prefix)
                return prefixes
    except Exception as e:
        logger("error", f"Prefix lookup failed: {e}")

    prefixes.append(DEFAULT_PREFIX)
    return prefixes

class WockBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        owner_ids = {int(OWNER_ID)} if OWNER_ID else set()
        
        super().__init__(
            command_prefix=get_prefix, 
            intents=intents,
            owner_ids=owner_ids,
            help_command=None 
        )
        
        self.config = {
            "lastfm": "",
            "wock_api": "",
            "color": 0x242429
        }
        self.startup_time = discord.utils.utcnow()
        self.session: aiohttp.ClientSession = None
        self.tunnel_process: asyncio.subprocess.Process | None = None
        self.web_runner: web.AppRunner | None = None
        self._patch_permission_checks()

    async def _start_web_server(self):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        views_dir = os.path.join(base_dir, "website", "views")
        public_dir = os.path.join(base_dir, "website", "public")
        session_secret = WEB_SESSION_SECRET.encode("utf-8")
        oauth_client_id = DISCORD_CLIENT_ID or (str(self.user.id) if self.user else None)
        oauth_client_secret = DISCORD_CLIENT_SECRET
        oauth_redirect_uri = DISCORD_REDIRECT_URI

        def _error_message(code: int) -> str:
            message_map = {
                400: "Bad request. The server could not process your request.",
                401: "Authentication is required to access this resource.",
                403: "Access denied. Your current credentials do not permit access to this resource.",
                404: "The page you're looking for doesn't exist or has been moved.",
                405: "Method not allowed for this endpoint.",
                429: "Too many requests. Please wait and try again.",
                500: "Internal server error. We hit an unexpected fault while processing this request.",
                502: "Bad gateway. The upstream service returned an invalid response.",
                503: "Service temporarily unavailable. Please try again shortly.",
            }
            return message_map.get(code, "An unexpected exception occurred. Please try again in a moment.")

        def _render_error(code: int = 404):
            safe_code = code if 100 <= int(code) <= 599 else 500
            return _render_view(
                "pages/system/error",
                replacements={
                    "errorCode": safe_code,
                    "errorMessage": _error_message(safe_code),
                },
                status=safe_code,
            )

        def _render_view(name: str, replacements: dict | None = None, status: int = 200, raw_replacements: dict | None = None):
            file_path = os.path.join(views_dir, f"{name}.ejs")
            if not os.path.exists(file_path):
                fallback = "Template not found"
                if name == "pages/system/error":
                    return web.Response(text=fallback, content_type="text/plain", status=500)
                return _render_error(404)

            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            raw_replacements = raw_replacements or {}
            for key, value in raw_replacements.items():
                content = content.replace(f"<%- {key} %>", str(value))

            replacements = replacements or {}
            for key, value in replacements.items():
                safe_value = html.escape(str(value))
                content = content.replace(f"<%= {key} %>", safe_value)

            content = re.sub(r"<%[\s\S]*?%>", "", content)
            return web.Response(text=content, content_type="text/html", status=status)

        def _is_api_request(request: web.Request) -> bool:
            return request.path.startswith("/api")

        def _encode_session(payload: dict) -> str:
            blob = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            token = base64.urlsafe_b64encode(blob).decode("ascii").rstrip("=")
            sig = hmac.new(session_secret, token.encode("utf-8"), hashlib.sha256).hexdigest()
            return f"{token}.{sig}"

        def _decode_session(token: str | None) -> dict | None:
            if not token or "." not in token:
                return None
            raw, sig = token.rsplit(".", 1)
            expected = hmac.new(session_secret, raw.encode("utf-8"), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(sig, expected):
                return None
            padded = raw + ("=" * (-len(raw) % 4))
            try:
                payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
            except Exception:
                return None
            if not isinstance(payload, dict):
                return None
            if int(payload.get("exp", 0)) <= int(time.time()):
                return None
            return payload

        def _set_dashboard_auth_cookie(response: web.StreamResponse, payload: dict):
            response.set_cookie(
                "wock_dash_auth",
                _encode_session(payload),
                max_age=60 * 60 * 24 * 7,
                httponly=True,
                samesite="Lax",
                secure=False,
                path="/",
            )

        def _clear_dashboard_auth_cookie(response: web.StreamResponse):
            response.del_cookie("wock_dash_auth", path="/")

        def _dashboard_session(request: web.Request) -> dict | None:
            return _decode_session(request.cookies.get("wock_dash_auth"))

        async def _fetch_discord_user(access_token: str) -> dict | None:
            headers = {"Authorization": f"Bearer {access_token}"}
            async with self.session.get("https://discord.com/api/v10/users/@me", headers=headers) as resp:
                if resp.status != 200:
                    return None
                return await resp.json()

        async def _fetch_admin_dashboard_guild_ids(access_token: str) -> set[int]:
            headers = {"Authorization": f"Bearer {access_token}"}
            async with self.session.get("https://discord.com/api/v10/users/@me/guilds", headers=headers) as resp:
                if resp.status != 200:
                    return set()
                guilds = await resp.json()

            admin_bit = 0x00000008
            permitted_ids = set()
            for g in guilds:
                try:
                    gid = int(g.get("id", 0))
                except Exception:
                    continue
                is_owner = bool(g.get("owner"))
                perms_val = int(g.get("permissions", "0") or "0")
                if is_owner or (perms_val & admin_bit):
                    if self.get_guild(gid):
                        permitted_ids.add(gid)
            return permitted_ids

        def _dashboard_unauthorized(request: web.Request):
            if _is_api_request(request):
                return web.json_response({"error": "Unauthorized"}, status=401)
            next_path = request.path_qs if request.path_qs.startswith("/dashboard") else "/dashboard"
            raise web.HTTPFound(location=f"/dashboard/login?next={next_path}")

        def _dashboard_forbidden(request: web.Request):
            if _is_api_request(request):
                return web.json_response({"error": "Forbidden"}, status=403)
            return _render_error(403)

        def _session_guild_ids(session_payload: dict) -> set[int]:
            out = set()
            for gid in session_payload.get("guild_ids", []):
                try:
                    out.add(int(gid))
                except Exception:
                    continue
            return out

        def _oauth_redirect_uri_for(request: web.Request) -> str:
            # Always use the configured redirect URI (defaults to https://wock.best/api/auth/callback)
            return oauth_redirect_uri

        @web.middleware
        async def error_middleware(request, handler):
            try:
                response = await handler(request)
                if response.status >= 400 and not _is_api_request(request):
                    return _render_error(response.status)
                return response
            except web.HTTPException as exc:
                if exc.status >= 400 and not _is_api_request(request):
                    return _render_error(exc.status)
                raise
            except Exception:
                if _is_api_request(request):
                    return web.json_response({"error": "Internal Server Error"}, status=500)
                return _render_error(500)

        async def home(_request: web.Request):
            return _render_view("pages/main/index")

        async def api_docs(_request: web.Request):
            return _render_view("pages/docs/api_docs")

        API_ENDPOINTS = [
            {"method": "GET", "path": "/api/stats", "desc": "Live bot stats and shard telemetry", "category": "Core", "requiresAuth": False, "params": []},
            {"method": "GET", "path": "/api/commands", "desc": "List command modules and commands", "category": "Core", "requiresAuth": False, "params": []},
            {"method": "GET", "path": "/api/soundcloud", "desc": "Search SoundCloud tracks/users/playlists", "category": "Music", "requiresAuth": True, "params": ["query", "type"]},
            {"method": "GET", "path": "/api/fortnite/lookup", "desc": "Find Fortnite cosmetics", "category": "Miscellaneous", "requiresAuth": True, "params": ["query"]},
            {"method": "GET", "path": "/api/fortnite/map", "desc": "Get current Fortnite map image", "category": "Miscellaneous", "requiresAuth": True, "params": []},
            {"method": "GET", "path": "/api/fortnite/news", "desc": "Get latest Fortnite BR news", "category": "Miscellaneous", "requiresAuth": True, "params": []},
            {"method": "GET", "path": "/api/fortnite/shop", "desc": "Get daily Fortnite shop image", "category": "Miscellaneous", "requiresAuth": True, "params": []},
            {"method": "GET", "path": "/api/urban", "desc": "Search Urban Dictionary", "category": "Miscellaneous", "requiresAuth": True, "params": ["query"]},
            {"method": "GET", "path": "/api/weather", "desc": "Get weather data by city", "category": "Miscellaneous", "requiresAuth": True, "params": ["city"]},
            {"method": "GET", "path": "/api/anime", "desc": "Search anime using Jikan", "category": "Miscellaneous", "requiresAuth": True, "params": ["query"]},
            {"method": "GET", "path": "/api/anime/character", "desc": "Search anime characters", "category": "Miscellaneous", "requiresAuth": True, "params": ["query"]},
            {"method": "GET", "path": "/api/pokemon", "desc": "Get Pokémon data by name", "category": "Miscellaneous", "requiresAuth": True, "params": ["name"]},
            {"method": "GET", "path": "/api/crypto", "desc": "Get crypto market data", "category": "Miscellaneous", "requiresAuth": True, "params": ["query"]},
        ]

        async def api_endpoints(_request: web.Request):
            endpoints = sorted(API_ENDPOINTS, key=lambda e: (e["category"], e["path"]))
            return web.json_response({"success": True, "endpoints": endpoints})

        def _uptime_seconds() -> int:
            delta = discord.utils.utcnow() - self.startup_time
            return max(int(delta.total_seconds()), 0)

        def _memory_mb() -> float:
            # Linux ru_maxrss is KiB
            return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0

        async def api_stats(_request: web.Request):
            try:
                guilds = list(self.guilds)
                total_guilds = len(guilds)
                total_users = sum((g.member_count or 0) for g in guilds)

                sorted_guilds = sorted(
                    [g for g in guilds if (g.member_count or 0) >= 150],
                    key=lambda g: g.member_count or 0,
                    reverse=True,
                )[:15]

                display_guilds = [
                    {
                        "name": g.name,
                        "members": f"{(g.member_count or 0):,}",
                        "icon": (g.icon.url if g.icon else "https://cdn.discordapp.com/embed/avatars/0.png"),
                    }
                    for g in sorted_guilds
                ]

                shard_stats = []
                raw_shards = getattr(self, "shards", None) or {}
                if raw_shards:
                    for shard_id, shard in raw_shards.items():
                        shard_guilds = [g for g in guilds if g.shard_id == shard_id]
                        shard_users = sum((g.member_count or 0) for g in shard_guilds)
                        shard_stats.append(
                            {
                                "id": shard_id,
                                "status": "Operational" if not shard.is_closed() else "Issues",
                                "ping": round((shard.latency or 0) * 1000, 2),
                                "uptime": _uptime_seconds(),
                                "guilds": len(shard_guilds),
                                "users": shard_users,
                            }
                        )
                else:
                    # Single-shard (non-AutoShardedBot)
                    shard_stats.append(
                        {
                            "id": 0,
                            "status": "Operational",
                            "ping": round((self.latency or 0) * 1000, 2),
                            "uptime": _uptime_seconds(),
                            "guilds": total_guilds,
                            "users": total_users,
                        }
                    )

                return web.json_response(
                    {
                        "avatar": str(self.user.display_avatar.url) if self.user else None,
                        "name": str(self.user.name) if self.user else "Wock",
                        "uptime": _uptime_seconds(),
                        "ping": round((self.latency or 0) * 1000),
                        "guilds": total_guilds,
                        "users": total_users,
                        "memory": f"{_memory_mb():.2f}",
                        "shards": shard_stats,
                        "displayGuilds": display_guilds,
                    }
                )
            except Exception as e:
                logger("error", f"Error in /api/stats: {e}")
                return web.json_response({"error": "Internal Server Error"}, status=500)

        async def api_commands(_request: web.Request):
            commands_by_module: dict[str, list[dict]] = {}

            def _is_excluded(cmd: commands.Command) -> bool:
                cog = (cmd.cog_name or "").lower()
                if cog in {"developer", "jishaku"}:
                    return True
                qn = (cmd.qualified_name or "").lower()
                if qn.startswith("jsk") or qn.startswith("jishaku"):
                    return True
                return False

            def _serialize_command(cmd: commands.Command, inherited_permissions: str = "none") -> dict:
                callback = getattr(cmd, "callback", None)
                description = (
                    getattr(callback, "command_description", None)
                    or cmd.help
                    or cmd.brief
                    or "No description available."
                )
                syntax = (
                    getattr(callback, "command_syntax", None)
                    or f"{DEFAULT_PREFIX}{cmd.qualified_name} {cmd.signature}".strip()
                )
                permissions = getattr(callback, "command_permissions", None) or inherited_permissions or "none"

                data = {
                    "name": cmd.name,
                    "aliases": list(getattr(cmd, "aliases", []) or []),
                    "description": description,
                    "syntax": syntax,
                    "permissions": permissions,
                    "subcommands": [],
                }

                if isinstance(cmd, commands.Group):
                    nested = []
                    for sub in cmd.commands:
                        if _is_excluded(sub):
                            continue
                        nested.append(_serialize_command(sub, inherited_permissions=permissions))
                    data["subcommands"] = nested

                return data

            for cmd in self.commands:
                if _is_excluded(cmd):
                    continue

                module = (cmd.cog_name or "Uncategorized")
                commands_by_module.setdefault(module, []).append(_serialize_command(cmd))

            return web.json_response(commands_by_module)

        async def validate_api_key(request: web.Request):
            key = request.headers.get("x-api-key", "").strip()
            if not key:
                return None, web.json_response({"error": "API key is required."}, status=401)

            # Keep legacy master key support for internal usage.
            if key == self.config.get("wock_api"):
                return {"source": "master"}, None

            if not hasattr(self, "db_client"):
                return None, web.json_response({"error": "Database unavailable."}, status=503)

            user_cfg = await UserConfig.find_one(
                UserConfig.api_key == key,
                UserConfig.api_authorized == True,
            )
            if not user_cfg:
                return None, web.json_response({"error": "Invalid API key."}, status=403)

            return user_cfg, None

        async def api_soundcloud(request: web.Request):
            _, auth_error = await validate_api_key(request)
            if auth_error:
                return auth_error

            query = request.query.get("query", "").strip()
            req_type = request.query.get("type", "tracks")
            auth = "OAuth 2-292593-994587358-Af8VbLnc6zIplJ"

            if not query:
                return web.json_response({"error": "Please provide a 'query' parameter."}, status=400)

            search_type = req_type.lower()
            if search_type in {"artist", "user"}:
                search_type = "users"
            if search_type == "playlist":
                search_type = "playlists"

            try:
                timeout = aiohttp.ClientTimeout(total=8)
                async with self.session.get(
                    f"https://api-v2.soundcloud.com/search/{search_type}",
                    params={"q": query, "limit": 1},
                    headers={"Authorization": auth},
                    timeout=timeout,
                ) as resp:
                    data = await resp.json(content_type=None)

                collection = (data or {}).get("collection") or []
                if not collection:
                    return web.json_response({"error": f"No {req_type} found for that query."}, status=404)

                top = collection[0]
                return web.json_response(
                    {
                        "success": True,
                        "type": req_type,
                        "data": {
                            "id": top.get("id"),
                            "title": top.get("title") or top.get("username"),
                            "url": top.get("permalink_url"),
                            "artwork": top.get("artwork_url") or top.get("avatar_url"),
                            "user": (top.get("user") or {}).get("username") or top.get("username"),
                            "duration": top.get("full_duration"),
                            "playback_count": top.get("playback_count") or 0,
                        },
                    }
                )
            except Exception as error:
                return web.json_response({"error": "SoundCloud API error.", "message": str(error)}, status=500)

        async def api_fortnite_lookup(request: web.Request):
            _, auth_error = await validate_api_key(request)
            if auth_error:
                return auth_error

            query = request.query.get("query", "").strip()
            if not query:
                return web.json_response({"error": "Please provide a cosmetic name to search for."}, status=400)

            try:
                async with self.session.get(
                    "https://fnbr.co/api/images",
                    params={"search": query},
                    headers={"x-api-key": "816ece3e-a07d-4856-a7de-101b00279ddf"},
                ) as resp:
                    data = await resp.json(content_type=None)

                if data.get("status") != 200 or not (data.get("data") or []):
                    return web.json_response({"error": "No Fortnite cosmetics found."}, status=404)

                return web.json_response({"success": True, "data": data["data"][0]})
            except Exception as error:
                return web.json_response({"error": "Fortnite API error.", "message": str(error)}, status=500)

        async def api_fortnite_map(request: web.Request):
            _, auth_error = await validate_api_key(request)
            if auth_error:
                return auth_error

            try:
                async with self.session.get("https://fortnite-api.com/v1/map") as resp:
                    data = await resp.json(content_type=None)

                return web.json_response(
                    {
                        "success": True,
                        "map_image": (((data or {}).get("data") or {}).get("images") or {}).get("pois"),
                    }
                )
            except Exception:
                return web.json_response({"error": "Failed to fetch Fortnite map."}, status=500)

        async def api_fortnite_news(request: web.Request):
            _, auth_error = await validate_api_key(request)
            if auth_error:
                return auth_error

            try:
                async with self.session.get("https://fortnite-api.com/v2/news") as resp:
                    data = await resp.json(content_type=None)

                motds = ((((data or {}).get("data") or {}).get("br") or {}).get("motds") or [])
                news = [
                    {"title": n.get("title"), "body": n.get("body"), "image": n.get("image")}
                    for n in motds
                ]
                return web.json_response({"success": True, "news": news})
            except Exception:
                return web.json_response({"error": "Failed to fetch Fortnite news."}, status=500)

        async def api_fortnite_shop(request: web.Request):
            _, auth_error = await validate_api_key(request)
            if auth_error:
                return auth_error

            now = datetime.utcnow()
            date_str = f"{now.day}-{now.month}-{now.year}"
            shop_image_url = f"https://bot.fnbr.co/shop-image/fnbr-shop-{date_str}.png"
            return web.json_response({"success": True, "shop_image": shop_image_url})

        async def api_urban(request: web.Request):
            _, auth_error = await validate_api_key(request)
            if auth_error:
                return auth_error

            query = request.query.get("query", "").strip()
            if not query:
                return web.json_response({"error": "Please provide a 'query' parameter."}, status=400)

            try:
                async with self.session.get(
                    "https://api.urbandictionary.com/v0/define",
                    params={"term": query},
                ) as resp:
                    data = await resp.json(content_type=None)

                results = (data or {}).get("list") or []
                if not results:
                    return web.json_response({"error": "No definitions found for that query."}, status=404)

                formatted = []
                for result in results:
                    formatted.append(
                        {
                            "word": result.get("word"),
                            "definition": (result.get("definition") or "").replace("[", "").replace("]", ""),
                            "example": (result.get("example") or "").replace("[", "").replace("]", ""),
                            "permalink": result.get("permalink"),
                            "stats": {
                                "upvotes": result.get("thumbs_up"),
                                "downvotes": result.get("thumbs_down"),
                            },
                            "author": result.get("author"),
                            "created_at": result.get("written_on"),
                        }
                    )

                return web.json_response({"success": True, "results": formatted})
            except Exception as error:
                return web.json_response({"error": "Urban Dictionary API error.", "message": str(error)}, status=500)

        async def api_weather(request: web.Request):
            _, auth_error = await validate_api_key(request)
            if auth_error:
                return auth_error

            city = request.query.get("city", "").strip()
            if not city:
                return web.json_response({"error": "Please provide a 'city' parameter."}, status=400)

            try:
                async with self.session.get(
                    "https://api.openweathermap.org/data/2.5/weather",
                    params={
                        "q": city,
                        "appid": "985f10d327f3695fa10aab134e0b6391",
                        "units": "metric",
                    },
                ) as resp:
                    if resp.status == 404:
                        return web.json_response({"error": "City not found."}, status=404)
                    data = await resp.json(content_type=None)

                temp_c = float(((data.get("main") or {}).get("temp") or 0))
                temp_f = (temp_c * 9 / 5) + 32
                weather = (data.get("weather") or [{}])[0]

                return web.json_response(
                    {
                        "success": True,
                        "location": {
                            "city": data.get("name"),
                            "country": (data.get("sys") or {}).get("country"),
                            "coordinates": data.get("coord"),
                        },
                        "weather": {
                            "main": weather.get("main"),
                            "description": weather.get("description"),
                            "icon": f"https://openweathermap.org/img/wn/{weather.get('icon')}@2x.png",
                        },
                        "stats": {
                            "temp_c": f"{temp_c:.1f}",
                            "temp_f": f"{temp_f:.1f}",
                            "feels_like_c": f"{float(((data.get('main') or {}).get('feels_like') or 0)):.1f}",
                            "humidity": (data.get("main") or {}).get("humidity"),
                            "wind_speed": (data.get("wind") or {}).get("speed"),
                            "visibility_km": f"{float((data.get('visibility') or 0) / 1000):.1f}",
                        },
                        "astronomy": {
                            "sunrise": (data.get("sys") or {}).get("sunrise"),
                            "sunset": (data.get("sys") or {}).get("sunset"),
                        },
                    }
                )
            except Exception as error:
                return web.json_response({"error": "Weather API error.", "message": str(error)}, status=500)

        async def api_anime(request: web.Request):
            _, auth_error = await validate_api_key(request)
            if auth_error:
                return auth_error

            query = request.query.get("query", "").strip()
            if not query:
                return web.json_response({"error": "Please provide an anime title to search for."}, status=400)

            try:
                async with self.session.get(
                    "https://api.jikan.moe/v4/anime",
                    params={"q": query, "limit": 1},
                ) as resp:
                    result = await resp.json(content_type=None)

                anime = (result.get("data") or [None])[0]
                if not anime:
                    return web.json_response({"error": "No anime found for that query."}, status=404)

                return web.json_response(
                    {
                        "success": True,
                        "data": {
                            "title": anime.get("title"),
                            "title_english": anime.get("title_english"),
                            "url": anime.get("url"),
                            "image": (((anime.get("images") or {}).get("jpg") or {}).get("large_image_url")),
                            "synopsis": anime.get("synopsis"),
                            "stats": {
                                "score": anime.get("score"),
                                "rank": anime.get("rank"),
                                "episodes": anime.get("episodes"),
                                "status": anime.get("status"),
                                "rating": anime.get("rating"),
                            },
                            "aired": ((anime.get("aired") or {}).get("string")),
                        },
                    }
                )
            except Exception as error:
                return web.json_response({"error": "Jikan API error.", "message": str(error)}, status=500)

        async def api_anime_character(request: web.Request):
            _, auth_error = await validate_api_key(request)
            if auth_error:
                return auth_error

            query = request.query.get("query", "").strip()
            if not query:
                return web.json_response({"error": "Please provide a character name."}, status=400)

            try:
                async with self.session.get(
                    "https://api.jikan.moe/v4/characters",
                    params={"q": query, "limit": 1},
                ) as resp:
                    result = await resp.json(content_type=None)

                char = (result.get("data") or [None])[0]
                if not char:
                    return web.json_response({"error": "No character found."}, status=404)

                return web.json_response(
                    {
                        "success": True,
                        "data": {
                            "name": char.get("name"),
                            "name_kanji": char.get("name_kanji"),
                            "url": char.get("url"),
                            "image": (((char.get("images") or {}).get("jpg") or {}).get("image_url")),
                            "about": char.get("about"),
                            "favorites": char.get("favorites"),
                        },
                    }
                )
            except Exception as error:
                return web.json_response({"error": "Jikan API error.", "message": str(error)}, status=500)

        async def api_pokemon(request: web.Request):
            _, auth_error = await validate_api_key(request)
            if auth_error:
                return auth_error

            name = request.query.get("name", "").strip()
            if not name:
                return web.json_response({"error": "Please provide a 'name' query parameter."}, status=400)

            try:
                pokemon_name = re.sub(r"\s+", "-", name).lower()
                async with self.session.get(f"https://pokeapi.co/api/v2/pokemon/{pokemon_name}") as resp:
                    if resp.status == 404:
                        return web.json_response({"error": "Pokémon not found."}, status=404)
                    data = await resp.json(content_type=None)

                return web.json_response(
                    {
                        "success": True,
                        "data": {
                            "id": data.get("id"),
                            "name": data.get("name"),
                            "image": f"https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/other/official-artwork/{data.get('id')}.png",
                            "types": [t.get("type", {}).get("name") for t in (data.get("types") or [])],
                            "abilities": [
                                {
                                    "name": a.get("ability", {}).get("name"),
                                    "is_hidden": a.get("is_hidden"),
                                }
                                for a in (data.get("abilities") or [])
                            ],
                            "stats": [
                                {
                                    "name": s.get("stat", {}).get("name"),
                                    "base_stat": s.get("base_stat"),
                                }
                                for s in (data.get("stats") or [])
                            ],
                            "height": data.get("height"),
                            "weight": data.get("weight"),
                        },
                    }
                )
            except Exception as error:
                return web.json_response({"error": "PokéAPI error.", "message": str(error)}, status=500)

        async def api_crypto(request: web.Request):
            _, auth_error = await validate_api_key(request)
            if auth_error:
                return auth_error

            query = request.query.get("query", "").strip()
            if not query:
                return web.json_response(
                    {"error": "Please provide a 'query' (e.g., btc, ethereum)."},
                    status=400,
                )

            try:
                async with self.session.get(
                    "https://api.coingecko.com/api/v3/search",
                    params={"query": query},
                ) as resp:
                    search_data = await resp.json(content_type=None)

                coin = (search_data.get("coins") or [None])[0]
                if not coin:
                    return web.json_response({"error": "Coin not found."}, status=404)

                async with self.session.get(
                    "https://api.coingecko.com/api/v3/coins/markets",
                    params={"vs_currency": "usd", "ids": coin.get("id"), "sparkline": "false"},
                ) as resp:
                    market_data = await resp.json(content_type=None)

                if not market_data:
                    return web.json_response({"error": "Coin not found."}, status=404)

                market = market_data[0]
                return web.json_response(
                    {
                        "success": True,
                        "coin": {
                            "id": market.get("id"),
                            "name": market.get("name"),
                            "symbol": str(market.get("symbol", "")).upper(),
                            "rank": market.get("market_cap_rank"),
                            "image": market.get("image"),
                        },
                        "market_data": {
                            "current_price": market.get("current_price"),
                            "price_change_24h_percent": (
                                f"{market.get('price_change_percentage_24h', 0):.2f}"
                                if market.get("price_change_percentage_24h") is not None
                                else None
                            ),
                            "market_cap": market.get("market_cap"),
                            "total_volume": market.get("total_volume"),
                            "high_24h": market.get("high_24h"),
                            "low_24h": market.get("low_24h"),
                        },
                        "all_time_high": {
                            "price": market.get("ath"),
                            "change_percent": (
                                f"{market.get('ath_change_percentage', 0):.1f}"
                                if market.get("ath_change_percentage") is not None
                                else None
                            ),
                            "date": market.get("ath_date"),
                        },
                    }
                )
            except Exception as error:
                message = "Rate limited by CoinGecko" if "429" in str(error) else str(error)
                return web.json_response({"error": "Crypto API error.", "message": message}, status=500)

        async def avatars_directory(_request: web.Request):
            return _render_view("pages/profiles/avatarDirectory")

        async def avatar_user(request: web.Request):
            try:
                user_id = request.match_info.get("user_id", "")
                coll = self.db_client.wock_db.get_collection("avatars")

                user_data = await coll.find_one({"userId": user_id})
                if not user_data:
                    user_data = await coll.find_one({"user_id": user_id})

                avatars = (user_data or {}).get("avatars", [])
                if not avatars:
                    return web.Response(text="<h1>404 - No history found.</h1>", content_type="text/html", status=404)

                user = None
                try:
                    user = await self.fetch_user(int(user_id))
                except Exception:
                    user = None

                username = user.name if user else user_id
                avatar_list = list(reversed(avatars))

                cards = []
                for av in avatar_list:
                    url = html.escape(str(av.get("url", "")))
                    changed_at = html.escape(str(av.get("changedAt", "unknown")))
                    if not url:
                        continue
                    cards.append(
                        f"""
<div class=\"avatar-card\">
  <img src=\"{url}\" alt=\"avatar\" loading=\"lazy\" />
  <div class=\"meta\">{changed_at}</div>
  <a href=\"{url}\" target=\"_blank\" rel=\"noopener noreferrer\">Open</a>
</div>
"""
                    )

                page = f"""
<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Wock | {html.escape(username)}</title>
  <style>
    body {{ margin:0; font-family:Inter,system-ui,-apple-system,Segoe UI,Roboto,sans-serif; background:#07070b; color:#ececf3; }}
    .wrap {{ max-width:1100px; margin:0 auto; padding:28px; }}
    h1 {{ margin:0 0 8px; }}
    .sub {{ color:#a8a8b9; margin-bottom:18px; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(220px,1fr)); gap:14px; }}
    .avatar-card {{ background:#12121a; border:1px solid #292936; border-radius:12px; padding:10px; }}
    .avatar-card img {{ width:100%; aspect-ratio:1/1; object-fit:cover; border-radius:8px; }}
    .meta {{ font-size:12px; color:#9a9aae; margin:8px 0; }}
    a {{ color:#fff; text-decoration:none; font-size:13px; }}
  </style>
</head>
<body>
  <div class=\"wrap\">
    <h1>Avatar History</h1>
    <div class=\"sub\">{html.escape(username)} ({html.escape(user_id)})</div>
    <div class=\"grid\">{''.join(cards)}</div>
  </div>
</body>
</html>
"""
                return web.Response(text=page, content_type="text/html")
            except Exception as e:
                logger("error", f"[WEB ERROR] Avatar Lookup: {e}")
                return web.Response(text="Internal Server Error", status=500)

        async def profiles_directory(_request: web.Request):
            try:
                page_raw = _request.query.get("page", "1")
                page = max(int(page_raw), 1)
            except ValueError:
                page = 1

            page_size = 24

            try:
                profiles = await BioProfile.find_all().sort(-BioProfile.views).to_list()
            except Exception as e:
                logger("error", f"[WEB ERROR] Profile Directory: {e}")
                return _render_error(500)

            total_bios = len(profiles)
            total_pages = max((total_bios + page_size - 1) // page_size, 1)
            page = min(page, total_pages)

            start = (page - 1) * page_size
            current = profiles[start:start + page_size]

            cards = []
            for profile in current:
                username = html.escape(profile.username)
                display_name = html.escape(profile.display_name or username)
                avatar = html.escape(profile.avatar_url or "https://cdn.discordapp.com/embed/avatars/0.png")
                color = html.escape(profile.username_color or "#ffffff")
                views = f"{int(profile.views or 0):,}"
                cards.append(
                    f"""
<a href="/@{username}" class="user-card" data-name="{username.lower()}">
  <img src="{avatar}" alt="" loading="lazy">
  <span class="username" style="color: {color};">@{username}</span>
  <span class="display-name">{display_name}</span>
  <div class="view-count"><i class="far fa-eye" style="font-size: 10px;"></i> {views}</div>
</a>
"""
                )

            pagination = []
            if page > 1:
                pagination.append(f'<a href="/profiles?page={page - 1}" class="page-btn"><i class="fas fa-chevron-left"></i></a>')

            start_page = max(1, page - 2)
            end_page = min(total_pages, start_page + 4)
            if end_page - start_page < 4:
                start_page = max(1, end_page - 4)

            for i in range(start_page, end_page + 1):
                active = " active" if i == page else ""
                pagination.append(f'<a href="/profiles?page={i}" class="page-btn{active}">{i}</a>')

            if page < total_pages:
                pagination.append(f'<a href="/profiles?page={page + 1}" class="page-btn"><i class="fas fa-chevron-right"></i></a>')

            total_views = sum(int(p.views or 0) for p in profiles)

            directory_html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover" />
    <title>Wock | Bio Directory</title>
    <link rel="icon" type="image/png" id="favicon" href="/avatar.png">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500&display=swap" rel="stylesheet">
    <style>{_request.app["bio_directory_css"] if "bio_directory_css" in _request.app else ""}</style>
</head>
<body>
    <nav class="nav-container">
        <div class="dropdown">
            <div class="dropbtn">
                <img id="navAvatar" src="/logo.png" alt="">
                <span>Profiles</span>
                <i class="fas fa-chevron-down" style="font-size: 10px; opacity: 0.3;"></i>
            </div>
            <div class="dropdown-content">
                <a href="/"><i class="fas fa-home"></i> Home</a>
                <a href="/commands"><i class="fas fa-bolt"></i> Commands</a>
                <a href="/status"><i class="fas fa-signal"></i> Status</a>
                <a href="/avatars"><i class="fas fa-address-book"></i> Directory</a>
                <a href="/profiles" class="active"><i class="fas fa-user-circle"></i> Profiles</a>
                <a href="/embed"><i class="fas fa-code"></i> Embeds</a>
                <a href="/docs"><i class="fas fa-book"></i> Docs</a>
                <a href="/gallery"><i class="fas fa-images"></i> Gallery</a>
            </div>
        </div>
    </nav>

    <aside class="sidebar">
        <div class="sidebar-label">Community</div>
        <div class="stat-card"><label>Active Bios</label><span>{total_bios:,}</span></div>
        <div class="stat-card"><label>Global Reach</label><span>{total_views:,} Views</span></div>
        <div style="margin-top: auto;"><a href="/" style="text-decoration:none; color:var(--text-dim); font-size:12px; display:flex; align-items:center; gap:10px; padding:15px; background: rgba(255,255,255,0.02); border-radius:12px; border: 1px solid var(--border);"><i class="fas fa-plus"></i> Create Profile</a></div>
    </aside>

    <main class="main-content">
        <header class="content-header">
            <div class="title-area"><h1>Bio Directory</h1><p style="color:var(--text-dim); font-size: 14px; margin-top: 5px;">Explore custom identities created on wock.best</p></div>
            <div class="search-box"><i class="fas fa-search" style="color: #444; font-size: 14px;"></i><input type="text" id="bioSearch" placeholder="Search by name..." onkeyup="filterBios()"></div>
        </header>
        <div class="scroll-area">
            <div class="user-grid" id="bioGallery">{''.join(cards)}</div>
            <div class="pagination">{''.join(pagination)}</div>
        </div>
    </main>

    <script>
        async function fetchBotAvatar() {{
            try {{
                const res = await fetch('/api/stats');
                const data = await res.json();
                if(data.avatar) {{
                    document.getElementById('favicon').href = data.avatar;
                    document.getElementById('navAvatar').src = data.avatar;
                }}
            }} catch(e) {{}}
        }}
        fetchBotAvatar();

        function filterBios() {{
            const input = document.getElementById('bioSearch').value.toLowerCase();
            const cards = document.querySelectorAll('.user-card');
            cards.forEach(card => {{
                const name = card.getAttribute('data-name');
                card.style.display = name.includes(input) ? 'flex' : 'none';
            }});
        }}
    </script>
</body>
</html>
"""

            return web.Response(text=directory_html, content_type="text/html")

        async def profile_username(request: web.Request):
            try:
                username = request.match_info.get("username", "").strip().lower()
                if not username:
                    return _render_error(404)

                profile = await BioProfile.find_one(BioProfile.username == username)
                if not profile:
                    return _render_error(404)

                lfm_record = await LastfmData.find_one(LastfmData.user_id == profile.user_id)

                profile.views = int(profile.views or 0) + 1
                profile.updated_at = datetime.utcnow()
                await profile.save()

                lastfm_data = None
                if lfm_record and lfm_record.username:
                    try:
                        async with self.session.get(
                            "http://ws.audioscrobbler.com/2.0/",
                            params={
                                "method": "user.getrecenttracks",
                                "user": lfm_record.username,
                                "api_key": self.config.get("lastfm"),
                                "format": "json",
                                "limit": 1,
                            },
                            timeout=aiohttp.ClientTimeout(total=8),
                        ) as resp:
                            payload = await resp.json(content_type=None)
                            tracks = (payload or {}).get("recenttracks", {}).get("track") or []
                            if tracks:
                                lastfm_data = tracks[0]
                    except Exception as e:
                        logger("warn", f"[WEB WARN] Last.fm lookup failed for {username}: {e}")

                safe_username = html.escape(profile.username)
                safe_description = html.escape(profile.description or "welcome to my profile")
                safe_avatar = html.escape(profile.avatar_url or "https://cdn.discordapp.com/embed/avatars/0.png")
                background_raw = profile.background_url or profile.avatar_url or "https://cdn.discordapp.com/embed/avatars/0.png"
                background_attr = html.escape(background_raw, quote=True)
                safe_color = html.escape(profile.username_color or "#ffffff")
                safe_views = f"{int(profile.views or 0):,}"

                group_badge = ""
                groups = await BioGroup.find_all().to_list()
                for group in groups:
                    members = {group.owner_id, *(group.admin_ids or []), *(group.members or [])}
                    if profile.user_id in members:
                        group_name = html.escape(group.name or "group")
                        raw_slug = re.sub(r"[^a-z0-9]+", "-", (group.name or "group").lower()).strip("-") or "group"
                        group_slug = html.escape(raw_slug)
                        icon_url = html.escape(getattr(group, "icon_url", "") or "", quote=True)
                        icon_html = f'<img src="{icon_url}" alt="group icon" style="width:18px;height:18px;border-radius:50%;object-fit:cover;border:1px solid rgba(255,255,255,.2);" />' if icon_url else ''
                        role = "Owner" if profile.user_id == group.owner_id else ("Admin" if profile.user_id in (group.admin_ids or []) else "Member")
                        group_badge = (
                            f'<a href="/groups/{group_slug}" style="display:inline-flex;align-items:center;gap:8px;text-decoration:none;margin:0 auto 10px;padding:6px 12px;border:1px solid rgba(255,255,255,.12);border-radius:999px;color:#dbe0f2;background:rgba(255,255,255,.04);font-size:12px;font-weight:600;">'
                            f'{icon_html}<span>{group_name}</span><span style="opacity:.7">• {role}</span></a>'
                        )
                        break

                social_html_parts = []
                for conn in (profile.connections or []):
                    platform = html.escape((conn.platform or "").strip())
                    handle = html.escape((conn.username or "").strip())
                    if not platform or not handle:
                        continue
                    social_html_parts.append(
                        f'<span style="font-size:12px;color:#d3d8ea;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);padding:6px 10px;border-radius:999px;">{platform}: {handle}</span>'
                    )

                social_html = ""
                if social_html_parts:
                    social_html = f'<div style="display:flex;flex-wrap:wrap;gap:8px;justify-content:center;">{"".join(social_html_parts)}</div>'

                lastfm_html = ""
                if isinstance(lastfm_data, dict):
                    image_list = lastfm_data.get("image") or []
                    image_url = ""
                    if len(image_list) >= 3 and isinstance(image_list[2], dict):
                        image_url = image_list[2].get("#text", "")

                    now_playing = bool((lastfm_data.get("@attr") or {}).get("nowplaying"))
                    track_name = html.escape(str(lastfm_data.get("name") or "Unknown"))
                    artist_obj = lastfm_data.get("artist")
                    artist_name = html.escape(
                        str(artist_obj.get("#text") if isinstance(artist_obj, dict) else artist_obj or "Unknown")
                    )
                    art = html.escape(image_url or "https://www.last.fm/static/images/lastfm_avatar_twitter.66cd2c90961a.png")
                    badge = "Now Playing" if now_playing else "Last Played"
                    pulse = '<div class="pulse"></div>' if now_playing else ''
                    lastfm_html = f"""
<div class="lastfm-container">
  <img src="{art}" class="lastfm-art">
  <div class="lastfm-details">
    <div class="status-badge">{pulse}<i class="fab fa-lastfm"></i> {badge}</div>
    <span class="track-name">{track_name}</span>
    <span class="artist-name">{artist_name}</span>
  </div>
</div>
"""

                profile_html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>@{safe_username} | wock.best</title>
    <link rel="icon" type="image/png" id="favicon" href="/avatar.png">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" crossorigin="anonymous" referrerpolicy="no-referrer" />
    <style>
        :root {{ --bg-dark:#050505; --text-main:#ffffff; --text-dim:#a0a0a0; --lfm-red:#d31f27; --glass:rgba(0,0,0,.55); --glass-border:rgba(255,255,255,.1); }}
        * {{ margin:0; padding:0; box-sizing:border-box; }}
        body, html {{ height:100%; width:100%; background-color:var(--bg-dark); font-family:'Inter','Segoe UI',sans-serif; color:var(--text-main); overflow-y:auto; overflow-x:hidden; }}
        .background-blur {{ position:fixed; inset:0; background-size:cover; background-position:center; filter:blur(30px) brightness(0.3); transform:scale(1.15); z-index:-1; }}
        .main-wrapper {{ min-height:100%; display:flex; justify-content:center; align-items:center; padding:40px 20px; }}
        .profile-card {{ width:100%; max-width:420px; text-align:center; padding:30px 20px; background:var(--glass); border:1px solid var(--glass-border); backdrop-filter:blur(20px); -webkit-backdrop-filter:blur(20px); border-radius:24px; box-shadow:0 20px 50px rgba(0,0,0,.6); }}
        .avatar {{ width:90px; height:90px; border-radius:50%; object-fit:cover; margin-bottom:15px; border:2px solid rgba(255,255,255,.1); box-shadow:0 8px 20px rgba(0,0,0,.4); }}
        .username {{ font-size:22px; font-weight:800; margin:0; display:flex; align-items:center; justify-content:center; gap:6px; letter-spacing:-.5px; }}
        .description {{ color:var(--text-dim); font-size:14px; margin:10px 0 16px; line-height:1.6; word-wrap:break-word; }}
        .widget-area {{ width:100%; display:flex; flex-direction:column; gap:12px; }}
        .lastfm-container {{ background:rgba(255,255,255,.04); border:1px solid rgba(255,255,255,.06); border-radius:16px; padding:10px; display:flex; align-items:center; gap:10px; text-align:left; }}
        .lastfm-art {{ width:44px; height:44px; border-radius:8px; object-fit:cover; }}
        .lastfm-details {{ flex:1; overflow:hidden; }}
        .status-badge {{ font-size:9px; text-transform:uppercase; font-weight:800; color:var(--lfm-red); display:flex; align-items:center; gap:5px; margin-bottom:2px; letter-spacing:.5px; }}
        .pulse {{ width:6px; height:6px; background-color:var(--lfm-red); border-radius:50%; animation:pulse-red 1.5s infinite; }}
        .track-name {{ font-size:13px; font-weight:700; display:block; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
        .artist-name {{ font-size:11px; color:var(--text-dim); }}
        .view-counter {{ font-size:10px; color:var(--text-dim); margin-top:20px; opacity:.5; text-transform:uppercase; letter-spacing:2px; font-weight:600; }}
        @keyframes pulse-red {{ 0% {{ transform:scale(.95); box-shadow:0 0 0 0 rgba(211,31,39,.6); }} 70% {{ transform:scale(1); box-shadow:0 0 0 5px rgba(211,31,39,0); }} 100% {{ transform:scale(.95); box-shadow:0 0 0 0 rgba(211,31,39,0); }} }}
    </style>
</head>
<body>
        <div class="background-blur" style="background-image:url('{background_attr}');"></div>
    <div class="main-wrapper">
        <div class="profile-card">
            <img src="{safe_avatar}" class="avatar" alt="Avatar">
            <h1 class="username" style="color: {safe_color};">@{safe_username}<i class="fas fa-check-circle" style="color:#3897f0;font-size:15px;" title="Verified"></i></h1>
                        {group_badge}
            <p class="description">{safe_description}</p>
            <div class="widget-area">{lastfm_html}{social_html}</div>
            <div class="view-counter"><i class="far fa-eye"></i> {safe_views} Views</div>
        </div>
    </div>
</body>
</html>
"""
                return web.Response(text=profile_html, content_type="text/html")
            except Exception as err:
                logger("error", f"[WEB ERROR] profile /@ lookup: {err}")
                return _render_error(500)

        async def group_view(request: web.Request):
            try:
                group_slug = (request.match_info.get("groupname", "") or "").strip().lower()
                if not group_slug:
                    return _render_error(404)

                groups = await BioGroup.find_all().to_list()
                group = None
                for g in groups:
                    g_slug = re.sub(r"[^a-z0-9]+", "-", (g.name or "").strip().lower()).strip("-")
                    if (g.name or "").strip().lower() == group_slug or g_slug == group_slug:
                        group = g
                        break

                if not group:
                    return _render_error(404)

                member_ids = {group.owner_id, *(group.admin_ids or []), *(group.members or [])}
                profiles = await BioProfile.find_all().to_list()
                members = [p for p in profiles if p.user_id in member_ids]
                members.sort(key=lambda p: (int(p.views or 0), p.username), reverse=True)

                cards = []
                for p in members:
                    role = "Owner" if p.user_id == group.owner_id else ("Admin" if p.user_id in (group.admin_ids or []) else "Member")
                    uname = html.escape(p.username)
                    display = html.escape(p.display_name or p.username)
                    avatar = html.escape(p.avatar_url or "https://cdn.discordapp.com/embed/avatars/0.png")
                    color = html.escape(p.username_color or "#ffffff")
                    views = f"{int(p.views or 0):,}"
                    cards.append(
                        f"""
<a href="/@{uname}" class="card">
  <img src="{avatar}" alt="avatar" />
  <div class="meta">
    <span class="uname" style="color:{color};">@{uname}</span>
    <span class="dname">{display}</span>
    <span class="role">{role}</span>
  </div>
  <span class="views"><i class="far fa-eye"></i> {views}</span>
</a>
"""
                    )

                group_title = group.name
                group_icon = html.escape(getattr(group, "icon_url", "") or "", quote=True)
                banner = html.escape(getattr(group, "banner_url", "") or "")
                created_at = group.created_at if getattr(group, "created_at", None) else datetime.utcnow()
                created_label = created_at.strftime("%b %d, %Y")

                icon_block = (
                    f'<img class="icon" src="{group_icon}" alt="group icon" />'
                    if group_icon
                    else '<i class="fas fa-users" style="opacity:.7;"></i>'
                )
                banner_block = f'<img class="banner" src="{banner}" alt="group banner" />' if banner else ''
                cards_html = ''.join(cards) if cards else '<div style="color:var(--dim);padding:6px;">No members found.</div>'

                return _render_view(
                    "pages/profiles/group",
                    replacements={
                        "group.title": group_title,
                        "group.memberCount": len(members),
                        "group.createdLabel": created_label,
                    },
                    raw_replacements={
                        "group.icon": icon_block,
                        "group.banner": banner_block,
                        "group.cards": cards_html,
                    },
                )
            except Exception as e:
                logger("error", f"[WEB ERROR] group page lookup: {e}")
                return _render_error(500)

        async def commands_view(_request: web.Request):
            return _render_view("pages/docs/commands")

        async def docs_view(_request: web.Request):
            return _render_view("pages/docs/docs")

        async def embed_view(_request: web.Request):
            return _render_view("pages/media/embed")

        async def gallery_view(_request: web.Request):
            try:
                assets = await Upload.find(
                    Upload.is_nsfw != True,
                    Upload.is_private != True,
                ).sort(-Upload.created_at).limit(100).to_list()
                assets = [a for a in assets if not str(a.file_name or "").lower().startswith("qr_")]

                cards = []
                for img in assets:
                    file_name = html.escape(img.file_name)
                    uploader = html.escape(img.uploader_name or "Anonymous")
                    raw_url = html.escape(img.url)
                    cards.append(
                        f"""
<div class="gallery-item">
  <div class="img-container">
    <img src="{raw_url}" alt="Wock Asset" loading="lazy">
    <div class="img-overlay">
      <a href="/gallery/{file_name}" class="view-btn">VIEW ASSET</a>
      <a href="{raw_url}" target="_blank" class="view-btn secondary">RAW LINK</a>
    </div>
  </div>
  <div class="item-details">
    <span class="file-name" title="{file_name}">{file_name}</span>
    <div class="uploader-info">
      <span class="uploader-tag"><i class="fas fa-user-circle"></i> <b>{uploader}</b></span>
      <span style="font-size: 11px; color: var(--text-dim); display: flex; align-items: center; gap: 5px;"><i class="far fa-clock"></i> Active</span>
    </div>
  </div>
</div>
"""
                    )

                empty_block = """
<div class="empty-state">
  <i class="fas fa-ghost" style="font-size: 40px; margin-bottom: 15px; opacity: 0.2;"></i>
  <p>The gallery is currently empty.</p>
</div>
"""

                return _render_view(
                    "pages/media/gallery",
                    replacements={
                        "gallery.title": "Public Gallery",
                    },
                    raw_replacements={
                        "gallery.cards": "".join(cards) if cards else empty_block,
                    },
                )
            except Exception as err:
                logger("error", f"[WEB ERROR] Gallery page: {err}")
                return _render_error(500)

        async def gallery_asset_view(request: web.Request):
            try:
                file_name = request.match_info.get("file_name", "")
                if not file_name:
                    return _render_error(404)

                asset = await Upload.find_one(Upload.file_name == file_name)
                if not asset:
                    return _render_error(404)

                ext = (file_name.rsplit(".", 1)[-1].lower() if "." in file_name else "")
                is_video = ext in {"mp4", "webm", "mov"}
                is_audio = ext in {"mp3", "wav", "ogg"}
                media_type = "video" if is_video else ("audio" if is_audio else "image")

                media_html = ""
                safe_url = html.escape(asset.url, quote=True)
                safe_name = html.escape(asset.file_name)
                if media_type == "video":
                    media_html = f'<video controls playsinline style="max-width:100%; max-height:80vh; display:block;"><source src="{safe_url}" type="video/{ext}"></video>'
                elif media_type == "audio":
                    media_html = f'<audio controls style="width:100%;"><source src="{safe_url}" type="audio/{ext}"></audio>'
                else:
                    media_html = f'<img src="{safe_url}" alt="{safe_name}">'

                return _render_view(
                    "pages/media/viewer",
                    replacements={
                        "asset.fileName": asset.file_name,
                        "asset.uploaderName": asset.uploader_name or "Anonymous",
                        "asset.url": asset.url,
                        "asset.id": str(asset.id),
                    },
                    raw_replacements={
                        "asset.media": media_html,
                    },
                )
            except Exception as err:
                logger("error", f"[WEB ERROR] Gallery asset page: {err}")
                return _render_error(500)

        async def status_view(_request: web.Request):
            return _render_view("pages/system/status")

        async def updates_view(_request: web.Request):
            return _render_view("pages/system/updates")

        async def error_view(request: web.Request):
            try:
                code = int(request.query.get("code", "404"))
            except ValueError:
                code = 404
            return _render_error(code)

        async def dashboard_login(request: web.Request):
            if not oauth_client_id:
                return _render_error(503)

            next_path = request.query.get("next", "/dashboard")
            if not next_path.startswith("/dashboard"):
                next_path = "/dashboard"

            state = secrets.token_urlsafe(24)
            redirect_uri = _oauth_redirect_uri_for(request)
            location = (
                f"https://discord.com/api/oauth2/authorize"
                f"?client_id={oauth_client_id}"
                f"&redirect_uri={quote(redirect_uri, safe='')}"
                f"&response_type=code"
                f"&scope=identify%20guilds"
                f"&state={state}"
            )
            response = web.HTTPFound(location=location)
            response.set_cookie("wock_dash_state", state, max_age=300, httponly=True, samesite="Lax", secure=False, path="/")
            response.set_cookie("wock_dash_next", next_path, max_age=300, httponly=True, samesite="Lax", secure=False, path="/")
            raise response

        async def dashboard_oauth_bridge(_request: web.Request):
            html_doc = """
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Discord Login</title></head>
<body style="font-family:Inter,system-ui,-apple-system,sans-serif;background:#0b0d12;color:#e8eaf0;display:grid;place-items:center;min-height:100vh;margin:0;">
    <div id="msg">Finishing Discord sign-in…</div>
    <script>
        (async () => {
            const hash = new URLSearchParams((location.hash || '').replace(/^#/, ''));
            const access_token = hash.get('access_token');
            const state = hash.get('state');
            const msg = document.getElementById('msg');
            if (!access_token || !state) {
                msg.textContent = 'Sign-in failed: missing token.';
                setTimeout(() => location.href = '/dashboard', 1200);
                return;
            }
            try {
                const res = await fetch('/api/dashboard/session', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ access_token, state })
                });
                if (!res.ok) throw new Error('HTTP ' + res.status);
                const data = await res.json();
                location.href = data.next || '/dashboard';
            } catch {
                msg.textContent = 'Sign-in failed. Please try again.';
                setTimeout(() => location.href = '/dashboard', 1200);
            }
        })();
    </script>
</body>
</html>
"""
            return web.Response(text=html_doc, content_type="text/html")

        async def dashboard_callback(request: web.Request):
            if not oauth_client_id or not oauth_client_secret:
                return _render_error(503)

            expected_state = request.cookies.get("wock_dash_state")
            state = request.query.get("state")
            code = request.query.get("code")
            if not expected_state or not state or state != expected_state or not code:
                return _render_error(401)

            token_url = "https://discord.com/api/v10/oauth2/token"
            form_data = {
                "client_id": oauth_client_id,
                "client_secret": oauth_client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": _oauth_redirect_uri_for(request),
            }
            headers = {"Content-Type": "application/x-www-form-urlencoded"}
            async with self.session.post(token_url, data=form_data, headers=headers) as token_resp:
                if token_resp.status != 200:
                    return _render_error(401)
                token_data = await token_resp.json()

            access_token = token_data.get("access_token")
            if not access_token:
                return _render_error(401)

            user = await _fetch_discord_user(access_token)
            if not user:
                return _render_error(401)

            admin_guild_ids = await _fetch_admin_dashboard_guild_ids(access_token)
            payload = {
                "user_id": str(user.get("id")),
                "username": user.get("username") or "Discord User",
                "avatar": user.get("avatar"),
                "guild_ids": [str(gid) for gid in sorted(admin_guild_ids)],
                "exp": int(time.time()) + (60 * 60 * 24 * 7),
            }

            next_path = request.cookies.get("wock_dash_next", "/dashboard")
            if not next_path.startswith("/dashboard"):
                next_path = "/dashboard"

            response = web.HTTPFound(location=next_path)
            _set_dashboard_auth_cookie(response, payload)
            response.del_cookie("wock_dash_state", path="/")
            response.del_cookie("wock_dash_next", path="/")
            raise response

        async def api_dashboard_session(request: web.Request):
            try:
                body = await request.json()
                access_token = str(body.get("access_token") or "")
                state = str(body.get("state") or "")
                expected_state = request.cookies.get("wock_dash_state")
                if not access_token or not state or not expected_state or state != expected_state:
                    return web.json_response({"error": "Invalid OAuth state"}, status=401)

                user = await _fetch_discord_user(access_token)
                if not user:
                    return web.json_response({"error": "Unauthorized"}, status=401)

                admin_guild_ids = await _fetch_admin_dashboard_guild_ids(access_token)
                payload = {
                    "user_id": str(user.get("id")),
                    "username": user.get("username") or "Discord User",
                    "avatar": user.get("avatar"),
                    "guild_ids": [str(gid) for gid in sorted(admin_guild_ids)],
                    "exp": int(time.time()) + (60 * 60 * 24 * 7),
                }

                next_path = request.cookies.get("wock_dash_next", "/dashboard")
                if not next_path.startswith("/dashboard"):
                    next_path = "/dashboard"

                response = web.json_response({"ok": True, "next": next_path})
                _set_dashboard_auth_cookie(response, payload)
                response.del_cookie("wock_dash_state", path="/")
                response.del_cookie("wock_dash_next", path="/")
                return response
            except Exception as e:
                logger("error", f"[API] Dashboard session: {e}")
                return web.json_response({"error": "Internal Server Error"}, status=500)

        async def dashboard_logout(_request: web.Request):
            response = web.HTTPFound(location="/dashboard")
            _clear_dashboard_auth_cookie(response)
            raise response

        async def dashboard_view(_request: web.Request):
            if not _dashboard_session(_request):
                return _dashboard_unauthorized(_request)
            return _render_view("pages/admin/dashboard")

        async def api_dashboard_guilds(request: web.Request):
            try:
                session_payload = _dashboard_session(request)
                if not session_payload:
                    return _dashboard_unauthorized(request)
                allowed_ids = _session_guild_ids(session_payload)

                guilds = []
                for g in self.guilds:
                    if g.id not in allowed_ids:
                        continue
                    guilds.append({
                        "id":           str(g.id),
                        "name":         g.name,
                        "icon":         str(g.icon.url) if g.icon else None,
                        "member_count": g.member_count or 0,
                    })
                guilds.sort(key=lambda x: x["member_count"], reverse=True)
                return web.json_response(guilds)
            except Exception as e:
                logger("error", f"[API] Dashboard guilds: {e}")
                return web.json_response({"error": str(e)}, status=500)

        async def api_dashboard_guild(request: web.Request):
            try:
                session_payload = _dashboard_session(request)
                if not session_payload:
                    return _dashboard_unauthorized(request)

                guild_id_str = request.match_info.get("guild_id", "")
                if not guild_id_str.isdigit():
                    return web.json_response({"error": "Invalid guild id"}, status=400)
                guild_id = int(guild_id_str)
                if guild_id not in _session_guild_ids(session_payload):
                    return _dashboard_forbidden(request)
                guild = self.get_guild(guild_id)
                if not guild:
                    return web.json_response({"error": "Guild not found"}, status=404)

                cfg = await GuildConfig.find_one(GuildConfig.guild_id == guild_id)
                if cfg is None:
                    cfg = GuildConfig(guild_id=guild_id)
                sb = await StarboardConfig.find_one(StarboardConfig.guild_id == guild_id)
                if sb is None:
                    sb = StarboardConfig(guild_id=guild_id)

                channels = [
                    {"id": str(c.id), "name": c.name, "type": c.type.value}
                    for c in sorted(guild.channels, key=lambda c: c.name)
                ]
                roles = [
                    {"id": str(r.id), "name": r.name, "color": r.color.value}
                    for r in guild.roles
                    if r.name != "@everyone"
                ]

                return web.json_response({
                    "id":            str(guild.id),
                    "name":          guild.name,
                    "icon":          str(guild.icon.url) if guild.icon else None,
                    "member_count":  guild.member_count or 0,
                    "channel_count": len(guild.channels),
                    "role_count":    len(guild.roles),
                    "channels":      channels,
                    "roles":         roles,
                    "config": {
                        "prefix":                cfg.prefix,
                        "antinuke_enabled":      cfg.antinuke_enabled,
                        "antinuke_action":       cfg.antinuke_action,
                        "antinuke_threshold":    cfg.antinuke_threshold,
                        "modlog_channel_id":     str(cfg.modlog_channel_id) if cfg.modlog_channel_id else None,
                        "filter_invites":        cfg.filter_invites,
                        "filter_invites_action": cfg.filter_invites_action,
                        "filter_spam":           cfg.filter_spam,
                        "filter_spam_action":    cfg.filter_spam_action,
                        "filter_words":          cfg.filter_words,
                        "filter_words_action":   cfg.filter_words_action,
                        "voicemaster_enabled":   cfg.voicemaster_enabled,
                        "voicemaster_channel_id":str(cfg.voicemaster_channel_id) if cfg.voicemaster_channel_id else None,
                        "leveling_enabled":      cfg.leveling_enabled,
                        "level_channel_id":      str(cfg.level_channel_id) if cfg.level_channel_id else None,
                        "level_message":         cfg.level_message,
                        "mute_role_id":          str(cfg.mute_role_id) if cfg.mute_role_id else None,
                        "jail_role_id":          str(cfg.jail_role_id) if cfg.jail_role_id else None,
                    },
                    "starboard": {
                        "starboard_channel_id": str(sb.starboard_channel_id) if sb.starboard_channel_id else None,
                        "threshold":            sb.threshold,
                        "emoji":                sb.emoji,
                    },
                })
            except Exception as e:
                logger("error", f"[API] Dashboard guild: {e}")
                return web.json_response({"error": str(e)}, status=500)

        async def api_dashboard_save(request: web.Request):
            try:
                session_payload = _dashboard_session(request)
                if not session_payload:
                    return _dashboard_unauthorized(request)

                guild_id_str = request.match_info.get("guild_id", "")
                if not guild_id_str.isdigit():
                    return web.json_response({"error": "Invalid guild id"}, status=400)
                guild_id = int(guild_id_str)
                if guild_id not in _session_guild_ids(session_payload):
                    return _dashboard_forbidden(request)
                if not self.get_guild(guild_id):
                    return web.json_response({"error": "Guild not found"}, status=404)

                data = await request.json()

                cfg = await GuildConfig.find_one(GuildConfig.guild_id == guild_id)
                if not cfg:
                    cfg = GuildConfig(guild_id=guild_id)
                    await cfg.insert()
                sb = await StarboardConfig.find_one(StarboardConfig.guild_id == guild_id)
                if not sb:
                    sb = StarboardConfig(guild_id=guild_id)
                    await sb.insert()

                def _int(v):
                    try: return int(v) if v else None
                    except: return None

                cfg.prefix                = (data.get("prefix") or ";")[:5]
                cfg.antinuke_enabled      = bool(data.get("antinuke_enabled"))
                cfg.antinuke_action       = data.get("antinuke_action", "ban")
                cfg.antinuke_threshold    = _int(data.get("antinuke_threshold")) or 3
                cfg.modlog_channel_id     = _int(data.get("modlog_channel_id"))
                cfg.filter_invites        = bool(data.get("filter_invites"))
                cfg.filter_invites_action = data.get("filter_invites_action", "kick")
                cfg.filter_spam           = bool(data.get("filter_spam"))
                cfg.filter_spam_action    = data.get("filter_spam_action", "kick")
                cfg.filter_words          = bool(data.get("filter_words"))
                cfg.filter_words_action   = data.get("filter_words_action", "kick")
                cfg.voicemaster_enabled   = bool(data.get("voicemaster_enabled"))
                cfg.voicemaster_channel_id= _int(data.get("voicemaster_channel_id"))
                cfg.leveling_enabled      = bool(data.get("leveling_enabled"))
                cfg.level_channel_id      = _int(data.get("level_channel_id"))
                cfg.level_message         = data.get("level_message") or None
                cfg.mute_role_id          = _int(data.get("mute_role_id"))
                cfg.jail_role_id          = _int(data.get("jail_role_id"))
                await cfg.save()

                sb.starboard_channel_id = _int(data.get("starboard_channel_id"))
                sb.threshold            = _int(data.get("starboard_threshold")) or 5
                sb.emoji                = data.get("starboard_emoji") or "⭐"
                await sb.save()

                return web.json_response({"ok": True})
            except Exception as e:
                logger("error", f"[API] Dashboard save: {e}")
                return web.json_response({"error": str(e)}, status=500)

        async def bot_avatar(_request: web.Request):
            if self.user and self.user.display_avatar:
                url = str(self.user.display_avatar.with_size(256).url)
                raise web.HTTPFound(location=url)
            raise web.HTTPNotFound()

        async def health(_request: web.Request):
            return web.json_response({"ok": True, "service": "wock-bot"})

        app = web.Application(middlewares=[error_middleware])
        try:
            bio_directory_path = os.path.join(views_dir, "pages", "profiles", "bioDirectory.ejs")
            with open(bio_directory_path, "r", encoding="utf-8") as f:
                raw = f.read()
                css_match = re.search(r"<style>([\s\S]*?)</style>", raw)
                app["bio_directory_css"] = css_match.group(1) if css_match else ""
        except Exception:
            app["bio_directory_css"] = ""

        app.router.add_static("/css", os.path.join(public_dir, "css"), show_index=False)
        app.router.add_static("/js", os.path.join(public_dir, "js"), show_index=False)

        app.router.add_get("/", home)
        app.router.add_get("/api", api_docs)
        app.router.add_get("/api/", api_docs)
        app.router.add_get("/api/endpoints", api_endpoints)
        app.router.add_get("/api/stats", api_stats)
        app.router.add_get("/api/commands", api_commands)
        app.router.add_get("/api/soundcloud", api_soundcloud)
        app.router.add_get("/api/fortnite/lookup", api_fortnite_lookup)
        app.router.add_get("/api/fortnite/map", api_fortnite_map)
        app.router.add_get("/api/fortnite/news", api_fortnite_news)
        app.router.add_get("/api/fortnite/shop", api_fortnite_shop)
        app.router.add_get("/api/urban", api_urban)
        app.router.add_get("/api/weather", api_weather)
        app.router.add_get("/api/anime", api_anime)
        app.router.add_get("/api/anime/character", api_anime_character)
        app.router.add_get("/api/pokemon", api_pokemon)
        app.router.add_get("/api/crypto", api_crypto)
        app.router.add_get("/avatars", avatars_directory)
        app.router.add_get("/avatars/{user_id}", avatar_user)
        app.router.add_get("/profiles", profiles_directory)
        app.router.add_get(r"/@{username}", profile_username)
        app.router.add_get("/groups/{groupname}", group_view)
        app.router.add_get("/commands", commands_view)
        app.router.add_get("/docs", docs_view)
        app.router.add_get("/embed", embed_view)
        app.router.add_get("/gallery", gallery_view)
        app.router.add_get("/gallery/{file_name}", gallery_asset_view)
        app.router.add_get("/status", status_view)
        app.router.add_get("/updates", updates_view)
        app.router.add_get("/error", error_view)
        app.router.add_get("/dashboard/login",                dashboard_login)
        app.router.add_get("/dashboard/oauth",                dashboard_oauth_bridge)
        app.router.add_get("/dashboard/callback",             dashboard_callback)
        app.router.add_get("/api/auth/callback",              dashboard_callback)
        app.router.add_get("/dashboard/logout",               dashboard_logout)
        app.router.add_post("/api/dashboard/session",         api_dashboard_session)
        app.router.add_get("/api/dashboard/guilds",          api_dashboard_guilds)
        app.router.add_get("/api/dashboard/{guild_id}",       api_dashboard_guild)
        app.router.add_post("/api/dashboard/{guild_id}/save", api_dashboard_save)
        app.router.add_get("/dashboard",                      dashboard_view)
        app.router.add_get("/dashboard/{tail:.*}",            dashboard_view)
        app.router.add_get("/avatar.png", bot_avatar)
        app.router.add_get("/health", health)

        self.web_runner = web.AppRunner(app)
        await self.web_runner.setup()
        site = web.TCPSite(self.web_runner, WEB_HOST, WEB_PORT)
        await site.start()
        logger("success", f"Web origin started on http://{WEB_HOST}:{WEB_PORT}")

    async def _start_tunnel(self):
        if not AUTO_START_TUNNEL:
            logger("info", "cloudflared autostart disabled (WOCK_AUTOSTART_TUNNEL=false)")
            return

        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yml")
        if not os.path.exists(config_path):
            logger("warn", f"Tunnel config not found: {config_path}")
            return

        if not (shutil.which("cloudflared") or shutil.which("cloudflare")):
            logger("warn", "cloudflared not found in PATH; skipping tunnel startup")
            return

        try:
            self.tunnel_process = await asyncio.create_subprocess_exec(
                "cloudflared",
                "tunnel",
                "--config",
                config_path,
                "run",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            logger("success", f"Started cloudflared tunnel for wock.best (pid {self.tunnel_process.pid})")
        except Exception as e:
            logger("error", f"Failed to start cloudflared tunnel: {e}")

    def _patch_permission_checks(self):
        if getattr(commands, "_wock_fake_permissions_patched", False):
            return

        async def _collect_fake_perms(ctx):
            if not ctx.guild or not hasattr(ctx.author, "roles"):
                return set()
            cfg = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
            if not cfg or not getattr(cfg, "fake_permissions", None):
                return set()

            fake_perms = set()
            for role in ctx.author.roles:
                fake_perms.update(cfg.fake_permissions.get(str(role.id), []))
            return fake_perms

        def patched_has_permissions(**perms):
            invalid = set(perms) - set(discord.Permissions.VALID_FLAGS)
            if invalid:
                raise TypeError(f"Invalid permission(s): {', '.join(invalid)}")

            async def predicate(ctx):
                permissions = ctx.channel.permissions_for(ctx.author)
                missing = [name for name, value in perms.items() if getattr(permissions, name) != value]
                if not missing:
                    return True

                fake_perms = await _collect_fake_perms(ctx)
                remaining = []
                for name in missing:
                    required = perms.get(name)
                    if required is True and name in fake_perms:
                        continue
                    remaining.append(name)

                if not remaining:
                    return True
                raise commands.MissingPermissions(remaining)

            return commands.check(predicate)

        def patched_has_guild_permissions(**perms):
            invalid = set(perms) - set(discord.Permissions.VALID_FLAGS)
            if invalid:
                raise TypeError(f"Invalid permission(s): {', '.join(invalid)}")

            async def predicate(ctx):
                if not ctx.guild:
                    raise commands.NoPrivateMessage()

                permissions = ctx.author.guild_permissions
                missing = [name for name, value in perms.items() if getattr(permissions, name) != value]
                if not missing:
                    return True

                fake_perms = await _collect_fake_perms(ctx)
                remaining = []
                for name in missing:
                    required = perms.get(name)
                    if required is True and name in fake_perms:
                        continue
                    remaining.append(name)

                if not remaining:
                    return True
                raise commands.MissingPermissions(remaining)

            return commands.check(predicate)

        commands.has_permissions = patched_has_permissions
        commands.has_guild_permissions = patched_has_guild_permissions
        commands._wock_fake_permissions_patched = True

    async def setup_hook(self):
        self.session = aiohttp.ClientSession()

        await self._start_web_server()

        try:
            self.db_client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
            await init_beanie(
                database=self.db_client.wock_db, 
                document_models=[LastfmData, GuildConfig, UserConfig, ModCase, Warning, AFK, EntertainmentInteraction, MuhaProfile, Giveaway, StarboardConfig, StarboardPost, Gang, GardenData, DungeonData, BioProfile, BioGroup, Upload]
            )
            logger("success", "MongoDB connection established")
        except Exception as e:
            logger("error", f"Failed to connect to MongoDB: {e}")

        await self.load_extension('jishaku')
        
        if os.path.exists('./cogs'):
            for filename in os.listdir('./cogs'):
                if filename.endswith('.py') and filename != "__init__.py":
                    try:
                        await self.load_extension(f'cogs.{filename[:-3]}')
                        logger("success", f"Loaded extension: {filename}")
                    except Exception as e:
                        logger("error", f"Failed to load {filename}: {e}")
        
        if os.path.exists('./utils'):
            for filename in os.listdir('./utils'):
                if filename.endswith('.py') and filename not in ["__init__.py", "logger.py", "parser.py", "command_meta.py", "paginator.py", "catbox.py"]:
                    try:
                        await self.load_extension(f'utils.{filename[:-3]}')
                        logger("success", f"Loaded extension: {filename}")
                    except Exception as e:
                        logger("error", f"Failed to load {filename}: {e}")

        await self._start_tunnel()

    async def on_ready(self):
        await self.change_presence(
            activity=discord.Activity(type=discord.ActivityType.watching, name="wock.best")
        )
        logger("success", f"Logged in as {self.user} ({self.user.id})")

    async def before_invoke(self, ctx):
        """Check if command or module is disabled before invoking"""
        if not ctx.guild:
            return
        
        try:
            config = await GuildConfig.find_one(GuildConfig.guild_id == ctx.guild.id)
            if not config:
                return
            
            # Check if command is disabled
            if ctx.command:
                cmd_name = ctx.command.qualified_name.lower()

                # Check command-specific permission restrictions
                command_restrictions = getattr(config, "command_restrictions", {}) or {}
                required_perms = command_restrictions.get(cmd_name, [])
                if required_perms:
                    channel_perms = ctx.channel.permissions_for(ctx.author)

                    fake_perms = set()
                    fake_map = getattr(config, "fake_permissions", {}) or {}
                    for role in getattr(ctx.author, "roles", []):
                        fake_perms.update(fake_map.get(str(role.id), []))

                    missing = []
                    for perm in required_perms:
                        has_real = getattr(channel_perms, perm, False)
                        has_fake = perm in fake_perms
                        if not has_real and not has_fake:
                            missing.append(perm)

                    if missing:
                        embed = discord.Embed(
                            color=0xf25d5d,
                            description=f"You need the following permission(s) to use **{cmd_name}**: {', '.join(f'`{p}`' for p in missing)}"
                        )
                        await ctx.send(embed=embed)
                        raise commands.CheckFailure("Command restricted by permission gate")

                if cmd_name in config.disabled_commands:
                    restrictions = config.disabled_commands[cmd_name]
                    
                    # Check if 
                    # disabled for everyone
                    if restrictions.get("all"):
                        embed = discord.Embed(color=0xf25d5d, description=f"Command **{cmd_name}** is disabled in this server.")
                        await ctx.send(embed=embed)
                        raise commands.CheckFailure("Command disabled")
                    
                    # Check if disabled for this user
                    if ctx.author.id in restrictions.get("users", []):
                        embed = discord.Embed(color=0xf25d5d, description=f"Command **{cmd_name}** is disabled for you.")
                        await ctx.send(embed=embed)
                        raise commands.CheckFailure("Command disabled for user")
                    
                    # Check if disabled for user's roles
                    user_role_ids = [r.id for r in ctx.author.roles]
                    if any(role_id in restrictions.get("roles", []) for role_id in user_role_ids):
                        embed = discord.Embed(color=0xf25d5d, description=f"Command **{cmd_name}** is disabled for your role.")
                        await ctx.send(embed=embed)
                        raise commands.CheckFailure("Command disabled for role")
                    
                    # Check if disabled in this channel
                    if ctx.channel.id in restrictions.get("channels", []):
                        embed = discord.Embed(color=0xf25d5d, description=f"Command **{cmd_name}** is disabled in this channel.")
                        await ctx.send(embed=embed)
                        raise commands.CheckFailure("Command disabled in channel")
                
                # Check if module/cog is disabled
                if ctx.command.cog:
                    cog_name = ctx.command.cog.qualified_name.lower()
                    if cog_name in config.disabled_modules:
                        restrictions = config.disabled_modules[cog_name]
                        
                        # Check if disabled for everyone
                        if restrictions.get("all"):
                            embed = discord.Embed(color=0xf25d5d, description=f"Module **{cog_name}** is disabled in this server.")
                            await ctx.send(embed=embed)
                            raise commands.CheckFailure("Module disabled")
                        
                        # Check if disabled for this user
                        if ctx.author.id in restrictions.get("users", []):
                            embed = discord.Embed(color=0xf25d5d, description=f"Module **{cog_name}** is disabled for you.")
                            await ctx.send(embed=embed)
                            raise commands.CheckFailure("Module disabled for user")
                        
                        # Check if disabled for user's roles
                        if any(role_id in restrictions.get("roles", []) for role_id in user_role_ids):
                            embed = discord.Embed(color=0xf25d5d, description=f"Module **{cog_name}** is disabled for your role.")
                            await ctx.send(embed=embed)
                            raise commands.CheckFailure("Module disabled for role")
                        
                        # Check if disabled in this channel
                        if ctx.channel.id in restrictions.get("channels", []):
                            embed = discord.Embed(color=0xf25d5d, description=f"Module **{cog_name}** is disabled in this channel.")
                            await ctx.send(embed=embed)
                            raise commands.CheckFailure("Module disabled in channel")
        except commands.CheckFailure:
            raise
        except Exception:
            pass


    async def warn(self, ctx, message):
        e = discord.Embed(color=0x242429, description=message)
        return await ctx.send(embed=e)

    async def grant(self, ctx, message):
        e = discord.Embed(color=0x242429, description=message)
        return await ctx.send(embed=e)

    async def neutral(self, ctx, message):
        e = discord.Embed(color=0x242429, description=message)
        return await ctx.send(embed=e)

    async def deny(self, ctx, message):
        e = discord.Embed(color=0x242429, description=message)
        return await ctx.send(embed=e)

    async def close(self):
        logger("info", "closing sessions.")
        if self.tunnel_process and self.tunnel_process.returncode is None:
            self.tunnel_process.terminate()
            try:
                await asyncio.wait_for(self.tunnel_process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self.tunnel_process.kill()
                await self.tunnel_process.wait()

        if self.session and not self.session.closed:
            await self.session.close()
            await asyncio.sleep(0.25)
        if self.web_runner:
            await self.web_runner.cleanup()
        if hasattr(self, 'db_client'): 
            self.db_client.close()
        await super().close()

async def main():
    bot = WockBot()
    async with bot:
        await bot.start(TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger("warn", "Process terminated by user")
