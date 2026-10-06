"""彩票游戏的纯 Python 逻辑和 SQLite 持久化。"""

from __future__ import annotations

import itertools
import json
import random
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable


GAME_RED = "双色球"
GAME_FRONT = "大乐透"


@dataclass(frozen=True)
class TicketSpec:
    game: str
    primary: tuple[int, ...]
    secondary: tuple[int, ...]
    mode: str = "复式"
    primary_dan: tuple[int, ...] = ()
    secondary_dan: tuple[int, ...] = ()
    multiplier: int = 1
    additional: bool = False

    def __post_init__(self) -> None:
        # SQLite JSON 将 tuple 还原为 list，统一成不可变序列，保证兑奖组合逻辑一致。
        object.__setattr__(self, "primary", tuple(self.primary))
        object.__setattr__(self, "secondary", tuple(self.secondary))
        object.__setattr__(self, "primary_dan", tuple(self.primary_dan))
        object.__setattr__(self, "secondary_dan", tuple(self.secondary_dan))

    @property
    def combinations(self) -> int:
        if self.game == GAME_RED:
            return _dan_combinations(self.primary_dan, self.primary, 6) * len(self.secondary)
        return _dan_combinations(self.primary_dan, self.primary, 5) * _dan_combinations(
            self.secondary_dan, self.secondary, 2
        )

    @property
    def stake(self) -> int:
        unit = 3 if self.game == GAME_FRONT and self.additional else 2
        return self.combinations * unit * self.multiplier


def _choose(n: int, r: int) -> int:
    if r < 0 or r > n:
        return 0
    return len(list(itertools.combinations(range(n), r)))


def _dan_combinations(dan: tuple[int, ...], tuo: tuple[int, ...], required: int) -> int:
    if dan:
        available = len(set(tuo) - set(dan))
        return _choose(available, required - len(dan))
    return _choose(len(tuo), required)


def _numbers(value: str) -> tuple[int, ...]:
    return tuple(int(item) for item in re.findall(r"\d+", value))


def _validated(items: Iterable[int], low: int, high: int, label: str) -> tuple[int, ...]:
    result = tuple(sorted(set(items)))
    if any(item < low or item > high for item in result):
        raise ValueError(f"{label}号码必须在 {low}-{high} 之间。")
    return result


def _label_numbers(raw: str, labels: tuple[str, ...]) -> tuple[int, ...]:
    for label in labels:
        match = re.search(rf"{re.escape(label)}\s*[:：]?\s*([^;；|]+)", raw)
        if match:
            return _numbers(match.group(1))
    return ()


