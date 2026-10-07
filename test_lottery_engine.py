import tempfile
import asyncio
from datetime import date, timedelta
from pathlib import Path

from PIL import Image

from lottery_engine import GAME_FRONT, GAME_RED, LotteryDB, generate_draw, parse_ticket, weighted_scratch
from scratch import ScratchCard, ScratchRow, find_font, parse_tier, render_card
from trend import render_trend


def test_ticket_parsing_and_limits():
    single = parse_ticket(GAME_RED, "01 02 03 04 05 06 + 07")
    assert single.combinations == 1
    assert single.stake == 2

    full_width_plus = parse_ticket(GAME_RED, "01 02 03 04 05 06 ＋ 07")
    assert full_width_plus.primary == single.primary
    assert full_width_plus.secondary == single.secondary

    multi = parse_ticket(GAME_RED, "01 02 03 04 05 06 07 + 01 02 复式")
    assert multi.combinations == 14
    assert multi.stake == 28

    dantuo = parse_ticket(GAME_FRONT, "前区胆码:01,02;前区拖码:03,04,05,06,07;后区:01,02")
    assert dantuo.combinations == 10

    extra = parse_ticket(GAME_FRONT, "01 02 03 04 05 + 06 07 追加 倍投2")
    assert extra.stake == 6
    assert extra.additional is True

    full_width_front = parse_ticket(GAME_FRONT, "01 02 03 04 05 ＋ 06 07")
    assert full_width_front.combinations == 1


def test_draw_is_persisted_and_redemption_is_idempotent():
    with tempfile.TemporaryDirectory() as temp_dir:
        db = LotteryDB(Path(temp_dir) / "lottery.db")
        today = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        db.checkin("100", "玩家", today, 50)
        spec = parse_ticket(GAME_RED, "01 02 03 04 05 06 + 07")
        db.buy("100", "玩家", today, spec, yesterday)
        first_draw = db.get_draw(GAME_RED, yesterday)
        assert first_draw == db.get_draw(GAME_RED, yesterday)
        result = db.settle("100", GAME_RED, today, 30)
        assert result["tickets"] == 1
        second = db.settle("100", GAME_RED, today, 30)
        assert second["tickets"] == 0
        db.close()


def test_scratch_probabilities_are_normalized():
    prize, probability = weighted_scratch({"0": 0, "10": 10})
    assert prize == 10
    assert probability == 1.0


def test_draw_shapes():
    red = generate_draw(GAME_RED)
    front = generate_draw(GAME_FRONT)
    assert len(red[0]) == 6 and len(red[1]) == 1
    assert len(front[0]) == 5 and len(front[1]) == 2


def test_scratch_rows_stack_and_render():
    card = ScratchCard(
        tier=10,
        winning=22,
        rows=[ScratchRow([1, 22, 3], 30), ScratchRow([4, 5, 22], 400000)],
        maximum=400000,
    )
    assert card.hits == 2
    assert card.total == 400030
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "ticket.png"
        render_card(card, "玩家", 400100, 1, path, find_font())
        with Image.open(path) as image:
            assert image.size == (840, 1160)
            image.verify()


def test_scratch_sale_is_idempotent():
    with tempfile.TemporaryDirectory() as temp_dir:
        db = LotteryDB(Path(temp_dir) / "lottery.db")
        card = ScratchCard(10, 22, [ScratchRow([22], 30)], 30)
        day = date.today().isoformat()
        db.checkin("200", "玩家", day, 50)
        first = db.buy_scratch("200", "玩家", day, card, "message-1")
        second = db.buy_scratch("200", "玩家", day, card, "message-1")
        assert first is not None
        assert second is None
        assert db.player("200")["balance"] == 70
        db.close()


def test_invalid_scratch_price_is_rejected_before_purchase():
    try:
        parse_tier("刮刮乐 33")
    except ValueError as exc:
        assert "5、10、20、50、100" in str(exc)
    else:
        raise AssertionError("非法刮刮乐金额未被拒绝")


def test_db_work_is_offloaded_without_blocking_event_loop():
    async def scenario():
        ticks = 0

        async def heartbeat():
            nonlocal ticks
            for _ in range(6):
                await asyncio.sleep(0.01)
                ticks += 1

        def blocking_db_work():
            with tempfile.TemporaryDirectory() as temp_dir:
                db = LotteryDB(Path(temp_dir) / "lottery.db")
                day = date.today().isoformat()
                for index in range(40):
                    db.checkin(str(index), "player", day, 50)
                db.close()

        await asyncio.gather(asyncio.to_thread(blocking_db_work), heartbeat())
        return ticks

    assert asyncio.run(scenario()) == 6


def test_new_dantuo_syntax_and_ticket_history():
    red = parse_ticket(GAME_RED, "胆码 1 2 拖码 3 4 5 6 7 蓝 8")
    assert red.primary_dan == (1, 2)
    assert red.primary == (1, 2, 3, 4, 5, 6, 7)
    assert red.secondary == (8,)

    front = parse_ticket(GAME_FRONT, "前区胆码 1 2 前区拖码 3 4 5 6 7 后区 1 2")
    assert front.primary_dan == (1, 2)
    assert front.primary == (1, 2, 3, 4, 5, 6, 7)
    assert front.secondary == (1, 2)

    with tempfile.TemporaryDirectory() as temp_dir:
        db = LotteryDB(Path(temp_dir) / "lottery.db")
        day = date.today().isoformat()
        db.checkin("300", "玩家", day, 500)
        ticket_id = db.buy("300", "玩家", day, red, day)
        rows = db.list_tickets("300", day)
        assert rows and rows[0]["id"] == ticket_id
        assert rows[0]["settled"] == 0
        db.close()


def test_trend_image():
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "trend.png"
        draws = [("2026-10-06", ((1, 2, 3, 4, 5, 6), (7,)))]
        render_trend(GAME_RED, draws, path, find_font())
        with Image.open(path) as image:
            assert image.width == 980
            image.verify()
