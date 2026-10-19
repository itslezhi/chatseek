# ChatSeek

在自己的 Mac 上检索微信聊天，用自己配置的大模型 API 问答和总结，并查看回答引用的原文。

聊天同步、关键词搜索、SQLite 存储和 BGE 向量计算在本机完成。AI 问答会将问题、聊天证据及必要历史问答发送到**你配置的模型 API**；完整范围总结可能发送所选范围全部已同步文字。默认不连接项目作者的服务器，不需要邀请码、网站或自建网关，API 费用由用户与模型服务商结算。

> 当前是开发者预览版。微信读取流程面向 **Apple Silicon 原生 macOS**，开发环境验证记录为微信 4.1.15、macOS 26.4。跨机器首次接入仍需验证；Windows、Intel Mac 和其他微信版本尚未支持或验证。当前读取本机微信数据库，没有完整聊天历史文件导入器。

## 功能

- 按好友备注、昵称、群名或内部 ID 精确选择一个会话并只读同步，支持切换会话与独立索引。
- 本地关键词搜索、512 维 BGE/FAISS 向量检索与可选混合检索。
- 带原文引用的问答、连续追问、日期筛选和完整范围总结；范围过大时分批阅读。
- 问答历史、新对话、归档、最近删除，以及图片/视频抽帧预览和可选视觉识别。
- 浏览器界面、命令行和可选的 Mac `.app` 启动器。

## 从源码启动

需要 Apple Silicon Mac、Python 3.12、联网安装依赖。当前二进制依赖要求 macOS 14 或更新；这不代表所有系统/微信版本均已验证。微信首次接入还需要 Xcode Command Line Tools 中的 `/usr/bin/lldb` 和系统 Python。