def parse_ticket(game: str, raw: str) -> TicketSpec:
    """解析紧凑中文投注格式，支持单式、复式、胆拖、倍投和大乐透追加。"""
    text = raw.strip()
    additional = game == GAME_FRONT and bool(re.search(r"追加|加倍", text, re.I))
    multiplier_match = re.search(r"(?:倍投|倍|x|X)\s*(\d+)", text)
    multiplier = int(multiplier_match.group(1)) if multiplier_match else 1
    if not 1 <= multiplier <= 99:
        raise ValueError("倍投范围是 1-99 倍。")
    text = re.sub(r"追加|加倍|倍投\s*\d+|倍投\s*\d+|倍\s*\d+|[xX]\s*\d+", "", text)
    is_dantuo = "胆拖" in text or "胆码" in text or "拖码" in text

    if game == GAME_RED:
        primary_low, primary_high, primary_need = 1, 33, 6
        secondary_low, secondary_high, secondary_need = 1, 16, 1
        dan = _label_numbers(text, ("红球胆码", "胆码")) if is_dantuo else ()
        tuo = _label_numbers(text, ("红球拖码", "拖码")) if is_dantuo else ()
        primary = _label_numbers(text, ("红球", "红", "号码"))
        secondary = _label_numbers(text, ("蓝球", "蓝"))
        if not primary and not dan:
            segments = re.split(r"\+|加", text, maxsplit=1)
            primary = _numbers(segments[0])
            secondary = _numbers(segments[1]) if len(segments) == 2 else ()
        if dan:
            primary = tuple(sorted(set(dan + tuo)))
        primary = _validated(primary, primary_low, primary_high, "红球")
        secondary = _validated(secondary, secondary_low, secondary_high, "蓝球")
        if dan and not 1 <= len(dan) <= 5:
            raise ValueError("双色球胆码需要 1-5 个。")
        if len(primary) < primary_need or len(secondary) < secondary_need:
            raise ValueError("双色球需要至少 6 个红球和 1 个蓝球。")
        mode = "胆拖" if dan else ("复式" if len(primary) > 6 or len(secondary) > 1 else "单式")
        if _dan_combinations(tuple(sorted(set(dan))), primary, 6) * len(secondary) * 2 * multiplier > 20000:
            raise ValueError("双色球单张投注金额不能超过 20000 龙门币。")
        return TicketSpec(GAME_RED, primary, secondary, mode, tuple(sorted(set(dan))), (), multiplier, False)

    primary_low, primary_high, primary_need = 1, 35, 5
    secondary_low, secondary_high, secondary_need = 1, 12, 2
    primary_dan = _label_numbers(text, ("前区胆码", "前胆码")) if is_dantuo else ()
    primary_tuo = _label_numbers(text, ("前区拖码", "前拖码")) if is_dantuo else ()
    secondary_dan = _label_numbers(text, ("后区胆码", "后胆码")) if is_dantuo else ()
    secondary_tuo = _label_numbers(text, ("后区拖码", "后拖码")) if is_dantuo else ()
    primary = _label_numbers(text, ("前区", "前区号码", "前"))
    secondary = _label_numbers(text, ("后区", "后区号码", "后"))
    if not primary and not primary_dan:
        segments = re.split(r"\+|加", text, maxsplit=1)
        primary = _numbers(segments[0])
        secondary = _numbers(segments[1]) if len(segments) == 2 else ()
    if primary_dan:
        primary = tuple(sorted(set(primary_dan + primary_tuo)))
    if secondary_dan:
        secondary = tuple(sorted(set(secondary_dan + secondary_tuo)))
    primary = _validated(primary, primary_low, primary_high, "前区")
    secondary = _validated(secondary, secondary_low, secondary_high, "后区")
    if primary_dan and not 1 <= len(primary_dan) <= 4:
        raise ValueError("大乐透前区胆码需要 1-4 个。")
    if secondary_dan and not 1 <= len(secondary_dan) <= 1:
        raise ValueError("大乐透后区胆码最多 1 个。")
    if len(primary) < primary_need or len(secondary) < secondary_need:
        raise ValueError("大乐透需要至少 5 个前区和 2 个后区号码。")
    mode = "胆拖" if primary_dan or secondary_dan else (
        "复式" if len(primary) > 5 or len(secondary) > 2 else "单式"
    )
    result = TicketSpec(
        GAME_FRONT,
        primary,
        secondary,
        mode,
        tuple(sorted(set(primary_dan))),
        tuple(sorted(set(secondary_dan))),
        multiplier,
        additional,
    )
    if result.stake > (30000 if additional else 20000):
        raise ValueError("大乐透单张投注金额超过上限。")
    return result


def generate_draw(game: str) -> tuple[tuple[int, ...], tuple[int, ...]]:
    if game == GAME_RED:
        return tuple(sorted(random.sample(range(1, 34), 6))), tuple(sorted(random.sample(range(1, 17), 1)))
    return tuple(sorted(random.sample(range(1, 36), 5))), tuple(sorted(random.sample(range(1, 13), 2)))


def prize_level(game: str, primary_hits: int, secondary_hits: int) -> tuple[int, int]:
    if game == GAME_RED:
        if primary_hits == 6 and secondary_hits == 1:
            return 1, 5_000_000
        if primary_hits == 6:
            return 2, 500_000
        if primary_hits == 5 and secondary_hits == 1:
            return 3, 3_000
        if primary_hits == 5 or (primary_hits == 4 and secondary_hits == 1):
            return 4, 200
        if primary_hits == 4 or (primary_hits == 3 and secondary_hits == 1):
            return 5, 10
        if secondary_hits == 1:
            return 6, 5
        return 0, 0
    if primary_hits == 5 and secondary_hits == 2:
        return 1, 5_000_000
    if primary_hits == 5 and secondary_hits == 1:
        return 2, 500_000
    if primary_hits == 5 or (primary_hits == 4 and secondary_hits == 2):
        return 3, 10_000
    if primary_hits == 4 and secondary_hits == 1:
        return 4, 3_000
    if primary_hits == 4 or (primary_hits == 3 and secondary_hits == 2):
        return 5, 300
    if primary_hits == 3 and secondary_hits == 1:
        return 6, 200
    if primary_hits == 2 and secondary_hits == 2:
        return 7, 10
    if primary_hits == 1 and secondary_hits == 2:
        return 7, 5
    return 0, 0


