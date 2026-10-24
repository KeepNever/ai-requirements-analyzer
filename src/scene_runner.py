"""场景运行入口（Task 7）：清洗 → 入库 → 场景分析 → 引用 → 报告 → 落盘。

当前场景：service_diagnosis（学员服务诊断）。售前需求分析走原 workflow，不受影响。

用法：
  python src/scene_runner.py --scene service_diagnosis --case test_data/case_30_学员学习困境对话.md [--real]
"""
import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import storage  # noqa: E402
from pipeline import cleaner  # noqa: E402
from pipeline.service_diagnosis import (  # noqa: E402
    SD_PROMPT_VERSION, analyze_service_diagnosis,
    build_citations_service, build_service_report)

MIN_MATERIAL_CHARS = 30


def run_scene_case(material_path: str, scene: str = "service_diagnosis",
                   mock: bool = True, project_name: str | None = None) -> Path:
    """跑一次场景分析并落盘到版本化存储，返回 run 目录。"""
    if scene != "service_diagnosis":
        raise ValueError(f"未知场景：{scene}")
    material_path = Path(material_path)

    # 1. 清洗（cleaner v3，无 P 编号自动兜底）
    data = cleaner.clean(cleaner.load_material(material_path))
    material_len = len(data.cleaned_text())
    if material_len < MIN_MATERIAL_CHARS:
        raise ValueError(f"材料不足（清洗后仅 {material_len} 字符），拒收")

    # 2. 入库（项目/文档/版本）
    pname = project_name or material_path.stem
    pid = storage.slug(pname)
    if not storage.get_project(pid):
        storage.create_project(pid)
    doc = storage.add_document(pid, material_path.name,
                               material_path.read_text(encoding="utf-8"))
    refs = [{"document_id": doc["document"]["document_id"], "version_id": "v1"}]

    # 3. 运行（meta 带 scene 标识——场景隔离的锚点）
    rdir = storage.create_run(pid, refs, SD_PROMPT_VERSION, "deepseek-chat")
    meta = storage._read_json(rdir / "meta.json")
    meta["scene"] = scene
    storage._write_json(rdir / "meta.json", meta)

    # 4. 场景分析
    sd = analyze_service_diagnosis(data.cleaned_text(), mock=mock)

    # 5. 引用（同一 Citation 结构）+ 报告
    cits = build_citations_service(sd, pid, refs)
    report_md = build_service_report(sd, pname, rdir.name)

    # 6. 落盘
    storage.save_run_outputs(rdir, sd, report_md, cits, None)
    return rdir


def main():
    parser = argparse.ArgumentParser(description="场景分析运行器（服务诊断等）")
    parser.add_argument("--scene", default="service_diagnosis")
    parser.add_argument("--case", required=True, help="材料路径")
    parser.add_argument("--project", default=None, help="项目名（默认取材料文件名）")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--real", action="store_true")
    mode_group.add_argument("--mock", action="store_true")
    args = parser.parse_args()
    rdir = run_scene_case(args.case, scene=args.scene, mock=not args.real,
                          project_name=args.project)
    print(f"[场景:{args.scene}] 完成 ✓ 运行目录：{rdir}")
    print(f"[场景:{args.scene}] 报告：{rdir / 'report.md'}")


if __name__ == "__main__":
    main()
