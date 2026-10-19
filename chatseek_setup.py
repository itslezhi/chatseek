"""Explicit first-run setup; never initializes WeChat on page load."""
import json
import os
from pathlib import Path
import platform
import plistlib
import subprocess
import threading
import signal
import re
import hashlib
from chatseek_beta_client import private_json

_lock = threading.Lock()
_job = {'running':False,'message':''}
_process = None
_model_lock = threading.Lock()
_model_job = {'running':False,'error':'','message':''}


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def _checks(private):
    try:
        value=json.loads((Path(private)/'setup-checks.json').read_text())
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):
        return {}


def _remember(private,name,signature):
    with _lock:
        values=_checks(private);values[name]=signature
        private_json(Path(private)/'setup-checks.json',values)


def _environment():
    native=platform.system()=='Darwin' and platform.machine()=='arm64'
    version=platform.mac_ver()[0]
    recent=bool(version and int(version.split('.')[0])>=14)
    app=Path('/Applications/WeChat.app/Contents/Info.plist')
    lldb=Path('/usr/bin/lldb')
    checks=[{'label':'Apple Silicon 原生 macOS','passed':native},
            {'label':'macOS 14 或更新','passed':recent},
            {'label':'已安装微信','passed':app.is_file()},
            {'label':'Xcode Command Line Tools / LLDB','passed':lldb.is_file()}]
    signature=_digest({'system':platform.system(),'machine':platform.machine(),
                       'version':version,'checks':checks,
                       'wechat_modified':app.stat().st_mtime_ns if app.exists() else None,
                       'lldb_modified':lldb.stat().st_mtime_ns if lldb.exists() else None})
    return {'checks':checks,'signature':signature,'ready':all(c['passed'] for c in checks)}


def _wechat_signature(private):
    private=Path(private)
    account=private/('target.json' if (private/'target.json').exists() else 'account.json')
    try:
        root=json.loads(account.read_text())['db_root']
        keys=hashlib.sha256((private/'wechat-init/keys.json').read_bytes()).hexdigest()
        return _digest({'root':root,'keys':keys})
    except (OSError,ValueError,KeyError,TypeError):
        return ''


def _api_signature(config):
    from chatseek_llm import normalized,public_settings
    return _digest({'settings':public_settings(config),'key':normalized(config)['DEEPSEEK_API_KEY']})


def model_status(private):
    from chatseek_models import locate
    from wechat_embedding_client import worker_ready
    location=locate(private)
    with _model_lock:job=dict(_model_job)
    return {**job,'available':location['available'],'source':location['source'],
            'ready':worker_ready(private) and not job['running'] and not job['error']}


def stop_bootstrap():
    process = _process
    if process is not None and process.poll() is None:
        os.killpg(process.pid,signal.SIGINT)
        try:process.wait(timeout=20)
        except subprocess.TimeoutExpired:pass


def accounts():
    base = Path.home()/'Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files'
    return sorted(p.resolve() for p in base.glob('*/db_storage') if p.is_dir())


def status(private):
    import wechat_rag as rag
    from chatseek_beta_client import settings
    private = Path(private)
    version = ''
    try:
        with Path('/Applications/WeChat.app/Contents/Info.plist').open('rb') as handle:
            version = plistlib.load(handle).get('CFBundleShortVersionString','')
    except (OSError,ValueError):
        pass
    selected = (private/'target.json').exists()
    from chatseek_llm import public_settings
    config=rag.env_config()
    try:
        defaults=json.loads((rag.ROOT/'beta-defaults.json').read_text())
    except (OSError,ValueError):
        defaults={}
    environment=_environment()
    checked=_checks(private)
    wechat_signature=_wechat_signature(private)
    model=model_status(private)
    verified={'environment':environment['ready'] and checked.get('environment')==environment['signature'],
              'wechat':bool(wechat_signature and checked.get('wechat')==wechat_signature),
              'api':bool(config.get('DEEPSEEK_API_KEY') and checked.get('api')==_api_signature(config)),
              'model':model['ready']}
    return {'supported':platform.system()=='Darwin' and platform.machine()=='arm64',
            'gateway_url':defaults.get('gateway_url',''),
            'wechat_version':version,'tested_wechat_version':'4.1.15',
            'lldb_available':Path('/usr/bin/lldb').exists(),
            'accounts':[{'id':str(p),'label':p.parent.name} for p in accounts()],
            'keys_ready':(private/'wechat-init/keys.json').exists(),
            'account_ready':selected or (private/'account.json').exists(),
            'contact_ready':selected,'api_configured':bool(config.get('DEEPSEEK_API_KEY')),
            'model_config':public_settings(config),
            'checks':verified,'environment':{'checks':environment['checks']},'local_model':model,
            'runtime':'app' if getattr(__import__('sys'),'frozen',False) else 'source',
            'beta_activated':bool(settings(private).get('token')),
            'data_dir':str(private),'bootstrap':dict(_job)}


