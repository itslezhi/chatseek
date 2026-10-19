"""User-owned OpenAI-compatible model configuration; no product relay by default."""
from urllib.parse import urlsplit

DEFAULT_BASE = 'https://api.deepseek.com'
DEFAULT_MODEL = 'deepseek-flash'
FIELDS = ('LLM_API_KEY','LLM_BASE_URL','LLM_MODEL','LLM_VISION_MODEL',
          'LLM_CONTEXT_TOKENS','LLM_MAX_OUTPUT_TOKENS','LLM_JSON_MODE')


def validate_base_url(value):
    if not isinstance(value,str) or any(c.isspace() for c in value):
        raise ValueError('请输入模型 API Base URL。')
    parts = urlsplit(value)
    if not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError('API 地址不能包含用户名、密码、查询参数或片段。')
    if parts.scheme!='https' and not (parts.scheme=='http' and parts.hostname in ('127.0.0.1','localhost','::1')):
        raise ValueError('远程模型 API 请使用 HTTPS；本机模型服务可使用 HTTP。')
    # Users paste either a base URL or the full Chat Completions endpoint.
    return value.rstrip('/').removesuffix('/chat/completions')


def normalized(config):
    result=dict(config)
    base=validate_base_url(config.get('LLM_BASE_URL') or config.get('DEEPSEEK_BASE_URL') or DEFAULT_BASE)
    result['LLM_BASE_URL']=base
    result['DEEPSEEK_API_KEY']=config.get('LLM_API_KEY',config.get('DEEPSEEK_API_KEY',''))
    result['DEEPSEEK_MODEL']=config.get('LLM_MODEL') or config.get('DEEPSEEK_MODEL') or DEFAULT_MODEL
    own_vision='deepseek-flash' if urlsplit(base).hostname=='api.deepseek.com' else ''
    result['DEEPSEEK_VISION_MODEL']=config.get('LLM_VISION_MODEL',config.get('DEEPSEEK_VISION_MODEL',own_vision))
    for public,legacy in (('LLM_CONTEXT_TOKENS','DEEPSEEK_CONTEXT_TOKENS'),('LLM_MAX_OUTPUT_TOKENS','DEEPSEEK_MAX_OUTPUT_TOKENS')):
        if config.get(public):result[legacy]=config[public]
    return result


def completion_url(config):
    if config.get('CHATSEEK_COMPLETIONS_URL'):
        value=config['CHATSEEK_COMPLETIONS_URL']
        return validate_base_url(value)+'/chat/completions'
    return normalized(config)['LLM_BASE_URL']+'/chat/completions'


def provider_options(config):
    # DeepSeek's extension is not part of the common Chat Completions schema.
    return {'thinking':{'type':'disabled'}} if urlsplit(completion_url(config)).hostname=='api.deepseek.com' else {}


def public_settings(config):
    value=normalized(config)
    return {'base_url':value['LLM_BASE_URL'],'model':value['DEEPSEEK_MODEL'],
            'vision_model':value['DEEPSEEK_VISION_MODEL'],
            'context_tokens':int(value.get('DEEPSEEK_CONTEXT_TOKENS',128000)),
            'max_output_tokens':int(value.get('DEEPSEEK_MAX_OUTPUT_TOKENS',8192)),
            'json_mode':str(value.get('LLM_JSON_MODE','true')).lower() not in ('false','0','no')}
