# 参与贡献

请先阅读 README 的系统范围和 SECURITY.md。仅使用你有权处理的数据；提交测试请用合成联系人、消息、图片和占位 Key。

建议通过 Issue 描述可复现的问题，再提交小范围 Pull Request。涉及模型适配时，请说明兼容协议、结构化输出、流式响应与视觉输入的支持情况；不要仅依据 HTTP 200 宣称整个工作流兼容。

验证命令：

```sh
.venv/bin/python -m unittest discover -s tests
node tests/test_stream_transport.js
```

不要提交 `.env`、`.private/`、微信数据库/密钥、模型缓存、应用用户数据、构建输出、个人报告或未经检查的截图。不要为测试执行真实微信初始化或真实付费调用。

`vendor/wechat_local` 中的代码须保留其许可证和来源说明。贡献代码将按仓库 LICENSE 约定发布。
