#!/bin/zsh
cd -- "$(dirname "$0")" || exit 1
if [[ ! -x .venv/bin/python ]]; then
  print '未找到项目 Python 环境，请先按 README 安装依赖。'
  read '?按回车关闭窗口…'
  exit 1
fi
print '启动后请在浏览器打开 http://127.0.0.1:8765'
.venv/bin/python wechat_rag.py web
