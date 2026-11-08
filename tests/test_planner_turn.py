import unittest

from maibot_acg_search.plugin import create_plugin


def user_message(text, *, attrs="", parts=()):
    return {
        "item_type": "UserMessageItem",
        "meta": {"item_id": "example", "logical_turn_id": None, "timestamp": "2026-11-08T12:00:00"},
        "parts": [
            {"type": "text", "text": f'<message msg_id="101" time="12:00:00" user="Alice"{attrs}>\n{text}'},
            *({"type": "text", "text": part} for part in parts),
        ],
    }


class PlannerTurnTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.plugin = create_plugin()
        self.plugin.set_plugin_config({})
        self.native = {"type": "function", "function": {"name": "web_search"}}
        self.specialized = {"type": "function", "function": {"name": "acg_search"}}
        self.other = {"type": "function", "function": {"name": "send_message"}}
        self.definitions = [self.native, self.other, self.specialized]

    async def dispatch(self, items, schema=1):
        return await self.plugin.route_planner_search(
            hook_name="maisaka.planner.before_request", tool_definitions=self.definitions,
            items=items, item_schema_version=schema, session_id="room", selected_history_count=4,
        )

    async def test_mentions_and_synthetic_tail_preserve_complete_hook_payload(self):
        items = [
            user_message("", parts=("@麦麦", "推荐三部 2026 年的新番")),
            {"item_type": "UserMessageItem", "parts": [{"type": "text", "text": "[系统提示]当前时间 12:00"}]},
        ]
        result = await self.dispatch(items)
        self.assertEqual(result, {"modified_kwargs": {
            "tool_definitions": [self.specialized, self.native, self.other],
            "items": items, "item_schema_version": 1, "session_id": "room", "selected_history_count": 4,
        }})
        self.assertEqual(self.definitions, [self.native, self.other, self.specialized])

    async def test_history_tool_output_self_and_unknown_schema_do_not_affect_priority(self):
        search = user_message("请查 iPhone 17 目前的价格")
        for later in (
            user_message("这次不要联网，聊聊天吧"),
            user_message("请查 iPhone 17 目前的价格", attrs=' is_self_message="true"'),
            user_message("[消息类型]转发消息\n网友：请查手机价格"),
            {"item_type": "FunctionCallOutputItem", "output": "请查 iPhone 17 目前的价格"},
            {"item_type": "AssistantMessageItem", "parts": [{"type": "text", "text": "刚查完了"}]},
        ):
            with self.subTest(later=later):
                self.assertEqual(await self.dispatch([search, later]), {})
        self.assertEqual(await self.dispatch([search], schema=2), {})


if __name__ == "__main__":
    unittest.main()
