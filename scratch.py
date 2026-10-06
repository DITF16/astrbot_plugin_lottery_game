"""Scratch ticket outcomes and local raster rendering; no remote renderer needed."""

from __future__ import annotations

import math
import random
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

RNG = random.SystemRandom()
TIERS = (5, 10, 20, 50, 100)


def parse_tier(message):
    """Return the requested scratch price, or raise before any balance mutation."""
    match = re.fullmatch(r"刮刮乐(?:\s*(\d+))?", message.strip())
    if not match:
        raise ValueError("刮刮乐价格仅支持 5、10、20、50、100，例如：刮刮乐 10。")
    tier = int(match.group(1) or 10)
    if tier not in TIERS:
        raise ValueError("刮刮乐价格仅支持 5、10、20、50、100，请重新选择。")
    return tier
PRIZES = {
    5: (5, 10, 15, 20, 30, 50, 60, 100, 200, 500, 1000, 5000, 10000, 50000, 200000),
    10: (10, 20, 30, 40, 50, 60, 100, 200, 500, 1000, 5000, 10000, 50000, 100000, 400000),
    20: (20, 30, 40, 50, 60, 100, 200, 500, 1000, 2000, 5000, 10000, 100000, 200000, 1000000),
    50: (50, 60, 100, 150, 200, 300, 500, 1000, 2000, 5000, 10000, 50000, 200000, 500000, 2000000),
    100: (100, 150, 200, 300, 500, 600, 1000, 2000, 5000, 10000, 50000, 100000, 500000, 1000000, 5000000),
}
WEIGHTS = (600000, 200000, 100000, 50000, 25000, 12000, 6000, 3000, 1500, 1000, 700, 500, 200, 99, 1)
WIN_RATES = (.35, .42, .50, .58, .66)
DEFAULT_TIERS = {
    str(tier): {"0": 1 - win, **{
        str(prize): win * weight / sum(WEIGHTS)
        for prize, weight in zip(PRIZES[tier], WEIGHTS)
    }} for tier, win in zip(TIERS, WIN_RATES)
}
LEGACY_TIERS = {
    "5": {"0": .65, "5": .20, "10": .10, "20": .04, "50": .009, "100": .001},
    "10": {"0": .58, "10": .22, "20": .12, "50": .06, "100": .018, "200": .002},
    "20": {"0": .50, "20": .24, "50": .14, "100": .08, "200": .038, "500": .002},
    "50": {"0": .42, "50": .25, "100": .15, "200": .10, "500": .075, "2000": .005},
    "100": {"0": .34, "100": .26, "200": .16, "500": .12, "2000": .115, "10000": .005},
}


def validate_table(table):
    if not isinstance(table, dict) or not table:
        raise ValueError("刮刮乐奖金表须为非空 JSON 对象。")
    result = {}
    for amount, weight in table.items():
        if not str(amount).isascii() or not str(amount).isdigit():
            raise ValueError("刮刮乐奖金必须为非负整数。")
        prize = int(amount)
        if prize > 100_000_000 or isinstance(weight, bool):
            raise ValueError("单票奖金上限为 1 亿，概率须为有限非负数。")
        value = float(weight)
        if not math.isfinite(value) or value < 0:
            raise ValueError("刮刮乐概率须为有限非负数。")
        result[prize] = value
    if not any(prize > 0 and value > 0 for prize, value in result.items()):
        raise ValueError("至少配置一个概率大于零的正奖金。")
    if not math.isfinite(sum(result.values())):
        raise ValueError("刮刮乐概率总和过大。")
    return result


@dataclass
class ScratchRow:
    numbers: list[int]
    prize: int


@dataclass
class ScratchCard:
    tier: int
    winning: int
    rows: list[ScratchRow]
    maximum: int

    @property
    def total(self):
        return sum(row.prize for row in self.rows if self.winning in row.numbers)

    @property
    def hits(self):
        return sum(self.winning in row.numbers for row in self.rows)

    def to_dict(self):
        return asdict(self)


def make_card(tier, table, rng=RNG):
    table = validate_table(table)
    total = rng.choices(list(table), weights=list(table.values()), k=1)[0]
    positive = sorted(p for p, w in table.items() if p and w > 0)
    # Draw the total once, then distribute it. Multiple hits do not inflate odds or top prize.
    parts = [total] if total else []
    while parts and len(parts) < 5:
        choices = [(i, p) for i, amount in enumerate(parts) for p in positive
                   if p < amount and amount - p in positive]
        if not choices or rng.random() > .72:
            break
        index, value = rng.choice(choices)
        amount = parts.pop(index)
        parts.extend((value, amount - value))
    winning = rng.randint(1, 60)
    hit_rows = dict(zip(rng.sample(range(10), len(parts)), parts))
    pool = [n for n in range(1, 61) if n != winning]
    # Show diverse face values, including the jackpot, without treating decoys as wins.
    decoys = rng.sample(positive, min(10, len(positive)))
    decoys += [rng.choice(positive) for _ in range(10 - len(decoys))]
    decoys[rng.randrange(10)] = max(positive)
    rows = []
    for index, count in enumerate((9, 8, 7, 7, 6, 5, 4, 3, 2, 1)):
        numbers = rng.sample(pool, count)
        if index in hit_rows:
            numbers[rng.randrange(count)] = winning
        rows.append(ScratchRow(numbers, hit_rows.get(index, decoys[index])))
    card = ScratchCard(tier, winning, rows, max(positive))
    assert card.total == total
    return card


