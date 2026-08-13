注 本项目大部分由Ai完成

# AstrBot DeepSeek 原生联网搜索插件

给你的 AstrBot 机器人接上 DeepSeek 官方内置的联网搜索能力。

## 它做了什么

插件会在 AstrBot 的 AI 工具列表里注册一个名为 `deepseek_web_search` 的工具。当你的机器人觉得需要联网查最新信息时，她会调用这个工具——插件用你配置的 DeepSeek API Key 请求 DeepSeek 官方的 `/responses` 接口，并带上官方内置的 `web_search` 工具，搜完把结果和来源链接交还给机器人，由她整理成回复。

也就是说，不需要再让机器人自己爬网页、自己写 Python 代码去搜了。

## 安装

1. 把整个 `astrbot_plugin_deepseek_search` 文件夹复制到 AstrBot 的 `data/plugins/` 目录下。
2. 打开 AstrBot WebUI，进入「插件管理」，刷新/启用这个插件。
3. 在插件配置里填入你的 DeepSeek API Key（在 platform.deepseek.com 的 API keys 页面创建，`sk-` 开头）。填好后保存，并在插件管理里「重载插件」。
4. 确认你的聊天模型填的是 `deepseek-v4-flash` 或 `deepseek-v4-pro`（两者都支持官方联网搜索）。

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
| `model` | 调用的 DeepSeek 模型名（`deepseek-v4-flash` 或 `deepseek-v4-pro`） | `deepseek-v4-flash` |
| `base_url` | API 地址，一般不用改 | `https://api.deepseek.com` |
| `timeout_seconds` | 单次搜索超时（秒） | `30` |
| `max_output_tokens` | 搜索回答最大输出 token | `4096` |
| `sources_limit` | 返回给 AI 的来源条数上限 | `5` |

## 支持模型

- `deepseek-v4-flash`（默认）：轻量快速，日常够用。
- `deepseek-v4-pro`：V4 Pro 正式版（模型版本 `DeepSeek-V4-Pro-0813`），Agent 能力更强，价格更高。在插件配置里把 `model` 改成 `deepseek-v4-pro` 即可。

两者都支持 DeepSeek 官方内置联网搜索。

## 注意事项

- 联网搜索会额外消耗 token。一次搜索连同搜索结果一起可能消耗 8000~15000+ token，如果用量大，注意留意 DeepSeek 的账户余额。
- 插件要求 AstrBot **4.5.7 及以上版本**（新 Agent 工具系统）。
- 如果没填 API Key，插件加载时会在日志里打警告，机器人调用工具时也会直接返回错误提示。
- 想确认插件是否注册成功，可以在 AstrBot 日志里搜索「DeepSeek 原生联网搜索工具已注册」。

## 更新日志

- v1.1.0：新增 `deepseek-v4-pro` 正式版支持（配置里可选）。
- v1.0.0：首个版本。