class LotteryDB:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._create_schema()

    def _create_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS players (
                user_id TEXT PRIMARY KEY,
                nickname TEXT NOT NULL,
                balance INTEGER NOT NULL DEFAULT 0,
                total_winnings INTEGER NOT NULL DEFAULT 0,
                last_nickname_date TEXT
            );
            CREATE TABLE IF NOT EXISTS checkins (
                user_id TEXT NOT NULL,
                day TEXT NOT NULL,
                PRIMARY KEY (user_id, day)
            );
            CREATE TABLE IF NOT EXISTS draws (
                game TEXT NOT NULL,
                draw_date TEXT NOT NULL,
                primary_numbers TEXT NOT NULL,
                secondary_numbers TEXT NOT NULL,
                PRIMARY KEY (game, draw_date)
            );
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                game TEXT NOT NULL,
                purchased_at TEXT NOT NULL,
                draw_date TEXT NOT NULL,
                spec_json TEXT NOT NULL,
                stake INTEGER NOT NULL,
                settled INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS scratch_sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_key TEXT UNIQUE,
                user_id TEXT NOT NULL,
                tier INTEGER NOT NULL,
                prize INTEGER NOT NULL,
                card_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def scratch_seen(self, message_key: str) -> bool:
        with self._lock:
            return bool(message_key and self.conn.execute(
                "SELECT 1 FROM scratch_sales WHERE message_key=?", (message_key,)
            ).fetchone())

    def buy_scratch(self, user_id, nickname, day, card, message_key):
        # Lock before reading balances or event IDs, including across plugin instances.
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                if self.scratch_seen(message_key):
                    self.conn.rollback()
                    return None
                self.upsert_player(user_id, nickname, day)
                balance = int(self.player(user_id)["balance"])
                if balance < card.tier:
                    raise ValueError(f"余额不足，需要 {card.tier} 龙门币，当前余额 {balance}。")
                prize = card.total
                self.conn.execute(
                    "UPDATE players SET balance=balance-?+?, total_winnings=total_winnings+? WHERE user_id=?",
                    (card.tier, prize, prize, user_id),
                )
                result = self.conn.execute(
                    "INSERT INTO scratch_sales(message_key,user_id,tier,prize,card_json,created_at) VALUES(?,?,?,?,?,?)",
                    (message_key or None, user_id, card.tier, prize,
                     json.dumps(card.to_dict(), ensure_ascii=False), day),
                )
                self.conn.commit()
                return int(result.lastrowid), balance - card.tier + prize
            except BaseException:
                self.conn.rollback()
                raise

    def upsert_player(self, user_id: str, nickname: str, today: str) -> None:
        with self._lock:
            self.conn.execute(
                """INSERT INTO players(user_id, nickname, last_nickname_date) VALUES(?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    nickname=CASE WHEN players.last_nickname_date IS NULL OR players.last_nickname_date <> excluded.last_nickname_date
                        THEN excluded.nickname ELSE players.nickname END,
                    last_nickname_date=excluded.last_nickname_date""",
                (user_id, nickname, today),
            )

    def ensure_player(self, user_id: str, nickname: str, today: str) -> sqlite3.Row:
        with self._lock, self.conn:
            self.upsert_player(user_id, nickname, today)
            return self.player(user_id)

    def add_balance(self, user_id: str, amount: int) -> int:
        with self._lock, self.conn:
            self.conn.execute("UPDATE players SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
            return int(self.player(user_id)["balance"])

    def record_scratch(self, user_id: str, tier: int, prize: int) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS scratch_history (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, tier INTEGER, prize INTEGER, created_at TEXT)"
            )
            self.conn.execute(
                "INSERT INTO scratch_history(user_id, tier, prize, created_at) VALUES(?, ?, ?, ?)",
                (user_id, tier, prize, datetime.now().isoformat(timespec="seconds")),
            )

    def player(self, user_id: str) -> sqlite3.Row:
        with self._lock:
            row = self.conn.execute("SELECT * FROM players WHERE user_id = ?", (user_id,)).fetchone()
            if row is None:
                raise ValueError("玩家记录不存在。")
            return row

    def checkin(self, user_id: str, nickname: str, day: str, amount: int) -> tuple[bool, int]:
        with self._lock, self.conn:
            self.upsert_player(user_id, nickname, day)
            inserted = self.conn.execute(
                "INSERT OR IGNORE INTO checkins(user_id, day) VALUES(?, ?)", (user_id, day)
            ).rowcount
            if inserted:
                self.conn.execute("UPDATE players SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
            return bool(inserted), int(self.player(user_id)["balance"])

    def buy(self, user_id: str, nickname: str, day: str, spec: TicketSpec, draw_date: str) -> int:
        with self._lock, self.conn:
            self.upsert_player(user_id, nickname, day)
            balance = int(self.player(user_id)["balance"])
            if balance < spec.stake:
                raise ValueError(f"余额不足，需要 {spec.stake} 龙门币，当前余额 {balance}。")
            cur = self.conn.execute(
                "INSERT INTO tickets(user_id, game, purchased_at, draw_date, spec_json, stake) VALUES(?, ?, ?, ?, ?, ?)",
                (user_id, spec.game, datetime.now().isoformat(timespec="seconds"), draw_date, json.dumps(spec.__dict__), spec.stake),
            )
            self.conn.execute("UPDATE players SET balance = balance - ? WHERE user_id = ?", (spec.stake, user_id))
            return int(cur.lastrowid)

    def get_draw(self, game: str, draw_date: str) -> tuple[tuple[int, ...], tuple[int, ...]]:
        with self._lock:
            row = self.conn.execute("SELECT * FROM draws WHERE game = ? AND draw_date = ?", (game, draw_date)).fetchone()
            if row is None:
                numbers = generate_draw(game)
                self.conn.execute(
                    "INSERT INTO draws(game, draw_date, primary_numbers, secondary_numbers) VALUES(?, ?, ?, ?)",
                    (game, draw_date, json.dumps(numbers[0]), json.dumps(numbers[1])),
                )
                self.conn.commit()
                return numbers
            return tuple(json.loads(row["primary_numbers"])), tuple(json.loads(row["secondary_numbers"]))

    def settle(self, user_id: str, game: str, today: str, history_days: int) -> dict[str, Any]:
        with self._lock:
            cutoff = (date.fromisoformat(today) - timedelta(days=history_days - 1)).isoformat()
            tickets = self.conn.execute(
                "SELECT * FROM tickets WHERE user_id = ? AND game = ? AND settled = 0 AND draw_date >= ? AND draw_date < ?",
                (user_id, game, cutoff, today),
            ).fetchall()
            total = 0
            detail: list[str] = []
            with self.conn:
                for ticket in tickets:
                    spec = TicketSpec(**json.loads(ticket["spec_json"]))
                    winning = self.get_draw(game, ticket["draw_date"])
                    ticket_total = 0
                    for primary in _ticket_primary_combinations(spec):
                        for secondary in _ticket_secondary_combinations(spec):
                            level, amount = prize_level(
                                game,
                                len(set(primary) & set(winning[0])),
                                len(set(secondary) & set(winning[1])),
                            )
                            if amount:
                                if game == GAME_FRONT and spec.additional and level in (1, 2):
                                    amount = int(amount * 1.8)
                                ticket_total += amount * spec.multiplier
                    total += ticket_total
                    detail.append(f"{ticket['draw_date']}：{ticket_total} 龙门币")
                    self.conn.execute("UPDATE tickets SET settled = 1 WHERE id = ?", (ticket["id"],))
                if total:
                    self.conn.execute(
                        "UPDATE players SET balance = balance + ?, total_winnings = total_winnings + ? WHERE user_id = ?",
                        (total, total, user_id),
                    )
            return {"tickets": len(tickets), "total": total, "detail": detail}

    def leaderboard(self, limit: int = 10) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(
                "SELECT nickname, balance, total_winnings FROM players ORDER BY (balance + total_winnings) DESC, balance DESC LIMIT ?",
                (limit,),
            ).fetchall()

    def cleanup_draws(self, keep_days: int, today: str) -> None:
        cutoff = (date.fromisoformat(today) - timedelta(days=keep_days - 1)).isoformat()
        with self._lock, self.conn:
            self.conn.execute("DELETE FROM draws WHERE draw_date < ?", (cutoff,))


def _ticket_primary_combinations(spec: TicketSpec) -> Iterable[tuple[int, ...]]:
    if spec.game == GAME_RED:
        required = 6
    else:
        required = 5
    if spec.primary_dan:
        for extra in itertools.combinations([n for n in spec.primary if n not in spec.primary_dan], required - len(spec.primary_dan)):
            yield tuple(sorted(spec.primary_dan + extra))
    else:
        yield from itertools.combinations(spec.primary, required)


def _ticket_secondary_combinations(spec: TicketSpec) -> Iterable[tuple[int, ...]]:
    required = 1 if spec.game == GAME_RED else 2
    if spec.secondary_dan:
        for extra in itertools.combinations([n for n in spec.secondary if n not in spec.secondary_dan], required - len(spec.secondary_dan)):
            yield tuple(sorted(spec.secondary_dan + extra))
    else:
        yield from itertools.combinations(spec.secondary, required)


def weighted_scratch(prize_probabilities: dict[str, Any]) -> tuple[int, float]:
    choices = [(int(prize), max(0.0, float(probability))) for prize, probability in prize_probabilities.items()]
    total = sum(probability for _, probability in choices)
    if not choices or total <= 0:
        raise ValueError("刮刮乐概率配置为空或总概率不大于 0。")
    selected = random.random() * total
    cursor = 0.0
    for prize, probability in choices:
        cursor += probability
        if selected <= cursor:
            return prize, probability / total
    prize, probability = choices[-1]
    return prize, probability / total
