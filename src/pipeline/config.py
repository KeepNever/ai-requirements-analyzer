"""项目配置：从项目根目录 .env 读取 API 配置（M1 最小实现，不引入第三方依赖）。"""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]  # src/pipeline/config.py → 项目根


def _load_dotenv() -> dict:
    """极简 .env 解析：KEY=VALUE，# 开头为注释。"""
    env = {}
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


_ENV = _load_dotenv()
ENV = _ENV  # 公开只读访问（llm_client 读价格配置用）

# DeepSeek OpenAI 兼容接口
DEEPSEEK_API_KEY = _ENV.get("DEEPSEEK_API_KEY", os.environ.get("DEEPSEEK_API_KEY", ""))
DEEPSEEK_BASE_URL = _ENV.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
# 模型 ID 可替换（模型横评时只改这里）——占位名以 DeepSeek 官方文档为准
MODEL_MAIN = _ENV.get("MODEL_MAIN", "deepseek-chat")
TEMPERATURE = float(_ENV.get("TEMPERATURE", "0.1"))
# 引用校验失败自动重试次数（05 框架兜底设计：重试一次，再失败降级）
VALIDATE_RETRY = int(_ENV.get("VALIDATE_RETRY", "1"))


def require_api_key():
    if not DEEPSEEK_API_KEY:
        raise RuntimeError(
            "未找到 DEEPSEEK_API_KEY。请在项目根目录创建 .env 文件（参考 .env.example），"
            "或用 --mock 模式离线跑通链路。"
        )
