"""Сбор клипов с Twitch через Helix API.

Почему именно клипы: их нарезали зрители. Кто-то смотрел стрим и нажал
«клип», потому что там что-то произошло. То есть отбор моментов уже сделан
толпой, у каждого клипа есть счётчик просмотров, и по нему видно, что
зашло. Ни Whisper, ни скоринг для этого не нужны.

Ключи берутся из окружения:
    TWITCH_CLIENT_ID
    TWITCH_CLIENT_SECRET
Регистрируются на dev.twitch.tv как приложение. Нужен только app access
token (client credentials) — доступ к чужим аккаунтам не требуется.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

TOKEN_URL = "https://id.twitch.tv/oauth2/token"
API_BASE = "https://api.twitch.tv/helix"

# Helix отдаёт максимум 100 записей за запрос.
PAGE_SIZE = 100

RAW_DIR = Path("clips/raw")
MANIFEST = Path("config/twitch_clips.json")


class TwitchError(RuntimeError):
    pass


@dataclass
class Clip:
    id: str
    url: str
    title: str
    broadcaster: str
    creator: str
    views: int
    duration: float
    language: str
    created_at: str
    thumbnail: str

    @property
    def slug(self) -> str:
        """Имя файла: просмотры вперёд, чтобы сортировка была осмысленной."""
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in self.id)
        return f"{self.views:08d}_{safe}"


def _request(url: str, *, data: bytes | None = None, headers: dict | None = None) -> dict:
    req = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:300]
        raise TwitchError(f"HTTP {exc.code} на {url.split('?')[0]}: {body}") from exc
    except urllib.error.URLError as exc:
        raise TwitchError(f"сеть недоступна: {exc.reason}") from exc


def app_token(client_id: str, client_secret: str) -> str:
    payload = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
    }).encode()
    data = _request(TOKEN_URL, data=payload)
    token = data.get("access_token")
    if not token:
        raise TwitchError(f"токен не выдан: {data}")
    return token


def credentials() -> tuple[str, str]:
    cid = os.environ.get("TWITCH_CLIENT_ID", "").strip()
    secret = os.environ.get("TWITCH_CLIENT_SECRET", "").strip()
    if not cid or not secret:
        raise TwitchError(
            "нет ключей. Заведи приложение на dev.twitch.tv и выставь "
            "TWITCH_CLIENT_ID и TWITCH_CLIENT_SECRET"
        )
    return cid, secret


def _headers(client_id: str, token: str) -> dict:
    return {"Client-Id": client_id, "Authorization": f"Bearer {token}"}


def api_get(path: str, params: dict, client_id: str, token: str) -> dict:
    url = f"{API_BASE}/{path}?{urllib.parse.urlencode(params, doseq=True)}"
    return _request(url, headers=_headers(client_id, token))


def game_id(name: str, client_id: str, token: str) -> str:
    data = api_get("games", {"name": name}, client_id, token)
    items = data.get("data") or []
    if not items:
        raise TwitchError(
            f"игра {name!r} не найдена. Название должно совпадать точно, "
            f"как на twitch.tv — например 'Counter-Strike'"
        )
    return items[0]["id"]


def parse_clip(row: dict) -> Clip:
    return Clip(
        id=row.get("id", ""),
        url=row.get("url", ""),
        title=row.get("title", ""),
        broadcaster=row.get("broadcaster_name", ""),
        creator=row.get("creator_name", ""),
        views=int(row.get("view_count", 0)),
        duration=float(row.get("duration", 0)),
        language=row.get("language", ""),
        created_at=row.get("created_at", ""),
        thumbnail=row.get("thumbnail_url", ""),
    )


def fetch_clips(
    game: str,
    *,
    days: int = 7,
    limit: int = 100,
    client_id: str | None = None,
    token: str | None = None,
) -> list[Clip]:
    """Топ клипов по игре за последние `days` дней.

    Helix при заданном окне отдаёт клипы по убыванию просмотров, поэтому
    первая же страница — это и есть лучшее за период.
    """
    if client_id is None or token is None:
        cid, secret = credentials()
        client_id = cid
        token = app_token(cid, secret)

    gid = game_id(game, client_id, token)

    ended = datetime.now(timezone.utc)
    started = ended - timedelta(days=days)
    params = {
        "game_id": gid,
        "started_at": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ended_at": ended.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "first": min(PAGE_SIZE, limit),
    }

    out: list[Clip] = []
    cursor = None
    while len(out) < limit:
        page = dict(params)
        if cursor:
            page["after"] = cursor
        data = api_get("clips", page, client_id, token)

        rows = data.get("data") or []
        if not rows:
            break
        out.extend(parse_clip(r) for r in rows)

        cursor = (data.get("pagination") or {}).get("cursor")
        if not cursor:
            break

    return out[:limit]


def select(
    clips: list[Clip],
    *,
    min_views: int = 0,
    min_seconds: float = 10.0,
    max_seconds: float = 60.0,
    languages: list[str] | None = None,
) -> list[Clip]:
    """Отсев под требования кампании.

    Нижняя граница по длине не случайна: кампания не принимает ролики
    короче 10 секунд, а клип короче этого уже не вытянуть.
    """
    langs = {l.lower() for l in languages} if languages else None
    out = []

    for c in clips:
        if c.views < min_views:
            continue
        if not (min_seconds <= c.duration <= max_seconds):
            continue
        if langs and c.language.lower() not in langs:
            continue
        out.append(c)

    return sorted(out, key=lambda c: c.views, reverse=True)


def save_manifest(clips: list[Clip], path: Path = MANIFEST) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([asdict(c) for c in clips], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def load_manifest(path: Path = MANIFEST) -> list[Clip]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    return [Clip(**r) for r in rows]


def download(clips: list[Clip], out_dir: Path = RAW_DIR) -> list[Path]:
    """Скачивание через yt-dlp: он знает формат клипов твича сам."""
    from .shell import require, run

    require("yt-dlp")
    out_dir.mkdir(parents=True, exist_ok=True)
    done: list[Path] = []

    for clip in clips:
        target = out_dir / f"{clip.slug}.mp4"
        if target.exists():
            print(f"[skip] уже есть: {target.name}")
            done.append(target)
            continue

        cmd = ["yt-dlp", "-f", "bestvideo+bestaudio/best",
               "--merge-output-format", "mp4", "-o", str(target), clip.url]
        print(f"[twitch] {clip.views:>7,} просмотров  {clip.title[:50]}")
        if run(cmd).returncode == 0 and target.exists():
            done.append(target)

    return done
