from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class Settings:
    discord_token: str
    channel_id: int
    aurral_url: str
    aurral_key: str
    lidarr_url: str
    lidarr_key: str
    prowlarr_url: str
    prowlarr_key: str
    db_path: str
    tz: str


_REQUIRED = {
    "discord_token": "DISCORD_TOKEN",
    "channel_id": "DISCORD_CHANNEL_ID",
    "aurral_url": "AURRAL_URL",
    "aurral_key": "AURRAL_API_KEY",
    "lidarr_url": "LIDARR_URL",
    "lidarr_key": "LIDARR_API_KEY",
    "prowlarr_url": "PROWLARR_URL",
    "prowlarr_key": "PROWLARR_API_KEY",
}


def load_settings(env: Mapping[str, str]) -> Settings:
    missing = [v for v in _REQUIRED.values() if not env.get(v, "").strip()]
    if missing:
        raise RuntimeError("Missing required environment variables: " + ", ".join(missing))
    vals = {k: env[v].strip() for k, v in _REQUIRED.items()}
    try:
        vals["channel_id"] = int(vals["channel_id"])
    except ValueError:
        raise RuntimeError("DISCORD_CHANNEL_ID must be an integer") from None
    return Settings(
        db_path=env.get("DB_PATH", "/data/discovery.db").strip() or "/data/discovery.db",
        tz=env.get("TZ", "America/Chicago").strip() or "America/Chicago",
        **vals,
    )
