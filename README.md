# 微信聊天记忆助手

面向一个明确指定的微信好友：从 Mac 本地数据库只读同步文字消息，在本机检索相关聊天，再通过 DeepSeek 生成带原文引用的答案。

当前已在这台 Mac 验证：微信 4.1.15（270135）、macOS 26.4、Apple Silicon。初始化完成，目标好友保存在本机 `.private/target.json`。配置和聊天记录均被 Git 忽略。不要将 `.private` 或 `.env` 打包分享。

## 使用

在这个项目目录执行：

```sh
# 查看记录条数、最近同步时间、API 是否配置（不输出密钥）
.venv/bin/python wechat_rag.py status

# 启动后台同步，每五秒检查一次；已运行时会拒绝重复启动
.venv/bin/python wechat_rag.py start --interval 5

# 本地检索并显示消息原文，不调用云端 API
.venv/bin/python wechat_rag.py search '聚餐几点'

# 配置密钥后进行问答
.venv/bin/python wechat_rag.py ask '总结最近的聊天内容'

# 查看最近记录，包括表情包、图片等尚未识别的消息标记
.venv/bin/python wechat_rag.py history --limit 30

# 可选：输出全部证据，或保留原始 JSON 供其他程序读取
.venv/bin/python wechat_rag.py ask '总结最近的聊天内容' --sources
.venv/bin/python wechat_rag.py search '健身' --json

# 停止本项目的后台同步
.venv/bin/python wechat_rag.py stop
```

后台同步在本机进程中运行。Mac 重启后需要重新执行 `start`；它没有被设置为系统开机启动项。同步日志只有计数和错误类型，不包含消息正文或密钥。

## DeepSeek 配置

1. 在 <https://platform.deepseek.com/> 登录并创建 API Key。
2. 在本机 `.env` 文件的 `DEEPSEEK_API_KEY=` 后填入密钥并保存。
3. 执行上面的 `ask` 命令。

默认模型是官方文档当前列出的 `deepseek-flash`，可以在 `.env` 的 `DEEPSEEK_MODEL` 中调整。客户端固定调用 `https://api.deepseek.com/chat/completions`，每次问答只提交问题和检索到的相关文字片段；后台同步与本地搜索不调用模型。

API 文档：<https://api-docs.deepseek.com/>。没有密钥时可以完成本地同步和检索，但不能生成回答。`ask` 默认显示可读的总结及引用原文，不再把整个 JSON 直接输出到终端。后台同步只记录消息，调用 `ask` 时才生成总结。

## 实现

- 原始微信数据库：SQLCipher 只读连接，并读取已提交的 WAL 新消息。
- 本地存储：SQLite。消息及分库同步进度在同一事务中提交；按来源库和消息 ID 去重。
- 联系人隔离：索引绑定账号目录及唯一联系人 ID，同名联系人不会自动选择第一个。
- 检索基线：中文双字词、英文词的 FTS5/BM25 检索；保留一小时内的相邻消息，帮助保留更正和上下文。总结请求选择最近的有限条文字记录。
- 生成：将检索证据传入 DeepSeek，并检查回答中的引用编号是否对应提供的原文。编号有效不等于模型理解必然正确，界面或调用方应展示原文供核对。

这版没有向量检索和网页界面。先把自动同步、检索与生成闭环跑通，再用实际问题评测是否需要增加 embedding 和 rerank。

## 范围与限制

- 只能读取电脑上已经存在的记录，不会自动补齐手机中未迁移到电脑的历史。
- 当前只理解文字。总结和相邻上下文中会保留图片、语音、表情包等的类型标记；`history` 也展示系统消息。原图片、动态表情和音频尚未解码或展示，类型标记不代表已经理解其内容。搜索“表情包”“图片”等可按消息类型查找。
- 持续同步新增消息，并重新检查最近 200 条记录的内容变化；尚未覆盖任意历史删除、所有撤回语义或数据库重建后的自动迁移。
- 账号目录、数据库盐值变化或消息 ID 回退会中止本轮同步并记录错误，避免混入其他账号或跳过消息。
- 新消息采集的底层能力已经在模拟加密 WAL 上验证；真实好友新增消息的实时验收需等该会话产生新消息。
- 此程序没有微信消息发送功能。

## 开发与测试

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python vendor/wechat_local/sqlcipher_probe.py self-test
.venv/bin/python -m unittest discover -s tests -v
```

这台 Mac 的 Python 没有默认 CA 文件时，安装命令可加 `--cert /etc/ssl/cert.pem`，继续正常验证 HTTPS 证书。

`vendor/wechat_local` 来自 MIT 许可的本地读取工具，原始来源、版本及哈希见 `provenance.json`。本项目为 Python 3.12 调整了 Zstandard 解压，并从项目内的 `sqlcipher3` wheel 加载 SQLCipher。初始化只重新签名临时副本；原版微信签名已验证保持不变。日常同步复用本机已保存的密钥，不重复执行初始化。
