"""Native AstrBot plugin page APIs, using the dashboard bridge."""
import asyncio
import logging
from astrbot.api.web import request, error_response


class LotteryWeb:
    def __init__(self, store):
        self.store = store
        self.routes = []
        self.context = None

    def register(self, context):
        self.context = context
        for name, handler, methods in (
            ("overview", self.overview, ["GET"]),
            ("users", self.users, ["GET"]),
            ("user", self.user, ["GET"]),
            ("edit", self.edit, ["POST"]),
        ):
            route = "/astrbot_plugin_lottery_game/" + name
            context.register_web_api(route, handler, methods, "彩票数据管理 " + name)
            self.routes.append((route, handler))

    def unregister(self):
        if self.context:
            self.context.registered_web_apis[:] = [api for api in self.context.registered_web_apis
                if not any(api[0] == route and api[1] == handler for route, handler in self.routes)]
        self.routes.clear()

    async def _call(self, method, *args):
        try:
            return await asyncio.to_thread(method, *args)
        except (ValueError, TypeError) as exc:
            return error_response(str(exc))
        except Exception:
            logging.getLogger(__name__).exception("Lottery page request failed")
            return error_response("服务暂时忙碌，请稍后重试", status_code=503)

    async def overview(self):
        return await self._call(self.store.overview)

    async def users(self):
        try:
            page = int(request.query.get("page", "1"))
            if not 1 <= page <= 1000000:
                raise ValueError()
        except (ValueError, TypeError):
            return error_response("页码不合法")
        return await self._call(self.store.users, request.query.get("q", "")[:200], page)

    async def user(self):
        return await self._call(self.store.user, request.query.get("uid", ""))

    async def edit(self):
        try:
            body = await request.json()
            if not isinstance(body, dict) or set(body) != {"uid", "changes"} or not isinstance(body["uid"], str):
                raise ValueError()
        except (ValueError, TypeError):
            return error_response("修改请求格式不合法")
        result = await self._call(self.store.edit, body["uid"], body["changes"])
        return {"ok": True} if result is None else result
