注 本项目大部分由Ai完成

# AstrBot DeepSeek 原生联网搜索插件

给你的 AstrBot 机器人接上 DeepSeek 官方内置的联网搜索能力。

## 它做了什么

插件会在 AstrBot 的 AI 工具列表里注册一个名为 `deepseek_web_search` 的工具。当你的机器人觉得需要联网查最新信息时，她会调用这个工具——插件用你配置的 DeepSeek API Key 请求 DeepSeek 的 **Anthropic 兼容 Messages 端点**，并声明官方内置的 `web_search` 服务端工具，由 DeepSeek 服务端完成检索，再把结果和来源链接交还给机器人，由她整理成回复。

也就是说，不需要再让机器人自己爬网页、自己写 Python 代码去搜了。

## 原理（重要，别改错端点）

DeepSeek 只在 **Anthropic 兼容端点**上执行服务端联网搜索：

```
POST https://api.deepseek.com/anthropic/v1/messages
x-api-key: sk-...
anthropic-version: 2023-06-01

{
  "model": "deepseek-v4-flash",
  "max_tokens": 4096,
  "messages": [{"role": "user", "content": [{"type": "text", "text": "<查询>"}]}],
  "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}]
}
```

搜索结果以结构化的 `web_search_tool_result` 内容块返回（每条结果是 `url` / `title` / `page_age`），摘录挂在引用它的 `text` 块的 `citations[]` 里，按 url 关联回来。

> ⚠️ **不要改用 OpenAI 兼容的 `/responses` 端点。** 按 DeepSeek 官方 Responses API 兼容性表，`web_search` / `file_search` / `code_interpreter` 等内置工具在 `/responses` 上被标注为 *Ignored*（原文：*Unsupported parameters are silently ignored and do not cause errors*）。它的失败形态极具迷惑性：**HTTP 200、零报错、模型回一句「我无法联网」**，插件拿到 0 条来源还以为搜索成功了——v1.1.0 就是踩了这个坑。
>
> 参考：[Using the Responses API](https://api-docs.deepseek.com/guides/responses_api/)（Tools 表）、[Using the Anthropic API](https://api-docs.deepseek.com/guides/anthropic_api/)（`server_tool_use` / `web_search_tool_result` 均为 Supported）。

v1.2.0 起，插件在解析响应后会**强制校验 `web_search_tool_result` 块是否存在**：缺失即判定搜索失败并明确回报，不会再静默降级。

## 安装

1. 把整个 `astrbot_plugin_deepseek_search` 文件夹复制到 AstrBot 的 `data/plugins/` 目录下。
2. 打开 AstrBot WebUI，进入「插件管理」，刷新/启用这个插件。
3. 在插件配置里填入你的 DeepSeek API Key（在 platform.deepseek.com 的 API keys 页面创建，`sk-` 开头）。填好后保存，并在插件管理里「重载插件」。
4. 确认你的聊天模型填的是 `deepseek-v4-flash` 或 `deepseek-v4-pro`。**注意：这里指的是插件配置里的 `model` 字段**——它是被插件单独调用来做搜索的，和 AstrBot 里聊天用的主模型相互独立（主模型甚至可以是别的厂商）。

## 使用

直接对机器人说类似这样的话即可：

- “帮我搜一下 xxx 的最新消息”
- “查一下洛天依官方账号最新的投稿”
- “现在 B 站热搜第一是什么”

如果她没主动调用搜索，可以明确补一句“联网搜一下”。

## 配置项说明

| 配置项 | 说明 | 默认值 |
| --- | --- | --- |
| `api_key` | DeepSeek API Key，必填 | 空 |
| `model` | 搜索时调用的 DeepSeek 模型名（`deepseek-v4-flash` 或 `deepseek-v4-pro`） | `deepseek-v4-flash` |
| `base_url` | API 地址，插件会自动补全成 `/anthropic/v1/messages`，一般不用改 | `https://api.deepseek.com` |
| `timeout_seconds` | 单次搜索超时（秒）。要等服务端检索+总结，建议不低于 30 | `30` |
| `max_output_tokens` | 搜索回答最大输出 token | `4096` |
| `max_uses` | 一次搜索允许服务端最多发起几次检索（每次都是一轮额外模型调用，会额外计费） | `5` |
| `sources_limit` | 返回给 AI 的来源条数上限 | `5` |

`base_url` 的补全规则：填 `https://api.deepseek.com`、`.../anthropic`、`.../anthropic/v1` 都会得到 `.../anthropic/v1/messages`；已经以 `/messages` 结尾则原样使用（方便指向自定义网关）。

## 搜不到？三步排查

1. **先跑探针**，一次判定哪条链路真的能搜：

   ```bash
   python probe_search.py --api-key sk-xxxxxxxx
   ```

   它同时打 Anthropic 链路和 Responses 链路，按响应里**有没有结构化检索结果块**判定（而不是看模型说了什么）。看到 `Anthropic Messages + web_search_20250305 ：可搜索 ✅` 就说明链路通。

2. **看日志关键字**。注册成功会打印：

   ```
   DeepSeek 原生联网搜索工具已注册（端点 https://api.deepseek.com/anthropic/v1/messages，模型 ...）
   ```

   搜到东西会打印 `DeepSeek 联网搜索完成，query=...，来源 N 条，端点 ...`。如果 `来源 0 条` 且模型在推脱「无法联网」，v1.2.0 会直接报错并在日志里给出 `响应块 [...]` 诊断，不会再假装成功。

3. **常见原因**：
   - `base_url` 指向了不支持服务端 `web_search` 的中转网关 → 换回官方 `api.deepseek.com`。
   - API Key 无效或余额不足 → 日志里会有 `HTTP 401/402` 及详情。
   - 网络到 `api.deepseek.com` 不通 → 日志会有重试记录和最终失败原因（插件会重试 3 次）。
   - 改完配置必须在插件管理里**重载插件**，工具是加载时注册的。

## 开发

```bash
python selftest.py       # 离线自检：解析逻辑 + 端点规整，不需要 astrbot/pydantic/网络
python probe_search.py   # 联网探针：验证两条端点的真实行为（需 API Key）
```

## 支持模型

- `deepseek-v4-flash`（默认）：轻量快速，日常够用。
- `deepseek-v4-pro`：Agent 能力更强，价格更高。在插件配置里把 `model` 改成 `deepseek-v4-pro` 即可。

两者都支持 DeepSeek 官方内置联网搜索。

## 注意事项

- 联网搜索会额外消耗 token。一次搜索连同搜索结果一起可能消耗 8000~15000+ token，如果用量大，注意留意 DeepSeek 的账户余额。
- 插件要求 AstrBot **4.5.7 及以上版本**（新 Agent 工具系统）。
- 如果没填 API Key，插件加载时会在日志里打警告，机器人调用工具时也会直接返回错误提示。
- 想确认插件是否注册成功，可以在 AstrBot 日志里搜索「DeepSeek 原生联网搜索工具已注册」。

## 更新日志

- v1.2.0：**修复搜不到任何东西**——改用官方真正执行服务端搜索的 Anthropic 兼容 `/anthropic/v1/messages` + `web_search_20250305`，不再使用会静默忽略内置工具的 `/responses`；新增「未真正搜索即报错」的硬校验、请求重试、来源摘要（`citations[].cited_text`）、`max_uses` 配置；新增 `probe_search.py` 与 `selftest.py`。
- v1.1.0：新增 `deepseek-v4-pro` 正式版支持（配置里可选）。
- v1.0.0：首个版本。
