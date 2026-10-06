"""AstrBot 彩票游戏插件。"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

try:
    from .lottery_engine import GAME_FRONT, GAME_RED, LotteryDB, parse_ticket, weighted_scratch
except ImportError:
    from lottery_engine import GAME_FRONT, GAME_RED, LotteryDB, parse_ticket, weighted_scratch


class LotteryPlugin(Star):
    """提供刮刮乐、双色球、大乐透和龙门币管理。"""

    def __init__(self, context: Context, config: Any = None):
        super().__init__(context)
        self.config = config or {}
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_data_path

            data_root = get_astrbot_data_path()
        except ImportError:
            data_root = Path(__file__).resolve().parent / "data"
        self.data_dir = Path(data_root) / "plugin_data" / "astrbot_plugin_lottery_game"
        self.db = LotteryDB(self.data_dir / "lottery.db")

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        """解析彩票相关中文消息。"""
        message = (event.message_str or "").strip()
        if message.startswith("/"):
            message = message[1:].strip()
        if not message:
            return

        try:
            result = await self._dispatch(event, message)
        except ValueError as exc:
            yield event.plain_result(f"{exc}\n发送“双色球帮助”或“大乐透帮助”查看示例。")
        except Exception:
            logger.exception("彩票插件处理消息失败")
            yield event.plain_result("彩票系统暂时忙碌，请稍后再试。")
        else:
            if result is not None:
                yield result

    async def _dispatch(self, event: AstrMessageEvent, message: str):
        user_id = str(event.get_sender_id())
        nickname = event.get_sender_name() or user_id
        today = date.today().isoformat()
        yesterday = (date.fromisoformat(today) - timedelta(days=1)).isoformat()
        self.db.get_draw(GAME_RED, yesterday)
        self.db.get_draw(GAME_FRONT, yesterday)
        self.db.cleanup_draws(max(30, int(self.config.get("draw_history_days", 30))), today)

        if message in ("彩票签到", "签到"):
            checked, balance = self.db.checkin(user_id, nickname, today, self._daily_checkin())
            if checked:
                return event.plain_result(f"签到成功，获得 {self._daily_checkin()} 龙门币。当前余额：{balance}")
            return event.plain_result(f"今天已经签到过了。当前余额：{balance} 龙门币。")

        if message in ("彩票余额", "余额"):
            self.db.upsert_player(user_id, nickname, today)
            self.db.conn.commit()
            row = self.db.player(user_id)
            return event.plain_result(
                f"{nickname}，当前余额：{row['balance']} 龙门币\n累计中奖：{row['total_winnings']} 龙门币"
            )

        if message == "富豪榜":
            return event.plain_result(self._leaderboard_text())

        if message.startswith("刮刮乐"):
            return await self._scratch(event, user_id, nickname, today, message)

        for game in (GAME_RED, GAME_FRONT):
            if message == f"{game}帮助":
                return event.plain_result(self._help_text(game))
            if message == f"{game}兑奖":
                return event.plain_result(self._redeem(user_id, game, nickname, today))
            if message == game or message.startswith(game):
                raw = message[len(game):].strip(" +:：")
                if not raw:
                    return event.plain_result(self._usage_text(game))
                spec = parse_ticket(game, raw)
                ticket_id = self.db.buy(user_id, nickname, today, spec, today)
                return event.plain_result(
                    f"购买成功！{game} {spec.mode}，{spec.combinations} 注，{spec.multiplier} 倍"
                    f"{'，追加' if spec.additional else ''}，扣除 {spec.stake} 龙门币。\n彩票编号：{ticket_id}\n开奖后可发送“{game}兑奖”。"
                )
        return None

    async def _scratch(self, event: AstrMessageEvent, user_id: str, nickname: str, today: str, message: str):
        match = re.search(r"(?:刮刮乐)\s*(5|10|20|50|100)?", message)
        tier = int(match.group(1)) if match and match.group(1) else 10
        tiers = self._scratch_config()
        if str(tier) not in tiers:
            raise ValueError("刮刮乐档位支持 5、10、20、50、100。")
        self.db.upsert_player(user_id, nickname, today)
        if int(self.db.player(user_id)["balance"]) < tier:
            raise ValueError(f"余额不足，{tier} 龙门币刮刮乐需要先签到或购买彩票中奖。")
        self.db.add_balance(user_id, -tier)
        prize, _ = weighted_scratch(tiers[str(tier)])
        self.db.add_balance(user_id, prize)
        self.db.record_scratch(user_id, tier, prize)
        balance = self.db.player(user_id)["balance"]
        image = await self._scratch_image(nickname, tier, prize, balance)
        if image:
            yield_chain = event.chain_result([image])
            return yield_chain
        return event.plain_result(f"刮刮乐 {tier} 龙门币：{'恭喜中奖 ' + str(prize) + ' 龙门币！' if prize else '本次未中奖。'}当前余额：{balance}")

    async def _scratch_image(self, nickname: str, tier: int, prize: int, balance: int):
        safe_name = nickname.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        headline = f"恭喜中奖 {prize} 龙门币" if prize else "谢谢参与，下次好运"
        color = "#d62828" if prize else "#475569"
        template = f"""
        <html><body style='margin:0;background:#e8edf3;font-family:Arial,"Microsoft YaHei",sans-serif;'>
        <div style='width:720px;padding:30px;background:linear-gradient(135deg,#fff7d6,#ffffff);color:#172033;'>
          <div style='display:flex;justify-content:space-between;align-items:center;'>
            <div style='font-size:24px;font-weight:700;color:#a33b12;'>龙门彩票 · 幸运刮刮乐</div>
            <div style='font-size:15px;color:#64748b;'>LOTTERY GAME</div>
          </div>
          <div style='margin-top:24px;padding:30px;text-align:center;border:5px solid #e0a52c;border-radius:16px;background:#fffdf4;'>
            <div style='font-size:18px;color:#7c5b12;'>{safe_name} 的 {tier} 龙门币刮刮乐</div>
            <div style='margin:22px auto;padding:26px 16px;border-radius:12px;background:#f3df9e;color:{color};font-size:40px;font-weight:800;'>{headline}</div>
            <div style='font-size:17px;color:#64748b;'>全部刮开 · 余额 {balance} 龙门币</div>
          </div>
          <div style='margin-top:20px;color:#64748b;font-size:14px;'>祝你好运，理性游戏 · DITF16</div>
        </div></body></html>
        """
        try:
            url = await self.html_render(template, {}, options={"type": "png", "full_page": True})
            return __import__("astrbot.api.message_components", fromlist=["Image"]).Image.fromURL(url)
        except Exception:
            logger.exception("刮刮乐图片生成失败")
            return None

    def _daily_checkin(self) -> int:
        return max(0, int(self.config.get("daily_checkin", 50)))

    def _scratch_config(self) -> dict[str, dict[str, float]]:
        raw = self.config.get("scratch_tiers", {})
        if isinstance(raw, str):
            raw = json.loads(raw)
        if not isinstance(raw, dict) or not raw:
            raise ValueError("刮刮乐概率配置为空，请在插件配置中填写 JSON。")
        return raw

    def _redeem(self, user_id: str, game: str, nickname: str, today: str) -> str:
        self.db.upsert_player(user_id, nickname, today)
        yesterday = (date.fromisoformat(today) - timedelta(days=1)).isoformat()
        self.db.get_draw(game, yesterday)
        result = self.db.settle(user_id, game, today, max(30, int(self.config.get("draw_history_days", 30))))
        if not result["tickets"]:
            return f"暂时没有可兑奖的{game}彩票。开奖后次日即可兑奖，开奖号码保留 30 天。"
        if result["total"]:
            detail = "\n".join(result["detail"])
            return f"{game}兑奖完成！本次获得 {result['total']} 龙门币。\n{detail}"
        return f"{game}兑奖完成，本次没有中奖。共核对 {result['tickets']} 张彩票。"

    def _leaderboard_text(self) -> str:
        rows = self.db.leaderboard()
        if not rows:
            return "富豪榜暂时没有玩家，发送“彩票签到”开始游戏。"
        lines = ["🏆 彩票富豪榜（余额 + 累计中奖）"]
        for index, row in enumerate(rows, 1):
            total = int(row["balance"]) + int(row["total_winnings"])
            lines.append(f"{index}. {row['nickname']}：{total} 龙门币（余额 {row['balance']}，累计中奖 {row['total_winnings']}）")
        return "\n".join(lines)

    @staticmethod
    def _usage_text(game: str) -> str:
        if game == GAME_RED:
            return "双色球用法：双色球 01 02 03 04 05 06 + 07\n支持：复式、胆拖、倍投 2-99 倍。每注 2 龙门币，不支持追加。"
        return "大乐透用法：大乐透 01 02 03 04 05 + 06 07\n支持：复式、胆拖、倍投 2-99 倍、追加。"

    @staticmethod
    def _help_text(game: str) -> str:
        if game == GAME_RED:
            return "双色球：选 1-33 中 6 个红球和 1-16 中 1 个蓝球，每注 2 龙门币。\n示例：双色球 01 02 03 04 05 06 + 07\n支持复式、胆拖和倍投，不支持追加。开奖后发送“双色球兑奖”。"
        return "大乐透：选 1-35 中 5 个前区和 1-12 中 2 个后区，每注 2 龙门币。\n示例：大乐透 01 02 03 04 05 + 06 07 追加 倍投2\n追加每注 3 龙门币，只对一、二等奖按 80% 追加。开奖后发送“大乐透兑奖”。"

    async def terminate(self):
        self.db.close()


@register("astrbot_plugin_lottery_game", "DITF16", "双色球、大乐透与刮刮乐小游戏", "1.0.0")
class Main(LotteryPlugin):
    pass
