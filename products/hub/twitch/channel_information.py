from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any, Mapping
from urllib.parse import urlparse

from shared.streamhouse_runtime.json_store import atomic_write_json, load_json_with_backup
from shared.streamhouse_runtime.paths import user_data_root


SOCIAL_SERVICES: tuple[tuple[str, str], ...] = (
    ("discord", "Discord"),
    ("youtube", "YouTube"),
    ("tiktok", "TikTok"),
    ("instagram", "Instagram"),
    ("bluesky", "Bluesky"),
    ("twitter", "X / Twitter"),
    ("facebook", "Facebook"),
    ("website", "Website"),
)
SOCIAL_SERVICE_LABELS = dict(SOCIAL_SERVICES)
CHANNEL_INFORMATION_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("discord_url", "Discord URL", "discord"),
    ("youtube_url", "YouTube URL", "youtube"),
    ("schedule", "Schedule", "schedule"),
    ("rules", "Rules", "rules"),
    ("server_info", "Server Information", "server_info"),
)
CHANNEL_INFORMATION_FIELD_LABELS = {
    key: label for key, label, _source in CHANNEL_INFORMATION_FIELDS
}


def normalize_social_url(value: object) -> str:
    clean = str(value or "").strip()
    if not clean:
        return ""
    if "\n" in clean or "\r" in clean:
        raise ValueError("Links must stay on one line.")
    if any(character.isspace() for character in clean):
        raise ValueError("Links cannot contain spaces.")
    if len(clean) > 400:
        raise ValueError("Links must be 400 characters or fewer.")
    candidate = clean if "://" in clean else f"https://{clean}"
    parsed = urlparse(candidate)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Enter a valid web link, such as https://example.com.")
    return candidate


def normalize_multiline_text(value: object) -> str:
    lines = [line.strip() for line in str(value or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


@dataclass(slots=True)
class SocialLink:
    enabled_in_socials: bool = False
    url: str = ""

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> SocialLink:
        return cls(
            enabled_in_socials=bool(values.get("enabled_in_socials", False)),
            url=normalize_social_url(values.get("url", "")),
        )


def _default_social_links() -> dict[str, SocialLink]:
    return {service_id: SocialLink() for service_id, _label in SOCIAL_SERVICES}


@dataclass(slots=True)
class ChannelInformation:
    social_links: dict[str, SocialLink] = field(default_factory=_default_social_links)
    schedule: str = ""
    rules: str = ""
    server_info: str = ""

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> ChannelInformation:
        raw_links = values.get("social_links", {})
        if not isinstance(raw_links, dict):
            raise ValueError("Channel Information social links must be an object.")
        links = _default_social_links()
        for service_id, _label in SOCIAL_SERVICES:
            raw = raw_links.get(service_id, {})
            if raw is None:
                continue
            if not isinstance(raw, dict):
                raise ValueError(f"The {SOCIAL_SERVICE_LABELS[service_id]} social link must be an object.")
            links[service_id] = SocialLink.from_dict(raw)
        return cls(
            social_links=links,
            schedule=normalize_multiline_text(values.get("schedule", "")),
            rules=normalize_multiline_text(values.get("rules", "")),
            server_info=normalize_multiline_text(values.get("server_info", "")),
        )


class ChannelInformationStore:
    VERSION = 1

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or user_data_root() / "twitch" / "channel-information.json"
        self.information = ChannelInformation()
        self._lock = RLock()

    def load(self) -> ChannelInformation:
        with self._lock:
            if not self.path.exists():
                self.information = ChannelInformation()
                return self.snapshot()
            payload = load_json_with_backup(self.path)
            if not isinstance(payload, dict):
                raise ValueError("Channel Information must contain a JSON object.")
            try:
                version = int(payload.get("version", 1))
            except (TypeError, ValueError) as error:
                raise ValueError("Channel Information has an invalid schema version.") from error
            if version > self.VERSION:
                raise ValueError("Channel Information data is newer than this app.")
            self.information = ChannelInformation.from_dict(payload)
            return self.snapshot()

    def save(self, information: ChannelInformation | None = None) -> ChannelInformation:
        with self._lock:
            candidate = ChannelInformation.from_dict(
                asdict(information if information is not None else self.information)
            )
            atomic_write_json(
                self.path,
                {
                    "version": self.VERSION,
                    **asdict(candidate),
                },
            )
            self.information = candidate
            return self.snapshot()

    def snapshot(self) -> ChannelInformation:
        with self._lock:
            return deepcopy(self.information)

    def field_value(self, field_id: str) -> str:
        clean = str(field_id).strip().casefold()
        with self._lock:
            if clean == "discord_url":
                return self.information.social_links["discord"].url
            if clean == "youtube_url":
                return self.information.social_links["youtube"].url
            if clean in {"schedule", "rules", "server_info"}:
                return str(getattr(self.information, clean)).strip()
        raise ValueError("Unknown Channel Information field.")

    def field_available(self, field_id: str) -> bool:
        try:
            return bool(self.field_value(field_id))
        except ValueError:
            return False

    def usable_social_links(self) -> tuple[tuple[str, str], ...]:
        with self._lock:
            selected: list[tuple[str, str]] = []
            seen: set[str] = set()
            for service_id, label in SOCIAL_SERVICES:
                link = self.information.social_links[service_id]
                if not link.enabled_in_socials or not link.url:
                    continue
                key = link.url.casefold().rstrip("/")
                if key in seen:
                    continue
                seen.add(key)
                selected.append((label, link.url))
            return tuple(selected)

    def build_social_links_message(self, maximum_characters: int = 480) -> str:
        limit = min(max(int(maximum_characters), 50), 480)
        parts: list[str] = []
        for label, url in self.usable_social_links():
            candidate = " | ".join((*parts, f"{label}: {url}"))
            if len(candidate) > limit:
                break
            parts.append(f"{label}: {url}")
        return " | ".join(parts)
