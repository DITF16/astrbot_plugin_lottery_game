import tempfile
from datetime import date, timedelta
from pathlib import Path

from lottery_engine import GAME_FRONT, GAME_RED, LotteryDB, generate_draw, parse_ticket, weighted_scratch


def test_ticket_parsing_and_limits():
    single = parse_ticket(GAME_RED, "01 02 03 04 05 06 + 07")
    assert single.combinations == 1
    assert single.stake == 2

    multi = parse_ticket(GAME_RED, "01 02 03 04 05 06 07 + 01 02 复式")
    assert multi.combinations == 14
    assert multi.stake == 28

    dantuo = parse_ticket(GAME_FRONT, "前区胆码:01,02;前区拖码:03,04,05,06,07;后区:01,02")
    assert dantuo.combinations == 10

    extra = parse_ticket(GAME_FRONT, "01 02 03 04 05 + 06 07 追加 倍投2")
    assert extra.stake == 12
    assert extra.additional is True


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
