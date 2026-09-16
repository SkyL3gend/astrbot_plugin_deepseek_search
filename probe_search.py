#!/usr/bin/env python3
"""DeepSeek 联网搜索端点探针。

一次跑完两条链路，判定**哪条真的会让 DeepSeek 服务端执行联网搜索**：

  1. Anthropic 兼容 ``/anthropic/v1/messages`` + ``web_search_20250305``  ← 本插件走这条
  2. OpenAI 兼容 ``/responses`` + ``{"type": "web_search"}``              ← 官方文档标注为 Ignored

判定标准不是「模型说了什么」，而是响应里**有没有结构化检索结果块**
（Anthropic 侧 ``web_search_tool_result``，Responses 侧 ``web_search_call``）。
模型嘴上说「我无法联网」而没有任何检索块，就是搜索根本没执行。

只依赖标准库，用法：

    python probe_search.py --api-key sk-xxxxxxxx
    # 或先设环境变量
    python probe_search.py

退出码：0 = Anthropic 链路可搜索；1 = 两条链路都搜不到。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "https://api.deepseek.com"
ANTHROPIC_API_VERSION = "2023-06-01"
PROBE_QUERY = "DeepSeek 官方 API 最新公告"


def post(url: str, headers: dict, payload: dict, timeout: float) -> tuple[int, dict | str]:
    """发一个 POST，返回 (status, 解析后的 JSON 或原始文本)。"""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return 0, f"{type(exc).__name__}: {exc}"

    try:
        return status, json.loads(body)
    except ValueError:
        return status, body


def probe_anthropic(api_key: str, base_url: str, model: str, timeout: float) -> dict:
    url = f"{base_url.rstrip('/')}/anthropic/v1/messages"
    status, data = post(
        url,
        {
            "x-api-key": api_key,
            "authorization": f"Bearer {api_key}",
            "anthropic-version": ANTHROPIC_API_VERSION,
            "content-type": "application/json",
        },
        {
            "model": model,
            "max_tokens": 1024,
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": PROBE_QUERY}]}
            ],
            "tools": [
                {"type": "web_search_20250305", "name": "web_search", "max_uses": 3}
            ],
        },
        timeout,
    )
    blocks = data.get("content") if isinstance(data, dict) else None
    types = [b.get("type") for b in blocks or [] if isinstance(b, dict)]
    results = [
        item
        for b in blocks or []
        if isinstance(b, dict) and b.get("type") == "web_search_tool_result"
        for item in (b.get("content") or [])
        if isinstance(item, dict) and item.get("type") == "web_search_result"
    ]
    text = "\n".join(
        b.get("text", "")
        for b in blocks or []
        if isinstance(b, dict) and b.get("type") == "text"
    )
    return {
        "label": "Anthropic /anthropic/v1/messages + web_search_20250305",
        "url": url,
        "status": status,
        "blocks": types,
        "sources": results,
        "searched": bool(results),
        "text": text.strip(),
        "raw": data if status != 200 else None,
    }


def probe_responses(api_key: str, base_url: str, model: str, timeout: float) -> dict:
    url = f"{base_url.rstrip('/')}/responses"
    status, data = post(
        url,
        {
            "authorization": f"Bearer {api_key}",
            "content-type": "application/json",
        },
        {
            "model": model,
            "input": [
                {"role": "user", "content": [{"type": "input_text", "text": PROBE_QUERY}]}
            ],
            "tools": [{"type": "web_search"}],
            "max_output_tokens": 1024,
        },
        timeout,
    )
    items = data.get("output") if isinstance(data, dict) else None
    types = [i.get("type") for i in items or [] if isinstance(i, dict)]
    calls = [i for i in items or [] if isinstance(i, dict) and i.get("type") == "web_search_call"]
    text = (data.get("output_text") or "") if isinstance(data, dict) else ""
    return {
        "label": "OpenAI /responses + web_search",
        "url": url,
        "status": status,
        "blocks": types,
        "sources": [],
        "searched": bool(calls),
        "text": (text or "").strip(),
        "raw": data if status != 200 else None,
    }


def report(result: dict) -> None:
    print("=" * 78)
    print(f"链路      : {result['label']}")
    print(f"端点      : {result['url']}")
    print(f"HTTP      : {result['status']}")
    print(f"内容块    : {result['blocks'] or '（无）'}")
    if result["raw"] is not None:
        print(f"响应体    : {json.dumps(result['raw'], ensure_ascii=False)[:600]}")
    print(f"检索结果块: {'有 ✅' if result['searched'] else '没有 ❌'}")
    if result["sources"]:
        for i, item in enumerate(result["sources"][:5], 1):
            print(f"  {i}. {item.get('title') or '(无标题)'} — {item.get('url')}")
    if result["text"]:
        preview = result["text"][:200].replace("\n", " ")
        print(f"模型正文  : {preview}{'…' if len(result['text']) > 200 else ''}")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="DeepSeek 联网搜索端点探针")
    parser.add_argument("--api-key", default=os.environ.get("DEEPSEEK_API_KEY", ""))
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()

    api_key = args.api_key.strip()
    if not api_key:
        print("缺少 API Key：请用 --api-key 传入，或设置环境变量 DEEPSEEK_API_KEY。")
        return 2

    print(f"\n探针查询：{PROBE_QUERY!r}   模型：{args.model}\n")
    anthropic = probe_anthropic(api_key, args.base_url, args.model, args.timeout)
    report(anthropic)
    responses = probe_responses(api_key, args.base_url, args.model, args.timeout)
    report(responses)

    print("=" * 78)
    print("结论")
    print(f"  Anthropic Messages + web_search_20250305 ：{'可搜索 ✅' if anthropic['searched'] else '搜不到 ❌'}")
    print(f"  OpenAI Responses    + web_search         ：{'可搜索 ✅' if responses['searched'] else '搜不到 ❌'}")
    if anthropic["searched"]:
        print("\n  → 本插件当前实现（Anthropic 链路）可用。")
        return 0
    print(
        "\n  → 两条链路都没有触发服务端搜索。请检查 base_url 是否指向官方 "
        "api.deepseek.com、API Key 是否有效、以及网关是否支持服务端 web_search。"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
