"""离线自检：不需要 astrbot / pydantic / 网络，用桩模块直接压 main.py 的解析逻辑。

    python selftest.py

退出码 0 = 全部通过。重点防的是 v1.1.0 的静默失败形态：响应里没有
``web_search_tool_result`` 块时必须判定为「搜索失败」，绝不能把模型的
「我无法联网」当成搜索结果交给上层 Agent。
"""
import json
import sys
import types

# ---------- stub pydantic ----------
pyd = types.ModuleType("pydantic")


class Field:
    def __init__(self, default_factory=None, **kw):
        self.default_factory = default_factory


pyd.Field = Field
sys.modules["pydantic"] = pyd

pdc = types.ModuleType("pydantic.dataclasses")


def dataclass(cls=None, **kw):
    def wrap(c):
        return c
    return wrap(cls) if cls is not None else wrap


pdc.dataclass = dataclass
sys.modules["pydantic.dataclasses"] = pdc

# ---------- stub astrbot ----------
class _Sub:
    """让桩类支持 X[Y] 下标。"""

    def __class_getitem__(cls, item):
        return cls


class FunctionTool(_Sub):
    pass


class _Logger:
    def __getattr__(self, name):
        return lambda *a, **k: None


astrbot = types.ModuleType("astrbot")
api = types.ModuleType("astrbot.api")
api.logger = _Logger()
star = types.ModuleType("astrbot.api.star")
star.Context = star.Star = object
star.register = lambda *a, **k: (lambda c: c)

core = types.ModuleType("astrbot.core")
agent = types.ModuleType("astrbot.core.agent")
run_context = types.ModuleType("astrbot.core.agent.run_context")
run_context.ContextWrapper = _Sub
tool = types.ModuleType("astrbot.core.agent.tool")
tool.FunctionTool = FunctionTool
tool.ToolExecResult = str
agentctx = types.ModuleType("astrbot.core.astr_agent_context")
agentctx.AstrAgentContext = _Sub

for name, mod in {
    "astrbot": astrbot, "astrbot.api": api, "astrbot.api.star": star,
    "astrbot.core": core, "astrbot.core.agent": agent,
    "astrbot.core.agent.run_context": run_context,
    "astrbot.core.agent.tool": tool,
    "astrbot.core.astr_agent_context": agentctx,
}.items():
    sys.modules[name] = mod
astrbot.api = api
astrbot.core = core
core.agent = agent

import main  # noqa: E402

failures = []


def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"      got : {got!r}")
        print(f"      want: {want!r}")
        failures.append(label)


# ---- 1. 端点规整 ----
cases = [
    ("https://api.deepseek.com", "https://api.deepseek.com/anthropic/v1/messages"),
    ("https://api.deepseek.com/", "https://api.deepseek.com/anthropic/v1/messages"),
    ("https://api.deepseek.com/anthropic", "https://api.deepseek.com/anthropic/v1/messages"),
    ("https://api.deepseek.com/anthropic/v1", "https://api.deepseek.com/anthropic/v1/messages"),
    ("https://api.deepseek.com/anthropic/v1/messages", "https://api.deepseek.com/anthropic/v1/messages"),
    ("https://my-gw/v1/messages", "https://my-gw/v1/messages"),
    ("", "https://api.deepseek.com/anthropic/v1/messages"),
]
for src, want in cases:
    check(f"messages_endpoint({src!r})", main.messages_endpoint(src), want)

# ---- 2. 一次成功的搜索响应 ----
good = {
    "content": [
        {"type": "server_tool_use", "id": "s1", "name": "web_search", "input": {"query": "q"}},
        {
            "type": "web_search_tool_result",
            "tool_use_id": "s1",
            "content": [
                {"type": "web_search_result", "url": "https://a.test/1", "title": "标题A", "page_age": "2026-09-01"},
                {"type": "web_search_result", "url": "https://a.test/2", "title": ""},
                {"type": "web_search_result", "url": "https://a.test/1", "title": "重复应去重"},
            ],
        },
        {
            "type": "text",
            "text": "这是模型对搜索结果的总结。",
            "citations": [
                {"type": "web_search_result_location", "url": "https://a.test/1", "cited_text": "  摘录   文本  "},
            ],
        },
    ]
}
searched, sources, errors = main.sources_of(good, 5)
check("good.searched", searched, True)
check("good.errors", errors, [])
check("good.len", len(sources), 2)
check("good[0]", sources[0], "标题A: https://a.test/1（2026-09-01）\n   摘录：摘录 文本")
check("good[1] 无标题回退 url", sources[1], "https://a.test/2")
check("good.text", main.text_of(good), "这是模型对搜索结果的总结。")
check("good.blocks", main.block_types(good), "server_tool_use, web_search_tool_result, text")

check("limit 截断", len(main.sources_of(good, 1)[1]), 1)

# ---- 3. 真实故障形态：/responses 被忽略后的响应（只有 text，没有检索块） ----
broken = {
    "content": [
        {"type": "text", "text": "抱歉，我无法获取实时热点新闻。我的知识截止到 2024年6月，且当前没有联网能力。"},
    ]
}
searched, sources, errors = main.sources_of(broken, 5)
check("broken.searched", searched, False)
check("broken.sources", sources, [])
check("broken.blocks", main.block_types(broken), "text")

# 完全空的响应（例如被网关吃掉）
check("empty.searched", main.sources_of({}, 5)[0], False)
check("empty.blocks", main.block_types({}), "空")

# ---- 4. 服务端搜索自身报错 ----
err = {"content": [
    {"type": "web_search_tool_result", "content": {"type": "web_search_tool_result_error", "error_code": "max_uses_exceeded"}},
]}
searched, sources, errors = main.sources_of(err, 5)
check("err.searched", searched, True)
check("err.errors", errors, ["max_uses_exceeded"])
check("err.sources", sources, [])

# ---- 5. 错误响应体解析 ----
check(
    "error_detail anthropic",
    main.error_detail(json.dumps({"type": "error", "error": {"type": "authentication_error", "message": "invalid api key"}})),
    "invalid api key",
)
check("error_detail 非 json", main.error_detail("<html>502 Bad Gateway</html>"), "<html>502 Bad Gateway</html>")
check("error_detail 空", main.error_detail(""), "（响应体为空）")
check("error_detail str error", main.error_detail(json.dumps({"error": "boom"})), "boom")

# ---- 6. 配置解析兜底 ----
check("_int_or 坏值", main._int_or({"x": "abc"}, "x", 7), 7)
check("_int_or 0", main._int_or({"x": 0}, "x", 7), 7)
check("_int_or 正常", main._int_or({"x": "12"}, "x", 7), 12)
check("_float_or 负值", main._float_or({"x": -3}, "x", 30.0), 30.0)

print()
if failures:
    print(f"❌ {len(failures)} 项失败: {failures}")
    sys.exit(1)
print("✅ 全部通过")
