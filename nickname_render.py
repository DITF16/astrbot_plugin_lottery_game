"""Font fallback and grapheme-aware nickname rasterization."""

from functools import lru_cache
from pathlib import Path
import subprocess
import unicodedata

import regex
from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont, features


@lru_cache(maxsize=16)
def font_info(path):
    with TTFont(path, fontNumber=0, lazy=True) as font:
        cmap = frozenset(font.getBestCmap() or {})
        sizes = set()
        for name in ("CBLC", "EBLC"):
            if name in font:
                sizes.update(strike.bitmapSizeTable.ppemY for strike in font[name].strikes)
        return cmap, tuple(sorted(sizes))


@lru_cache(maxsize=16)
def system_font(family):
    try:
        result = subprocess.run(
            ["fc-match", "-f", "%{file}", family], capture_output=True,
            text=True, timeout=3, check=True,
        )
        path = result.stdout.strip()
        if Path(path).is_file():
            return path
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


@lru_cache(maxsize=16)
def fallback_paths(primary, emoji):
    if emoji and not Path(emoji).is_file():
        raise ValueError("符号/表情字体文件不存在，请检查 emoji_font_path。")
    if not emoji:
        emoji = next((path for path in (
            "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
            "C:/Windows/Fonts/seguiemj.ttf",
            system_font("Noto Color Emoji"),
        ) if path and Path(path).is_file()), "")
    candidates = [primary, emoji, system_font("Noto Sans SC"),
                  system_font("DejaVu Sans"), system_font("Unifont")]
    paths = tuple(dict.fromkeys(path for path in candidates if path and Path(path).is_file()))
    # Parse once here so invalid configured fonts produce an actionable error.
    for path in paths:
        try:
            font_info(path)
        except Exception as exc:
            raise ValueError(f"无法读取昵称字体：{path}") from exc
    return paths, emoji


def _visible_codes(cluster):
    return {ord(char) for char in cluster
            if ord(char) not in (0x200d, 0xfe0e, 0xfe0f)
            and not 0xe0020 <= ord(char) <= 0xe007f}


@lru_cache(maxsize=128)
def _load_font(path, size):
    _, strikes = font_info(path)
    native = min(strikes, key=lambda value: abs(value - size)) if strikes else size
    return ImageFont.truetype(path, native), native


@lru_cache(maxsize=512)
def _glyph(cluster, path, size, color):
    font, native = _load_font(path, size)
    scale = size / native
    if not features.check("raqm"):
        # BASIC layout cannot shape joiners or flag tags; do not print their tofu glyphs.
        cluster = "".join(char for char in cluster if ord(char) != 0x200d
                          and not 0xe0020 <= ord(char) <= 0xe007f)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    bbox = probe.textbbox((0, 0), cluster, font=font, anchor="ls", embedded_color=True)
    left, top, right, bottom = bbox
    tile = Image.new("RGBA", (max(1, right - left), max(1, bottom - top)))
    ImageDraw.Draw(tile).text((-left, -top), cluster, font=font,
                              fill=color, anchor="ls", embedded_color=True)
    tile = tile.resize((max(1, round(tile.width * scale)),
                        max(1, round(tile.height * scale))), Image.Resampling.LANCZOS)
    advance = probe.textlength(cluster, font=font) * scale
    return tile, left * scale, top * scale, advance


def nickname_image(text, primary, emoji, size, color, max_width):
    paths, emoji_path = fallback_paths(primary, emoji)
    clusters = regex.findall(r"\X", unicodedata.normalize("NFC", " ".join(str(text).split())))
    glyphs = []
    for cluster in clusters:
        codes = _visible_codes(cluster)
        prefer_emoji = "️" in cluster or bool(regex.search(r"\p{Emoji_Presentation}", cluster))
        ordered = ([emoji_path] + list(paths)) if prefer_emoji and emoji_path else paths
        chosen = next((path for path in ordered if codes <= font_info(path)[0]), None)
        # Unsupported glyphs are explicitly visible, rather than silently disappearing.
        if chosen is None:
            cluster, chosen = "?", primary
        glyphs.append(_glyph(cluster, chosen, size, color))
    suffix = _glyph("...", primary, size, color)
    width = sum(glyph[3] for glyph in glyphs)
    if width > max_width:
        while glyphs and width + suffix[3] > max_width:
            width -= glyphs.pop()[3]
        glyphs.append(suffix)
    # Reserve ink extents, including combining marks and emoji extending above baseline.
    baseline = max([size] + [-glyph[2] for glyph in glyphs])
    descent = max([size * .25] + [glyph[2] + glyph[0].height for glyph in glyphs])
    image = Image.new("RGBA", (max_width, max(1, round(baseline + descent))))
    x = 0.0
    for tile, left, top, advance in glyphs:
        image.alpha_composite(tile, (round(x + left), round(baseline + top)))
        x += advance
    return image


def draw_nickname(image, xy, text, primary, emoji="", size=28, color="#203b30", max_width=380):
    tile = nickname_image(text, primary, emoji, size, color, max_width)
    image.paste(tile, xy, tile)