def _require(private,step):
    values=status(private)['checks']
    steps=('environment','wechat','api','model')
    if not all(values[name] for name in steps[:steps.index(step)+1]):
        raise RuntimeError('请先完成并验证前面的配置步骤。')


def check_environment(private):
    info=_environment()
    if not info['ready']:
        raise RuntimeError('环境检查未通过：'+'、'.join(c['label'] for c in info['checks'] if not c['passed']))
    try:
        result=subprocess.run(['/usr/bin/lldb','-P'],capture_output=True,timeout=20)
    except (OSError,subprocess.TimeoutExpired):
        raise RuntimeError('LLDB 检查失败，请安装或修复 Xcode Command Line Tools。') from None
    if result.returncode:
        raise RuntimeError('LLDB 尚未就绪，请安装 Xcode Command Line Tools。')
    _remember(private,'environment',info['signature'])
    return {'verified':True}


def check_wechat(private):
    import wechat_rag as rag
    _require(private,'environment')
    signature=_wechat_signature(private)
    if not signature or _job['running']:
        raise RuntimeError('请先完成微信账号选择和密钥初始化。')
    try:
        _,reader=rag.load_runtime()
        with reader.open(reader.root/'contact/contact.db') as db:
            db.query('SELECT count(*) FROM sqlite_master')
    except Exception:
        raise RuntimeError('微信数据库读取检查失败，请确认账号、密钥和文件访问权限。') from None
    _remember(private,'wechat',signature)
    return {'verified':True}


def prepare_model(private):
    from wechat_embedding_client import request
    with _model_lock:
        if _model_job['running']:return dict(_model_job)
        _model_job.update(running=True,error='',message='正在检查本地向量模型…')
    def run():
        try:
            result=request(private,{'action':'tokens','text':'本地模型检查'})
            if not isinstance(result.get('tokens'),list) or not result['tokens']:
                raise ValueError()
            with _model_lock:_model_job.update(running=False,error='',message='本地向量模型已就绪。')
        except Exception:
            with _model_lock:_model_job.update(running=False,error='本地模型检查未通过，请检查网络、模型文件和运行依赖后重试。',message='')
    threading.Thread(target=run,daemon=True).start()
    return model_status(private)


def choose_account(private, value):
    if not isinstance(value,str) or Path(value).resolve() not in accounts():
        raise ValueError('请选择检测到的本机账号。')
    private = Path(private)
    if (private/'target.json').exists():
        raise RuntimeError('已有会话配置；本内测版暂不支持切换微信账号。')
    existing = private/'account.json'
    if existing.exists() and json.loads(existing.read_text())['db_root']!=value and (private/'wechat-init/keys.json').exists():
        raise RuntimeError('已有另一账号的密钥，请使用独立用户数据目录。')
    private_json(existing,{'db_root':value,'chat_id':'','display_name':''})
    return status(private)


def start_bootstrap(private, acknowledged):
    global _job
    import wechat_rag as rag
    if acknowledged is not True:
        raise ValueError('请先阅读并同意临时副本初始化说明。')
    _require(private,'environment')
    current = status(private)
    if not current['supported'] or not current['account_ready'] or not current['lldb_available']:
        raise RuntimeError('需要 Apple Silicon Mac、可用 LLDB，并先选择微信账号。')
    check = subprocess.run(['/usr/bin/lldb','-P'],capture_output=True,timeout=20)
    if check.returncode:
        raise RuntimeError('LLDB 未就绪，请安装 Xcode Command Line Tools 后重试。')
    with _lock:
        if _job['running']:
            raise RuntimeError('初始化正在进行，请完成临时微信窗口的登录。')
        target,_ = rag.load_runtime()
        state = Path(private)/'wechat-init'
        if state.exists():
            raise RuntimeError('已有初始化目录；请退出应用后保留并检查该目录，再重试。')
        _job = {'running':True,'message':'正在准备临时微信副本，请等待登录窗口…'}

    def run():
        global _job,_process
        try:
            command = ['/usr/bin/python3',str(rag.ROOT/'vendor/wechat_local/bootstrap_keys.py'),
                       '--db-root',target['db_root'],'--state-dir',str(state),'--acknowledge-debug-copy']
            with subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,start_new_session=True) as proc:
                _process=proc
                messages = {'copy_prepared':'请在临时微信窗口确认登录；如提示，请用手机完成登录。',
                            'launched':'临时微信已启动，请完成登录并等待数据库密钥验证。',
                            'waiting':'正在等待密钥，请完成临时微信窗口的登录。',
                            'keys_saved_privately':'密钥已保存；初始化结束后请回到 ChatSeek。'}
                for line in proc.stdout:
                    try:
                        event = json.loads(line)
                        if event.get('type') in messages:_job['message']=messages[event['type']]
                    except ValueError:pass
                code = proc.wait()
            _job = {'running':False,'message':'初始化完成。请重新打开原版微信，再选择联系人。' if code==0 and (state/'keys.json').exists()
                    else '初始化未完成。请退出应用并检查初始化目录；勿删除原版微信数据。'}
        except Exception:
            _job = {'running':False,'message':'初始化未完成，请确认已退出微信并安装命令行开发工具。'}
        finally:
            _process=None
    threading.Thread(target=run,daemon=True).start()
    return dict(_job)


