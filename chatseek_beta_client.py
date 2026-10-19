"""Invite activation. Only a per-install bearer token is kept on the client."""
import json
import os
from pathlib import Path
import secrets
import ssl
import tempfile
import urllib.error
import urllib.request
from urllib.parse import urlsplit


def private_json(path, value):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(value, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def settings(private):
    try:
        return json.loads((Path(private)/'beta.json').read_text())
    except (OSError, ValueError):
        return {}


def gateway_url(value):
    if not isinstance(value,str):
        raise ValueError('请输入网关 HTTPS 地址。')
    parts = urlsplit(value)
    local = parts.hostname in ('127.0.0.1','localhost')
    if parts.scheme != 'https' and not (parts.scheme == 'http' and local):
        raise ValueError('内测网关必须使用 HTTPS；本机联调可使用 HTTP。')
    if not parts.hostname or parts.username or parts.password or parts.query or parts.fragment or parts.path not in ('','/'):
        raise ValueError('网关地址只填写域名和端口。')
    return value.rstrip('/')


ERRORS = {401:'邀请码或激活凭证无效，请重新激活。',403:'内测已到期、被停用，或邀请码已绑定另一台设备。',
          409:'内测名额已满或当前有请求处理中，请稍后重试。',413:'本次材料超过内测单次上限，请缩小聊天日期范围。',
          429:'内测调用次数或 token 额度已用完，请联系内测管理员。',
          502:'模型服务请求失败，请稍后重试；已发起的调用可能计入额度。',
          503:'内测网关暂不可用，请稍后重试。'}


def gateway_error(status, detail=''):
    return ERRORS.get(status, f'内测服务请求失败（HTTP {status}）。')


def request(base, path, body=None, token=None):
    headers = {'Content-Type':'application/json'}
    if token:
        headers['Authorization']='Bearer '+token
    req = urllib.request.Request(base+path, data=None if body is None else json.dumps(body).encode(),headers=headers)
    try:
        context=ssl.create_default_context(cafile='/etc/ssl/cert.pem' if Path('/etc/ssl/cert.pem').exists() else None)
        with urllib.request.urlopen(req, timeout=20, context=context) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(gateway_error(exc.code)) from None
    except (OSError, ValueError):
        raise RuntimeError('无法连接内测网关，请检查地址和网络。') from None


def activate(private, base, code):
    base = gateway_url(base)
    if not isinstance(code,str) or not 8 <= len(code.strip()) <= 128:
        raise ValueError('请输入完整邀请码。')
    saved = settings(private)
    device = saved.get('device') or secrets.token_urlsafe(32)
    # Persist before the network call so a lost activation response can be retried.
    private_json(Path(private)/'beta.json', dict(saved,device=device))
    result = request(base, '/v1/activate', {'code':code.strip(),'device':device})
    private_json(Path(private)/'beta.json', {'device':device,'url':base,'token':result['token']})
    return result['quota']


def quota(private):
    saved = settings(private)
    if not saved.get('token'):
        return {'activated':False}
    return {'activated':True, **request(saved['url'],'/v1/quota',token=saved['token'])}


def apply_beta_config(config, private):
    saved = settings(private)
    if saved.get('token') and saved.get('url'):
        return {**config,'DEEPSEEK_API_KEY':saved['token'],'LLM_API_KEY':saved['token'],
                'CHATSEEK_USE_BETA_GATEWAY':'1',
                'CHATSEEK_COMPLETIONS_URL':saved['url']+'/v1/chat/completions'}
    return config
