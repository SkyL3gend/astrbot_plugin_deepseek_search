# AstrBot DeepSeek 网页搜索与抓取

使用 2026-10-17 开放的 **DeepSeek 自研搜索 API**，为 AstrBot 提供网页搜索和正文读取。插件直接返回资料，由当前聊天模型整理回答；不再经由 `/responses` 调用 Pro 生成摘要，也不要求聊天模型属于 DeepSeek。

## 安装与使用

1. 在 AstrBot 插件管理中从本仓库链接安装，或将仓库放在 `data/plugins/astrbot_plugin_deepseek_search/`。
2. 在插件配置中填写 DeepSeek API Key，保存并重载。
3. 确认当前会话配置允许此插件，当前人格允许对应工具，主聊天模型支持工具调用。
4. 尝试“搜索 DeepSeek 2026 年 10 月的搜索 API 公告，附来源”；需要阅读全文时，让模型调用抓取工具。

| 工具 | 用途 | 参数 |
| --- | --- | --- |
| `deepseek_web_search` | 调用 `POST /v1/tools/search`，返回标题、来源、长摘要、日期、站点及评分 | `query` 必填，`ori_question` 可选，均为 1～128 个字符 |
| `deepseek_web_fetch` | 调用 `POST /v1/tools/fetch`，返回指定页面 Markdown 正文 | `url` 必填，HTTP/HTTPS，最多 8192 字符，不允许用户名或密码 |

搜索语法原样传递，支持 `site:`、`-site:`、排除词、精确短语、`lang:`、`after:` 和 `before:`。例如：

```text
DeepSeek 搜索 API site:api-docs.deepseek.com after:2026-10-01
```

搜索无结果时会明确告知。搜索结果按 URL 去重，优先返回适合模型阅读的 `summary`，缺失时回退到 `snippet`；未知日期不补造。网页属于外部资料，工具描述提醒模型不要执行网页中的指令。

## 从 1.x 升级到 2.0.0

本次将底层接口从 Responses 内置搜索更换为独立 Search/Fetch API：

- 保留工具名 `deepseek_web_search` 和必填参数 `query`，已有调用方式继续有效。
- 保留 `api_key`、`base_url`、`timeout_seconds` 和 `sources_limit`。根地址以及以 `/v1` 结尾的地址均可使用；第三方地址须支持新的两个接口。
- `model` 和 `max_output_tokens` 已移出配置页面，旧配置即使保留这些键也会被忽略，不会自动改写配置。**无须切换 Pro，旧 Flash 配置不阻止搜索。**
- `sources_limit` 现在直接控制搜索条数 `count`，有效范围 1～50；旧值为 0、超出范围或无效时回退到 5。
- 新增 `deepseek_web_fetch` 工具，请在需要使用它的人格中授权。
- 搜索结果直接交给主模型，不再由插件额外请求 DeepSeek 生成模型。Search/Fetch 请求费与主聊天模型处理结果的 token 费用分开计算。

## 配置

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `api_key` | 空 | DeepSeek API Key，必填 |
| `base_url` | `https://api.deepseek.com` | API 基础地址，可带 `/v1` |
| `timeout_seconds` | `30` | 单次请求总超时，有限正数；升级保留原值 |
| `sources_limit` | `5` | 搜索返回条数，整数 1～50 |
| `need_content` | `false` | 搜索同时返回正文；默认仅取摘要，必要时再抓取 |
| `localization` | 空 | 可选 IPv4/IPv6 地址，用于优先返回本地结果；不会自动采集用户 IP，仅随搜索请求发送 |
| `content_max_chars` | `12000` | 每个网页正文返回字符上限，整数 1～200000；截断会标注 |

无效数值回退到默认值；正文上限按单个结果计算，开启正文并增加搜索条数会增大主模型上下文用量。未填密钥时工具仍注册，但调用只返回配置提示。

`base_url` 不允许包含用户名、密码、查询参数或 URL 片段；第三方网关可保留路径前缀。无效地址在发出请求前返回提示。Search 开启正文后，每条结果会附带 `truncated` 布尔值；Fetch 同样提供该字段，模型可以据此判断是否拿到了完整正文。

## 计费与服务限制

Search 和 Fetch 按成功请求次数计费，不按返回条数或 token 计费；主聊天模型读取结果仍可能产生 token 费用。空搜索结果、失败和超时的计费规则以 [官方网页搜索与抓取指南](https://api-docs.deepseek.com/zh-cn/guides/search/) 为准。

官网针对 Harness/Claude Code/Codex 服务端工具调用的优惠不应视为本插件直接调用接口的免费承诺。价格、优惠和账号限速可能调整，使用前请查看上述官方指南。

抓取结果受目标网页可用性和访问策略影响，无法保证登录后页面或实时热榜可读取。HTTP 429、超时及其他错误均返回提示；插件不自动重试或自动抓取所有搜索结果，以免隐式增加请求次数。用户或模型可按提示稍后重试。

## AstrBot 兼容与开发验证

保留 AstrBot `>=4.5.7` 的接口基线；本次对照官方 4.29.1 源码（`fc6b326`）维护，未对所有旧版本运行验证。使用文档支持的 `FunctionTool`、`ContextWrapper`、`Context.add_llm_tools()`，由 `Star` 子类及 `metadata.yaml` 自动加载，不使用已弃用的 `@register`。不依赖特定消息平台。

请求结束或取消时关闭 HTTP 会话；错误提示不回显上游错误正文或密钥。无持久连接和后台任务需要卸载清理。

```sh
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

测试模拟 AstrBot 宿主和 HTTP 响应，不消耗真实 API 额度。上线前还需在实际 AstrBot 中验证加载、重载、人格授权以及有效 Key 的 Search/Fetch 调用。

## 官方维护依据（2026-10-20 核对）

- [DeepSeek 网页搜索与抓取指南](https://api-docs.deepseek.com/zh-cn/guides/search/)
- [AstrBot 插件开发指南](https://docs.astrbot.app/dev/star/plugin-new.html)
- [AstrBot AI 工具接口](https://docs.astrbot.app/dev/star/guides/ai.html)
- [AstrBot 注册装饰器弃用说明](https://github.com/AstrBotDevs/AstrBot/blob/fc6b326/astrbot/core/star/register/star.py)

## 更新日志

- **2.0.1**：复核官方插件与 Search/Fetch 接口；新增可选本地化 IP 配置；拒绝会造成错误路由的 API 基础地址和非法网页端口、空白或控制字符；修复布尔超时配置；补齐可空参数 schema、搜索正文截断标志及 Pydantic 运行依赖。
- **2.0.0**：切换独立 Search API，新增 Fetch 工具；移除对 Pro/生成模型的依赖，迁移旧配置；更新参数校验、正文截断、来源去重、错误处理和回归测试。此前本地 1.2.0 草稿未发布，由此版本取代。
- **1.1.0**：新增 `deepseek-v4-pro` 配置选项（旧 Responses 方案）。
- **1.0.0**：首个版本。

本项目大部分由 AI 辅助完成。许可证：MIT。