def _api_values(key, *, base_url='https://api.deepseek.com',model='deepseek-flash',vision_model=None,
             context_tokens=128000,max_output_tokens=8192,json_mode=True):
    from chatseek_llm import validate_base_url
    base_url=validate_base_url(base_url)
    if not isinstance(key,str) or not 1<=len(key)<=512 or not key.isascii() or any(c.isspace() for c in key):
        raise ValueError('请输入有效的模型 API Key；本机免鉴权服务可填写 local。')
    if vision_model is None:vision_model='deepseek-flash' if base_url=='https://api.deepseek.com' else ''
    for name in (model,vision_model):
        if not isinstance(name,str) or len(name)>128 or (name and not re.fullmatch(r'[A-Za-z0-9_./:@+-]+',name)):
            raise ValueError('模型名称格式无效，请复制服务商提供的模型 ID。')
    if not model or type(context_tokens) is not int or not 1024<=context_tokens<=1000000 or type(max_output_tokens) is not int or not 128<=max_output_tokens<=32768 or type(json_mode) is not bool:
        raise ValueError('请检查模型名称、上下文容量和输出上限。')
    return {'LLM_API_KEY':key,'LLM_BASE_URL':base_url,'LLM_MODEL':model,'LLM_VISION_MODEL':vision_model,
            'LLM_CONTEXT_TOKENS':context_tokens,'LLM_MAX_OUTPUT_TOKENS':max_output_tokens,
            'LLM_JSON_MODE':str(json_mode).lower(),'CHATSEEK_USE_BETA_GATEWAY':'0'}


def save_api(private,key,**options):
    values=_api_values(key,**options)
    import tempfile
    path = Path(private)/'.env'
    fd,tmp = tempfile.mkstemp(dir=Path(private))
    try:
        with os.fdopen(fd,'w') as handle:
            handle.write(''.join(f'{name}={value}\n' for name,value in values.items()))
        os.replace(tmp,path)
    finally:Path(tmp).unlink(missing_ok=True)
    from chatseek_beta_client import settings
    saved = settings(private)
    if saved:
        saved.pop('token',None)
        private_json(Path(private)/'beta.json',saved)
    return {'saved':True}


def verify_api(private,key,**options):
    import wechat_rag as rag
    _require(private,'wechat')
    if not key:key=rag.env_config().get('DEEPSEEK_API_KEY','')
    values=_api_values(key,**options)
    if any(name in os.environ and str(os.environ[name])!=str(values[name]) for name in values if name.startswith('LLM_')):
        raise RuntimeError('本机环境变量正在覆盖模型配置，请先移除对应的 LLM_* 环境变量。')
    try:
        answer=rag.model_completion({**values,'LLM_MAX_OUTPUT_TOKENS':'128'},
            'Connection check. Return only a JSON object with ok set to true.',
            {'task':'connection_check','expected':{'ok':True}},json_mode=True,max_output_tokens=128,timeout=45)
        value=json.loads(answer)
        if not isinstance(value,dict) or value.get('ok') is not True:raise ValueError()
    except Exception:
        raise RuntimeError('API 验证未通过，请检查 Base URL、Model Name、API Key 和 JSON 兼容选项。') from None
    save_api(private,key,**options)
    _remember(private,'api',_api_signature(rag.env_config()))
    return {'verified':True,'saved':True}
