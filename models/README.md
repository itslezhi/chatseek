# 随包向量模型

Git 仓库不提交大体积权重。GitHub Releases 中的 ChatSeek-source-with-model.zip
会将公共模型放在 models/bge-small-zh-v1.5/，程序优先从该目录离线加载。
使用含模型包仍需安装 Python 依赖；它不是完整离线安装包，也不包含微信密钥或聊天数据。

模型为 [BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5)，
固定 revision 7999e1d3359715c523056ef9478215996d62a620。模型卡标注 MIT 许可，
原始模型卡随包保留，原项目许可证见 [FlagEmbedding-LICENSE](FlagEmbedding-LICENSE)。
bge-small-zh-v1.5.manifest.json 记录从该 revision 核对过的各文件 SHA-256。

普通 Git 克隆在没有随包模型时会复用本机数据目录的缓存，缺失时由配置向导下载一次。
也可设置 CHATSEEK_EMBEDDING_MODEL_DIR 指向完整的本地模型目录。

构建含模型源码包：

    .venv/bin/python scripts/build_offline_source.py --model-dir /完整模型目录

构建含模型 Mac App：

    .venv/bin/python scripts/build_macos.py --embedding-model /完整模型目录
