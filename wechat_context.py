"""Input budgets and typed provider failures for direct-first summaries."""
import json

class ContextLimitError(RuntimeError):pass
class OutputLimitError(RuntimeError):pass


def estimate_tokens(system,payload):
    text=system+json.dumps(payload,ensure_ascii=False)
    try:
        import tiktoken
        # Approximation with headroom; provider context errors remain definitive.
        return int(len(tiktoken.get_encoding('cl100k_base').encode(text,disallowed_special=()))*1.15)+128
    except Exception:
        return len(text.encode('utf-8'))+128


def context_capacity(config):
    default=1_000_000 if config.get('DEEPSEEK_MODEL','deepseek-flash') in ('deepseek-flash','deepseek-v4-pro') else 128_000
    value=int(config.get('DEEPSEEK_CONTEXT_TOKENS',default))
    if not 1024<=value<=1_000_000:raise ValueError('上下文容量须为 1024–1000000 tokens。')
    return value


def direct_fits(config,system,payload):
    return estimate_tokens(system,payload)+int(config.get('DEEPSEEK_MAX_OUTPUT_TOKENS',8192))+4096<=context_capacity(config)


def context_error(status,body):
    if status not in (400,413,422):return False
    text=body.lower()
    return any(word in text for word in ('context_length_exceeded','maximum context','context length','context window','too many tokens','上下文长度','上下文超'))
