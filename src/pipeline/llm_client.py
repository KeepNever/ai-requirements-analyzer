"""LLM 客户端：OpenAI 兼容 chat/completions，纯标准库 urllib 实现。

M2 治理升级（对应 05 框架兜底机制 §8 与 08 审阅 P2 待办）：
- 错误分类差异化重试：限流/网络/5xx → 指数退避重试；鉴权(401)/参数(400) → 立即失败不重试
- usage 统计：每次调用记录 token 与估算成本，供熔断与预算控制（evaluation 层汇总落盘）

运行时零第三方依赖（开发期 dev 依赖 pytest 不计入）。
"""
import contextvars
import json
import time
import urllib.error
import urllib.request

from . import config

# 请求级用户 Key：线程化服务下每请求独立 context，互不串线。
# 优先级：用户自带 Key > 服务端 .env Key；DEMO_MODE 下服务端 Key 被屏蔽（保护服务方 token）。
_user_api_key: contextvars.ContextVar = contextvars.ContextVar("user_api_key", default="")


def set_request_user_key(key: str):
    _user_api_key.set((key or "").strip())


def _resolve_key() -> str:
    user_key = (_user_api_key.get() or "").strip()
    if user_key:
        return user_key
    if (config.ENV.get("DEMO_MODE", "").strip() == "1"):
        return ""          # 演示模式：绝不消耗服务方 token
    return config.DEEPSEEK_API_KEY


class LLMError(Exception):
    """LLM 调用失败基类。"""


class LLMFatalError(LLMError):
    """不可恢复错误（鉴权失败、参数错误、上下文超限）——重试无意义，立即抛出。"""


class LLMRetryableError(LLMError):
    """可恢复错误（限流、超时、网络抖动、5xx、空内容）——按策略退避重试。"""


# ---------------- usage / 成本统计 ----------------
# DeepSeek 官方价（USD / 每百万 token），可用 .env 覆盖；汇率可配置
PRICE_INPUT_USD_PER_M = float(config.ENV.get("PRICE_INPUT_USD_PER_M", "0.14"))
PRICE_OUTPUT_USD_PER_M = float(config.ENV.get("PRICE_OUTPUT_USD_PER_M", "0.28"))
USD_TO_CNY = float(config.ENV.get("USD_TO_CNY", "7.2"))

usage_log: list = []  # [{model, prompt_tokens, completion_tokens, cost_cny, node}]


def usage_summary() -> dict:
    """汇总本次进程内的全部调用用量（评测报告与成本熔断用）。"""
    calls = len(usage_log)
    pt = sum(u["prompt_tokens"] for u in usage_log)
    ct = sum(u["completion_tokens"] for u in usage_log)
    return {
        "调用次数": calls,
        "输入token": pt,
        "输出token": ct,
        "估算成本元": round(sum(u["cost_cny"] for u in usage_log), 4),
        "明细": usage_log,
    }


def reset_usage():
    usage_log.clear()


def _record_usage(model: str, data: dict, node: str):
    u = (data.get("usage") or {}) if isinstance(data, dict) else {}
    pt, ct = int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0))
    cost = (pt / 1e6 * PRICE_INPUT_USD_PER_M + ct / 1e6 * PRICE_OUTPUT_USD_PER_M) * USD_TO_CNY
    usage_log.append({"model": model, "prompt_tokens": pt, "completion_tokens": ct,
                      "cost_cny": round(cost, 6), "node": node})


def _classify_and_raise(e: Exception, status: int | None):
    """按 HTTP 状态/异常类型分类：致命错误不重试，可恢复错误进退避循环。"""
    if status in (401, 403):
        raise LLMFatalError(f"鉴权失败（HTTP {status}）：检查 .env 中的 DEEPSEEK_API_KEY") from e
    if status == 400:
        raise LLMFatalError(f"请求被拒绝（HTTP 400）：参数或上下文长度问题 → {e}") from e
    if status == 402:
        raise LLMFatalError("余额不足（HTTP 402）：请充值后再跑 Baseline") from e
    raise LLMRetryableError(str(e)) from e


def chat(messages: list, model: str | None = None, temperature: float | None = None,
         json_mode: bool = True, max_retries: int = 3, timeout: int = 120,
         node: str = "") -> str:
    """调用 chat/completions，返回 assistant 文本。错误分类差异化重试（指数退避）。"""
    model = model or config.MODEL_MAIN
    temperature = config.TEMPERATURE if temperature is None else temperature

    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "response_format": {"type": "json_object"} if json_mode else {"type": "text"},
    }
    url = config.DEEPSEEK_BASE_URL.rstrip("/") + "/chat/completions"
    api_key = _resolve_key()
    if not api_key:
        raise LLMFatalError(
            "当前为演示模式且未配置个人模型 Key：请在右上角「模型设置」填入你自己的 "
            "DeepSeek API Key，或使用离线演示模式。"
        )
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
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
                raise LLMRetryableError("模型返回空内容")
            _record_usage(model, data, node)
            return content
        except urllib.error.HTTPError as e:
            _classify_and_raise(e, e.code)  # 致命错误直接抛，可恢复进重试
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last_err = LLMRetryableError(str(e))
        except LLMRetryableError as e:
            last_err = e
        if attempt < max_retries:
            time.sleep(min(2 ** attempt, 8))  # 指数退避：2s/4s/8s 封顶
    raise LLMError(f"LLM 调用失败（重试 {max_retries} 次）：{last_err}")
