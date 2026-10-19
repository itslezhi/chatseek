# 腾讯云中国大陆内测部署

> 此目录保留可选的自托管补贴额度实验。公开版 ChatSeek 默认使用个人 API 配置，不提供邀请码界面；普通使用者无需部署本目录。若自行启用网关，需要额外集成激活入口和凭证配置。

适用于单台新建的 Ubuntu Server 24.04 LTS（也支持 22.04）、systemd、单实例 SQLite 网关。当前只完成本地准备与测试，尚未在云服务器执行；需要完成下列云资源准备后验收公网服务。

## 先准备云资源

1. 腾讯云账号完成实名认证。购买轻量应用服务器，初始配置可选 2 核 2GB、40GB 或以上系统盘；选择 **Ubuntu Server 24.04 LTS 64位系统镜像**。不要选桌面版、付费应用市场镜像或 GPU。广州、上海等大陆地域按用户位置选；实际价格以购买页为准。AI 费用另由 DeepSeek 计费。
2. 注册自己持有的域名并完成实名认证。确认所购服务器符合腾讯云备案条件，然后通过腾讯云办理 ICP 备案。备案前不要对外开放内测。当前项目网关只是技术组件；对外服务的名称、内容和主体资料应按实际情况提交备案。
3. 在 DNS 中添加 `api` 子域名的 A 记录，值为服务器公网 IPv4。没有配置 IPv6 的服务器不要添加 AAAA 记录。本文的 `api.your-domain.com` 必须替换成真实域名。
4. 腾讯云实例防火墙及系统防火墙允许公网 TCP 80/443（证书签发、续期和 HTTPS）；SSH 22 尽量限制到管理员 IP，或使用控制台登录。**不开放 8787**。允许服务器出站 HTTPS，以连接证书服务、软件源及 DeepSeek。
5. 准备 DeepSeek API Key，在服务商控制台设置预算或余额保护。Key 只在服务器隐藏输入，不发聊天、不放客户端、不上传 `.env`。先确认用户同意相关材料经过网关及模型服务商。

