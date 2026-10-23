import asyncio
import sqlite3
import tempfile
import time
from pathlib import Path
from datetime import date, timedelta

from lottery_engine import LotteryDB, GAME_RED, parse_ticket
from scratch import ScratchCard, ScratchRow, find_font
from statistics_store import StatisticsStore
from leaderboard import render_leaderboard


def test_statistics_recovery_edit_and_rankings():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)/"lottery.db"
        db = LotteryDB(path)
        day = date.today().isoformat()
        yesterday = (date.today()-timedelta(days=1)).isoformat()
        db.checkin("1","玩家一",day,100)
        db.checkin("2","玩家二",day,100)
        card = ScratchCard(10,22,[ScratchRow([22],20),ScratchRow([22],30)],400000)
        assert db.buy_scratch("1","玩家一",day,card,"first")
        assert db.buy_scratch("1","玩家一",day,card,"first") is None
        spec = parse_ticket(GAME_RED,"1 2 3 4 5 6+7")
        db.buy("2","玩家二",day,spec,yesterday)
        db.conn.execute("INSERT INTO draws VALUES(?,?,?,?)",(GAME_RED,yesterday,'[1,2,3,4,5,6]','[7]'))
        db.conn.commit()
        assert db.settle("2",GAME_RED,day,30)["total"]==5000000
        store = StatisticsStore(path)
        store.process_batch()
        store.process_batch()
        one = store.user("1")
        assert one["scratch_count"]==1 and one["scratch_max"]==50
        assert one["rewards"]==[{"amount":20,"count":1},{"amount":30,"count":1}]
        two = store.user("2")
        assert two["lottery_count"]==1 and two["lottery_max"]==5000000
        assert db.leaderboard(metric="highest")[0]["user_id"]=="2"
        store.edit("1",{"total_winnings":6000000,"scratch_count":9,"rewards":[{"amount":20,"count":5}]})
        assert db.leaderboard()[0]["user_id"]=="1"
        db.buy_scratch("1","玩家一",day,card,"second")
        StatisticsStore(path).process_batch()
        assert store.user("1")["scratch_count"]==10
        assert store.user("1")["rewards"]==[{"amount":20,"count":6},{"amount":30,"count":1}]
        for data in ({"balance":-1},{"balance":True},{"rewards":[{"amount":20,"count":-2}]},{"bad":1}):
            try:
                store.edit("1",data)
            except ValueError:
                pass
            else:
                raise AssertionError(data)
        render_leaderboard([dict(r) for r in db.leaderboard()],Path(directory)/"dual.png",find_font(),day,
                           highest_rows=[dict(r) for r in db.leaderboard(metric="highest")])
        db.close()


def test_old_schema_migrates_without_losing_money():
    with tempfile.TemporaryDirectory() as directory:
        path=Path(directory)/"old.db"
        conn=sqlite3.connect(path)
        conn.executescript("""CREATE TABLE players(user_id TEXT PRIMARY KEY,nickname TEXT NOT NULL,
            balance INTEGER NOT NULL DEFAULT 0,total_winnings INTEGER NOT NULL DEFAULT 0,last_nickname_date TEXT);
            INSERT INTO players VALUES('1','旧玩家',123,999,NULL);
            CREATE TABLE tickets(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT NOT NULL,game TEXT NOT NULL,
            purchased_at TEXT NOT NULL,draw_date TEXT NOT NULL,spec_json TEXT NOT NULL,stake INTEGER NOT NULL,settled INTEGER NOT NULL DEFAULT 0);""")
        conn.close()
        db=LotteryDB(path)
        assert db.player("1")["balance"]==123
        assert "prize" in {r[1] for r in db.conn.execute("PRAGMA table_info(tickets)")}
        db.close()


def test_worker_runs_independently_and_stops():
    with tempfile.TemporaryDirectory() as directory:
        db=LotteryDB(Path(directory)/"worker.db")
        day=date.today().isoformat()
        db.checkin("1","玩家",day,50)
        store=StatisticsStore(db.path)
        store.start()
        try:
            card=ScratchCard(10,22,[ScratchRow([22],20)],20)
            db.buy_scratch("1","玩家",day,card,"worker-test")
            deadline=time.monotonic()+4
            while store.user("1")["scratch_count"]!=1 and time.monotonic()<deadline:
                time.sleep(.05)
            assert store.user("1")["scratch_count"]==1
            assert store.thread.name=="lottery-statistics"
        finally:
            store.close()
            assert not store.thread.is_alive()
            db.close()
