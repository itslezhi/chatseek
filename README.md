# ChatSeek

在自己的 Mac 或 Windows 电脑上检索微信聊天，用自己配置的大模型 API 问答和总结，并查看回答引用的原文。

聊天同步、关键词搜索、SQLite 存储和 BGE 向量计算在本机完成。AI 问答会将问题、聊天证据及必要历史问答发送到**你配置的模型 API**；完整范围总结可能发送所选范围全部已同步文字。默认不连接项目作者的服务器，不需要邀请码、网站或自建网关，API 费用由用户与模型服务商结算。

> 当前是开发者预览版。微信读取流程面向 **Apple Silicon 原生 macOS**，开发环境验证记录为微信 4.1.15、macOS 26.4。新增 **Windows 10/11 x64 + 微信 4.x** 的实验性接入。Windows 的进程扫描及真实账号连续同步尚待实机验证，不能将合成测试视为所有微信版本都兼容；微信 3.x、Intel Mac 暂不支持。当前读取本机微信数据库，没有完整聊天历史文件导入器。

## 功能

- 按好友备注、昵称、群名或内部 ID 精确选择一个会话并只读同步，支持切换会话与独立索引。
- 本地关键词搜索、512 维 BGE/FAISS 向量检索与可选混合检索。
- 带原文引用的问答、连续追问、日期筛选和完整范围总结；范围过大时分批阅读。
- 问答历史、新对话、归档、最近删除，以及图片/视频抽帧预览和可选视觉识别。
- 浏览器界面、命令行、Mac `.app` 和 Windows 启动脚本 / 可构建的 `.exe`。
- 当前好友或群聊的全部本机历史导出为 HTML、TXT、JSON；不受页面 500 条显示上限限制。

## Windows 启动与持续同步

需要 Windows 10/11 x64、64 位 Python 3.12、微信 4.x 和联网安装依赖。克隆或解压项目后双击 `start_windows.cmd`，首次创建虚拟环境并安装固定版本依赖，随后自动打开网页。保持控制台窗口运行；按 Ctrl+C 正常退出。源码依赖包含 Windows SQLCipher wheel，无需安装 LLDB 或 Xcode。

1. 在 Windows 微信登录你要使用的账号，保持登录，并打开要同步的会话。
2. 在配置向导检查环境。自动发现 `Documents\xwechat_files`（含系统重定向的文档目录）和注册表记录的数据位置；自定义位置可直接填写 `db_storage` 的完整路径，例如 `D:\WeChat\xwechat_files\账号目录\db_storage`。也可通过 `CHATSEEK_WECHAT_DIR` 指定搜索根目录。
3. 确认账号后勾选 Windows 只读接入说明，点击“开始首次接入”。程序仅扫描**当前 Windows 用户**的 `Weixin.exe` / `WeChat.exe` 可读内存，寻找 SQLCipher 原始密钥缓冲区，并实际验证所选账号的联系人及全部消息分片；不注入代码、不改微信文件、不保存内存转储。未全部验证则不保存凭据，可以重试。扫描最多约 120 秒。
4. 点击验证读取，继续配置自己的模型 API 和本地向量模型。已保存的接入凭据在下次启动复用。
5. 输入好友完整昵称或备注，选择会话，再开启右上角自动同步；每 5 秒跟随当前选中的会话更新本机记录。

如果提示无法读取进程，先确认微信和 ChatSeek 使用同一 Windows 用户及权限级别。若没有发现全部密钥，打开目标会话后重试；微信升级可能改变内存布局或数据库结构，此时会显示失败，不能保证自动适配。不会要求关闭安全软件或系统保护。媒体解码沿用现有实现，Windows 各版本媒体格式仍需实测。

Windows 启动器默认数据目录是 `%LOCALAPPDATA%\ChatSeek`；直接运行 `wechat_rag.py web` 默认仍使用项目 `.private`。请使用同一种启动方式，或通过 `CHATSEEK_DATA_DIR` 显式指定同一目录，以免误以为需要重新初始化。Windows 文件访问由 NTFS ACL 控制，不使用 Unix 权限位作为判断依据。

手动启动（PowerShell）：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -X utf8 chatseek_desktop.py
```

## 安装到 DeepSeek Harness

在 Harness 的 **Plugins → Add plugin** 输入 `github:itslezhi/chatseek`，或执行：

```sh
npx @deepseek-ai/dsh plugin --profile web add github:itslezhi/chatseek
```

提供聊天状态、选人、历史、搜索、单次同步、持续同步和完整历史导出 7 个工具。需先在同一台电脑启动 ChatSeek 并完成微信接入；插件不会自动初始化微信。需要 Harness 0.2.2-rc.1+（0.2.x）及 Node.js 24.9+。访问确认仍由 ChatSeek 控制，检索出的消息会进入 Harness 会话及其配置的模型。完整导出保存在本地文件中。

详细安装、权限、导出位置和配置见 [DeepSeek Harness 插件说明](integrations/deepseek-harness/README.md)。

## 导出一个人的全部历史

先选择该好友，进入“聊天记录”，选择 HTML、TXT 或 JSON，点击“导出全部历史”。导出前会继续同步到导出开始时的消息进度，超过单次同步批次上限也会接着读取；失败不会生成声称完整的文件。导出不调用模型，包含完整长文字、发送者、北京时间及非文字消息类型标记。导出的是本机能够读取的历史，无法补齐手机独有、已删除或尚未迁移的消息；本功能不打包媒体文件。HTML 会转义聊天中的标签和脚本，JSON 适合后续处理。

## macOS 从源码启动

需要 Apple Silicon Mac、Python 3.12、联网安装依赖。当前二进制依赖要求 macOS 14 或更新；这不代表所有系统/微信版本均已验证。微信首次接入还需要 Xcode Command Line Tools 中的 `/usr/bin/lldb` 和系统 Python。

希望省去单独下载向量模型，可在 [GitHub Releases](https://github.com/itslezhi/chatseek/releases) 下载 **ChatSeek-source-with-model.zip**，解压后进入 ChatSeek 目录，再从下面的创建虚拟环境步骤开始。这个包包含公共模型，仍需联网安装 Python 依赖；普通 Git 克隆不包含模型权重。

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

第二步会区分“尚未选择数据”“等待首次接入”“已有接入配置”和“读取已验证”。发现本机微信数据不等于已经选择账号，也不代表微信当前已登录。没有初始化凭据的新设备会从首次接入开始；已接入设备显示保存的账号卡片，不显示无法操作的下拉框。账号昵称只在你点击读取验证后从本机联系人信息核对，无法识别时保留中性标签，不猜测账号身份。

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

源码运行的数据在 `.private/`，个人模型配置可位于项目 `.env` 或 `.private/.env`；可通过 `CHATSEEK_DATA_DIR` 使用独立目录。`.app` 默认使用 `~/Library/Application Support/ChatSeek/`，Windows 启动器默认使用 `%LOCALAPPDATA%\ChatSeek`。本机保存聊天原文、问答、媒体描述、缓存和微信数据库密钥；目前使用普通 SQLite 和权限受限文件，**未作应用层加密**。

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

## 可选：构建 Windows 应用

在原生 Windows x64 环境执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe -X utf8 scripts/build_windows.py
```

输出 `dist/ChatSeek/ChatSeek.exe` 和 `dist/ChatSeek-Windows-x64.zip`。保留整个目录，不要单独复制 EXE。构建脚本运行数据库、FAISS、FFmpeg 自检；这不替代真实微信账号验收。当前未提供已签名 Windows 安装包。

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
