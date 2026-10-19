# AstrBot DeepSeek 网页搜索与抓取

通过 DeepSeek 搜索引擎为 AstrBot 提供网页搜索与正文抓取，由聊天模型结合搜索资料生成回答并引用来源。

## 安装

1. 准备 AstrBot **4.5.7 及以上版本**，并配置支持工具调用的聊天模型。
2. 在 AstrBot 插件管理中通过仓库链接安装：
   ```text
   https://github.com/SkyL3gend/astrbot_plugin_deepseek_search
   ```
3. 在插件配置中填写 [DeepSeek API Key](https://platform.deepseek.com/)，保存并重载插件。
4. 在当前会话配置中启用插件，并在使用的人格中允许 `deepseek_web_search` 和 `deepseek_web_fetch` 工具。

## 使用

直接向机器人提出搜索或阅读网页的请求，例如：

- “搜索 DeepSeek 的最新公告，附上来源链接。”
- “搜索 AstrBot 插件开发教程，优先参考官方文档。”
- “阅读这个网页，整理主要内容：https://example.com/article”

模型会根据请求选择工具。如果没有调用，可以明确要求“联网搜索”或“读取网页正文”。

| 工具 | 功能 |
| --- | --- |
| `deepseek_web_search` | 搜索网页，返回标题、摘要、来源链接和日期等信息 |
| `deepseek_web_fetch` | 读取指定网页的 Markdown 正文 |

搜索支持站点、语言和日期过滤，例如：

```text
AstrBot 插件 site:docs.astrbot.app lang:zh after:2026-01-01
```

搜索词最长 128 个字符。抓取地址须为 HTTP/HTTPS 链接，最长 8192 个字符，且不能包含用户名或密码。

## 配置

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `api_key` | 空 | DeepSeek API Key，必填 |
| `base_url` | `https://api.deepseek.com` | API 基础地址，可带 `/v1`；使用第三方网关时须支持 Search/Fetch 接口 |
| `timeout_seconds` | `30` | 单次请求超时秒数，须为正整数 |
| `sources_limit` | `5` | 搜索返回条数，整数 1～50 |
| `need_content` | `false` | 搜索时同时返回网页正文；关闭时返回摘要，可按需调用抓取工具 |
| `localization` | 空 | 可选 IPv4/IPv6 地址，用于优先返回本地结果；留空不指定 |
| `content_max_chars` | `12000` | 每个网页正文的字符上限，整数 1～200000；超出时截断并标注 |

修改配置后保存并重载插件。`base_url` 不能包含用户名、密码、查询参数或 URL 片段。

## 注意事项

- Search/Fetch 请求会产生 API 费用，聊天模型处理搜索结果也可能产生 token 费用。价格及限速规则请查看 [DeepSeek 官方指南](https://api-docs.deepseek.com/zh-cn/guides/search/)。
- 增加搜索条数或开启正文会增大聊天模型的上下文用量，请按需设置。
- 网页抓取受目标站点的可用性和访问策略影响，登录后页面或动态内容可能无法读取。
- 搜索无结果时可调整关键词、站点或日期范围。遇到限速或超时，请稍后重试；插件不会自动重试。
- 如工具无法调用，请检查插件及人格的工具授权、模型的工具调用支持，以及 API Key 和账户余额。
- `localization` 中填写的 IP 会随搜索请求发送给 DeepSeek；插件不会自动获取用户 IP。

## 许可证

[MIT](LICENSE)
