"""Приёмка роликов через Telegram: бот шлёт скачанное, ты жмёшь кнопку.

Смысл — убрать ручной просмотр папок. Скачанные клипы складываются в
очередь, бот отправляет каждый в личку с кнопками, решение раскладывает
файл по папкам. Дальше по конвейеру идёт только одобренное.

Два режима кнопок:
    both       две кнопки, взял / пропустил. Явно, но при полусотне
               роликов в день это полсотни нажатий.
    skip-only  одна кнопка «пропустить». Всё, что не отклонили за
               `hold` минут, уходит дальше само. Жать приходится только
               по браку, а его меньшинство.

Работает на long polling: вебхук не нужен, белый IP не нужен.

Ключи из окружения:
    TELEGRAM_BOT_TOKEN   от @BotFather
    TELEGRAM_CHAT_ID     твой id, узнаётся у @userinfobot
"""

from __future__ import annotations

import json
import mimetypes
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

API_BASE = "https://api.telegram.org"

QUEUE_DIR = Path("clips/queue")
APPROVED_DIR = Path("clips/approved")
REJECTED_DIR = Path("clips/rejected")
STATE_PATH = Path("review_state.json")

MODES = ("both", "skip-only")

# Bot API не принимает файлы больше этого через sendVideo.
MAX_UPLOAD_MB = 50

APPROVE = "ok"
REJECT = "no"


class TelegramError(RuntimeError):
    pass


def credentials() -> tuple[str, str]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        raise TelegramError(
            "нет ключей. Заведи бота у @BotFather, узнай свой id у "
            "@userinfobot и выставь TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID"
        )
    return token, chat


def _encode_multipart(fields: dict, files: dict) -> tuple[bytes, str]:
    """Собрать multipart/form-data вручную: тянуть requests ради этого незачем."""
    boundary = uuid.uuid4().hex
    sep = f"--{boundary}".encode()
    body = bytearray()

    for name, value in fields.items():
        if value is None:
            continue
        body += sep + b"\r\n"
        body += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        body += str(value).encode() + b"\r\n"

    for name, path in files.items():
        path = Path(path)
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body += sep + b"\r\n"
        body += (
            f'Content-Disposition: form-data; name="{name}"; '
            f'filename="{path.name}"\r\n'
        ).encode()
        body += f"Content-Type: {ctype}\r\n\r\n".encode()
        body += path.read_bytes() + b"\r\n"

    body += f"--{boundary}--\r\n".encode()
    return bytes(body), f"multipart/form-data; boundary={boundary}"


