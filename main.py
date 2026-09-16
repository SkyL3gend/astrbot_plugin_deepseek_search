"""AstrBot 插件：把 DeepSeek 官方**服务端**联网搜索能力注册成一个 LLM 工具。

为什么要走 Anthropic 兼容端点
------------------------------
DeepSeek 只在 Anthropic 兼容的 Messages 端点执行服务端 ``web_search``：

    POST {base_url}/anthropic/v1/messages
    tools: [{"type": "web_search_20250305", "name": "web_search", "max_uses": N}]

搜索结果以结构化的 ``web_search_tool_result`` 内容块返回，模型据此组织回答。

**不要**改回 OpenAI 兼容的 ``/responses`` 端点。按 DeepSeek 官方 Responses API
兼容性表，``web_search`` / ``file_search`` / ``code_interpreter`` 等内置工具在
``/responses`` 上会被**静默忽略**（原文："Unsupported parameters are silently
ignored and do not cause errors"，Tools 表内标注 "Ignored"）。其失败形态极具
迷惑性：HTTP 200、没有任何报错、模型回一句「我无法联网」，插件拿到 0 条来源还
以为搜索成功了。本模块因此在解析后强制校验 ``web_search_tool_result`` 块的
存在性，缺失即判定为搜索失败，绝不把模型的「我无法联网」当成搜索结果上交。
"""

import asyncio
import json
from typing import Any

from pydantic import Field
from pydantic.dataclasses import dataclass

from astrbot.api import logger
from astrbot.api.star import Context, Star, register
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
ANTHROPIC_API_VERSION = "2023-06-01"
WEB_SEARCH_TOOL_TYPE = "web_search_20250305"
DEFAULT_MAX_USES = 5
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 1.5


def messages_endpoint(base_url: str) -> str:
    """把用户填的 base_url 规整成 Anthropic 兼容的 ``/messages`` 端点。

    ``https://api.deepseek.com``            → ``.../anthropic/v1/messages``
    ``https://api.deepseek.com/anthropic``  → ``.../anthropic/v1/messages``
    ``https://api.deepseek.com/anthropic/v1`` → ``.../anthropic/v1/messages``
    ``https://api.deepseek.com/anthropic/v1/messages`` → 原样返回
    ``https://my-gateway/v1/messages``      → 原样返回（自定义网关）
    """
    url = (base_url or "").strip().rstrip("/") or DEFAULT_BASE_URL
    if url.endswith("/messages"):
        return url
    if url.endswith("/anthropic/v1"):
        return f"{url}/messages"
    if url.endswith("/anthropic"):
        return f"{url}/v1/messages"
    return f"{url}/anthropic/v1/messages"


def text_of(data: dict) -> str:
    """拼接响应里所有 ``text`` 块的正文（即模型对搜索结果的总结）。"""
    parts: list[str] = []
    for block in data.get("content") or []:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = block.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    return "\n".join(parts)


def citation_snippets(data: dict) -> dict[str, str]:
    """从 ``text`` 块的 ``citations[]`` 收集 ``url → 摘录``（首次出现优先）。

    ``web_search_result`` 条目通常只有 url/title/page_age，没有正文摘录；
    摘录单独挂在引用它的 text 块的 citations 里，靠 url 关联回来。
    """
    snippets: dict[str, str] = {}
    for block in data.get("content") or []:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        for cite in block.get("citations") or []:
            if not isinstance(cite, dict):
                continue
            url = cite.get("url")
            excerpt = cite.get("cited_text")
            if not isinstance(url, str) or not isinstance(excerpt, str):
                continue
            url = url.strip()
            excerpt = " ".join(excerpt.split())
            if url and excerpt and url not in snippets:
                snippets[url] = excerpt
    return snippets


def sources_of(data: dict, limit: int) -> tuple[bool, list[str], list[str]]:
    """收集 ``web_search_tool_result`` 块内的来源。

    :returns: ``(是否真的执行过搜索, 来源列表, 服务端搜索错误列表)``
    """
    searched = False
    errors: list[str] = []
    sources: list[str] = []
    seen: set[str] = set()
    snippets = citation_snippets(data)

    for block in data.get("content") or []:
        if not isinstance(block, dict) or block.get("type") != "web_search_tool_result":
            continue
        searched = True

        content = block.get("content")
        if isinstance(content, dict):
            # 服务端搜索自身失败时，Anthropic 返回单个 error 对象而非结果数组。
            code = content.get("error_code") or content.get("type") or "unknown_error"
            errors.append(str(code))
            continue

        for item in content or []:
            if not isinstance(item, dict) or item.get("type") != "web_search_result":
                continue
            url = item.get("url")
            if not isinstance(url, str) or not url.strip():
                continue
            url = url.strip()
            if url in seen:
                continue
            seen.add(url)

            title = item.get("title")
            title = title.strip() if isinstance(title, str) else ""
            label = f"{title}: {url}" if title else url

            page_age = item.get("page_age")
            if isinstance(page_age, str) and page_age.strip():
                label += f"（{page_age.strip()}）"

            excerpt = snippets.get(url)
            if excerpt:
                label += f"\n   摘录：{excerpt}"
            sources.append(label)

    return searched, sources[:limit], errors


