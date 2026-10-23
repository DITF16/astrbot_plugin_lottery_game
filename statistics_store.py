"""Recoverable, incremental statistics on an independent SQLite connection/thread."""

import json
import logging
import sqlite3
import threading
from collections import Counter
from datetime import datetime


FIELDS = ("balance", "total_winnings", "lottery_count", "scratch_count", "lottery_max", "scratch_max")


class StatisticsStore:
    def __init__(self, path):
        self.path = str(path)
        self.stop = threading.Event()
        self.thread = None

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=.15)
        conn.row_factory = sqlite3.Row
        return conn

    def start(self):
        self.thread = threading.Thread(target=self._run, name="lottery-statistics", daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=5)

    def _run(self):
        while not self.stop.is_set():
            try:
                self.process_batch()
            except sqlite3.OperationalError:
                logging.getLogger(__name__).debug("Statistics database busy; retrying next cycle")
            except Exception:
                logging.getLogger(__name__).exception("Statistics update failed; source records retained")
            self.stop.wait(1)

    def process_batch(self):
        conn = self.connect()
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                for kind, table in (("lottery", "tickets"), ("scratch", "scratch_sales")):
                    cursor = conn.execute("SELECT value FROM stats_cursor WHERE kind=?", (kind,)).fetchone()
                    last = cursor[0] if cursor else 0
                    rows = conn.execute(f"SELECT * FROM {table} WHERE id>? ORDER BY id LIMIT 100", (last,)).fetchall()
                    for row in rows:
                        uid = row["user_id"]
                        conn.execute("INSERT OR IGNORE INTO player_stats(user_id) VALUES(?)", (uid,))
                        conn.execute(f"UPDATE player_stats SET {kind}_count={kind}_count+1 WHERE user_id=?", (uid,))
                        if kind == "scratch":
                            conn.execute("UPDATE player_stats SET scratch_max=MAX(scratch_max,?) WHERE user_id=?", (row["prize"], uid))
                            card = json.loads(row["card_json"])
                            rewards = Counter(int(item["prize"]) for item in card["rows"] if card["winning"] in item["numbers"])
                            for amount, count in rewards.items():
                                conn.execute("""INSERT INTO scratch_rewards VALUES(?,?,?)
                                ON CONFLICT(user_id,amount) DO UPDATE SET count=count+excluded.count""", (uid, amount, count))
                    if rows:
                        conn.execute("INSERT OR REPLACE INTO stats_cursor VALUES(?,?)", (kind, rows[-1]["id"]))
                settled = conn.execute("""SELECT id,user_id,prize FROM tickets WHERE settled=1 AND prize IS NOT NULL
                    AND id NOT IN (SELECT ticket_id FROM stats_settled) ORDER BY id LIMIT 100""").fetchall()
                for row in settled:
                    conn.execute("INSERT OR IGNORE INTO player_stats(user_id) VALUES(?)", (row["user_id"],))
                    conn.execute("UPDATE player_stats SET lottery_max=MAX(lottery_max,?) WHERE user_id=?", (row["prize"],row["user_id"]))
                    conn.execute("INSERT INTO stats_settled VALUES(?)", (row["id"],))
        finally:
            conn.close()

    def users(self, search="", page=1):
        conn = self.connect()
        try:
            query = """FROM players p LEFT JOIN player_stats s ON p.user_id=s.user_id
                WHERE p.user_id LIKE ? OR p.nickname LIKE ?"""
            args = (f"%{search}%", f"%{search}%")
            count = conn.execute("SELECT COUNT(*) " + query, args).fetchone()[0]
            fields = ",".join(f"COALESCE(s.{key},0) AS {key}" for key in FIELDS[2:])
            rows = conn.execute("SELECT p.*, " + fields + " " + query + " ORDER BY p.total_winnings DESC,p.user_id LIMIT 25 OFFSET ?", (*args, (page-1)*25)).fetchall()
            return {"items": [dict(row) for row in rows], "total":count, "page":page}
        finally:
            conn.close()

    def user(self, uid):
        conn = self.connect()
        try:
            row = conn.execute("SELECT * FROM players WHERE user_id=?",(uid,)).fetchone()
            if row is None:
                raise ValueError("玩家不存在")
            stats = conn.execute("SELECT * FROM player_stats WHERE user_id=?",(uid,)).fetchone()
            data = dict(row)
            data.update(dict(stats) if stats else {key:0 for key in FIELDS[2:]})
            data["rewards"] = [dict(row) for row in conn.execute("SELECT amount,count FROM scratch_rewards WHERE user_id=? ORDER BY amount",(uid,))]
            return data
        finally:
            conn.close()

    def edit(self, uid, data):
        if not isinstance(data, dict) or set(data) - set(FIELDS) - {"rewards", "nickname"}:
            raise ValueError("修改字段不合法")
        for field in FIELDS:
            if field in data and (type(data[field]) is not int or not 0 <= data[field] <= 9_000_000_000_000_000):
                raise ValueError("数值须为 0 到 9000000000000000 的整数")
        if "nickname" in data and (not isinstance(data["nickname"], str) or not 1 <= len(data["nickname"].strip()) <= 200):
            raise ValueError("昵称长度须为1-200")
        rewards = data.get("rewards")
        if rewards is not None:
            if not isinstance(rewards,list) or len(rewards)>1000:
                raise ValueError("奖励明细不合法")
            amounts = set()
            for item in rewards:
                if not isinstance(item,dict) or set(item)!={"amount","count"} or any(type(item[k]) is not int or not 0<=item[k]<=9_000_000_000_000_000 for k in item) or item["amount"] in amounts:
                    raise ValueError("奖励金额/次数须为非负整数且金额不能重复")
                amounts.add(item["amount"])
        conn = self.connect()
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                before = conn.execute("SELECT * FROM players WHERE user_id=?",(uid,)).fetchone()
                if before is None:
                    raise ValueError("玩家不存在")
                stats = conn.execute("SELECT * FROM player_stats WHERE user_id=?",(uid,)).fetchone()
                snapshot = dict(before)
                snapshot.update(dict(stats) if stats else {})
                snapshot["rewards"] = [dict(r) for r in conn.execute("SELECT amount,count FROM scratch_rewards WHERE user_id=?",(uid,))]
                conn.execute("INSERT OR IGNORE INTO player_stats(user_id) VALUES(?)",(uid,))
                for field,value in data.items():
                    if field == "rewards":
                        conn.execute("DELETE FROM scratch_rewards WHERE user_id=?",(uid,))
                        conn.executemany("INSERT INTO scratch_rewards VALUES(?,?,?)",[(uid,r["amount"],r["count"]) for r in value])
                    else:
                        table = "players" if field in ("nickname","balance","total_winnings") else "player_stats"
                        conn.execute(f"UPDATE {table} SET {field}=? WHERE user_id=?",(value,uid))
                conn.execute("INSERT INTO admin_audit(user_id,changed_at,before_json,after_json) VALUES(?,?,?,?)",(uid,datetime.now().isoformat(),json.dumps(snapshot,ensure_ascii=False),json.dumps(data,ensure_ascii=False)))
        finally:
            conn.close()

    def overview(self):
        conn = self.connect()
        try:
            summary = dict(conn.execute("SELECT COUNT(*) AS users,COALESCE(SUM(balance),0) AS balance,COALESCE(SUM(total_winnings),0) AS winnings FROM players").fetchone())
            summary.update(dict(conn.execute("SELECT COALESCE(SUM(lottery_count),0) AS lottery_count,COALESCE(SUM(scratch_count),0) AS scratch_count,COALESCE(MAX(MAX(lottery_max,scratch_max)),0) AS highest FROM player_stats").fetchone()))
            rewards = [dict(r) for r in conn.execute("SELECT amount,SUM(count) AS count FROM scratch_rewards GROUP BY amount ORDER BY amount")]
            trend = [dict(r) for r in conn.execute("""SELECT substr(created_at,1,10) AS day,COUNT(*) AS count,SUM(prize) AS amount,
                SUM(CASE WHEN prize>0 THEN 1 ELSE 0 END) AS wins FROM scratch_sales
                WHERE created_at>=date('now','-29 days') GROUP BY substr(created_at,1,10) ORDER BY day""")]
            summary["legacy_unknown"] = conn.execute("SELECT COUNT(*) FROM tickets WHERE settled=1 AND prize IS NULL").fetchone()[0]
            lottery_trend = [dict(r) for r in conn.execute("""SELECT substr(purchased_at,1,10) AS day,
                COUNT(*) AS count,COALESCE(SUM(prize),0) AS amount,
                SUM(CASE WHEN prize>0 THEN 1 ELSE 0 END) AS wins FROM tickets
                WHERE purchased_at>=date('now','-29 days') GROUP BY substr(purchased_at,1,10) ORDER BY day""")]
            return {"summary":summary,"rewards":rewards,"trend":trend,"lottery_trend":lottery_trend}
        finally:
            conn.close()
