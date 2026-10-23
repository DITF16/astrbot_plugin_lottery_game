import asyncio
import importlib.util
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import patch
from lottery_engine import LotteryDB
from statistics_store import StatisticsStore


def test_native_registration_query_edit_and_reload_cleanup():
    async def scenario():
        request = types.SimpleNamespace(query={})
        web = types.ModuleType("astrbot.api.web")
        web.request = request
        web.error_response = lambda message, **kwargs: {"error":message}
        with patch.dict(sys.modules, {"astrbot.api.web":web}):
            spec=importlib.util.spec_from_file_location("lottery_native_web_test",Path(__file__).with_name("admin_web.py"))
            module=importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            db=LotteryDB(Path(directory)/"test.db")
            db.checkin("123","玩家","2026-10-23",50)
            store=StatisticsStore(db.path)
            context=types.SimpleNamespace(registered_web_apis=[])
            def register(route,handler,methods,desc):
                context.registered_web_apis[:]=[api for api in context.registered_web_apis if api[0]!=route]
                context.registered_web_apis.append((route,handler,methods,desc))
            context.register_web_api=register
            first=module.LotteryWeb(store)
            first.register(context)
            assert len(context.registered_web_apis)==4
            assert all(api[0].startswith("/astrbot_plugin_lottery_game/") for api in context.registered_web_apis)
            assert (await first.users())["total"]==1
            async def body():
                return {"uid":"123","changes":{"balance":123}}
            request.json=body
            assert await first.edit()=={"ok":True}
            assert store.user("123")["balance"]==123
            request.query={"uid":"123"}
            assert (await first.user())["nickname"]=="玩家"
            request.query={"page":"-1"}
            assert "error" in await first.users()
            newer=module.LotteryWeb(store)
            newer.register(context)
            first.unregister()
            assert len(context.registered_web_apis)==4
            newer.unregister()
            assert context.registered_web_apis==[]
            html=Path("pages/dashboard/index.html").read_text(encoding="utf-8")
            assert "bridge-sdk.js" in html and "bridge.ready()" in html
            assert "fetch(" not in html
            db.close()
    asyncio.run(scenario())
