"""LLM 客户端：OpenAI 兼容 chat/completions，纯标准库 urllib 实现。

设计要点（对应 05 框架兜底机制）：
- 失败自动重试（网络类错误），重试耗尽抛 LLMError
- 温度默认低值（结构化分析任务要稳定不要创造力）
"""
import json
import time
import urllib.error
import urllib.request

from . import config


class LLMError(Exception):
    pass


def chat(messages: list, model: str | None = None, temperature: float | None = None,
         json_mode: bool = True, max_retries: int = 2, timeout: int = 120) -> str:
    """调用 chat/completions，返回 assistant 文本。json_mode=True 时强制 JSON 输出。"""
    model = model or config.MODEL_MAIN
    temperature = config.TEMPERATURE if temperature is None else temperature

    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "response_format": {"type": "json_object"} if json_mode else {"type": "text"},
    }
    url = config.DEEPSEEK_BASE_URL.rstrip("/") + "/chat/completions"
    config.require_api_key()
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
    }

    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(
                url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST"
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            if not content or not content.strip():
                raise LLMError("模型返回空内容")
            return content
        except (urllib.error.HTTPError, urllib.error.URLError, KeyError, json.JSONDecodeError, LLMError) as e:
            last_err = e
            if attempt < max_retries:
                time.sleep(2 * attempt)  # 线性退避
    raise LLMError(f"LLM 调用失败（重试 {max_retries} 次）：{last_err}")
