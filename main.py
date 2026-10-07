"""AstrBot 彩票游戏插件。"""

from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict
from datetime import date, timedelta
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .lottery_engine import GAME_FRONT, GAME_RED, LotteryDB, parse_ticket
from .scratch import DEFAULT_TIERS, LEGACY_TIERS, TIERS, find_font, make_card, parse_tier, render_card, validate_table
from .trend import render_trend


@register("astrbot_plugin_lottery_game", "DITF16", "双色球、大乐透与刮刮乐小游戏", "1.2.0")
class LotteryPlugin(Star):
    """提供刮刮乐、双色球、大乐透和龙门币管理。"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config or {}
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_data_path

            data_root = get_astrbot_data_path()
        except ImportError:
            data_root = Path("data")
        self.data_dir = Path(data_root) / "plugin_data" / "astrbot_plugin_lottery_game"
        self.db = LotteryDB(self.data_dir / "lottery.db")
        self._seen = OrderedDict()
        self._scratch_lock = asyncio.Lock()
        self._upgrade_scratch_config()

    def _upgrade_scratch_config(self):
        raw = self.config.get("scratch_tiers")
        try:
            old = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            return
        if old and old != LEGACY_TIERS:
            return
        self.config["scratch_tiers"] = json.dumps(DEFAULT_TIERS, ensure_ascii=False, indent=2)
        if hasattr(self.config, "save_config"):
            self.config.save_config()
        logger.info("刮刮乐已升级为多行奖金表；自定义旧概率表不会被覆盖。")

    @staticmethod
    def _message_key(event):
        message_id = getattr(event.message_obj, "message_id", None)
        if not message_id:
            return ""
        return json.dumps([str(event.unified_msg_origin), str(event.get_sender_id()), str(message_id)])

    @staticmethod
    def _recognized(message):
        return message in (
            "彩票帮助", "彩票签到", "签到", "彩票余额", "余额", "富豪榜", "我的彩票",
            f"{GAME_RED}走势", f"{GAME_FRONT}走势",
        ) or message.startswith(("刮刮乐", GAME_RED, GAME_FRONT))

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        """解析彩票相关中文消息。"""
        message = (event.message_str or "").strip()
        if message.startswith("/"):
            message = message[1:].strip()
        if not self._recognized(message):
            return
        event.stop_event()
        key = self._message_key(event)
        now = time.monotonic()
        while self._seen and (now - next(iter(self._seen.values())) > 600 or len(self._seen) >= 2048):
            self._seen.popitem(last=False)
        if key and key in self._seen:
            return
        if key:
            self._seen[key] = now
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
        if message == "彩票帮助":
            return event.plain_result(
                "彩票游戏菜单\n"
                "彩票签到：每天领取龙门币\n"
                "彩票余额：余额与累计中奖\n"
                "刮刮乐 [5/10/20/50/100]：默认10币，全部刮开，中奖自动到账\n"
                "双色球 / 大乐透：查看投注参数与例子\n"
                "双色球帮助 / 大乐透帮助：玩法简介\n"
                "双色球兑奖 / 大乐透兑奖：领取已开奖彩票奖金\n"
                "我的彩票：查看近30天购买记录和兑奖状态\n"
                "双色球走势 / 大乐透走势：查看近30日开奖号码走势图\n"
                "富豪榜：查看余额＋累计中奖前十名"
            )
        user_id = str(event.get_sender_id())
        nickname = event.get_sender_name() or user_id
        today = date.today().isoformat()
        if message.startswith("刮刮乐"):
            return await self._scratch(event, user_id, nickname, today, message)
        if message in (f"{GAME_RED}走势", f"{GAME_FRONT}走势"):
            return await self._trend_image(event, message.removesuffix("走势"), today)
        result = await asyncio.to_thread(
            self._dispatch_sync,
            user_id,
            nickname,
            today,
            message,
        )
        return event.plain_result(result) if result is not None else None

    def _dispatch_sync(self, user_id: str, nickname: str, today: str, message: str):
        yesterday = (date.fromisoformat(today) - timedelta(days=1)).isoformat()
        self.db.get_draw(GAME_RED, yesterday)
        self.db.get_draw(GAME_FRONT, yesterday)
        self.db.cleanup_draws(max(30, int(self.config.get("draw_history_days", 30))), today)

        if message in ("彩票签到", "签到"):
            checked, balance = self.db.checkin(user_id, nickname, today, self._daily_checkin())
            if checked:
                return f"签到成功，获得 {self._daily_checkin()} 龙门币。当前余额：{balance}"
            return f"今天已经签到过了。当前余额：{balance} 龙门币。"

        if message in ("彩票余额", "余额"):
            row = self.db.ensure_player(user_id, nickname, today)
            return (
                f"{nickname}，当前余额：{row['balance']} 龙门币\n累计中奖：{row['total_winnings']} 龙门币"
            )

        if message == "富豪榜":
            return self._leaderboard_text()

        if message == "我的彩票":
            return self._my_tickets_text(user_id, today)

        for game in (GAME_RED, GAME_FRONT):
            if message == f"{game}帮助":
                return self._help_text(game)
            if message == f"{game}兑奖":
                return self._redeem(user_id, game, nickname, today)
            if message == game or message.startswith(game):
                raw = message[len(game):].strip(" +:：")
                if not raw:
                    return self._usage_text(game)
                spec = parse_ticket(game, raw)
                ticket_id = self.db.buy(user_id, nickname, today, spec, today)
                return (
                    f"购买成功！{game} {spec.mode}，{spec.combinations} 注，{spec.multiplier} 倍"
                    f"{'，追加' if spec.additional else ''}，扣除 {spec.stake} 龙门币。\n彩票编号：{ticket_id}\n开奖后可发送“{game}兑奖”。"
                )
        return None

    async def _scratch(self, event: AstrMessageEvent, user_id: str, nickname: str, today: str, message: str):
        tier = parse_tier(message)
        tiers = await asyncio.to_thread(self._scratch_config)
        async with self._scratch_lock:
            key = self._message_key(event)
            if await asyncio.to_thread(self.db.scratch_seen, key):
                return None
            card = await asyncio.to_thread(make_card, tier, tiers[str(tier)])
            sale = await asyncio.to_thread(self.db.buy_scratch, user_id, nickname, today, card, key)
            if sale is None:
                return None
            ticket_id, balance = sale
            try:
                font = await asyncio.to_thread(find_font, self.config.get("scratch_font_path", ""))
                path = self.data_dir / "scratch_images" / f"{ticket_id}.png"
                await asyncio.to_thread(render_card, card, nickname, balance, ticket_id, path, font)
                await asyncio.to_thread(self._prune_images, path.parent)
                return event.image_result(str(path))
            except Exception:
                logger.exception("刮刮乐图片生成失败，保留已完成的票据与结算")
                return event.plain_result(
                    f"票号 {ticket_id} 已结算：命中 {card.hits} 行，奖金 {card.total} 龙门币，余额 {balance}。"
                    "图片生成失败，本消息不会再次扣款。"
                )

    @staticmethod
    def _prune_images(directory):
        cutoff = time.time() - 86400 * 2
        for path in directory.glob("*.png"):
            if path.stem.isdigit() and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)

    def _daily_checkin(self) -> int:
        return max(0, int(self.config.get("daily_checkin", 50)))

    def _scratch_config(self) -> dict[str, dict[str, float]]:
        raw = self.config.get("scratch_tiers", {})
        if isinstance(raw, str):
            raw = json.loads(raw)
        if not isinstance(raw, dict) or not raw:
            raise ValueError("刮刮乐概率配置为空，请在插件配置中填写 JSON。")
        if set(raw) != {str(tier) for tier in TIERS}:
            raise ValueError("刮刮乐配置须包含 5、10、20、50、100 五档。")
        for table in raw.values():
            validate_table(table)
        return raw

    def _redeem(self, user_id: str, game: str, nickname: str, today: str) -> str:
        self.db.upsert_player(user_id, nickname, today)
        yesterday = (date.fromisoformat(today) - timedelta(days=1)).isoformat()
        self.db.get_draw(game, yesterday)
        result = self.db.settle(user_id, game, today, max(30, int(self.config.get("draw_history_days", 30))))
        if not result["tickets"]:
            return f"暂时没有可兑奖的{game}彩票。开奖后次日即可兑奖，开奖号码保留 30 天。"
        lines = [f"{game}兑奖完成，共核对 {result['tickets']} 张彩票。"]
        for record in result["records"]:
            lines.append(self._settlement_line(game, record))
        lines.append(f"本次合计获得：{result['total']} 龙门币。")
        return "\n".join(lines)

    def _my_tickets_text(self, user_id: str, today: str) -> str:
        tickets = self.db.list_tickets(user_id, today, 30)
        if not tickets:
            return "近 30 天没有购买记录。"
        lines = ["近 30 天购票记录："]
        for ticket in tickets:
            spec = json.loads(ticket["spec_json"])
            numbers = self._ticket_numbers(spec)
            state = "已兑奖" if ticket["settled"] else "未兑奖"
            purchase_date = ticket["purchased_at"][:10]
            lines.append(
                f"#{ticket['id']}｜{ticket['game']}｜{numbers}｜购买日期 {purchase_date}｜{state}"
            )
        return "\n".join(lines)

    @staticmethod
    def _ticket_numbers(spec: dict) -> str:
        primary = " ".join(str(number) for number in spec["primary"])
        secondary = " ".join(str(number) for number in spec["secondary"])
        return f"{primary}+{secondary}"

    @classmethod
    def _settlement_line(cls, game: str, record: dict) -> str:
        winning_date = date.fromisoformat(record["draw_date"])
        spec = record["spec"]
        numbers = cls._ticket_numbers({
            "primary": spec.primary,
            "secondary": spec.secondary,
        })
        date_text = f"{winning_date.year}年{winning_date.month:02d}月{winning_date.day:02d}日"
        if record["prize"]:
            return f"{date_text} {numbers} {game}中奖{record['prize']}龙门币"
        return f"{date_text} {numbers} {game}未中奖"

    async def _trend_image(self, event: AstrMessageEvent, game: str, today: str):
        draws = await asyncio.to_thread(self.db.history_draws, game, today, 30)
        font = await asyncio.to_thread(find_font, self.config.get("scratch_font_path", ""))
        path = self.data_dir / "trend_images" / f"{game}_{today}.png"
        await asyncio.to_thread(render_trend, game, draws, path, font)
        return event.image_result(str(path))

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
            return (
                "双色球选号：+ 左边选 1-33 的 6 个红球，右边选 1-16 的 1 个蓝球；每注 2 龙门币。\n"
                "单式：双色球 01 02 03 04 05 06 + 07\n"
                "复式：双色球 01 02 03 04 05 06 07 + 01 02（红球 7 个、蓝球 2 个自动组合）\n"
                "胆拖：双色球 胆码:01,02;拖码:03,04,05,06,07;蓝:08\n"
                "倍投：在末尾加 倍投2，范围 2-99 倍；双色球不支持追加。"
            )
        return (
            "大乐透选号：+ 左边选 1-35 的 5 个前区号码，右边选 1-12 的 2 个后区号码；基本每注 2 龙门币。\n"
            "单式：大乐透 01 02 03 04 05 + 06 07\n"
            "复式：大乐透 前区:01,02,03,04,05,06;后区:01,02（自动组合多注）\n"
            "胆拖：大乐透 前区胆码:01,02;前区拖码:03,04,05,06,07;后区:01,02\n"
            "倍投：末尾加 倍投2，范围 2-99 倍；追加：末尾加 追加，每注 3 龙门币，只增加一、二等奖追加奖金。"
        )

    @staticmethod
    def _help_text(game: str) -> str:
        if game == GAME_RED:
            return (
                "------双色球玩法------\n"
                "【简单形式】\n"
                "发送命令（数字间要空格）：\"双色球 1 2 3 4 5 6+7\"\n\n"
                "【规则如下】\n"
                "选号：加号左边为红球，从数字1-33中选 6 个；+ 右边为蓝球，从数字1-16选 1 个；每注 2 龙门币。\n\n"
                "单式（普通玩法）：发送命令：\"双色球 1 2 3 4 5 6+7\"\n\n"
                "复式：红球选 7 个以上或蓝球选 2 个以上，系统自动组合；\n"
                "发送命令：\"双色球 1 2 3 4 5 6 7+1 2\"\n\n"
                "胆拖：设置必选胆码和搭配拖码；\n"
                "发送命令：\"双色球 胆码 1 2 拖码 3 4 5 6 7 蓝 8\"\n\n"
                "倍投：末尾加 倍投2，支持 2-99 倍，所有奖级按倍数结算。\n"
                "例：发送命令\"双色球 1 2 3 4 5 6+7 倍投2\"\n"
                "双色球不支持追加。开奖后发送“双色球兑奖”，兑奖期保留 30 天。"
            )
        return (
            "------大乐透玩法------\n"
            "【简单形式】\n"
            "发送命令（数字间要空格）：\"大乐透 1 2 3 4 5+1 2\"\n\n"
            "【规则如下】\n"
            "选号：加号左边为前区，从数字1-35中选 5 个；+ 右边为后区，从数字1-12选 2 个；基本每注 2 龙门币。\n\n"
            "单式（普通玩法）：发送命令：\"大乐透 1 2 3 4 5+1 2\"\n\n"
            "复式：前区选 6 个以上或后区选 3 个以上，系统自动组合；\n"
            "发送命令：\"大乐透 1 2 3 4 5 6+1 2\"\n\n"
            "胆拖：设置前区或后区胆码和拖码；\n"
            "发送命令：\"大乐透 前区胆码 1 2 前区拖码 3 4 5 6 7 后区 1 2\"\n\n"
            "倍投：末尾加 倍投2，支持 2-99 倍。\n"
            "追加：末尾加 追加，每注 3 龙门币，只对一、二等奖增加 80% 的追加奖金。\n"
            "例：发送命令\"大乐透 1 2 3 4 5+1 2 追加 倍投2\"\n"
            "开奖后发送“大乐透兑奖”，兑奖期保留 30 天。"
        )

    async def terminate(self):
        async with self._scratch_lock:
            self.db.close()