希望省去单独下载向量模型，可在 [GitHub Releases](https://github.com/itslezhi/chatseek/releases/latest) 下载 **ChatSeek-source-with-model.zip**，解压后进入 ChatSeek 目录，再从下面的创建虚拟环境步骤开始。这个包包含公共模型，仍需联网安装 Python 依赖；普通 Git 克隆不包含模型权重。

```sh
git clone https://github.com/itslezhi/chatseek.git
cd chatseek
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
.venv/bin/python wechat_rag.py web
```

浏览器打开 <http://127.0.0.1:8765>，进入逐步配置向导。每次只显示当前步骤，检查通过后才能点击“下一步”：

1. **运行环境**：检查 Apple Silicon、macOS、微信和 LLDB 是否就绪。
2. **微信接入**：先在原版微信登录一次，选择检测到的账号。正常退出微信，阅读并勾选初始化说明，再获取密钥；初始化会复制微信、重签临时副本并通过 LLDB 启动，原版签名保持不变。已有密钥时直接检查数据库只读访问，不重复初始化。如需系统权限，源码启动时通常为终端授权，按提示重启。
3. **模型 API**：填写通用的 **Base URL / Model Name / API Key**，点击“验证并保存”。验证只发送一条不含聊天的简短 JSON 测试请求，服务商可能计费；失败时保留原有配置。已有 Key 可以留空沿用，页面不会回显密钥。
4. **本地检索**：含模型包会自动检查随包模型，已有缓存直接复用；普通源码缺失模型时下载一次。准备在后台进行，结果显示在本步骤内，不会出现在“选择会话”后方。

完成向导后，在左侧输入好友完整昵称、备注或群名，选择并同步会话。初始化结束后请回到原版微信。

初始化不会随网页打开自动执行，不需要 `sudo`，也不要求关闭 SIP。若已有初始化目录而流程失败，请保留目录并检查，不要直接覆盖密钥。不要将真实微信数据库、聊天、密钥、截图或配置文件附到 Issue。

如果系统未安装命令行开发工具，可在终端执行 `xcode-select --install`，按 Apple 提示完成安装。源码运行不要求你购买 Apple Developer 会员；下载预先打包的 `.app` 则可能遇到系统的来源验证提示。

## 配置自己的大模型

使用兼容 **Chat Completions** 的服务，Base URL 可带 API 路径前缀；程序自动追加 `/chat/completions`，也接受粘贴完整端点地址。远程端点要求 HTTPS，本机 `127.0.0.1` / `localhost` 可用 HTTP。

```dotenv
LLM_API_KEY=填写自己的密钥
LLM_BASE_URL=https://your-provider.example/v1
LLM_MODEL=your-model-name
LLM_VISION_MODEL=
LLM_CONTEXT_TOKENS=128000
LLM_MAX_OUTPUT_TOKENS=8192
LLM_JSON_MODE=true
```

- 对其他服务商，填写其控制台给出的 Base URL 和实际模型 ID。支持的能力取决于服务商，不承诺所有模型即填即用。
- 文本模型需要能按指令输出有效 JSON 查询计划；默认发送 `response_format=json_object`。服务商不接受该参数时可将 `LLM_JSON_MODE=false`，模型仍须返回有效 JSON。
- 模型应支持普通 Chat Completions 消息；网页版默认请求流式回答。原生 Anthropic Messages、Responses 等其他协议没有适配，需选择兼容端点。
- 图片/视频画面识别需要在 `LLM_VISION_MODEL` 中明确配置支持 `image_url` 输入的模型。不填时媒体保留类型标记，不会把未识别内容当作已理解。语音尚不支持。
- 本机兼容服务如果不校验 Key，可以填写 `local`；本文没有提供或安装本地回答模型。Embedding 模型与回答模型相互独立。
- 旧版 `DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL` 等配置仍兼容，新的 `LLM_*` 配置优先。环境变量会覆盖同名文件配置；用网页保存后还需避免旧的环境变量覆盖新设置。

普通问题通常包含规划与回答至少两次模型请求，媒体识别和分批总结还会增加调用。首次模型请求可能发生在本地检索之前，未找到证据也可能产生费用。程序没有为自带 Key 用户设置测试名额和次数。

## 本地数据与隐私

| 操作 | 内容去向 |
|---|---|
| 同步聊天、浏览历史、关键词搜索 | 本机 |
| 建立向量索引 | 本机 CPU；首次需下载公共模型 |
| 问题理解和追问规划 | 问题、最近最多 6 轮问答等发送至配置的模型端点 |
| 具体问题回答 | 检索原文、相邻上下文、发送者信息和时间等发送至配置端点 |
| 完整范围总结 | 该范围全部已同步非系统消息，必要时分批发送 |
| 媒体识别 | 图片或视频抽样画面发送至配置的视觉模型端点 |

当前没有自动脱敏，也不能逐次预览完整外发请求。使用前应确认你对相关记录有处理权限，并理解模型服务商对数据的处理方式。

源码运行的数据在 `.private/`，个人模型配置可位于项目 `.env` 或 `.private/.env`；可通过 `CHATSEEK_DATA_DIR` 使用独立目录。`.app` 默认使用 `~/Library/Application Support/ChatSeek/`。本机保存聊天原文、问答、媒体描述、缓存和微信数据库密钥；目前使用普通 SQLite 和权限受限文件，**未作应用层加密**。

“最近删除”只改变问答状态，不会清除原始聊天索引。源码版需要先停止网页、自动同步及模型进程，再自行管理数据目录；Mac 启动器菜单提供清除 ChatSeek 聊天索引、问答及媒体的入口，保留初始化密钥、模型及账号配置。所有操作均不删除原版微信数据库。

网页只监听 `127.0.0.1`，不应将该网页服务直接暴露到公网。应用内“访问权限”确认不等于 macOS 系统权限。关闭浏览器不会自动停止源码后台；终端 Ctrl+C 停止网页服务，独立开启的自动同步可用下列 `stop` 命令停止。

## 命令行

```sh
.venv/bin/python wechat_rag.py select '好友完整备注或群名'
.venv/bin/python wechat_rag.py history --limit 30
.venv/bin/python wechat_rag.py search '会议'
.venv/bin/python wechat_rag.py ask '总结最近的聊天内容'
.venv/bin/python wechat_rag.py index
.venv/bin/python wechat_rag.py start --interval 5
.venv/bin/python wechat_rag.py stop
```

同名联系人需要根据返回候选指定 `--id`。当前仅同步选中的一个会话，不能自动补齐手机上未迁移到电脑的记录。完整范围总结覆盖的是本机已同步记录，媒体标记不代表理解了图片、视频或语音。引用编号检查不能证明所有事实推理都正确，请查看原文核对。

## 可选：构建 Mac 应用

```sh
.venv/bin/python -m pip install -r requirements-build.txt
.venv/bin/python scripts/build_macos.py
```

生成 `dist/ChatSeek.app` 和 ZIP。双击应用会启动本地后台并打开浏览器，Dock 中退出会管理后台进程。默认为临时本机签名，未完成 Developer ID 签名和 Apple 公证，也未承诺跨机首次接入成功。开发者证书就绪后可使用 `--identity` 和 `--notary-profile` 构建正式分发包。

可用 `--embedding-model /完整模型目录` 将经过校验的公共向量模型装入 App，首次运行无需单独下载。含模型源码包的构建方法和模型许可见 [models/README.md](models/README.md)。所有发行包均排除私人配置、聊天和微信密钥。

`beta_gateway/` 是可选的自托管补贴额度模块；公开版默认禁用，普通用户无需部署它。只有明确设置 `CHATSEEK_USE_BETA_GATEWAY=1` 并自行配置网关凭证时才启用旧网关路径；这与默认自带 Key 的使用方式分开。

## 开发与测试

```sh
.venv/bin/python -m unittest discover -s tests
node tests/test_stream_transport.js
node tests/test_setup_flow.js
```

测试使用合成聊天及模拟模型响应，不会初始化微信或调用真实付费模型。`scripts/check_threaded_rag.py` 和 `scripts/check_macos_bundle.py` 是额外的真实本地依赖/打包检查；公共模型未缓存时可能需要下载。无源码签名、无公网服务器即可开展开发者试用。

反馈问题时请遵守 [安全与隐私说明](SECURITY.md)，使用虚构材料复现。贡献说明见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可与致谢

项目采用 [MIT License](LICENSE)。`vendor/wechat_local/` 保留原有 MIT 许可证及 [来源记录](vendor/wechat_local/provenance.json)。BGE 模型、Python 依赖和其他第三方组件遵循各自许可证；微信为其权利人的产品，本项目与微信没有隶属或背书关系。