def block_types(data: dict) -> str:
    """响应里出现了哪些内容块类型——用于把「搜索没执行」的原因说清楚。"""
    types = [
        str(b.get("type"))
        for b in (data.get("content") or [])
        if isinstance(b, dict)
    ]
    return ", ".join(types) if types else "空"


def error_detail(body: str) -> str:
    """从 Anthropic 风格的错误响应体里挖出人话。"""
    try:
        parsed = json.loads(body)
    except ValueError:
        return body[:300] if body else "（响应体为空）"
    if isinstance(parsed, dict):
        error = parsed.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error)
        if isinstance(error, str):
            return error
        if parsed.get("message"):
            return str(parsed["message"])
    return body[:300]


def _int_or(config: dict, key: str, default: int) -> int:
    try:
        value = int(config.get(key, default))
    except (TypeError, ValueError):
        return default
    return value if value >= 1 else default


def _float_or(config: dict, key: str, default: float) -> float:
    try:
        value = float(config.get(key, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


@dataclass
class DeepSeekWebSearchTool(FunctionTool[AstrAgentContext]):
    """调用 DeepSeek 官方 Anthropic 兼容端点的服务端 ``web_search`` 工具。"""

    api_key: str = ""
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    timeout_seconds: float = 30.0
    max_output_tokens: int = 4096
    max_uses: int = DEFAULT_MAX_USES
    sources_limit: int = 5

    name: str = "deepseek_web_search"
    description: str = (
        "使用 DeepSeek 官方内置的联网搜索能力搜索最新信息。"
        "当你需要查找实时新闻、B站/微博/网页最新内容、最新动态，"
        "或用户明确要求联网查询时，调用此工具。参数 query 为要搜索的问题或关键词。"
        "返回结果以「来源」列表为准：只依据返回的来源作答；"
        "若结果明确说明搜索未执行，请如实告知用户，不要凭记忆编造。"
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "要搜索的问题或关键词，例如：洛天依官方账号最新投稿",
                },
            },
            "required": ["query"],
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs: Any
    ) -> ToolExecResult:
        query = (kwargs.get("query") or "").strip()
        if not query:
            return "搜索失败：没有收到要搜索的 query 参数。"
        if not self.api_key:
            return "搜索失败：插件未配置 DeepSeek API Key，请先在插件配置中填写。"

        url = messages_endpoint(self.base_url)
        headers = {
            # 官方 DeepSeek 认 x-api-key；部分 Anthropic 兼容网关认 Bearer，两个都发。
            "x-api-key": self.api_key,
            "authorization": f"Bearer {self.api_key}",
            "anthropic-version": ANTHROPIC_API_VERSION,
            "content-type": "application/json",
            "accept": "application/json",
        }
        payload = {
            "model": self.model,
            "max_tokens": self.max_output_tokens,
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": query}]}
            ],
            "tools": [
                {
                    "type": WEB_SEARCH_TOOL_TYPE,
                    "name": "web_search",
                    "max_uses": max(1, self.max_uses),
                }
            ],
        }

        data, failure = await self._post(url, headers, payload)
        if failure is not None:
            return failure

        text = text_of(data)
        searched, sources, search_errors = sources_of(data, self.sources_limit)

        # —— 关键防线：没有 web_search_tool_result 块 = 服务端根本没搜索。
        # 此时模型的正文往往是「我无法联网」之类的推脱，直接当结果返回会把
        # 「搜索失败」伪装成「搜索成功但没找到」，是本插件最难排查的故障。
        if not searched:
            logger.error(
                f"DeepSeek 未执行联网搜索：端点 {url}，模型 {self.model}，"
                f"响应块 [{block_types(data)}]"
            )
            return (
                "搜索失败：DeepSeek 没有执行联网搜索——响应里没有任何 "
                "web_search_tool_result 块，说明当前端点或模型不支持服务端 web_search。\n"
                "请如实告诉用户本次搜索未成功，不要凭记忆作答。\n"
                f"（诊断：端点 {url}，模型 {self.model}，响应块 [{block_types(data)}]）"
            )

        if not sources and search_errors:
            detail = "；".join(search_errors)
            logger.error(f"DeepSeek 服务端搜索报错：{detail}")
            return f"搜索失败：DeepSeek 服务端搜索报错（{detail}）。"

        if text:
            result = text
        elif sources:
            result = "模型没有给出摘要，以下是检索到的结果："
        else:
            result = "搜索已执行，但没有返回任何可用结果。"

        if sources:
            result += "\n\n来源：\n" + "\n".join(
                f"{i}. {s}" for i, s in enumerate(sources, 1)
            )
        else:
            result += "\n\n（注意：本次搜索没有返回任何来源链接，请谨慎使用以上内容。）"

        logger.info(
            f"DeepSeek 联网搜索完成，query={query!r}，来源 {len(sources)} 条，端点 {url}"
        )
        return result

    async def _post(
        self, url: str, headers: dict, payload: dict
    ) -> tuple[dict | None, str | None]:
        """发送一次搜索请求，返回 ``(响应体, 错误文案)``；二者恰有一个为 None。"""
        import aiohttp

        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        last_error = ""

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(
                        url, headers=headers, json=payload
                    ) as resp:
                        body = await resp.text()
                        if resp.status != 200:
                            detail = error_detail(body)
                            # 4xx 是配置/鉴权问题，重试没有意义；429 与 5xx 值得重试。
                            if resp.status < 500 and resp.status != 429:
                                logger.error(
                                    f"DeepSeek 联网搜索被拒绝：HTTP {resp.status}，{detail}"
                                )
                                return None, (
                                    f"搜索失败：DeepSeek 接口返回 {resp.status}。"
                                    f"详情：{detail}\n（端点 {url}）"
                                )
                            last_error = f"HTTP {resp.status}：{detail}"
                        else:
                            try:
                                return json.loads(body), None
                            except ValueError as exc:
                                return None, (
                                    f"搜索失败：响应不是合法 JSON（{exc}）。"
                                    f"原文：{body[:300]}"
                                )
            except Exception as exc:  # noqa: BLE001
                last_error = f"{type(exc).__name__}: {exc}"

            if attempt < MAX_ATTEMPTS:
                logger.warning(
                    f"DeepSeek 联网搜索第 {attempt}/{MAX_ATTEMPTS} 次请求失败"
                    f"（{last_error}），{RETRY_BACKOFF_SECONDS * attempt:.1f}s 后重试"
                )
                await asyncio.sleep(RETRY_BACKOFF_SECONDS * attempt)

        # 注意：此处已在 except 块之外，用 error 而不是 exception（后者会打印 NoneType: None）
        logger.error(f"DeepSeek 联网搜索请求最终失败：{last_error}")
        return None, (
            f"搜索失败：无法访问 {url}（已重试 {MAX_ATTEMPTS} 次）。"
            f"最后错误：{last_error}"
        )


