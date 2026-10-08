"""pytest 公共配置：把 src/ 加入导入路径（运行时零依赖纪律——只有 dev 环境装 pytest）。"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