class Bot:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id

    def _url(self, method: str) -> str:
        return f"{API_BASE}/bot{self.token}/{method}"

    def call(self, method: str, params: dict | None = None,
             files: dict | None = None, timeout: int = 60) -> dict:
        params = {k: v for k, v in (params or {}).items() if v is not None}

        if files:
            body, ctype = _encode_multipart(params, files)
            headers = {"Content-Type": ctype}
        else:
            body = urllib.parse.urlencode(params).encode()
            headers = {"Content-Type": "application/x-www-form-urlencoded"}

        req = urllib.request.Request(self._url(method), data=body, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise TelegramError(f"{method}: HTTP {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            raise TelegramError(f"{method}: сеть недоступна ({exc.reason})") from exc

        if not data.get("ok"):
            raise TelegramError(f"{method}: {data.get('description', data)}")
        return data.get("result", {})

    def send_video(self, path: Path, caption: str, keyboard: dict | None) -> int:
        size_mb = path.stat().st_size / 1024 / 1024
        if size_mb > MAX_UPLOAD_MB:
            raise TelegramError(
                f"{path.name}: {size_mb:.0f} МБ, лимит Bot API {MAX_UPLOAD_MB} МБ"
            )
        result = self.call(
            "sendVideo",
            {
                "chat_id": self.chat_id,
                "caption": caption,
                "parse_mode": "HTML",
                "reply_markup": json.dumps(keyboard) if keyboard else None,
            },
            files={"video": path},
            timeout=300,
        )
        return result["message_id"]

    def get_updates(self, offset: int | None, timeout: int = 25) -> list[dict]:
        return self.call(
            "getUpdates",
            {"offset": offset, "timeout": timeout,
             "allowed_updates": json.dumps(["callback_query"])},
            timeout=timeout + 15,
        )

    def answer_callback(self, callback_id: str, text: str) -> None:
        try:
            self.call("answerCallbackQuery",
                      {"callback_query_id": callback_id, "text": text})
        except TelegramError:
            # Просроченный callback — не повод ронять цикл.
            pass

    def edit_caption(self, message_id: int, caption: str) -> None:
        try:
            self.call("editMessageCaption", {
                "chat_id": self.chat_id,
                "message_id": message_id,
                "caption": caption,
                "parse_mode": "HTML",
            })
        except TelegramError:
            pass


def keyboard_for(mode: str, token: str) -> dict:
    if mode == "skip-only":
        buttons = [[{"text": "✖ Пропустить", "callback_data": f"{REJECT}:{token}"}]]
    else:
        buttons = [[
            {"text": "✔ Беру", "callback_data": f"{APPROVE}:{token}"},
            {"text": "✖ Пропустить", "callback_data": f"{REJECT}:{token}"},
        ]]
    return {"inline_keyboard": buttons}


@dataclass
class Pending:
    """Отправленный на приёмку ролик, решение по которому ещё не принято."""

    token: str
    path: str
    message_id: int
    sent_at: str

    def expired(self, hold_minutes: float) -> bool:
        sent = datetime.fromisoformat(self.sent_at)
        if not sent.tzinfo:
            sent = sent.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - sent >= timedelta(minutes=hold_minutes)


@dataclass
class State:
    offset: int | None = None
    pending: dict[str, Pending] = field(default_factory=dict)
    done: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "State":
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            offset=raw.get("offset"),
            pending={k: Pending(**v) for k, v in (raw.get("pending") or {}).items()},
            done=raw.get("done") or [],
        )

    def save(self, path: Path) -> None:
        path.write_text(json.dumps({
            "offset": self.offset,
            "pending": {k: vars(v) for k, v in self.pending.items()},
            "done": self.done[-2000:],
        }, indent=2, ensure_ascii=False), encoding="utf-8")


def decide(state: State, token: str, verdict: str) -> Pending | None:
    """Снять ролик с ожидания. None, если такого токена уже нет."""
    return state.pending.pop(token, None)


def settle(path: Path, verdict: str, approved: Path, rejected: Path) -> Path | None:
    """Разложить файл по папкам согласно решению."""
    target_dir = approved if verdict == APPROVE else rejected
    target_dir.mkdir(parents=True, exist_ok=True)
    src = Path(path)
    if not src.exists():
        return None
    dst = target_dir / src.name
    src.replace(dst)
    return dst


def new_files(queue: Path, state: State, pattern: str = "*.mp4") -> list[Path]:
    """Файлы из очереди, которые ещё не отправляли."""
    known = set(state.done) | {p.path for p in state.pending.values()}
    return sorted(f for f in queue.glob(pattern) if str(f) not in known)


def run(
    *,
    queue: Path = QUEUE_DIR,
    approved: Path = APPROVED_DIR,
    rejected: Path = REJECTED_DIR,
    state_path: Path = STATE_PATH,
    mode: str = "skip-only",
    hold_minutes: float = 30.0,
    batch: int = 10,
    once: bool = False,
    poll_seconds: float = 5.0,
) -> None:
    """Цикл приёмки. Крутится, пока не остановят."""
    if mode not in MODES:
        raise TelegramError(f"нет режима {mode!r}, есть: {', '.join(MODES)}")

    token, chat = credentials()
    bot = Bot(token, chat)
    state = State.load(state_path)
    queue.mkdir(parents=True, exist_ok=True)

    print(f"[review] режим {mode}"
          + (f", автопропуск через {hold_minutes:.0f} мин" if mode == "skip-only" else "")
          + f", очередь {queue}")

    while True:
        # 1. отправить новое
        for f in new_files(queue, state)[:batch]:
            tok = uuid.uuid4().hex[:12]
            caption = _caption(f, mode, hold_minutes)
            try:
                mid = bot.send_video(f, caption, keyboard_for(mode, tok))
            except TelegramError as exc:
                print(f"[review] не отправился {f.name}: {exc}")
                state.done.append(str(f))
                continue
            state.pending[tok] = Pending(tok, str(f), mid,
                                         datetime.now(timezone.utc).isoformat())
            print(f"[review] отправлен {f.name}")
        state.save(state_path)

        # 2. забрать нажатия
        try:
            updates = bot.get_updates(state.offset)
        except TelegramError as exc:
            print(f"[review] опрос не удался: {exc}")
            updates = []

        for upd in updates:
            state.offset = upd["update_id"] + 1
            cq = upd.get("callback_query")
            if not cq:
                continue

            verdict, _, tok = (cq.get("data") or "").partition(":")
            item = decide(state, tok, verdict)
            if not item:
                bot.answer_callback(cq["id"], "уже обработано")
                continue

            dst = settle(Path(item.path), verdict, approved, rejected)
            state.done.append(item.path)
            label = "взят" if verdict == APPROVE else "пропущен"
            bot.answer_callback(cq["id"], label)
            bot.edit_caption(item.message_id,
                             f"<b>{Path(item.path).name}</b>\n{label}")
            print(f"[review] {Path(item.path).name}: {label}"
                  + (f" -> {dst}" if dst else " (файл пропал)"))

        # 3. в skip-only всё, что пролежало без ответа, уходит дальше
        if mode == "skip-only":
            for tok, item in list(state.pending.items()):
                if not item.expired(hold_minutes):
                    continue
                state.pending.pop(tok, None)
                settle(Path(item.path), APPROVE, approved, rejected)
                state.done.append(item.path)
                bot.edit_caption(item.message_id,
                                 f"<b>{Path(item.path).name}</b>\nвзят автоматически")
                print(f"[review] {Path(item.path).name}: взят автоматически")

        state.save(state_path)

        if once:
            return
        time.sleep(poll_seconds)


def _caption(path: Path, mode: str, hold_minutes: float) -> str:
    size_mb = path.stat().st_size / 1024 / 1024
    tail = ("\nбез ответа уйдёт дальше через "
            f"{hold_minutes:.0f} мин" if mode == "skip-only" else "")
    return f"<b>{path.name}</b>\n{size_mb:.1f} МБ{tail}"
