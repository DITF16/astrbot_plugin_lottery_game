"""近期开奖走势图的本地图片渲染。"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

try:
    from .lottery_engine import GAME_FRONT, GAME_RED
except ImportError:
    from lottery_engine import GAME_FRONT, GAME_RED


def render_trend(game, draws, path, font_path):
    width = 980
    row_height = 42
    image = Image.new("RGB", (width, 150 + row_height * len(draws)), "#f5f7fa")
    draw = ImageDraw.Draw(image)
    title_font = ImageFont.truetype(font_path, 30)
    normal_font = ImageFont.truetype(font_path, 20)
    small_font = ImageFont.truetype(font_path, 17)
    title = f"{game}近30日开奖号码走势图"
    draw.text((30, 20), title, font=title_font, fill="#172033")
    draw.text((30, 66), "开奖日期", font=small_font, fill="#64748b")
    draw.text((190, 66), "开奖号码", font=small_font, fill="#64748b")
    draw.text((790, 66), "说明", font=small_font, fill="#64748b")
    draw.line((25, 100, width - 25, 100), fill="#cbd5e1", width=2)
    for index, (draw_date, numbers) in enumerate(reversed(draws)):
        y = 112 + index * row_height
        if index % 2 == 0:
            draw.rectangle((25, y - 4, width - 25, y + row_height - 4), fill="#ffffff")
        draw.text((35, y + 5), draw_date, font=small_font, fill="#475569")
        primary, secondary = numbers
        x = 190
        for number in primary:
            draw.ellipse((x, y, x + 30, y + 30), fill="#dc2626" if game == GAME_RED else "#2563eb")
            label = f"{number:02d}"
            bbox = draw.textbbox((0, 0), label, font=small_font)
            draw.text((x + (30 - bbox[2]) / 2, y + (30 - bbox[3] + bbox[1]) / 2), label, font=small_font, fill="#ffffff")
            x += 38
        draw.text((x + 12, y + 3), "+", font=normal_font, fill="#64748b")
        x += 42
        for number in secondary:
            draw.ellipse((x, y, x + 30, y + 30), fill="#2563eb" if game == GAME_RED else "#f59e0b")
            label = f"{number:02d}"
            bbox = draw.textbbox((0, 0), label, font=small_font)
            draw.text((x + (30 - bbox[2]) / 2, y + (30 - bbox[3] + bbox[1]) / 2), label, font=small_font, fill="#ffffff")
            x += 38
        draw.text((790, y + 5), "红+蓝" if game == GAME_RED else "前+后", font=small_font, fill="#64748b")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG")
    return str(path)
