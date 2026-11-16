# 在 DeepSeek Harness 中使用 ChatSeek

这是可直接安装的原生 Host 插件，提供 7 个 `chatseek_*` 工具。插件连接同一台电脑上的 ChatSeek 服务，不在安装时读取微信、不自动初始化或安装 Python、不需要再填写一份模型 API Key。支持 Windows 和 macOS；微信接入能力及 Windows 实验性限制沿用 ChatSeek。

## 安装

需要 DeepSeek Harness **0.2.2-rc.1 或更新的 0.2.x 版本**、Node.js **24.9+**，并已按项目 README 启动 ChatSeek。官方运行时兼容测试固定使用 0.2.2-rc.1；Harness 仍是开发预览版，后续 API 变化可能需要更新插件。

在 Harness 的 **Plugins → Add plugin** 输入：

```text
github:itslezhi/chatseek
```

或在终端执行（Windows PowerShell / macOS Terminal 均可）：

```sh
npx @deepseek-ai/dsh plugin --profile web add github:itslezhi/chatseek
```

使用其他 profile 时，把 `web` 换成实际 profile。安装后启用 **ChatSeek 微信记忆**；若 Harness 提示需要重启，重启对应 profile。本地开发也可在 Add plugin 选择项目根目录（含 `package.json`），或使用 `dsh plugin --profile web add /absolute/path/to/chatseek`。

插件安装不会启动 ChatSeek 后端。先在 ChatSeek 浏览器页面完成微信接入、选择好友，保持服务运行在 `http://127.0.0.1:8765`。Windows 可运行 `start_windows.cmd`。Harness 和 ChatSeek 必须运行在同一台电脑、同一网络环境中：Docker、WSL、远程服务器中的 `127.0.0.1` 不指向 Windows 桌面的 ChatSeek。

## 使用

可以对 Harness 说：

> 检查 ChatSeek 状态，读取当前好友最近 50 条消息，找出上次约定的时间并注明依据。

> 在 ChatSeek 选择“小明”，打开持续同步，再把本机能读取的全部聊天历史导出成 HTML。

| 工具 | 行为 |
|---|---|
| `chatseek_status` | 返回初始化状态、当前会话 context、访问模式和本机页面地址 |
| `chatseek_select` | 按完整昵称、备注或群名选择；重名时返回候选，不自动猜选 |
| `chatseek_history` | 读取最近 1–500 条已缓存消息，默认 30 条 |
| `chatseek_search` | 检索已有聊天证据，由 Harness 的模型回答 |
| `chatseek_sync` | 对指定 context 同步一次 |
| `chatseek_watch` | 启停每 5 秒自动同步；跟随全局选中的会话 |
| `chatseek_export` | 同步后将全部可读取历史流式保存为 HTML、TXT 或 JSON 文件 |

需要指定会话的工具必须使用 status/select 返回的 `context`。浏览器或另一个 Harness 会话切换好友后，旧 context 会失败；重新检查并确认目标，不能自动重试到另一个人的聊天。选人和后台同步是 ChatSeek 的共享状态。卸载插件不会停止用户开启的 ChatSeek 后台同步；请用 `chatseek_watch` 关闭或在 ChatSeek 页面关闭。

### 访问确认和数据去向

默认“询问请求”模式下，选人、同步、启动自动同步和导出会返回访问确认提示。请在 ChatSeek 页面完成对应操作；需要由 Harness 连续执行这些操作时，用户可亲自在 ChatSeek 页面切换到“完全访问”。插件不传 `access_approved`、不修改访问模式、不给模型提供提权参数。Harness 自身的工具权限策略仍然生效。

历史和检索读取已经授权建立的本机缓存。**返回的消息会进入 Harness 会话，并发送给 Harness 配置的模型服务商**；请按自己的隐私需求选择本地或远程模型。插件不调用 ChatSeek 的回答模型，也不会上传完整导出文件。安装包只包含桥接代码、说明、图标和元数据，不包含聊天数据库、密钥、模型权重或 Python 后端。

### 导出位置与限制

文件默认保存在当前用户的 `ChatSeek/exports` 目录（Windows 通常为 `C:\Users\用户名\ChatSeek\exports`，macOS 为 `/Users/用户名/ChatSeek/exports`）。每次生成唯一文件名，成功后工具只返回绝对路径和字节数，不将全文塞进模型上下文。Unix 文件权限为 0600；Windows 沿用当前用户目录的 ACL。

导出覆盖本机可读取的历史，无法补齐手机独有、已删除或未迁移的记录，不打包图片/视频等媒体原文件。单次导出上限 512 MiB，请求默认超时 120 秒；超过上限时用 ChatSeek 浏览器导出。失败、超时或取消会清理未完成文件。取消客户端请求不会撤销已经完成的后端同步，也不能保证中断正在进行的 Python 数据库操作。

## 可选配置

插件读取所在 Harness 插件条目的 `config`。使用 Harness 的配置编辑功能修改，插件本身不改写 profile：

```yaml
baseUrl: http://127.0.0.1:8765
exportDirectory: 'C:\Users\YourName\ChatSeek\exports'
timeoutMs: 120000
```

`exportDirectory` 可省略，macOS 请用对应绝对路径；`timeoutMs` 允许 100–600000 毫秒。`baseUrl` 只接受数值 IPv4 回环地址 `127.0.0.1`，可换端口，不接受远程地址、路径、凭据或重定向。没有联网安装脚本或自动打开数据库的启动钩子。

## 验证与排错

在项目根目录执行 `npm ci --ignore-scripts`、`npm test`，使用真实 Cordis、Harness tools 和 native executor，加上合成 HTTP 数据验证注册/卸载、调用、访问拒绝、会话切换、导出、取消和大小限制；不读取真实微信。`npm pack --dry-run` 可核对插件文件清单。

连接失败：确认 ChatSeek 页面能在 Harness 所在电脑打开；首次接入提示：在 ChatSeek 页面完成接入；访问确认提示：按上述访问模式操作；工具不存在：确认当前 profile 已启用插件并按提示重启。初始化与本机微信验证仍由 ChatSeek 负责，安装插件不会跳过它们。

参考：[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)、[官方文档](https://deepseek-harness.github.io/deepseek-harness/)。