@register(
    "astrbot_plugin_deepseek_search",
    "Codex",
    "为 DeepSeek 聊天模型提供官方原生联网搜索（web_search）工具",
    "1.2.0",
    "",
)
class DeepSeekSearchPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config

        api_key = (config.get("api_key") or "").strip()
        model = (config.get("model") or DEFAULT_MODEL).strip()
        base_url = (config.get("base_url") or DEFAULT_BASE_URL).strip()

        timeout = _float_or(config, "timeout_seconds", 30.0)
        max_tokens = _int_or(config, "max_output_tokens", 4096)
        max_uses = _int_or(config, "max_uses", DEFAULT_MAX_USES)
        sources_limit = _int_or(config, "sources_limit", 5)

        if not api_key:
            logger.warning(
                "DeepSeek 联网搜索插件未配置 API Key，"
                "请到插件配置中填写后重载插件。"
            )
            return

        tool = DeepSeekWebSearchTool(
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout_seconds=timeout,
            max_output_tokens=max_tokens,
            max_uses=max_uses,
            sources_limit=sources_limit,
        )
        self.context.add_llm_tools(tool)
        logger.info(
            f"DeepSeek 原生联网搜索工具已注册（端点 {messages_endpoint(base_url)}，"
            f"模型 {model}，超时 {timeout}s，max_uses {max_uses}，"
            f"最多返回 {sources_limit} 个来源）"
        )
