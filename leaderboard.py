"""Local raster rendering for the lottery wealth leaderboard."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def fit_text(draw, text, font, width):
    text = " ".join(str(text).split())
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "...", font=font) > width:
        text = text[:-1]
    return text + "..."


def render_leaderboard(rows, path, font_path, today):
    rows = rows[:10]
    width, row_height = 1080, 108
    height = 302 + max(1, len(rows)) * row_height
    image = Image.new("RGB", (width, height), "#f3f6f5")
    draw = ImageDraw.Draw(image)
    fonts = {size: ImageFont.truetype(font_path, size) for size in (18, 20, 24, 28, 42)}
    draw.rectangle((0, 0, width, 192), fill="#123e37")
    draw.rectangle((40, 40, 46, 150), fill="#edc871")
    draw.text((66, 40), "彩票富豪榜", font=fonts[42], fill="#ffffff")
    draw.text((68, 110), "总资产 = 当前余额 + 累计中奖", font=fonts[24], fill="#c9dfd5")
    draw.text((850, 54), "TOP 10", font=fonts[28], fill="#edc871")
    draw.text((850, 106), today, font=fonts[20], fill="#c9dfd5")
    draw.text((42, 215), "排名 / 玩家", font=fonts[20], fill="#60726a")
    draw.text((1008, 215), "总资产 · 龙门币", font=fonts[20], fill="#60726a", anchor="ra")

    if not rows:
        draw.text((540, 305), "暂无玩家，发送“彩票签到”开始游戏", font=fonts[24], fill="#60726a", anchor="mm")
    for index, row in enumerate(rows):
        y = 256 + index * row_height
        fill = "#fff7df" if index == 0 else "#ffffff"
        draw.rounded_rectangle((32, y, 1048, y + 96), radius=8, fill=fill)
        badge = ("#b78520", "#77878c", "#a87150")[index] if index < 3 else "#dfe8e3"
        draw.ellipse((52, y + 23, 102, y + 73), fill=badge)
        draw.text((77, y + 48), str(index + 1), font=fonts[24], fill="#ffffff" if index < 3 else "#496356", anchor="mm")
        name = fit_text(draw, row["nickname"] or row["user_id"], fonts[28], 380)
        draw.text((126, y + 15), name, font=fonts[28], fill="#203b30")
        draw.text((128, y + 58), f"QQ {row['user_id']}", font=fonts[18], fill="#748279")
        balance, winnings = int(row["balance"]), int(row["total_winnings"])
        total = fit_text(draw, f"{balance + winnings:,}", fonts[28], 440)
        draw.text((1008, y + 15), total, font=fonts[28], fill="#936711", anchor="ra")
        detail = fit_text(draw, f"余额 {balance:,}  /  累计中奖 {winnings:,}", fonts[18], 440)
        draw.text((1008, y + 58), detail, font=fonts[18], fill="#60726a", anchor="ra")
    draw.text((40, height - 28), "龙门币彩票 · 资产排行", font=fonts[18], fill="#748279", anchor="lm")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG")
    return str(path)
