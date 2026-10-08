#!/usr/bin/env python3
"""M1 入口：单案例跑通全链路。

用法（项目根目录）：
  python run_case.py                          # CASE-01 mock 模式（无需 API Key）
  python run_case.py --mock                   # 同上，显式
  python run_case.py --real                   # 真实调用（需 .env 配置 DEEPSEEK_API_KEY）
  python run_case.py --real --case test_data/case_02_零售微信记录.md
"""
import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from pipeline import workflow  # noqa: E402

DEFAULT_CASE = PROJECT_ROOT / "test_data" / "case_01_制造业会议转写.md"


def main():
    parser = argparse.ArgumentParser(description="AI 客户需求分析助手 · 最小 Workflow")
    parser.add_argument("--case", default=str(DEFAULT_CASE), help="案例材料路径")
    parser.add_argument("--real", action="store_true", help="真实调用 LLM（默认 mock 离线模式）")
    parser.add_argument("--mock", action="store_true", help="离线 mock 模式（默认行为，显式写出便于阅读）")
    args = parser.parse_args()

    mode = "真实调用" if args.real else "mock 离线"
    print(f"[M1] 运行模式：{mode}")
    print(f"[M1] 材料：{args.case}")
    out_dir = workflow.run_case(args.case, mock=not args.real)
    print(f"[M1] 完成 ✓ 产出目录：{out_dir}")
    print(f"[M1] 报告：{(out_dir / 'report.md')}")
    print(f"[M1] 结构化数据：{(out_dir / 'analysis.json')}")


if __name__ == "__main__":
    main()
