"""Unit tests: host interfaces are doubles, no live DeepSeek calls."""

import asyncio
import importlib.util
import logging
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Generic, TypeVar
from unittest.mock import MagicMock

import aiohttp
import pytest
from pydantic.dataclasses import dataclass


@pytest.fixture
def plugin(monkeypatch):
    t = TypeVar("t")

    @dataclass
    class FunctionTool(Generic[t]):
        name: str
        description: str
        parameters: dict
        handler: object = None
        handler_module_path: str | None = None
        active: bool = True

    class ContextWrapper(Generic[t]):
        pass

    class Star:
        def __init__(self, context):
            self.context = context

    modules = {
        "astrbot": {},
        "astrbot.api": {"logger": logging.getLogger("test")},
        "astrbot.api.star": {"Context": object, "Star": Star},
        "astrbot.core": {},
        "astrbot.core.agent": {},
        "astrbot.core.agent.run_context": {"ContextWrapper": ContextWrapper},
        "astrbot.core.agent.tool": {
            "FunctionTool": FunctionTool,
            "ToolExecResult": str,
        },
        "astrbot.core.astr_agent_context": {"AstrAgentContext": object},
    }
    for name, attrs in modules.items():
        module = ModuleType(name)
        module.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, module)
    spec = importlib.util.spec_from_file_location(
        "plugin_under_test", Path(__file__).parents[1] / "main.py"
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def fake_http(monkeypatch, plugin, data, status=200, error=None):
    state = SimpleNamespace(request=None, closed=False, timeout=None)

    class Response:
        async def __aenter__(self):
            if error:
                raise error
            return self

        async def __aexit__(self, *args):
            pass

        async def json(self):
            if isinstance(data, Exception):
                raise data
            return data

    class Session:
        def __init__(self, **kwargs):
            state.timeout = kwargs["timeout"].total

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            state.closed = True

        def post(self, url, **kwargs):
            state.request = {"url": url, **kwargs}
            result = Response()
            result.status = status
            return result

    monkeypatch.setattr(plugin.aiohttp, "ClientSession", Session)
    return state


def call(plugin, **kwargs):
    return asyncio.run(
        plugin.DeepSeekWebSearchTool(api_key="test-secret").call(None, **kwargs)
    )


def test_search_request(plugin, monkeypatch):
    item = {
        "title": "标题",
        "url": "https://example.com",
        "summary": "长摘要",
        "snippet": "短摘要",
        "date": "2026-10-17T00:00:00+00:00",
        "authority": 3,
        "rerank_score": 2.4,
    }
    state = fake_http(monkeypatch, plugin, {"results": [item, item]})
    import json

    data = json.loads(
        call(
            plugin,
            query="DeepSeek site:deepseek.com after:2026-10-17",
            ori_question="最新搜索 API 是什么？",
        )
    )
    assert len(data["results"]) == 1
    assert data["results"][0]["summary"] == "长摘要"
    assert data["results"][0]["date"] == item["date"]
    assert state.closed and state.timeout == 30
    assert state.request["url"] == "https://api.deepseek.com/v1/tools/search"
    assert state.request["json"] == {
        "query": "DeepSeek site:deepseek.com after:2026-10-17",
        "count": 5,
        "need_content": False,
        "ori_question": "最新搜索 API 是什么？",
    }
    assert state.request["allow_redirects"] is False
    assert "model" not in state.request["json"]


@pytest.mark.parametrize("query", [None, "", " ", [], 42, "中" * 129])
def test_invalid_query(plugin, monkeypatch, query):
    session = MagicMock(side_effect=AssertionError("must not request"))
    monkeypatch.setattr(plugin.aiohttp, "ClientSession", session)
    assert "query" in call(plugin, query=query)
    session.assert_not_called()


@pytest.mark.parametrize("question", ["", [], 42, "中" * 129])
def test_invalid_original_question(plugin, question):
    assert "ori_question" in call(plugin, query="test", ori_question=question)


def test_query_boundary_and_empty_results(plugin, monkeypatch):
    fake_http(monkeypatch, plugin, {"results": []})
    assert "没有找到" in call(plugin, query="中" * 128, ori_question=None)


def test_missing_key(plugin):
    assert "API Key" in asyncio.run(
        plugin.DeepSeekWebSearchTool().call(None, query="test")
    )


@pytest.mark.parametrize(
    "status", [301, 400, 401, 402, 403, 404, 408, 413, 429, 500, 504]
)
def test_http_errors(plugin, monkeypatch, status):
    state = fake_http(monkeypatch, plugin, {"secret": "test-secret"}, status=status)
    result = call(plugin, query="test")
    assert str(status) in result and "test-secret" not in result
    assert state.closed


@pytest.mark.parametrize(
    "data,expected",
    [
        ([], "不是响应对象"),
        ({"error": {"message": "secret"}}, "错误对象"),
        ({}, "results"),
        ({"results": {}}, "results"),
        (ValueError("secret"), "有效 JSON"),
        ({"results": [None, {"url": "javascript:alert(1)"}]}, "没有可用"),
    ],
)
def test_invalid_responses(plugin, monkeypatch, data, expected):
    fake_http(monkeypatch, plugin, data)
    result = call(plugin, query="test")
    assert expected in result and "secret" not in result


@pytest.mark.parametrize(
    "error,expected",
    [
        (asyncio.TimeoutError(), "超时"),
        (aiohttp.ClientConnectionError("secret"), "网络连接异常"),
    ],
)
def test_network_errors(plugin, monkeypatch, error, expected):
    state = fake_http(monkeypatch, plugin, None, error=error)
    assert expected in call(plugin, query="test")
    assert state.closed


def test_cancellation(plugin, monkeypatch):
    state = fake_http(monkeypatch, plugin, None, error=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        call(plugin, query="test")
    assert state.closed


@pytest.mark.parametrize(
    "base",
    [
        "https://api.deepseek.com",
        "https://api.deepseek.com/",
        "https://api.deepseek.com/v1",
        "https://api.deepseek.com/v1/",
    ],
)
def test_base_url(plugin, base):
    assert (
        plugin._endpoint(base, "search") == "https://api.deepseek.com/v1/tools/search"
    )


@pytest.mark.parametrize(
    "invalid", [None, "bad", -1, 0, True, float("inf"), float("nan")]
)
def test_invalid_configuration(plugin, invalid):
    context = SimpleNamespace(add_llm_tools=MagicMock())
    plugin.DeepSeekSearchPlugin(
        context,
        {
            "timeout_seconds": invalid,
            "sources_limit": invalid,
            "content_max_chars": invalid,
        },
    )
    search, fetch = context.add_llm_tools.call_args.args
    assert (search.timeout_seconds, search.sources_limit, fetch.content_max_chars) == (
        30,
        5,
        12000,
    )


def test_old_flash_config_works_without_model(plugin, monkeypatch):
    context = SimpleNamespace(add_llm_tools=MagicMock())
    config = {
        "api_key": " key ",
        "model": "deepseek-v4-flash",
        "max_output_tokens": 4096,
        "timeout_seconds": 60,
        "sources_limit": 7,
    }
    plugin.DeepSeekSearchPlugin(context, config)
    search, fetch = context.add_llm_tools.call_args.args
    state = fake_http(monkeypatch, plugin, {"results": []})
    assert "没有找到" in asyncio.run(search.call(None, query="test"))
    assert state.request["json"]["count"] == 7
    assert "model" not in state.request["json"]
    assert config["model"] == "deepseek-v4-flash"
    assert fetch.name == "deepseek_web_fetch"


def test_search_content_and_limit(plugin, monkeypatch):
    import json

    fake_http(
        monkeypatch,
        plugin,
        {
            "results": [
                {
                    "url": "https://example.com/a",
                    "snippet": "短摘要",
                    "content": "abcdef",
                },
                {"url": "https://example.com/b"},
            ]
        },
    )
    tool = plugin.DeepSeekWebSearchTool(
        api_key="key", need_content=True, sources_limit=1, content_max_chars=3
    )
    result = json.loads(asyncio.run(tool.call(None, query="test")))
    assert len(result["results"]) == 1
    assert result["results"][0]["summary"] == "短摘要"
    assert result["results"][0]["content"] == "abc\n[正文已截断]"


@pytest.mark.parametrize(
    "url",
    [
        "",
        None,
        "file:///tmp/a",
        "https://",
        "https://user:pass@example.com",
        "https://[bad",
        "https://example.com:wrong/",
        "https://example.com:65536/",
        "https://exa mple.com/",
        "https://example.com/line\nbreak",
        "https://example.com/" + "a" * 8192,
    ],
)
def test_invalid_fetch_url(plugin, url):
    assert "url" in asyncio.run(
        plugin.DeepSeekWebFetchTool(api_key="key").call(None, url=url)
    )


def test_fetch_request_and_truncation(plugin, monkeypatch):
    import json

    state = fake_http(
        monkeypatch, plugin, {"title": "页面", "content": "# Hello world"}
    )
    tool = plugin.DeepSeekWebFetchTool(api_key="key", content_max_chars=7)
    data = json.loads(asyncio.run(tool.call(None, url="https://example.com/article")))
    assert state.request["url"] == "https://api.deepseek.com/v1/tools/fetch"
    assert state.request["json"] == {"url": "https://example.com/article"}
    assert data["truncated"] is True and data["content"].startswith("# Hello")
    assert data["title"] == "页面" and state.closed


@pytest.mark.parametrize("content", [None, "", 3])
def test_fetch_empty_content(plugin, monkeypatch, content):
    fake_http(monkeypatch, plugin, {"content": content})
    assert "未返回可用" in asyncio.run(
        plugin.DeepSeekWebFetchTool(api_key="key").call(None, url="https://example.com")
    )


@pytest.mark.parametrize(
    "base",
    [
        "https://api.deepseek.com?mode=test",
        "https://api.deepseek.com/#fragment",
        "https://user:password@example.com",
        "file:///tmp/api",
        "https://example.com:bad",
    ],
)
def test_invalid_api_base_never_sends_credentials(plugin, monkeypatch, base):
    session = MagicMock(side_effect=AssertionError("must not request"))
    monkeypatch.setattr(plugin.aiohttp, "ClientSession", session)
    tool = plugin.DeepSeekWebSearchTool(api_key="secret", base_url=base)
    result = asyncio.run(tool.call(None, query="test"))
    assert "base_url" in result and "password" not in result
    session.assert_not_called()


@pytest.mark.parametrize("localization", ["", "1.1.1.1", "2001:db8::1"])
def test_localization_forwarded_from_config(plugin, monkeypatch, localization):
    context = SimpleNamespace(add_llm_tools=MagicMock())
    plugin.DeepSeekSearchPlugin(
        context, {"api_key": "key", "localization": localization}
    )
    search, _ = context.add_llm_tools.call_args.args
    state = fake_http(monkeypatch, plugin, {"results": []})
    asyncio.run(search.call(None, query="test"))
    assert state.request["json"].get("localization", "") == localization
    if not localization:
        assert "localization" not in state.request["json"]


def test_invalid_localization_does_not_request(plugin, monkeypatch):
    session = MagicMock(side_effect=AssertionError("must not request"))
    monkeypatch.setattr(plugin.aiohttp, "ClientSession", session)
    tool = plugin.DeepSeekWebSearchTool(api_key="key", localization="Shanghai")
    assert "localization" in asyncio.run(tool.call(None, query="test"))
    session.assert_not_called()


def test_search_truncation_metadata(plugin, monkeypatch):
    import json

    fake_http(
        monkeypatch,
        plugin,
        {
            "results": [
                {"url": "https://example.com/long", "content": "abcd"},
                {"url": "https://example.com/short", "content": "abc"},
            ]
        },
    )
    tool = plugin.DeepSeekWebSearchTool(
        api_key="key", need_content=True, content_max_chars=3
    )
    result = json.loads(asyncio.run(tool.call(None, query="test")))
    assert [item["truncated"] for item in result["results"]] == [True, False]