腾讯云参考：[轻量服务器](https://cloud.tencent.com/product/lighthouse)、[价格总览](https://cloud.tencent.com/document/product/1207/73452/)、[备案资源要求](https://cloud.tencent.com/document/product/243/18908/)、[实例防火墙](https://cloud.tencent.com/document/product/1207/44577/)。为备案购买前核实套餐时长与剩余有效期要求，不要仅根据促销价格下单。

截至 2026-10-19，腾讯云备案资源文档要求大陆轻量服务器按包年包月购买，累计时长至少 3 个月，且备案期间剩余有效期至少 1 个月。公开价格文档列出入门型 2 核 2GB、40GB、2Mbps、100GB/月为 35 元/月；产品页部分套餐展示价格与文档不同，促销也受账号资格影响，因此这只是选型参考，须在登录后的订单页核对具体配置、续费价和总价，再决定是否付款。域名注册及续费单独计算。此准备步骤未购买或开通任何资源。

## 构建与安装

在开发用 Mac 的项目目录构建独立部署包，无需重新打包 Mac 应用：

```sh
.venv/bin/python scripts/build_beta_gateway.py
```

上传 `dist/ChatSeek-beta-gateway.zip` 到服务器管理员目录并解压（可用控制台文件上传或 SSH/SCP）。不要上传整个项目、`.private`、本机 `.env` 或微信数据库。

在服务器执行，替换域名及证书通知邮箱：

```sh
unzip ChatSeek-beta-gateway.zip
cd chatseek-beta-gateway
sudo bash deploy/install.sh api.your-domain.com admin@your-domain.com
```

如果域名实名或 ICP 备案还在等待，只运行下面的准备命令即可。它安装基础 Python 环境、部署代码和 systemd 配置，并用合成数据检查授权、扣量和备份；不安装 Key、不启动服务、不启用备份定时器、不开放 HTTP/HTTPS、不修改防火墙，也不调用收费模型。完整安装命令要等域名与备案完成后再执行。

```sh
sudo bash deploy/stage.sh
```

脚本安装 Python、Nginx、Certbot，创建独立服务账号，提示隐藏输入 API Key，启动 HTTPS 及服务守护。需要可交互终端。默认域名必须直接解析到该服务器，先不接 CDN、WAF 或其他反向代理，以保证证书验证和按来源 IP 限流准确。新服务器也不需要额外安装宝塔面板。

仅修改 ChatSeek 专用配置；现有其他站点仍需管理员检查端口与域名冲突。脚本可在相同域名下重新运行，保留已有密钥和数据库；更换域名、恢复旧数据库或轮换密钥需单独处理。安装或证书签发失败会停止，修复 DNS/备案/防火墙后再运行。网关一直只监听本机 8787，首次证书签发前的 HTTP 仅提供证书验证，不开放 API。

## 验收与发码

脚本检查本机服务、公网 HTTPS 与未鉴权拒绝，不会生成邀请码或调用收费模型。另在管理员自己的 Mac 上执行一次外部检查，验证公网路径：

```sh
.venv/bin/python beta_gateway/deploy/check_gateway.py https://api.your-domain.com
```

在服务器查看服务状态（不会显示 API Key）：

```sh
sudo systemctl status chatseek-gateway nginx chatseek-backup.timer certbot.timer --no-pager
```

确认服务正常后，只发一个测试码，完成 Mac 客户端激活、额度查询、撤销等验收，再逐步发给真实测试者。邀请码只在发行时显示一次；不要把发码输出放进公开日志。

```sh
sudo -u chatseek python3 /opt/chatseek/server.py --db /var/lib/chatseek/usage.sqlite3 issue --label tester-01 --days 3 --calls 200 --daily 100 --tokens 5000000
sudo -u chatseek python3 /opt/chatseek/server.py --db /var/lib/chatseek/usage.sqlite3 list
sudo -u chatseek python3 /opt/chatseek/server.py --db /var/lib/chatseek/usage.sqlite3 revoke 1
```

默认累计激活名额为 20 人。不要同时启动多套独立数据库。长期部署不等于给每个用户永久额度；每码默认仍是激活后 3 天，发码时可调整 `--days`。一次普通问答通常至少消耗两次模型请求，媒体及分批总结会更多。真实问答验收会产生 DeepSeek 费用，先使用合成文本，不用私人聊天测试。

客户端配置网关地址为 `https://api.your-domain.com`。需要预填地址时重新构建 Mac 包：

```sh
.venv/bin/python scripts/build_macos.py --gateway-url https://api.your-domain.com
```

Mac 安装包正式分发仍需要单独验证签名、公证和其他 Mac 的首次启动，网关部署不会代替这些步骤。

## 限流、日志与备份

- 激活入口每 IP 每分钟 6 次，允许少量突发；每 IP 最多 12 个并行连接。共享出口的测试者可能共同触发限制，后续按实测调整。应用仍执行每码及全局额度、并发限制。
- Nginx 关闭访问日志，关闭代理缓冲，并将请求体缓冲上限设为 8MB，以减少聊天内容落临时文件的机会。不要再接记录正文或 Authorization 的 APM、代理、调试工具；不承诺内存、交换区或宿主机绝不留痕。
- `chatseek-backup.timer` 每天北京时间约 03:15–03:30 执行 SQLite 一致性备份，保留最近 14 份，文件位于 `/var/lib/chatseek-backups/`，权限仅服务账号及管理员可读。它能应对误操作，**不能应对整台服务器丢失**；上线前安排加密异机备份。备份包含授权账本，按敏感数据保存。
- HTTPS 证书通过 `certbot.timer` 自动续期；保持域名解析及 80 端口正常。部署后可用 `sudo certbot renew --dry-run` 检查续期通道。
- 需要恢复账本时先停止网关，保全当前数据库、WAL/SHM 及备份，核验所选备份后再恢复，避免已撤销邀请码或已消耗额度因回滚重新有效。恢复需管理员明确选择时间点。
