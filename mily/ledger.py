"""Учёт сетки: аккаунты, публикации, окно выплаты.

Две вещи, которые эта штука не даёт забыть.

Первая — окно. Ролики старше 7 дней не принимаются, а награду забирают
один раз. Значит забирать надо как можно позже внутри окна: снял на 10
тысячах — потерял те 100 тысяч, что пришли бы к седьмому дню. Пропустил
окно — потерял всё. Ledger считает дедлайны и показывает, что горит.

Вторая — срок жизни аккаунта. Аккаунт здесь расходник: важно не то,
забанят ли его, а сколько просмотров он отдаст до бана. Это считается
по факту, а не на глаз.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path("ledger.db")

# Ролики старше этого срока кампания не принимает.
CLAIM_WINDOW_DAYS = 7
# За сколько дней до дедлайна начинать предупреждать.
WARN_BEFORE_DAYS = 2

# Ставка кампании за 1000 просмотров, для прикидки в сводках.
DEFAULT_RATE = 1.5

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    name        TEXT PRIMARY KEY,
    device      TEXT,
    proxy       TEXT,
    created_at  TEXT NOT NULL,
    banned_at   TEXT
);

CREATE TABLE IF NOT EXISTS posts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account     TEXT NOT NULL REFERENCES accounts(name),
    video       TEXT NOT NULL,
    posted_at   TEXT NOT NULL,
    views       INTEGER NOT NULL DEFAULT 0,
    views_at    TEXT,
    claimed_at  TEXT,
    payout      REAL,
    note        TEXT
);

CREATE INDEX IF NOT EXISTS posts_account ON posts(account);
CREATE INDEX IF NOT EXISTS posts_claimed ON posts(claimed_at);
"""


def now() -> datetime:
    return datetime.now(timezone.utc)


def parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


# --- аккаунты ---------------------------------------------------------------

def add_account(conn, name: str, device: str = "", proxy: str = "") -> None:
    conn.execute(
        "INSERT OR IGNORE INTO accounts(name, device, proxy, created_at) "
        "VALUES (?,?,?,?)",
        (name, device, proxy, now().isoformat()),
    )
    conn.commit()


def ban_account(conn, name: str, when: datetime | None = None) -> bool:
    cur = conn.execute(
        "UPDATE accounts SET banned_at=? WHERE name=? AND banned_at IS NULL",
        ((when or now()).isoformat(), name),
    )
    conn.commit()
    return cur.rowcount > 0


# --- публикации -------------------------------------------------------------

def add_post(conn, account: str, video: str, posted_at: datetime | None = None) -> int:
    row = conn.execute("SELECT 1 FROM accounts WHERE name=?", (account,)).fetchone()
    if not row:
        raise ValueError(f"нет аккаунта {account!r} — заведи через `ledger account`")

    cur = conn.execute(
        "INSERT INTO posts(account, video, posted_at) VALUES (?,?,?)",
        (account, video, (posted_at or now()).isoformat()),
    )
    conn.commit()
    return cur.lastrowid


def set_views(conn, post_id: int, views: int) -> bool:
    cur = conn.execute(
        "UPDATE posts SET views=?, views_at=? WHERE id=?",
        (views, now().isoformat(), post_id),
    )
    conn.commit()
    return cur.rowcount > 0


def claim(conn, post_id: int, payout: float) -> bool:
    cur = conn.execute(
        "UPDATE posts SET claimed_at=?, payout=? WHERE id=? AND claimed_at IS NULL",
        (now().isoformat(), payout, post_id),
    )
    conn.commit()
    return cur.rowcount > 0


# --- окно выплаты -----------------------------------------------------------

@dataclass
class Due:
    id: int
    account: str
    video: str
    views: int
    hours_left: float

    def payout_estimate(self, rate: float = DEFAULT_RATE) -> float:
        """Прикидка выплаты по текущим просмотрам.

        Важно: платят за просмотры на момент забора, а не за итоговые.
        Ролик, который дойдёт до трёх миллионов за месяц, к седьмому дню
        может стоить в разы меньше — и это всё, что за него дадут.
        """
        return self.views / 1000 * rate

    @property
    def state(self) -> str:
        if self.hours_left <= 0:
            return "ПРОСРОЧЕН"
        if self.hours_left <= 24:
            return "горит"
        if self.hours_left <= WARN_BEFORE_DAYS * 24:
            return "скоро"
        return "ждёт"


def due_posts(conn, include_expired: bool = True) -> list[Due]:
    """Незабранные ролики, отсортированные по остатку времени."""
    deadline = timedelta(days=CLAIM_WINDOW_DAYS)
    current = now()
    out = []

    for row in conn.execute("SELECT * FROM posts WHERE claimed_at IS NULL"):
        left = (parse_dt(row["posted_at"]) + deadline - current).total_seconds() / 3600
        if left <= 0 and not include_expired:
            continue
        out.append(Due(row["id"], row["account"], row["video"], row["views"], left))

    return sorted(out, key=lambda d: d.hours_left)


# --- сводка -----------------------------------------------------------------

@dataclass
class AccountStats:
    name: str
    alive: bool
    days: float
    posts: int
    views: int
    payout: float

    @property
    def views_per_day(self) -> float:
        """Отдача аккаунта в сутки.

        Знаменатель не опускается ниже суток: аккаунт, заведённый час
        назад, иначе показывает отдачу в миллиарды просмотров в день.
        За неполные сутки отдача в день это просто все его просмотры.
        """
        return self.views / max(self.days, 1.0)


def account_stats(conn) -> list[AccountStats]:
    """Срок жизни и отдача по каждому аккаунту.

    Для живых срок считается до сейчас, для забаненных — до бана: иначе
    мёртвые аккаунты продолжали бы «стареть» и занижать среднюю отдачу.
    """
    current = now()
    out = []

    for acc in conn.execute("SELECT * FROM accounts ORDER BY created_at"):
        end = parse_dt(acc["banned_at"]) if acc["banned_at"] else current
        days = max((end - parse_dt(acc["created_at"])).total_seconds() / 86400, 0.0)

        agg = conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(views),0) v, COALESCE(SUM(payout),0) p "
            "FROM posts WHERE account=?",
            (acc["name"],),
        ).fetchone()

        out.append(AccountStats(
            name=acc["name"],
            alive=acc["banned_at"] is None,
            days=days,
            posts=agg["n"],
            views=agg["v"],
            payout=agg["p"],
        ))

    return out


def totals(conn) -> dict:
    row = conn.execute(
        "SELECT COUNT(*) posts, COALESCE(SUM(views),0) views, "
        "COALESCE(SUM(payout),0) payout, "
        "SUM(CASE WHEN claimed_at IS NULL THEN 1 ELSE 0 END) unclaimed FROM posts"
    ).fetchone()
    accs = conn.execute(
        "SELECT COUNT(*) total, SUM(CASE WHEN banned_at IS NULL THEN 1 ELSE 0 END) alive "
        "FROM accounts"
    ).fetchone()
    return {
        "posts": row["posts"],
        "views": row["views"],
        "payout": row["payout"],
        "unclaimed": row["unclaimed"] or 0,
        "accounts": accs["total"],
        "alive": accs["alive"] or 0,
    }
