from typing import Any, Optional

from pydantic import Field
from pydantic.dataclasses import dataclass

from astrbot.api import logger
from astrbot.api.star import Context, Star, register
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext


def _extract_text(data: dict) -> str:
    """从 DeepSeek /responses 响应中提取模型生成的文本。"""
    text = (data.get("output_text") or "").strip()
    if text:
        return text

    parts: list[str] = []
    for item in data.get("output") or []:
        if item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if content.get("type") in ("output_text", "text"):
                t = (content.get("text") or "").strip()
                if t:
                    parts.append(t)
    return "\n".join(parts)


def _extract_sources(data: dict, limit: int) -> list[str]:
    """收集响应中的来源引用（URL 标题 + 链接）。"""
    sources: list[str] = []
    seen: set[str] = set()

    def add(title: Optional[str], url: Optional[str]) -> None:
        if not url or url in seen:
            return
        seen.add(url)
        label = f"{title}: {url}" if title else url
        sources.append(label)

    # 顶层 annotations
    for ann in data.get("annotations") or []:
        if ann.get("type") in ("url_citation", "web_search"):
            add(ann.get("title"), ann.get("url"))

    # output 各条目内的 annotations / 搜索结果
    for item in data.get("output") or []:
        if item.get("type") == "message":
            for content in item.get("content") or []:
                for ann in content.get("annotations") or []:
                    add(ann.get("title"), ann.get("url"))
        elif item.get("type") == "web_search_call":
            for result in item.get("results") or []:
                add(result.get("title"), result.get("url"))

    return sources[:limit]


@dataclass
class DeepSeekWebSearchTool(FunctionTool[AstrAgentContext]):
    """调用 DeepSeek 官方 /responses 接口的内置 web_search 工具。"""

    api_key: str = ""
    model: str = "deepseek-v4-flash"
    base_url: str = "https://api.deepseek.com"
    timeout_seconds: float = 30.0
    max_output_tokens: int = 4096
    sources_limit: int = 5

    name: str = "deepseek_web_search"
    description: str = (
        "使用 DeepSeek 官方内置的联网搜索能力搜索最新信息。"
        "当你需要查找实时新闻、B站/微博/网页最新内容、最新动态，"
        "或用户明确要求联网查询时，调用此工具。参数 query 为要搜索的问题或关键词。"
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

        url = f"{self.base_url.rstrip('/')}/responses"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": query}],
                }
            ],
            "tools": [{"type": "web_search"}],
            "max_output_tokens": self.max_output_tokens,
        }

        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, headers=headers, json=payload) as resp:
                    if resp.status != 200:
                        body = (await resp.text())[:500]
                        return (
                            f"搜索失败：DeepSeek 接口返回 {resp.status}，"
                            f"详情：{body}"
                        )
                    data = await resp.json()
        except Exception as e:  # noqa: BLE001
            logger.exception("DeepSeek 联网搜索调用异常")
            return f"搜索失败：{type(e).__name__}: {e}"

        text = _extract_text(data)
        sources = _extract_sources(data, self.sources_limit)

        if text:
            result = text
        else:
            result = (
                "没有拿到模型生成的摘要，但搜索到了以下结果：\n"
                + "\n".join(f"- {s}" for s in sources)
                if sources
                else "搜索完成，但没有返回任何内容。"
            )

        if sources:
            result += "\n\n来源：\n" + "\n".join(
                f"{i}. {s}" for i, s in enumerate(sources, 1)
            )

        logger.info(f"DeepSeek 联网搜索完成，query={query!r}，来源 {len(sources)} 条")
        return result


@register(
    "astrbot_plugin_deepseek_search",
    "Codex",
    "为 DeepSeek 聊天模型提供官方原生联网搜索（web_search）工具",
    "1.0.0",
    "",
)
class DeepSeekSearchPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config

        api_key = (config.get("api_key") or "").strip()
        model = (config.get("model") or "deepseek-v4-flash").strip()
        base_url = (config.get("base_url") or "https://api.deepseek.com").strip()

        try:
            timeout = float(config.get("timeout_seconds", 30))
        except (TypeError, ValueError):
            timeout = 30.0
        try:
            max_tokens = int(config.get("max_output_tokens", 4096))
        except (TypeError, ValueError):
            max_tokens = 4096
        try:
            sources_limit = int(config.get("sources_limit", 5))
        except (TypeError, ValueError):
            sources_limit = 5

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
            sources_limit=sources_limit,
        )
        self.context.add_llm_tools(tool)
        logger.info(
            f"DeepSeek 原生联网搜索工具已注册（模型 {model}，"
            f"超时 {timeout}s，最多返回 {sources_limit} 个来源）"
        )