def find_font(custom=""):
    candidates = [custom] if custom else [
        "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    ]
    for candidate in candidates:
        if Path(candidate).is_file():
            ImageFont.truetype(candidate, 20)
            return candidate
    raise ValueError("未找到中文字体，请安装 fonts-noto-cjk 或在 scratch_font_path 设置中文字体路径。")


def render_card(card, nickname, balance, ticket_id, path, font_path):
    """Fixed 840x1160 print-style ticket with a fully exposed textured play area."""
    image = Image.new("RGB", (840, 1160), "#b81e27")
    draw = ImageDraw.Draw(image)
    texture = random.Random(ticket_id)

    def text(x, y, value, size=24, fill="#26282d", max_width=None):
        value = str(value)
        font = ImageFont.truetype(font_path, size)
        if max_width:
            while draw.textlength(value, font=font) > max_width and size > 13:
                size -= 1
                font = ImageFont.truetype(font_path, size)
            while draw.textlength(value, font=font) > max_width:
                value = value[:-2] + "…"
        draw.text((x, y), value, font=font, fill=fill)

    gold = "#ffdf7b"
    for inset in (9, 15):
        draw.rectangle((inset, inset, 839-inset, 1159-inset), outline=gold, width=2)
    for i in range(18):
        x = i * 60 - 100
        draw.line((x, 0, x + 240, 210), fill="#c93532", width=2)
    text(34, 27, "龙门彩票  /  即开型游戏票", 25, "#ffffff")
    text(621, 26, f"面值 {card.tier}", 27, gold)
    text(30, 65, "好运十倍", 67, gold)
    text(359, 81, "单票最高奖金", 22, "#ffffff")
    text(356, 111, f"{card.maximum:,}", 44, gold, 441)
    text(35, 176, "全区已刮开  ·  相同号码中该行奖金，多行中奖累加", 20, "#ffffff", 515)
    text(578, 169, "中奖号码", 18, "#ffffff")
    text(714, 161, f"{card.winning:02d}", 35, gold)
    text(35, 204, "10 次中奖机会  ·  命中多行奖金叠加", 17, gold)

    draw.rectangle((29, 226, 811, 943), fill="#d4d7d4")
    # Fine exposed coating marks, kept behind the dark print.
    for _ in range(1500):
        x, y = texture.randint(31, 808), texture.randint(228, 940)
        draw.line((x, y, min(809, x + texture.randint(2, 16)), min(941, y + 6)),
                  fill=texture.choice(("#c3c9c6", "#e4e6e2", "#bfc7c8")))
    text(44, 238, "我的号码", 24)
    text(650, 238, "本行奖金", 24)
    draw.line((43, 278, 795, 278), fill="#717d80", width=2)
    for index, row in enumerate(card.rows):
        y = 287 + index * 64
        start = 48 + (9 - len(row.numbers)) * 27
        hit = card.winning in row.numbers
        for j, number in enumerate(row.numbers):
            x = start + j * 61
            if number == card.winning:
                draw.rectangle((x-5, y-1, x+49, y+48), outline="#b8202b", width=3)
            text(x, y, f"{number:02d}", 32, "#a51e27" if number == card.winning else "#252d31")
        if hit:
            draw.rectangle((637, y-1, 794, y+48), outline="#b8202b", width=3)
        text(650, y+4, row.prize, 27, "#a51e27" if hit else "#252d31", 138)
        draw.line((44, y+56, 794, y+56), fill="#99a2a2", width=1)
    headline = f"命中 {card.hits} 行  ·  合计 {card.total:,} 龙门币" if card.total else "本票未中奖  ·  谢谢参与"
    text(35, 957, headline, 32, gold, 770)
    text(35, 1009, f"{nickname}  |  余额 {balance:,}", 23, "#ffffff", 765)
    text(35, 1051, f"票号 {ticket_id}  /  虚拟龙门币游戏  /  DITF16", 20, "#ffffff", 770)
    for i in range(155):
        x = 36 + i * 3
        draw.line((x, 1100, x, 1130), fill=gold, width=texture.choice((1, 2)))
    text(536, 1100, "好运相伴 · 龙门", 23, gold)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG")
    return str(path)
