"""Resolve account nicknames without using group cards."""

import asyncio
from collections import OrderedDict


class QQNicknames:
    def __init__(self):
        self._cache = OrderedDict()
        self._requests = asyncio.Semaphore(3)

    async def resolve(self, event, user_id, today):
        platform = event.get_platform_name()
        key = (str(getattr(event.message_obj, "self_id", "")), user_id)
        cached = self._cache.get(key)
        if cached and cached[0] == today:
            return cached[1]
        if platform == "aiocqhttp":
            try:
                async with self._requests:
                    result = await asyncio.wait_for(
                        event.bot.api.call_action("get_stranger_info", user_id=int(user_id), no_cache=True),
                        timeout=3,
                    )
                nickname = (result or {}).get("nickname")
                if isinstance(nickname, str) and nickname.strip():
                    self._cache[key] = (today, nickname.strip())
                    self._cache.move_to_end(key)
                    while len(self._cache) > 2048:
                        self._cache.popitem(last=False)
                    return nickname.strip()
            except Exception:
                # The raw sender nickname is the account name; never use sender.card.
                pass
            if str(event.get_sender_id()) == user_id:
                raw = getattr(event.message_obj, "raw_message", None)
                if isinstance(raw, dict):
                    sender = raw.get("sender") or {}
                    nickname = sender.get("nickname") if isinstance(sender, dict) else None
                    if isinstance(nickname, str) and nickname.strip():
                        return nickname.strip()
        return user_id
